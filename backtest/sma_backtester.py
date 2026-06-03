# backtest/sma_backtester.py
"""SMA 전략 백테스트 — 포지션 추적, 거래 실행, 지표 계산."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backtest.sma_signal import compute_sma, get_entry_signals


@dataclass
class _Position:
    """포지션 추적 클래스."""
    entry_type: str          # 'sma_breakout' | 'pullback'
    shares: float = 0.0      # 보유 수량 (정규화, 최초 1.0)
    cost_basis: float = 0.0  # 평균 매수 단가
    confirm_count: int = 0   # sma_breakout 분할매수 진행 카운트 (0=대기 중)
    filled_fractions: float = 0.0   # 분할 중 누적 매수 비율
    profit_levels_hit: set = field(default_factory=set)


def run_backtest(
    close: pd.Series,
    params: dict,
    fixed: dict,
) -> tuple[list[dict], dict]:
    """단일 파라미터 조합 백테스트.

    Args:
        close: 종가 시리즈 (인덱스: 거래일 DatetimeIndex)
        params: {"sma_period", "confirm_days", "lookback_days", "drawdown_pct"}
        fixed: {"commission", "stop_loss_pct", "profit_levels", "min_trades"}

    Returns:
        trades: 거래 목록 (dict per trade with keys: entry_type, exit_date, exit_price,
                avg_entry_price, pnl_pct, exit_reason)
        metrics: 성과 지표 dict (calmar, cagr, mdd, win_rate, profit_factor, ev,
                 total_trades, vs_buyhold)
    """
    sma = compute_sma(close, params["sma_period"])
    signals = get_entry_signals(
        close,
        params["sma_period"],
        params["lookback_days"],
        params["drawdown_pct"],
    )

    commission    = fixed["commission"]
    stop_loss_pct = fixed["stop_loss_pct"]
    profit_levels = fixed["profit_levels"]
    confirm_days  = params["confirm_days"]

    pos: _Position | None = None

    trades: list[dict] = []
    # 자본 곡선 (수익률 추적)
    capital = 1.0
    equity_curve: list[float] = [capital]

    def _close_trade(exit_date, exit_price, reason):
        nonlocal pos, capital
        if pos is None:
            return
        # 잔여 포지션 전량 매도
        proceeds = exit_price * pos.shares * (1 - commission)
        entry_value = pos.cost_basis * pos.shares
        pnl_pct = (proceeds / entry_value - 1) * 100 if entry_value > 0 else 0.0
        trades.append({
            "entry_type":      pos.entry_type,
            "exit_date":       exit_date,
            "exit_price":      exit_price,
            "avg_entry_price": pos.cost_basis,
            "pnl_pct":         pnl_pct,
            "exit_reason":     reason,
        })
        capital *= (proceeds / entry_value) if entry_value > 0 else 1.0
        pos = None

    def _partial_sell(exit_price, fraction):
        nonlocal pos, capital
        if pos is None or pos.shares <= 0:
            return
        qty = pos.shares * fraction
        proceeds = exit_price * qty * (1 - commission)
        cost = pos.cost_basis * qty
        capital *= (proceeds / cost) if cost > 0 else 1.0
        pos.shares -= qty

    for i, (dt, price) in enumerate(zip(close.index, close)):
        if pd.isna(sma.iloc[i]) or pd.isna(price):
            equity_curve.append(capital)
            continue

        current_sma = sma.iloc[i]
        signal      = signals.iloc[i]

        # ── 포지션 보유 중 ───────────────────────────────────────────────
        if pos is not None:
            gain_pct = (price / pos.cost_basis - 1) * 100 if pos.cost_basis > 0 else 0

            # 1. sma_breakout 분할매수 진행 중
            if pos.entry_type == 'sma_breakout' and pos.filled_fractions < 1.0 - 1e-9:
                if price < current_sma:
                    # 분할매수 중 SMA 이탈 → 손절
                    _close_trade(dt, price, "sma_exit")
                else:
                    # 추가 매수 1/confirm_days
                    frac = 1.0 / confirm_days
                    cost_add = price * frac * (1 + commission)
                    total_shares = pos.filled_fractions + frac
                    pos.cost_basis = (
                        (pos.cost_basis * pos.filled_fractions + cost_add)
                        / total_shares
                    )
                    pos.shares          = total_shares
                    pos.filled_fractions = total_shares
                    if math.isclose(pos.filled_fractions, 1.0, rel_tol=1e-6):
                        pos.filled_fractions = 1.0
            else:
                # 완전 진입 상태
                # 2. 손절 (눌림목 진입 전용)
                if pos.entry_type == 'pullback' and gain_pct <= -stop_loss_pct:
                    _close_trade(dt, price, "stop_loss")

                # 3. SMA 청산
                elif price < current_sma:
                    _close_trade(dt, price, "sma_exit")

                # 4. 단계별 익절
                elif pos is not None:
                    for threshold, fraction in profit_levels:
                        if threshold not in pos.profit_levels_hit and gain_pct >= threshold:
                            _partial_sell(price, fraction)
                            pos.profit_levels_hit.add(threshold)

        # ── 포지션 없음 — 신규 진입 ─────────────────────────────────────
        elif signal is not None and signal == signal:  # None/NaN 방지
            if signal == 'pullback':
                pos = _Position(
                    entry_type='pullback',
                    shares=1.0,
                    cost_basis=price * (1 + commission),
                    filled_fractions=1.0,
                )
            elif signal == 'sma_breakout':
                frac = 1.0 / confirm_days
                pos = _Position(
                    entry_type='sma_breakout',
                    shares=frac,
                    cost_basis=price * (1 + commission),
                    confirm_count=1,
                    filled_fractions=frac,
                )

        equity_curve.append(capital)

    # 마지막날 강제 청산
    if pos is not None:
        last_price = close.iloc[-1]
        last_date  = close.index[-1]
        _close_trade(last_date, last_price, "end_of_period")

    metrics = _compute_metrics(trades, equity_curve, close)
    return trades, metrics


def _compute_metrics(trades: list[dict], equity: list[float], close: pd.Series) -> dict:
    """거래 목록으로부터 성과 지표 계산."""
    if not trades:
        return {
            "calmar": 0.0, "cagr": 0.0, "mdd": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "ev": 0.0,
            "total_trades": 0, "vs_buyhold": 0.0,
        }

    equity_s = pd.Series(equity)
    running_max = equity_s.cummax()
    drawdowns = (equity_s - running_max) / running_max * 100
    mdd = drawdowns.min()  # 음수

    n_years = len(close) / 252
    final = equity[-1]
    cagr = (final ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

    calmar = cagr / abs(mdd) if mdd != 0 else 0.0

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_win  = np.mean(wins)  if wins   else 0.0
    avg_loss = abs(np.mean(losses)) if losses else 0.0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0.0
    ev = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    bh_return = (close.iloc[-1] / close.iloc[0] - 1) * 100
    vs_buyhold = (final - 1) * 100 - bh_return

    return {
        "calmar":        round(calmar, 4),
        "cagr":          round(cagr, 2),
        "mdd":           round(mdd, 2),
        "win_rate":      round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "ev":            round(ev, 2),
        "total_trades":  len(trades),
        "vs_buyhold":    round(vs_buyhold, 2),
    }
