import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from pykrx import stock

from core.scoring_engine import ScoringEngine
from models.signals import TechnicalResult

logger = logging.getLogger(__name__)


class TechnicalAnalysisAgent:
    """전략서 6~8장: 다중 이동평균 + 일목균형표 + 패턴 인식 + 지지/저항."""

    def __init__(self, engine: ScoringEngine):
        self.engine = engine
        self.last_df: pd.DataFrame | None = None  # 패턴 학습 재사용 (추가 API 호출 방지)

    async def run(self, ticker: str, df: pd.DataFrame | None = None) -> TechnicalResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._analyze, ticker, df)

    def _analyze(self, ticker: str, df: pd.DataFrame | None = None) -> TechnicalResult:
        if df is None:
            today = _today()
            try:
                # 240일선 + 일목균형표에 필요한 데이터: 최소 300 거래일 이상
                df = stock.get_market_ohlcv_by_date(_ago(420), today, ticker)
            except Exception as e:
                logger.warning("%s OHLCV 조회 실패: %s", ticker, e)
                self.last_df = None
                return _empty(ticker)

        if df is None or df.empty or len(df) < 60:
            self.last_df = None
            return _empty(ticker)

        self.last_df = df

        close = _col(df, ("종가", "Close"))
        high = _col(df, ("고가", "High"))
        low = _col(df, ("저가", "Low"))
        vol = _col(df, ("거래량", "Volume"))
        if close is None or high is None or low is None:
            return _empty(ticker)

        ma_flags = _ma_flags(close)
        ichi_flags = _ichimoku_flags(close, high, low)
        pattern = _detect_pattern(close, high, low, vol)
        support = _find_support(close, low)
        resistance = _find_resistance(close, high)
        atr = _calc_atr(high, low, close)
        current_price = float(close.iloc[-1])

        flags = {
            "ticker": ticker,
            "pattern": pattern,
            "support": support,
            "resistance": resistance,
            **ma_flags,
            **ichi_flags,
        }
        result = self.engine.score_technical(flags)
        result.current_price = current_price
        result.atr = atr
        return result


# ── 이동평균 플래그 (전략서 6-1) ──────────────────────────────────────────────

def _ma_flags(close: pd.Series) -> dict:
    n = len(close)
    c = float(close.iloc[-1])

    def ma(w):
        if n < w:
            return None
        v = float(close.rolling(w).mean().iloc[-1])
        return None if np.isnan(v) else v

    ma20, ma60, ma120, ma240 = ma(20), ma(60), ma(120), ma(240)

    def gt(a, b):
        return bool(a is not None and b is not None and a > b)

    # ma120 우상향: 지금 ma120 > 20거래일 전 ma120
    ma120_rising = False
    if ma120 is not None and n >= 140:
        past = close.rolling(120).mean().iloc[-21]
        if not np.isnan(past):
            ma120_rising = float(close.rolling(120).mean().iloc[-1]) > float(past)

    # 52주 신고가 5% 이내
    near_high = False
    if n >= 252:
        h52 = float(close.iloc[-252:].max())
        near_high = c >= h52 * 0.95

    return {
        "price_above_20ma": gt(c, ma20),
        "price_above_60ma": gt(c, ma60),
        "price_above_120ma": gt(c, ma120),
        "price_above_240ma": gt(c, ma240),
        "ma20_above_ma60": gt(ma20, ma60),
        "ma60_above_ma120": gt(ma60, ma120),
        "ma120_uptrend": ma120_rising,
        "near_52w_high_5pct": near_high,
    }


# ── 일목균형표 (전략서 6-2) ───────────────────────────────────────────────────

