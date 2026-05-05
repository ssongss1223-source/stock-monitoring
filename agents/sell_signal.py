import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from pykrx import stock

import config
from core.scoring_engine import ScoringEngine
from models.signals import SellSignal

logger = logging.getLogger(__name__)


class SellSignalAgent:
    """
    전략서 9장: 보유 종목별 매도 신호 스코어링.
    장세 무관, 항상 실행. portfolio.json의 보유 종목을 분석한다.
    """

    def __init__(self, engine: ScoringEngine):
        self.engine = engine

    async def run(self) -> list[SellSignal]:
        portfolio = _load_portfolio()
        if not portfolio:
            return []

        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, self._analyze, ticker, buy_price)
            for ticker, buy_price in portfolio.items()
        ]
        results = await asyncio.gather(*tasks)
        signals = [s for s in results if s is not None]

        # 우선순위 순 정렬 (1 = 가장 긴급)
        signals.sort(key=lambda s: s.priority)
        logger.info("SellSignal: 보유 %d종목 분석, 신호 %d개", len(portfolio), len(signals))
        return signals

    def _analyze(self, ticker: str, buy_price: float) -> Optional[SellSignal]:
        today = _today()
        try:
            df = stock.get_market_ohlcv_by_date(_ago(90), today, ticker)
        except Exception as e:
            logger.warning("%s OHLCV 조회 실패: %s", ticker, e)
            return None

        if df is None or df.empty or len(df) < 20:
            return None

        close = _col(df, ("종가", "Close"))
        high = _col(df, ("고가", "High"))
        low = _col(df, ("저가", "Low"))
        vol = _col(df, ("거래량", "Volume"))
        if close is None:
            return None

        current_price = float(close.iloc[-1])
        profit_pct = (current_price - buy_price) / buy_price * 100
        name = _get_name(ticker)

        # ── 1순위: 강제 손절 (즉시 발동) ─────────────────────────────────────
        forced_flags = _forced_stoploss_flags(current_price, buy_price, close, vol)
        if self.engine.check_forced_stoploss(forced_flags):
            reason = "매수가 대비 -5% 이탈" if forced_flags.get("stoploss_5pct") else "60일선 이탈 + 거래량 폭발"
            return SellSignal(
                ticker=ticker, name=name, action="stop_loss", priority=1,
                score=0, reason=reason,
                buy_price=buy_price, current_price=current_price, profit_pct=profit_pct,
            )

        # 공통 보조 데이터
        rsi = _compute_rsi(close)
        ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else None
        fi_sell = _foreign_sell(ticker, today)
        fi_buy_strong = _foreign_strong_buy(ticker, today)

        # ── 익절 스코어링 (9-1) ───────────────────────────────────────────────
        profit_flags = _profit_flags(profit_pct, rsi, close, vol, high, fi_sell)
        profit_score = self.engine.score_sell_profit(profit_flags)

        # ── 추세 이탈 스코어링 (9-2) ──────────────────────────────────────────
        trend_flags = _trend_break_flags(current_price, close, vol, rsi, low, fi_sell)
        trend_score = self.engine.score_sell_trend_break(trend_flags)

        # ── 보유 연장 억제 (9-4) ──────────────────────────────────────────────
        hold_flags = _hold_extension_flags(close, vol, high, fi_buy_strong)
        hold_mod = self.engine.score_sell_hold_extension(hold_flags)  # 음수

        # 실효 스코어 = 각 시나리오 점수 + 억제 점수(음수)
        eff_profit = profit_score + hold_mod
        eff_trend = trend_score + hold_mod

        pt = self.engine.sell_thresholds("profit_taking")
        tb = self.engine.sell_thresholds("trend_break")

        # ── 우선순위에 따른 액션 결정 (9-5) ──────────────────────────────────
        if eff_trend >= tb["strong_sell"]:
            return SellSignal(
                ticker=ticker, name=name, action="full_sell", priority=2,
                score=eff_trend, reason=_trend_reason(trend_flags, hold_mod),
                buy_price=buy_price, current_price=current_price, profit_pct=profit_pct,
            )
        if eff_profit >= pt["full_sell"]:
            return SellSignal(
                ticker=ticker, name=name, action="full_sell", priority=3,
                score=eff_profit, reason=_profit_reason(profit_flags, hold_mod),
                buy_price=buy_price, current_price=current_price, profit_pct=profit_pct,
            )
        if eff_profit >= pt["half_sell"]:
            return SellSignal(
                ticker=ticker, name=name, action="half_sell", priority=4,
                score=eff_profit, reason=_profit_reason(profit_flags, hold_mod),
                buy_price=buy_price, current_price=current_price, profit_pct=profit_pct,
            )

        # 기준 미달 → hold (신호 없음은 None 반환, 신호 있으면 hold 반환)
        if eff_trend > 0 or eff_profit > 0:
            return SellSignal(
                ticker=ticker, name=name, action="hold", priority=5,
                score=max(eff_profit, eff_trend),
                reason=f"억제조건 적용 후 기준 미달 (hold_mod={hold_mod})",
                buy_price=buy_price, current_price=current_price, profit_pct=profit_pct,
            )
        return None


