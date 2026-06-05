# backtest/pullback_signal.py
"""추세-눌림 타이밍 엔진 신호 함수."""
from __future__ import annotations

import pandas as pd


def regime_gate(
    close: pd.Series,
    trend_period: int = 200,
    slope_lookback: int = 20,
) -> pd.Series:
    """추세 게이트: 종가 > MA(trend_period) AND MA가 상승중.

    Returns:
        Boolean Series. NaN 없음 — 워밍업 기간은 False.
    """
    ma = close.rolling(trend_period).mean()
    above_ma = close > ma
    ma_rising = ma > ma.shift(slope_lookback)
    gate = above_ma & ma_rising
    return gate.fillna(False)


def pullback_entry(
    close: pd.Series,
    gate: pd.Series,
    entry_period: int = 50,
    band_pct: float = 5.0,
) -> pd.Series:
    """눌림 진입: 추세 ON + 종가가 MA(entry_period) ±band_pct% 내.

    Returns:
        Boolean Series.
    """
    ma_entry = close.rolling(entry_period).mean()
    upper = ma_entry * (1 + band_pct / 100)
    lower = ma_entry * (1 - band_pct / 100)
    in_band = (close <= upper) & (close >= lower)
    return (gate & in_band).fillna(False)


def exit_signal(
    close: pd.Series,
    trend_period: int = 200,
) -> pd.Series:
    """청산 신호: 종가 < MA(trend_period) — 추세 붕괴.

    Returns:
        Boolean Series.
    """
    ma = close.rolling(trend_period).mean()
    return (close < ma).fillna(False)


def compute_signals(
    ohlcv: pd.DataFrame,
    trend_period: int = 200,
    entry_period: int = 50,
    band_pct: float = 5.0,
    slope_lookback: int = 20,
    market_gate: pd.Series | None = None,
) -> pd.DataFrame:
    """OHLCV → regime / entry / exit 신호 DataFrame.

    Args:
        market_gate: 외부 시장 추세 게이트 (예: 코스피 regime_gate).
                     제공 시 개별 종목 gate와 AND 결합 — Layer1 regime gate.

    Returns:
        DataFrame(columns=['regime', 'entry', 'exit'], index=ohlcv.index)
    """
    close = ohlcv["close"]
    gate = regime_gate(close, trend_period, slope_lookback)
    if market_gate is not None:
        aligned = market_gate.reindex(close.index, fill_value=False)
        gate = gate & aligned
    entry = pullback_entry(close, gate, entry_period, band_pct)
    exits = exit_signal(close, trend_period)
    # 진입·청산 동시 불가 — 청산 우선
    entry = entry & ~exits
    return pd.DataFrame({"regime": gate, "entry": entry, "exit": exits},
                        index=ohlcv.index)
