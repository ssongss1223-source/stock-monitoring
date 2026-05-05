import asyncio
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from pykrx import stock

from core.scoring_engine import ScoringEngine
from models.signals import VolumeResult

logger = logging.getLogger(__name__)


class VolumeAnalysisAgent:
    """전략서 5장: 거래량 폭발 예측 + OBV + 수급 분석."""

    def __init__(self, engine: ScoringEngine):
        self.engine = engine

    async def run(self, ticker: str) -> VolumeResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._analyze, ticker)

    def _analyze(self, ticker: str) -> VolumeResult:
        today = _today()
        try:
            df = stock.get_market_ohlcv_by_date(_ago(90), today, ticker)
        except Exception as e:
            logger.warning("%s OHLCV 조회 실패: %s", ticker, e)
            return _empty(ticker)

        if df is None or df.empty or len(df) < 21:
            return _empty(ticker)

        close = _series(df, ("종가", "Close"))
        vol = _series(df, ("거래량", "Volume"))
        if close is None or vol is None:
            return _empty(ticker)

        obv = _compute_obv(close, vol)
        fi_buy, fi_sell = _foreign_inst_flow(ticker, today)

        flags = {
            "ticker": ticker,
            "vol_5d_above_20d": _vol_5d_above_20d(vol),
            "vol_consecutive_3d": _vol_consecutive_3d(vol),
            "vol_rise_price_flat": _vol_rise_price_flat(vol, close),
            "obv_uptrend_price_flat": _obv_uptrend_price_flat(obv, close),
            "vol_vs_52w_low_2x": _vol_vs_52w_low(vol, ticker, today),
            "foreign_inst_buy": fi_buy,
            "foreign_inst_sell": fi_sell,
            "short_interest_declining": _short_declining(ticker, today),
        }
        return self.engine.score_volume(flags)


# ── 조건 판별 함수 ────────────────────────────────────────────────────────────

def _vol_5d_above_20d(vol: pd.Series) -> bool:
    if len(vol) < 20:
        return False
    return float(vol.iloc[-5:].mean()) > float(vol.iloc[-20:].mean())


def _vol_consecutive_3d(vol: pd.Series) -> bool:
    if len(vol) < 3:
        return False
    return (float(vol.iloc[-1]) > float(vol.iloc[-2])) and (float(vol.iloc[-2]) > float(vol.iloc[-3]))


def _vol_rise_price_flat(vol: pd.Series, close: pd.Series, window: int = 10) -> bool:
    """주가 횡보(변동 3% 미만) + 거래량 우상향."""
    if len(vol) < window or len(close) < window:
        return False
    c = close.iloc[-window:]
    price_range = (float(c.max()) - float(c.min())) / float(c.min())
    vol_rising = float(vol.iloc[-1]) > float(vol.iloc[-window])
    return price_range < 0.03 and vol_rising


def _obv_uptrend_price_flat(obv: pd.Series, close: pd.Series, window: int = 10) -> bool:
    if len(obv) < window or len(close) < window:
        return False
    c = close.iloc[-window:]
    price_range = (float(c.max()) - float(c.min())) / float(c.min())
    obv_rising = float(obv.iloc[-1]) > float(obv.iloc[-window])
    return price_range < 0.03 and obv_rising


def _vol_vs_52w_low(vol: pd.Series, ticker: str, today: str) -> bool:
    """최근 5일 평균 거래량 ≥ 52주 저거래량 구간 평균의 2배."""
    try:
        df_52w = stock.get_market_ohlcv_by_date(_ago(365), today, ticker)
        if df_52w is None or df_52w.empty:
            return False
        vol_52w = _series(df_52w, ("거래량", "Volume"))
        if vol_52w is None or len(vol_52w) < 20:
            return False
        low_avg = float(vol_52w[vol_52w <= vol_52w.quantile(0.2)].mean())
        recent_avg = float(vol.iloc[-5:].mean())
        return recent_avg >= low_avg * 2
    except Exception:
        return False


def _foreign_inst_flow(ticker: str, today: str) -> tuple[bool, bool]:
    """(외국인/기관 순매수 여부, 순매도 여부). 최근 5거래일 합산.
    pykrx 1.2.8에서 detail=True 엔드포인트 불안정 → 실패 시 (False, False) 반환.
    """
    try:
        df = stock.get_market_trading_value_by_date(_ago(10), today, ticker, detail=True)
        if df is None or df.empty or len(df.columns) == 0:
            return False, False
        for col in ("외국인합계", "외국인"):
            if col in df.columns:
                net = float(df[col].iloc[-5:].sum())
                return net > 0, net < 0
        return False, False
    except Exception:
        return False, False


def _short_declining(ticker: str, today: str) -> bool:
    """공매도 잔량 최근 감소 추세.
    pykrx 1.2.8에서 해당 엔드포인트 불안정 → 실패 시 False 반환.
    """
    try:
        df = stock.get_shorting_balance_by_date(_ago(30), today, ticker)
        if df is None or df.empty or len(df) < 5 or len(df.columns) == 0:
            return False
        for col in ("잔량", "shortBalance", "공매도잔량"):
            if col in df.columns:
                return float(df[col].iloc[-1]) < float(df[col].iloc[-5])
        return False
    except Exception:
        return False


# ── 지표 계산 ─────────────────────────────────────────────────────────────────

def _compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _series(df: pd.DataFrame, candidates: tuple) -> pd.Series | None:
    for col in candidates:
        if col in df.columns:
            return df[col].astype(float)
    return None


def _empty(ticker: str) -> VolumeResult:
    return VolumeResult(ticker=ticker, volume_score=0, explosion_imminent=False, smart_money_flow="neutral")


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")