# ── 플래그 계산 함수 ──────────────────────────────────────────────────────────

def _forced_stoploss_flags(
    current: float, buy_price: float,
    close: pd.Series, vol: pd.Series | None,
) -> dict:
    loss_pct = (current - buy_price) / buy_price * 100
    stoploss_5pct = loss_pct <= -5.0

    # 60일선 이탈 + 거래량 폭발 하락
    stoploss_60ma = False
    if len(close) >= 60 and vol is not None and len(vol) >= 20:
        ma60 = float(close.rolling(60).mean().iloc[-1])
        avg_vol = float(vol.iloc[-20:].mean())
        high_vol = float(vol.iloc[-1]) > avg_vol * 1.5
        stoploss_60ma = (current < ma60) and high_vol

    return {"stoploss_5pct": stoploss_5pct, "stoploss_60ma_volume": stoploss_60ma}


def _profit_flags(
    profit_pct: float, rsi: float | None,
    close: pd.Series, vol: pd.Series | None,
    high: pd.Series | None, fi_sell: bool,
) -> dict:
    # 수익률 티어 (상호 배타적)
    profit_8pct = 8.0 <= profit_pct < 12.0
    profit_12pct = profit_pct >= 12.0

    # RSI 티어 (상호 배타적)
    rsi_above_70 = rsi is not None and 70.0 <= rsi < 80.0
    rsi_above_80 = rsi is not None and rsi >= 80.0

    # 거래량 없이 고점 횡보 3일
    high_range_low_vol_3d = False
    if vol is not None and high is not None and len(close) >= 3:
        avg_vol = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else float(vol.mean())
        recent_high = float(high.iloc[-3:].max())
        low_vol_3d = all(float(vol.iloc[-i]) < avg_vol * 0.7 for i in range(1, 4))
        near_high_3d = float(close.iloc[-1]) >= recent_high * 0.97
        high_range_low_vol_3d = low_vol_3d and near_high_3d

    # 장대 음봉
    large_bearish_candle = False
    if len(close) >= 2:
        day_change = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        large_bearish_candle = day_change <= -3.0

    return {
        "profit_8pct": profit_8pct,
        "profit_12pct": profit_12pct,
        "rsi_above_70": rsi_above_70,
        "rsi_above_80": rsi_above_80,
        "high_range_low_vol_3d": high_range_low_vol_3d,
        "large_bearish_candle": large_bearish_candle,
        "foreign_inst_net_sell": fi_sell,
    }


def _trend_break_flags(
    current: float, close: pd.Series, vol: pd.Series | None,
    rsi: float | None, low: pd.Series | None, fi_sell: bool,
) -> dict:
    ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else None

    break_20ma = ma20 is not None and current < ma20
    break_60ma = ma60 is not None and current < ma60

    # 이탈 당일 거래량 평균 이상
    break_with_high_vol = False
    if vol is not None and len(vol) >= 20 and (break_20ma or break_60ma):
        avg_vol = float(vol.iloc[-20:].mean())
        break_with_high_vol = float(vol.iloc[-1]) >= avg_vol

    # 직전 저점 하향 돌파
    lower_low = False
    if low is not None and len(low) >= 10:
        prev_low = float(low.iloc[-10:-1].min())
        lower_low = current < prev_low

    # 음봉 3일 연속
    bearish_3d = False
    if len(close) >= 3:
        bearish_3d = all(float(close.iloc[-i]) < float(close.iloc[-(i+1)]) for i in range(1, 4))

    # 외국인 3일 연속 순매도 (fi_sell 재활용: 최근 3일 합산 음수)
    fi_3d_sell = fi_sell  # _foreign_sell이 최근 5일 합산 음수 기준 (근사)

    return {
        "break_20ma": break_20ma,
        "break_60ma": break_60ma,
        "break_with_high_volume": break_with_high_vol,
        "rsi_below_45": rsi is not None and rsi <= 45.0,
        "lower_low": lower_low,
        "foreign_3d_net_sell": fi_3d_sell,
        "bearish_3d_consecutive": bearish_3d,
    }


