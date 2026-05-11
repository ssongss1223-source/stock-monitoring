import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pykrx import stock

from models.signals import PatternLearningResult

logger = logging.getLogger(__name__)

WINDOW_CANDIDATES = [10, 15, 20, 30, 40]
FUTURE_DAYS = 5
SUCCESS_THRESHOLD = 0.05   # +5%
TOP_K = 20
MIN_DATA_DAYS = 100
HOLDOUT_DAYS = 100
CACHE_FILE = Path("data/pattern_cache.json")


class StockPatternLearner:
    """종목별 과거 패턴과 현재 패턴의 유클리드 거리를 계산해 상승 확률을 추정한다."""

    async def run(self, ticker: str, df: pd.DataFrame | None = None,
                  df_60m: pd.DataFrame | None = None) -> PatternLearningResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._analyze, ticker, df, df_60m)

    @staticmethod
    def _insufficient(ticker: str) -> PatternLearningResult:
        return PatternLearningResult(ticker, 0.0, 0, 0.0, 0, 20, "N/A", "INSUFFICIENT")

    def _analyze(self, ticker: str, df: pd.DataFrame | None,
                 df_60m: pd.DataFrame | None = None) -> PatternLearningResult:
        try:
            return self._analyze_inner(ticker, df, df_60m)
        except Exception as e:
            logger.warning("%s 패턴 분석 오류: %s", ticker, e)
            return self._insufficient(ticker)

    def _analyze_inner(self, ticker: str, df: pd.DataFrame | None,
                       df_60m: pd.DataFrame | None = None) -> PatternLearningResult:
        # ── Step A: 데이터 준비 ───────────────────────────────────────────────
        if df is None or df.empty:
            today = date.today().strftime("%Y%m%d")
            start = (date.today() - timedelta(days=900)).strftime("%Y%m%d")
            try:
                df = stock.get_market_ohlcv_by_date(start, today, ticker)
            except Exception as e:
                logger.warning("%s OHLCV 조회 실패: %s", ticker, e)
                return self._insufficient(ticker)

        if df is None or df.empty:
            return self._insufficient(ticker)

        close = _col(df, ("종가", "Close"))
        vol = _col(df, ("거래량", "Volume"))
        if close is None or len(close) < MIN_DATA_DAYS:
            return self._insufficient(ticker)

        # 투자자 데이터 (4채널 시도)
        investor_df = _fetch_investor(ticker, len(close))
        has_investor = investor_df is not None

        # 60분봉 집계 특성
        hourly_features = _build_hourly_features(df.index, df_60m)

        if has_investor and hourly_features is not None:
            dim_label = "FULL(6ch)"
        elif has_investor:
            dim_label = "FULL(4ch)"
        elif hourly_features is not None:
            dim_label = "PARTIAL(4ch+H)"
        else:
            dim_label = "PARTIAL(2ch)"

        # ── Step B: 윈도우 최적화 (종목별 1회, 일별 캐싱) ──────────────────────
        optimal_w = _load_cache(ticker, dim_label)
        if optimal_w is None:
            optimal_w = _optimize_window(close, vol, investor_df, hourly_features)
            _save_cache(ticker, optimal_w, dim_label)

        # ── Step C: 역사적 윈도우 벡터 생성 ──────────────────────────────────
        corpus, labels, returns = _build_corpus(
            close, vol, investor_df, hourly_features, optimal_w
        )
        if corpus is None or len(corpus) < TOP_K:
            return self._insufficient(ticker)

        # 현재 패턴 벡터
        query = _make_vector(close, vol, investor_df, hourly_features, optimal_w, start_idx=len(close) - optimal_w)
        if query is None:
            return self._insufficient(ticker)

        # ── Step D: 유클리드 거리 ─────────────────────────────────────────────
        if np.linalg.norm(query) < 1e-10:
            return self._insufficient(ticker)

        dists = np.linalg.norm(corpus - query, axis=1)
        top_k_idx = np.argsort(dists)[:TOP_K]

        # ── Step E: 집계 및 등급 ──────────────────────────────────────────────
        top_labels = labels[top_k_idx]
        top_returns = returns[top_k_idx]

        confidence = float(np.mean(top_labels))
        similar_count = int(np.sum(top_labels))
        avg_return = float(np.mean(top_returns))

        grade = _grade(confidence, similar_count)

        return PatternLearningResult(
            ticker=ticker,
            pattern_confidence=confidence,
            similar_count=similar_count,
            avg_return_5d=avg_return,
            total_patterns=len(corpus),
            optimal_window=optimal_w,
            pattern_dim=dim_label,
            grade=grade,
        )


# ── 윈도우 최적화 ─────────────────────────────────────────────────────────────

