import asyncio
import json
import logging
import threading
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pykrx import stock

import config as _cfg
from core.scoring_engine import ScoringEngine
from models.signals import VolumeResult

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()


class VolumeProfileCache:
    """종목별 시간대별 거래량 분포 캐시 (당일 1회 갱신).

    캐시 구조 per ticker:
    {
        "date": "20260507",
        "hourly": {
            "9":  {"p90": ..., "p95": ..., "p99": ..., "mean": ...},
            "10": {...}, ...
        },
        "daily_turnover_p80": 5000000000.0
    }
    """

    _cache: dict = {}
    _loaded: bool = False

    @classmethod
    def get_or_update(cls, ticker: str, df_60m: pd.DataFrame) -> dict:
        with _CACHE_LOCK:
            cls._load()
            today_str = date.today().strftime("%Y%m%d")
            entry = cls._cache.get(ticker, {})
            if entry.get("date") == today_str:
                return entry
            profile = cls._compute(df_60m)
            profile["date"] = today_str
            cls._cache[ticker] = profile
            cls._save()
            return profile

    @classmethod
    def _load(cls) -> None:
        if cls._loaded:
            return
        p = Path(_cfg.VOLUME_PROFILE_CACHE)
        if p.exists():
            try:
                cls._cache = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                cls._cache = {}
        cls._loaded = True

    @classmethod
    def _compute(cls, df_60m: pd.DataFrame) -> dict:
        """시간대별 P90/P95/P99/mean + 일별 거래대금 P80 계산."""
        result: dict = {"hourly": {}, "daily_turnover_p80": 0.0}
        vol_name = _col(df_60m, ("Volume", "거래량"))
        close_name = _col(df_60m, ("Close", "종가"))
        if vol_name is None:
            return result

        df = df_60m[[c for c in [vol_name, close_name] if c]].copy()
        df.index = pd.to_datetime(df.index)
        df["_hour"] = df.index.hour
        df["_vol"] = df[vol_name].astype(float)

        # 시간대별 거래량 분포
        for hour, grp in df.groupby("_hour"):
            vols = grp["_vol"].dropna()
            if len(vols) >= 5:
                result["hourly"][str(hour)] = {
                    "p90": float(vols.quantile(0.90)),
                    "p95": float(vols.quantile(0.95)),
                    "p99": float(vols.quantile(0.99)),
                    "mean": float(vols.mean()),
                }

        # 일별 거래대금 P80
        if close_name:
            df["_tv"] = df[close_name].astype(float) * df["_vol"]
        else:
            df["_tv"] = df["_vol"]
        df["_date"] = df.index.normalize()
        daily_tv = df.groupby("_date")["_tv"].sum()
        if len(daily_tv) >= 5:
            result["daily_turnover_p80"] = float(daily_tv.quantile(0.80))

        return result

    @classmethod
    def _save(cls) -> None:
        try:
            p = Path(_cfg.VOLUME_PROFILE_CACHE)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(cls._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("VolumeProfileCache 저장 실패: %s", e)


class VolumeAnalysisAgent:
    """60분봉 기반 종목별 거래량 행동 프로파일링.

    종목마다 자신의 60분봉 히스토리에서 시간대별 P90/P95/P99를 산출하여
    고유 임계값으로 이상 수급 이벤트를 탐지한다.
    df_60m 없으면 volume_score=0 반환.
    """

    def __init__(self, engine: ScoringEngine):
        self.engine = engine

    async def run(
        self,
        ticker: str,
        df: pd.DataFrame | None = None,
        df_60m: pd.DataFrame | None = None,
    ) -> VolumeResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._analyze, ticker, df_60m)

    def _analyze(self, ticker: str, df_60m: pd.DataFrame | None) -> VolumeResult:
        if df_60m is None or df_60m.empty:
            logger.warning("%s 60분봉 데이터 없음 — volume_score=0", ticker)
            return _empty(ticker)

        today = _today()

        try:
            profile = VolumeProfileCache.get_or_update(ticker, df_60m)
        except Exception as e:
            logger.warning("%s 프로파일 캐시 오류: %s", ticker, e)
            profile = {}

        last_day = _last_day_df(df_60m)
        if last_day is None or last_day.empty:
            return _empty(ticker)

        fi_buy, fi_sell = _foreign_inst_flow(ticker, today)

        flags = {
            "ticker": ticker,
            "hourly_vol_ratio_p95": _same_time_vol_ratio(last_day, profile),
            "hourly_vol_zscore_high": _volume_zscore_60m(df_60m, last_day),
            "relative_turnover_high": _relative_turnover_60m(last_day, profile),
            "vwap_above_60m": _vwap_above_60m(last_day),
            "obv_slope_up_60m": _obv_slope_60m(df_60m),
            "obv_divergence_60m": _obv_divergence_60m(df_60m),
            "obv_acceleration_60m": _obv_acceleration_60m(df_60m),
            "foreign_inst_buy": fi_buy,
            "foreign_inst_sell": fi_sell,
        }
        return self.engine.score_volume(flags)


