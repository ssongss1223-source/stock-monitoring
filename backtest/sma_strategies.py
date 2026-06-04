# backtest/sma_strategies.py
"""SMA breakout / pullback 전략 함수. Account 프리미티브를 사용해 거래 실행."""
from __future__ import annotations

import pandas as pd

from backtest.account import Account
from backtest.sma_signal import compute_sma, get_breakout_signals, get_pullback_signals
from backtest.sma_config import FIXED


def _mid(high: float, low: float) -> float:
    return (high + low) / 2


def run_sma_breakout(
    ohlcv: pd.DataFrame,
    sma_period: int,
    profit_levels: list[tuple[float, float]] = FIXED["profit_levels"],
    commission: float = FIXED["commission"],
) -> tuple[list[dict], list[float]]:
    """SMA Breakout 전략 — 2일 분할 진입, SMA exit, 래더 익절.

    Args:
        ohlcv: DataFrame (index=DatetimeIndex, columns: close, high, low)
        sma_period: SMA 기간
        profit_levels: [(threshold_pct, fraction_of_holdings), ...]
        commission: 편도 수수료

    Returns:
        trades, equity_curve
    """
    close = ohlcv["close"]
    high  = ohlcv["high"]
    low   = ohlcv["low"]

    sma = compute_sma(close, sma_period)
    breakout = get_breakout_signals(close, sma_period)

    acc = Account(commission=commission)

    # 상태: 'none' | 'pending_half' | 'half' | 'pending_full' | 'full' | 'pending_exit'
    state = "none"
    exit_reason = ""

    for i in range(len(ohlcv)):
        dt    = str(ohlcv.index[i].date())
        c     = close.iloc[i]
        h     = high.iloc[i]
        lv    = low.iloc[i]
        mid   = _mid(h, lv)
        sma_v = sma.iloc[i]

        if pd.isna(sma_v) or pd.isna(c):
            acc.record_equity(c if not pd.isna(c) else 0)
            continue

        # ── 전날 결정 실행 ────────────────────────────────────────
        if state == "pending_half":
            acc.buy(dt, mid, fraction_of_cash=0.5)
            state = "half"

        elif state == "pending_full":
            acc.buy(dt, mid, fraction_of_cash=1.0)
            state = "full"

        elif state == "pending_exit":
            acc.sell(dt, mid, fraction_of_holdings=1.0, reason=exit_reason)
            state = "none"

        # ── 현재 포지션 처리 ───────────────────────────────────────
        if state == "half":
            if c < sma_v:
                # T+1 종가 < SMA → T+2에 손절
                state = "pending_exit"
                exit_reason = "sma_exit_half"
            else:
                # T+1 종가 >= SMA → T+2에 나머지 매수
                state = "pending_full"

        elif state == "full":
            gain = (c / acc.avg_entry_price - 1) * 100 if acc.avg_entry_price > 0 else 0
            if c < sma_v:
                state = "pending_exit"
                exit_reason = "sma_exit"
            else:
                for threshold, fraction in profit_levels:
                    if gain >= threshold and not acc.profit_level_hit(threshold):
                        acc.sell(dt, mid, fraction_of_holdings=fraction,
                                 reason=f"ladder_{int(threshold)}")
                        acc.mark_profit_level(threshold)

        # ── 신규 진입 ─────────────────────────────────────────────
        if state == "none" and breakout.iloc[i]:
            state = "pending_half"

        acc.record_equity(c)

    # 마지막날 강제 청산
    if acc.in_position:
        last_dt  = str(ohlcv.index[-1].date())
        last_mid = _mid(high.iloc[-1], low.iloc[-1])
        acc.sell(last_dt, last_mid, fraction_of_holdings=1.0, reason="end_of_period")

    return acc.trades, acc.equity_curve


def run_pullback(
    ohlcv: pd.DataFrame,
    sma_period: int,
    pullback_sma_delta: int,
    stop_loss_pct: float = 8.0,
    profit_levels: list[tuple[float, float]] = FIXED["profit_levels"],
    commission: float = FIXED["commission"],
) -> tuple[list[dict], list[float]]:
    """Pullback 전략 — fast SMA 터치 진입, SMA_main exit + hard stop, 래더 익절.

    Args:
        ohlcv: DataFrame (index=DatetimeIndex, columns: close, high, low)
        sma_period: 메인 SMA 기간
        pullback_sma_delta: fast SMA offset (fast_period = sma_period - delta)
        stop_loss_pct: hard stop (진입가 대비 -%,  예: 8.0 = -8%)
        profit_levels: 래더 익절 레벨
        commission: 편도 수수료

    Returns:
        trades, equity_curve
    """
    close = ohlcv["close"]
    high  = ohlcv["high"]
    low   = ohlcv["low"]

    sma_main = compute_sma(close, sma_period)
    pullback = get_pullback_signals(close, sma_period, pullback_sma_delta)

    acc = Account(commission=commission)

    # 상태: 'none' | 'pending_entry' | 'in' | 'pending_exit'
    state = "none"
    exit_reason = ""

    for i in range(len(ohlcv)):
        dt    = str(ohlcv.index[i].date())
        c     = close.iloc[i]
        h     = high.iloc[i]
        lv    = low.iloc[i]
        mid   = _mid(h, lv)
        sma_v = sma_main.iloc[i]

        if pd.isna(sma_v) or pd.isna(c):
            acc.record_equity(c if not pd.isna(c) else 0)
            continue

        # ── 전날 결정 실행 ────────────────────────────────────────
        if state == "pending_entry":
            acc.buy(dt, mid, fraction_of_cash=1.0)
            state = "in"

        elif state == "pending_exit":
            acc.sell(dt, mid, fraction_of_holdings=1.0, reason=exit_reason)
            state = "none"

        # ── 현재 포지션 처리 ───────────────────────────────────────
        if state == "in":
            gain = (c / acc.avg_entry_price - 1) * 100 if acc.avg_entry_price > 0 else 0

            # 1. 하드 스탑 (당일 중간값 즉시 매도)
            if gain <= -stop_loss_pct:
                acc.sell(dt, mid, fraction_of_holdings=1.0, reason="hard_stop")
                state = "none"

            # 2. SMA_main 이탈 → 다음날 청산
            elif c < sma_v:
                state = "pending_exit"
                exit_reason = "sma_exit"

            # 3. 래더 익절 (당일 중간값)
            else:
                for threshold, fraction in profit_levels:
                    if gain >= threshold and not acc.profit_level_hit(threshold):
                        acc.sell(dt, mid, fraction_of_holdings=fraction,
                                 reason=f"ladder_{int(threshold)}")
                        acc.mark_profit_level(threshold)

        # ── 신규 진입 ─────────────────────────────────────────────
        if state == "none" and pullback.iloc[i]:
            state = "pending_entry"

        acc.record_equity(c)

    # 마지막날 강제 청산
    if acc.in_position:
        last_dt  = str(ohlcv.index[-1].date())
        last_mid = _mid(high.iloc[-1], low.iloc[-1])
        acc.sell(last_dt, last_mid, fraction_of_holdings=1.0, reason="end_of_period")

    return acc.trades, acc.equity_curve