def _optimize_window(
    close: pd.Series,
    vol: pd.Series | None,
    investor_df: pd.DataFrame | None,
    hourly_features: pd.DataFrame | None = None,
) -> int:
    train_close = close.iloc[:-HOLDOUT_DAYS]
    train_vol = vol.iloc[:-HOLDOUT_DAYS] if vol is not None else None
    train_inv = investor_df.iloc[:-HOLDOUT_DAYS] if investor_df is not None else None
    train_hf = hourly_features.iloc[:-HOLDOUT_DAYS] if hourly_features is not None else None

    best_w, best_prec = 20, 0.0
    for w in WINDOW_CANDIDATES:
        corpus, labels, _ = _build_corpus(train_close, train_vol, train_inv, train_hf, w)
        if corpus is None or len(corpus) < TOP_K + 1:
            continue
        # 마지막 hold-out 기간의 각 포지션을 예측해 정밀도 계산
        precisions = []
        for i in range(max(0, len(train_close) - HOLDOUT_DAYS), len(train_close) - w - FUTURE_DAYS):
            q = _make_vector(train_close, train_vol, train_inv, train_hf, w, start_idx=i)
            if q is None:
                continue
            if np.linalg.norm(q) < 1e-10:
                continue
            dists = np.linalg.norm(corpus - q, axis=1)
            top_k_idx = np.argsort(dists)[:TOP_K]
            precisions.append(float(np.mean(labels[top_k_idx])))
        if precisions:
            prec = float(np.mean(precisions))
            if prec > best_prec:
                best_prec, best_w = prec, w

    return best_w


# ── 코퍼스 빌더 ───────────────────────────────────────────────────────────────

def _build_corpus(
    close: pd.Series,
    vol: pd.Series | None,
    investor_df: pd.DataFrame | None,
    hourly_features: pd.DataFrame | None,
    w: int,
) -> tuple[np.ndarray | None, np.ndarray, np.ndarray]:
    vectors, labels, rets = [], [], []
    n = len(close)
    for i in range(n - w - FUTURE_DAYS):
        vec = _make_vector(close, vol, investor_df, hourly_features, w, start_idx=i)
        if vec is None:
            continue
        future_close = close.iloc[i + w: i + w + FUTURE_DAYS]
        base_price = float(close.iloc[i + w - 1])
        if base_price <= 0:
            continue
        peak = float(future_close.max())
        ret = (peak - base_price) / base_price * 100.0
        label = 1 if peak >= base_price * (1 + SUCCESS_THRESHOLD) else 0
        vectors.append(vec)
        labels.append(label)
        rets.append(ret)

    if not vectors:
        return None, np.array([]), np.array([])
    return np.array(vectors), np.array(labels, dtype=np.float32), np.array(rets, dtype=np.float32)