# ── 60분봉 Feature 함수 ────────────────────────────────────────────────────────

def _same_time_vol_ratio(last_day: pd.DataFrame, profile: dict) -> bool:
    """마지막 거래일 내 어느 봉이든 동일 시간대 P95 초과 시 True."""
    vol_name = _col(last_day, ("Volume", "거래량"))
    if vol_name is None or last_day.empty:
        return False
    hourly = profile.get("hourly", {})
    for ts, row in last_day.iterrows():
        slot = hourly.get(str(ts.hour), {})
        p95 = slot.get("p95")
        if p95 is None:
            continue
        if float(row[vol_name]) >= p95:
            return True
    return False


def _volume_zscore_60m(
    df_60m: pd.DataFrame, last_day: pd.DataFrame, threshold: float = 2.0
) -> bool:
    """마지막 거래일 최대 거래량의 z-score >= threshold (과거 분포 기준)."""
    vol_name = _col(df_60m, ("Volume", "거래량"))
    if vol_name is None or last_day.empty:
        return False

    all_idx = pd.to_datetime(df_60m.index)
    last_norm = pd.to_datetime(last_day.index).normalize().min()
    hist_mask = all_idx.normalize() < last_norm
    hist_vol = df_60m.loc[hist_mask, vol_name].astype(float)

    if len(hist_vol) < 10:
        return False
    mean = float(hist_vol.mean())
    std = float(hist_vol.std())
    if std == 0:
        return False
    max_vol = float(last_day[vol_name].astype(float).max())
    return (max_vol - mean) / std >= threshold


def _relative_turnover_60m(last_day: pd.DataFrame, profile: dict) -> bool:
    """마지막 거래일 거래대금 합이 과거 P80 이상."""
    vol_name = _col(last_day, ("Volume", "거래량"))
    close_name = _col(last_day, ("Close", "종가"))
    if vol_name is None or last_day.empty:
        return False
    p80 = profile.get("daily_turnover_p80", 0.0)
    if p80 == 0:
        return False
    if close_name:
        tv = float(
            (last_day[close_name].astype(float) * last_day[vol_name].astype(float)).sum()
        )
    else:
        tv = float(last_day[vol_name].astype(float).sum())
    return tv >= p80


def _vwap_above_60m(last_day: pd.DataFrame) -> bool:
    """마지막 봉 종가 > 당일 VWAP."""
    close_name = _col(last_day, ("Close", "종가"))
    vol_name = _col(last_day, ("Volume", "거래량"))
    if close_name is None or vol_name is None or last_day.empty:
        return False
    closes = last_day[close_name].astype(float)
    vols = last_day[vol_name].astype(float)
    total_vol = float(vols.sum())
    if total_vol == 0:
        return False
    vwap = float((closes * vols).sum()) / total_vol
    return float(closes.iloc[-1]) > vwap