def _ichimoku_flags(close: pd.Series, high: pd.Series, low: pd.Series) -> dict:
    if len(close) < 52:
        return {k: False for k in ("ichimoku_triple_positive", "ichimoku_cloud_support",
                                    "ichimoku_cloud_break", "ichimoku_dead_cross")}

    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)

    c = float(close.iloc[-1])
    sa = float(span_a.iloc[-1]) if not pd.isna(span_a.iloc[-1]) else 0.0
    sb = float(span_b.iloc[-1]) if not pd.isna(span_b.iloc[-1]) else 0.0
    cloud_top = max(sa, sb)
    cloud_bot = min(sa, sb)

    above_cloud = c > cloud_top > 0
    below_cloud = c < cloud_bot and cloud_bot > 0

    tk = float(tenkan.iloc[-1]) if not pd.isna(tenkan.iloc[-1]) else 0.0
    kj = float(kijun.iloc[-1]) if not pd.isna(kijun.iloc[-1]) else 0.0
    tenkan_above = tk > kj

    # 후행스팬 > 26일 전 종가
    lagging_bullish = (len(close) >= 27 and float(close.iloc[-1]) > float(close.iloc[-27]))

    triple = tenkan_above and above_cloud and lagging_bullish

    # 구름대 지지: 위에 있지만 구름 상단 5% 이내 + 최근 저점이 구름 상단에 닿음
    cloud_support = False
    if above_cloud and cloud_top > 0 and len(low) >= 3:
        recent_low = float(low.iloc[-3:].min())
        cloud_support = c >= cloud_top * 0.98 and recent_low <= cloud_top * 1.01

    # 데드크로스: 오늘 tenkan < kijun && 어제 tenkan >= kijun
    dead_cross = False
    if len(tenkan) >= 2 and not pd.isna(tenkan.iloc[-2]) and not pd.isna(kijun.iloc[-2]):
        dead_cross = (float(tenkan.iloc[-1]) < float(kijun.iloc[-1])) and \
                     (float(tenkan.iloc[-2]) >= float(kijun.iloc[-2]))

    return {
        "ichimoku_triple_positive": triple,
        "ichimoku_cloud_support": cloud_support and not triple,
        "ichimoku_cloud_break": below_cloud,
        "ichimoku_dead_cross": dead_cross,
    }


# ── 패턴 인식 (전략서 7장) ───────────────────────────────────────────────────

def _detect_pattern(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    vol: pd.Series | None,
) -> Optional[str]:
    """우선순위 순서로 패턴 체크. 첫 번째 감지된 패턴 반환."""
    if _cup_handle(close, high, low):
        return "cup_handle"
    if _falling_box_breakout_pullback(close, high, low):
        return "falling_box_breakout"
    if _triangle_convergence(close, high, low, vol):
        return "triangle_convergence"
    if _bb_squeeze(close):
        return "bb_squeeze"
    return None


def _falling_box_breakout_pullback(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    box_window: int = 40,
    recent_window: int = 25,
) -> bool:
    """하락 박스 돌파 후 눌림목 패턴.
    1. 직전 box_window일간 하락 채널 (고점↓ + 저점↓, 최소 10% 하락)
    2. 최근 recent_window일 내 채널 상단선 돌파
    3. 돌파 후 조정이 상승 대비 1.5배 이상 완만하고 50% 미만 반납
    4. 현재가가 채널 상단선 위에서 유지 (±5%)
    """
    needed = box_window + recent_window
    if len(close) < needed:
        return False

    # ── 1. 하락 채널 확인 ─────────────────────────────────────────────────
    box_h = high.iloc[-needed:-recent_window].values.astype(float)
    box_l = low.iloc[-needed:-recent_window].values.astype(float)
    x = np.arange(box_window)
    high_coef = np.polyfit(x, box_h, 1)
    low_coef = np.polyfit(x, box_l, 1)

    if high_coef[0] >= 0 or low_coef[0] >= 0:
        return False

    # 하락폭 최소 10% (노이즈 필터)
    box_depth = (box_h[0] - box_l[-1]) / box_h[0] if box_h[0] > 0 else 0
    if box_depth < 0.10:
        return False

    # ── 2. 채널 상단선을 recent_window 구간으로 연장 ──────────────────────
    channel_tops = np.array([
        np.polyval(high_coef, box_window + i) for i in range(recent_window)
    ])
    recent_close = close.iloc[-recent_window:].values.astype(float)
    above = recent_close > channel_tops

    if not above.any():
        return False

    first_break = int(np.argmax(above))
    post_break = recent_close[first_break:]

    if len(post_break) < 2:
        return True  # 방금 돌파 — 돌파 신호

    # ── 3. 돌파 후 고점 찾기 ─────────────────────────────────────────────
    peak_rel = int(np.argmax(post_break))
    peak_abs = first_break + peak_rel

    if peak_abs == len(recent_close) - 1:
        return True  # 현재가 = 고점, 아직 조정 없음

    # ── 4. 조정 각도 분석 ─────────────────────────────────────────────────
    rise_days = peak_rel + 1
    correction_days = len(recent_close) - 1 - peak_abs

    if correction_days < 2:
        return False

    smooth = correction_days >= rise_days * 1.5

    peak_price = post_break[peak_rel]
    current = float(recent_close[-1])
    not_collapsed = (peak_price - current) / peak_price < 0.50 if peak_price > 0 else False
    still_above = current > channel_tops[-1] * 0.95

    return smooth and not_collapsed and still_above