def _make_vector(
    close: pd.Series,
    vol: pd.Series | None,
    investor_df: pd.DataFrame | None,
    hourly_features: pd.DataFrame | None,
    w: int,
    start_idx: int,
) -> np.ndarray | None:
    end_idx = start_idx + w
    if end_idx > len(close):
        return None

    c_slice = close.iloc[start_idx:end_idx].values.astype(float)
    if c_slice[0] <= 0:
        return None

    price_ret = (c_slice - c_slice[0]) / c_slice[0]

    channels = [price_ret]

    if vol is not None and len(vol) >= end_idx:
        v_slice = vol.iloc[start_idx:end_idx].values.astype(float)
        # 20일 이동평균 대비 비율
        roll_start = max(0, start_idx - 20)
        roll_vol = vol.iloc[roll_start:end_idx].values.astype(float)
        vol_avg = np.convolve(roll_vol, np.ones(min(20, len(roll_vol))) / min(20, len(roll_vol)), mode="valid")
        if len(vol_avg) >= w:
            avgs = vol_avg[-w:]
        else:
            avgs = np.ones(w) * (float(np.mean(v_slice)) if float(np.mean(v_slice)) > 0 else 1.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            vol_ratio = np.where(avgs > 0, v_slice / avgs, 1.0)
        channels.append(vol_ratio)

    if investor_df is not None and len(investor_df) >= end_idx:
        for col in ("외국인합계", "기관합계"):
            if col not in investor_df.columns:
                continue
            net = investor_df[col].iloc[start_idx:end_idx].values.astype(float)
            total_abs = investor_df.abs().sum(axis=1).iloc[start_idx:end_idx].values.astype(float)
            with np.errstate(invalid="ignore", divide="ignore"):
                ratio = np.where(total_abs > 0, net / total_abs, 0.0)
            channels.append(ratio)

    if hourly_features is not None and len(hourly_features) >= end_idx:
        for col in ("intraday_momentum", "intraday_range"):
            if col in hourly_features.columns:
                h_slice = hourly_features[col].iloc[start_idx:end_idx].values.astype(float)
                if len(h_slice) == w:
                    channels.append(h_slice)

    vec = np.concatenate(channels)
    if not np.isfinite(vec).all():
        vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    return vec


# ── 등급 판정 ─────────────────────────────────────────────────────────────────

def _grade(confidence: float, similar_count: int) -> str:
    if confidence >= 0.65 and similar_count >= 5:
        return "HIGH"
    if confidence >= 0.50 and similar_count >= 3:
        return "MEDIUM"
    if confidence >= 0.35 and similar_count >= 2:
        return "LOW"
    return "INSUFFICIENT"


# ── 캐시 ─────────────────────────────────────────────────────────────────────

def _load_cache(ticker: str, dim: str = "") -> int | None:
    today = date.today().strftime("%Y%m%d")
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            entry = data.get(ticker)
            if entry and entry.get("date") == today and entry.get("dim", "") == dim:
                return int(entry["window"])
    except Exception:
        pass
    return None


def _save_cache(ticker: str, window: int, dim: str = "") -> None:
    today = date.today().strftime("%Y%m%d")
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[ticker] = {"window": window, "date": today, "dim": dim}
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("패턴 캐시 저장 실패: %s", e)


# ── 투자자 데이터 ─────────────────────────────────────────────────────────────

def _fetch_investor(ticker: str, needed_rows: int) -> pd.DataFrame | None:
    try:
        today = date.today().strftime("%Y%m%d")
        # 필요 행수보다 여유있게 fetch (거래일 기준이므로 달력일 2배)
        start = (date.today() - timedelta(days=needed_rows * 2)).strftime("%Y%m%d")
        df = stock.get_market_trading_value_by_date(start, today, ticker, detail=True)
        if df is None or df.empty:
            return None
        req_cols = {"외국인합계", "기관합계"}
        if not req_cols.issubset(df.columns):
            return None
        return df.tail(needed_rows)
    except Exception as e:
        logger.debug("%s 투자자 데이터 조회 실패 (2ch fallback): %s", ticker, e)
        return None


# ── 60분봉 특성 빌더 ─────────────────────────────────────────────────────────

def _build_hourly_features(
    daily_index: pd.DatetimeIndex,
    df_60m: pd.DataFrame | None,
) -> pd.DataFrame | None:
    """
    60분봉을 일봉 타임스텝으로 집계하여 2채널 특성 생성.

    채널:
    - intraday_momentum: 각 거래일 (마지막캔들종가 - 첫번째캔들종가) / 첫번째캔들종가
    - intraday_range:    각 거래일 (max고가 - min저가) / 첫번째캔들종가

    반환: columns=['intraday_momentum', 'intraday_range'], index=daily_index (date만)
    실패 시 None 반환.
    """
    if df_60m is None or df_60m.empty:
        return None
    try:
        # 컬럼 정규화 (Close, High, Low 필요)
        col_map = {}
        for target, candidates in [
            ("Close", ("Close", "종가")),
            ("High",  ("High", "고가")),
            ("Low",   ("Low", "저가")),
        ]:
            for c in candidates:
                if c in df_60m.columns:
                    col_map[target] = c
                    break
        if len(col_map) < 3:
            return None

        # 날짜별 그룹화 (index가 DatetimeIndex 가정)
        df_60m = df_60m.copy()
        df_60m.index = pd.to_datetime(df_60m.index)
        grouped = df_60m.groupby(df_60m.index.date)

        records = {}
        for day, grp in grouped:
            if len(grp) < 2:
                continue
            first_close = float(grp[col_map["Close"]].iloc[0])
            last_close  = float(grp[col_map["Close"]].iloc[-1])
            day_high    = float(grp[col_map["High"]].max())
            day_low     = float(grp[col_map["Low"]].min())
            if first_close <= 0:
                continue
            records[day] = {
                "intraday_momentum": (last_close - first_close) / first_close,
                "intraday_range":    (day_high - day_low) / first_close,
            }

        if not records:
            return None

        result = pd.DataFrame.from_dict(records, orient="index")
        result.index = pd.to_datetime(result.index)

        # daily_index의 date와 매핑
        daily_dates = pd.to_datetime(daily_index).normalize()
        result = result.reindex(daily_dates)
        result = result.ffill().fillna(0.0)
        result.index = daily_index

        return result
    except Exception as e:
        logger.debug("60분봉 특성 생성 실패: %s", e)
        return None


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, candidates: tuple) -> pd.Series | None:
    for c in candidates:
        if c in df.columns:
            return df[c].astype(float)
    return None