def _obv_slope_60m(df_60m: pd.DataFrame, window: int = 20) -> bool:
    """최근 window개 60분봉 OBV 선형 기울기 양수."""
    close_name = _col(df_60m, ("Close", "종가"))
    vol_name = _col(df_60m, ("Volume", "거래량"))
    if close_name is None or vol_name is None or len(df_60m) < window:
        return False
    recent = df_60m.tail(window)
    close = recent[close_name].astype(float)
    vol = recent[vol_name].astype(float)
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * vol).cumsum()
    x = np.arange(len(obv))
    try:
        slope = float(np.polyfit(x, obv.values, 1)[0])
    except Exception:
        return False
    return slope > 0


def _obv_divergence_60m(df_60m: pd.DataFrame, window: int = 20) -> bool:
    """최근 20개 60분봉: 가격 횡보(-2%~+3%) + OBV 우상향 (조용한 매집 탐지)."""
    close_name = _col(df_60m, ("Close", "종가"))
    vol_name = _col(df_60m, ("Volume", "거래량"))
    if close_name is None or vol_name is None or len(df_60m) < window:
        return False
    recent = df_60m.tail(window)
    close = recent[close_name].astype(float)
    vol = recent[vol_name].astype(float)

    price_ret = (close.iloc[-1] - close.iloc[0]) / close.iloc[0]
    if price_ret >= 0.10:  # 급등 추격 제외
        return False
    if not (-0.02 <= price_ret <= 0.03):
        return False

    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * vol).cumsum()

    if obv.iloc[-1] <= obv.iloc[0]:  # ② OBV 변화율 > 0
        return False

    x = np.arange(len(obv))
    try:
        slope = float(np.polyfit(x, obv.values, 1)[0])
    except Exception:
        return False
    return slope > 0  # ③ OBV 회귀 기울기 > 0


def _obv_acceleration_60m(df_60m: pd.DataFrame, half: int = 10) -> bool:
    """최근 10봉 OBV 기울기 > 이전 10봉 기울기 × 1.3 (OBV 상승 가속)."""
    close_name = _col(df_60m, ("Close", "종가"))
    vol_name = _col(df_60m, ("Volume", "거래량"))
    if close_name is None or vol_name is None or len(df_60m) < half * 2:
        return False
    recent = df_60m.tail(half * 2)
    close = recent[close_name].astype(float)
    vol = recent[vol_name].astype(float)
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * vol).cumsum()

    x = np.arange(half)
    try:
        prev_slope = float(np.polyfit(x, obv.iloc[:half].values, 1)[0])
        curr_slope = float(np.polyfit(x, obv.iloc[half:].values, 1)[0])
    except Exception:
        return False

    if prev_slope <= 0 or curr_slope <= 0:  # ① ② 두 구간 모두 양수
        return False
    return curr_slope > prev_slope * 1.3  # ③ 가속 조건


# ── 수급 (pykrx 일봉 기반 유지) ───────────────────────────────────────────────

def _foreign_inst_flow(ticker: str, today: str) -> tuple[bool, bool]:
    """외국인/기관 순매수/순매도 여부. 최근 5거래일 합산. 2단계 fallback."""
    _FOREIGN_COLS = ("외국인합계", "외국인", "기관합계", "투신")
    for use_detail in (True, False):
        try:
            kw = {"detail": True} if use_detail else {}
            df = stock.get_market_trading_value_by_date(_ago(10), today, ticker, **kw)
            if df is not None and not df.empty:
                for col in _FOREIGN_COLS:
                    if col in df.columns:
                        net = float(df[col].iloc[-5:].sum())
                        return net > 0, net < 0
        except Exception:
            pass
    logger.warning("%s 수급 API 실패 — 외국인/기관 점수 0 처리", ticker)
    return False, False


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, candidates: tuple) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _last_day_df(df_60m: pd.DataFrame) -> pd.DataFrame | None:
    """df_60m에서 마지막 거래일 데이터만 추출."""
    idx = pd.to_datetime(df_60m.index)
    last_norm = idx.normalize().max()
    result = df_60m[idx.normalize() == last_norm].copy()
    result.index = pd.to_datetime(result.index)
    return result if not result.empty else None


def _empty(ticker: str) -> VolumeResult:
    return VolumeResult(ticker=ticker, volume_score=0, explosion_imminent=False, smart_money_flow="neutral")


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")
