# backtest/sma_metrics.py
"""SMA 백테스트 성과 지표 계산."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(
    trades: list[dict],
    equity_curve: list[float],
    test_close: pd.Series,
) -> dict:
    """trades + MTM equity_curve → 성과 지표.

    Args:
        trades: Account.trades (entry/exit 기록)
        equity_curve: Account.equity_curve (MTM, 매 거래일)
        test_close: 같은 구간의 종가 시리즈 (buy&hold 비교용)

    Returns:
        calmar, cagr, mdd, win_rate, profit_factor, ev, total_trades, vs_buyhold
    """
    if not equity_curve or len(equity_curve) < 2:
        return _empty_metrics()

    eq = pd.Series(equity_curve)
    running_max = eq.cummax()
    drawdowns = (eq - running_max) / running_max * 100
    mdd = float(drawdowns.min())  # 음수

    n_years = len(test_close) / 252
    final = equity_curve[-1]
    initial = equity_curve[0]
    cagr = ((final / initial) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0.0

    calmar = cagr / abs(mdd) if mdd != 0 else 0.0

    # 전량 청산 trades만 win/loss 계산 (fraction=1.0 or 마지막 거래)
    full_trades = [t for t in trades if t.get("fraction", 1.0) >= 1.0 - 1e-6]
    pnls = [t["pnl_pct"] for t in full_trades] if full_trades else []

    if pnls:
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) * 100
        avg_win  = float(np.mean(wins))  if wins   else 0.0
        avg_loss = float(abs(np.mean(losses))) if losses else 0.0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0.0
        ev = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)
    else:
        win_rate = profit_factor = ev = 0.0

    bh_return = (test_close.iloc[-1] / test_close.iloc[0] - 1) * 100
    vs_buyhold = (final / initial - 1) * 100 - bh_return

    return {
        "calmar":        round(calmar, 4),
        "cagr":          round(cagr, 2),
        "mdd":           round(mdd, 2),
        "win_rate":      round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "ev":            round(ev, 2),
        "total_trades":  len(full_trades),
        "vs_buyhold":    round(vs_buyhold, 2),
    }


def combine_50_50(
    equity_a: list[float],
    equity_b: list[float],
) -> list[float]:
    """두 equity_curve 50/50 가중 합성.

    길이가 다르면 짧은 쪽을 마지막 값으로 패딩.
    한 계좌가 비어있으면 (equity=초기값 유지) → 해당 절반은 현금 대기.
    """
    n = max(len(equity_a), len(equity_b))

    def pad(eq: list[float]) -> list[float]:
        if len(eq) >= n:
            return eq[:n]
        return eq + [eq[-1]] * (n - len(eq))

    a = pad(equity_a)
    b = pad(equity_b)
    return [0.5 * a[i] + 0.5 * b[i] for i in range(n)]


def _empty_metrics() -> dict:
    return {
        "calmar": 0.0, "cagr": 0.0, "mdd": 0.0,
        "win_rate": 0.0, "profit_factor": 0.0, "ev": 0.0,
        "total_trades": 0, "vs_buyhold": 0.0,
    }