def _bb_squeeze(close: pd.Series, window: int = 20) -> bool:
    """볼린저밴드 수축 (에너지 응축). 현재 밴드폭 < 최근 6개월 20th percentile."""
    if len(close) < 126:
        return False
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    bw = (2 * std / ma).dropna()
    if len(bw) < 60:
        return False
    hist = bw.iloc[-126:]
    return float(bw.iloc[-1]) < float(hist.quantile(0.2))


def _triangle_convergence(
    close: pd.Series, high: pd.Series, low: pd.Series, vol: pd.Series | None, window: int = 20
) -> bool:
    """삼각수렴: 고점 하락 + 저점 상승 + 거래량 감소."""
    if len(close) < window:
        return False
    x = np.arange(window)
    h = high.iloc[-window:].values.astype(float)
    lo = low.iloc[-window:].values.astype(float)

    high_slope = float(np.polyfit(x, h, 1)[0])
    low_slope = float(np.polyfit(x, lo, 1)[0])

    vol_shrinking = True
    if vol is not None and len(vol) >= window:
        v = vol.iloc[-window:].values.astype(float)
        vol_shrinking = float(np.polyfit(x, v, 1)[0]) < 0

    return high_slope < 0 and low_slope > 0 and vol_shrinking


def _cup_handle(close: pd.Series, high: pd.Series, low: pd.Series,
                cup_w: int = 60, handle_w: int = 20) -> bool:
    """컵앤핸들: 컵(U형) 후 핸들(15% 이내 눌림)."""
    if len(close) < cup_w + handle_w:
        return False
    cup_close = close.iloc[-(cup_w + handle_w):-handle_w]
    handle_close = close.iloc[-handle_w:]
    cup_high = float(high.iloc[-(cup_w + handle_w):-handle_w].max())
    cup_low = float(low.iloc[-(cup_w + handle_w):-handle_w].min())

    # U형 확인: 저점과 고점의 낙폭이 최소 5% 이상
    cup_depth = (cup_high - cup_low) / cup_low if cup_low > 0 else 0
    if cup_depth < 0.05:  # 최소 5% 낙폭 필요
        return False

    handle_low = float(handle_close.min())
    pullback = (cup_high - handle_low) / cup_high if cup_high > 0 else 1
    recovering = float(handle_close.iloc[-1]) > handle_low * 1.02
    return pullback <= 0.15 and recovering


# ── ATR (Average True Range) ─────────────────────────────────────────────────

def _calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> Optional[float]:
    """14일 ATR. 종목별 변동성 기반 손절폭 산출에 사용."""
    if len(close) < period + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = float(tr.rolling(period).mean().iloc[-1])
    return None if np.isnan(val) else val


# ── 지지/저항 (전략서 8장) ───────────────────────────────────────────────────

def _find_support(close: pd.Series, low: pd.Series, window: int = 60) -> Optional[float]:
    """최근 60일 20th percentile 저점을 지지선으로 사용."""
    if len(low) < 10:
        return None
    recent = low.iloc[-window:] if len(low) >= window else low
    return float(recent.quantile(0.2))


def _find_resistance(close: pd.Series, high: pd.Series, window: int = 60) -> Optional[float]:
    """최근 60일 80th percentile 고점을 저항선으로 사용."""
    if len(high) < 10:
        return None
    recent = high.iloc[-window:] if len(high) >= window else high
    return float(recent.quantile(0.8))


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, candidates: tuple) -> pd.Series | None:
    for c in candidates:
        if c in df.columns:
            return df[c].astype(float)
    return None


def _empty(ticker: str) -> TechnicalResult:
    return TechnicalResult(
        ticker=ticker, trend_score=0, ichimoku_score=0,
        pattern=None, support=None, resistance=None, current_price=0.0,
    )


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")