def _hold_extension_flags(
    close: pd.Series, vol: pd.Series | None,
    high: pd.Series | None, fi_buy_strong: bool,
) -> dict:
    # 거래량 동반 강한 양봉
    strong_bull = False
    if vol is not None and len(vol) >= 20 and len(close) >= 2:
        avg_vol = float(vol.iloc[-20:].mean())
        day_change = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        strong_bull = day_change >= 2.0 and float(vol.iloc[-1]) >= avg_vol * 1.5

    # 52주 신고가 경신 중
    new_52w_high = False
    if high is not None and len(high) >= 252:
        new_52w_high = float(high.iloc[-1]) >= float(high.iloc[-252:].max()) * 0.995

    # 20일선 강하게 우상향 (20일선이 20거래일 전보다 2% 이상 상승)
    ma20_strong = False
    if len(close) >= 40:
        ma20_now = float(close.rolling(20).mean().iloc[-1])
        ma20_prev = float(close.rolling(20).mean().iloc[-21])
        ma20_strong = (ma20_now - ma20_prev) / ma20_prev >= 0.02

    return {
        "strong_bullish_candle_volume": strong_bull,
        "foreign_strong_net_buy": fi_buy_strong,
        "new_52w_high": new_52w_high,
        "ma20_strong_uptrend": ma20_strong,
    }


# ── 사유 문자열 생성 ──────────────────────────────────────────────────────────

def _profit_reason(flags: dict, hold_mod: int) -> str:
    parts = []
    if flags.get("profit_12pct"):
        parts.append("수익률 12%↑")
    elif flags.get("profit_8pct"):
        parts.append("수익률 8%↑")
    if flags.get("rsi_above_80"):
        parts.append("RSI 80↑")
    elif flags.get("rsi_above_70"):
        parts.append("RSI 70↑")
    if flags.get("high_range_low_vol_3d"):
        parts.append("고점횡보3일")
    if flags.get("large_bearish_candle"):
        parts.append("장대음봉")
    if hold_mod < 0:
        parts.append(f"억제{hold_mod}점")
    return " / ".join(parts) or "익절 조건"


def _trend_reason(flags: dict, hold_mod: int) -> str:
    parts = []
    if flags.get("break_60ma"):
        parts.append("60일선 이탈")
    elif flags.get("break_20ma"):
        parts.append("20일선 이탈")
    if flags.get("lower_low"):
        parts.append("저점 하향")
    if flags.get("bearish_3d_consecutive"):
        parts.append("음봉3일")
    if hold_mod < 0:
        parts.append(f"억제{hold_mod}점")
    return " / ".join(parts) or "추세 이탈"


# ── 수급/지표 조회 ────────────────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    v = float(rsi.iloc[-1])
    return None if pd.isna(v) else v


def _foreign_sell(ticker: str, today: str) -> bool:
    try:
        df = stock.get_market_trading_value_by_date(_ago(10), today, ticker, detail=True)
        if df is None or df.empty:
            return False
        for col in ("외국인합계", "외국인"):
            if col in df.columns:
                return float(df[col].iloc[-5:].sum()) < 0
        return False
    except Exception:
        return False


def _foreign_strong_buy(ticker: str, today: str) -> bool:
    try:
        df = stock.get_market_trading_value_by_date(_ago(5), today, ticker, detail=True)
        if df is None or df.empty:
            return False
        for col in ("외국인합계", "외국인"):
            if col in df.columns:
                return float(df[col].iloc[-1]) > 0
        return False
    except Exception:
        return False


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _load_portfolio() -> dict[str, float]:
    try:
        with open(config.PORTFOLIO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("portfolio.json 로드 실패: %s", e)
        return {}


def _get_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        return ticker


def _col(df: pd.DataFrame, candidates: tuple) -> pd.Series | None:
    for c in candidates:
        if c in df.columns:
            return df[c].astype(float)
    return None


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")
