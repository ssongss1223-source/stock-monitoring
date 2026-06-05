# backtest/pullback_runner.py
"""추세-눌림 타이밍 엔진 백테스트 러너."""
from __future__ import annotations

import pandas as pd

from backtest.account import Account
from backtest.sma_metrics import compute_metrics
from backtest.pullback_signal import compute_signals

_COMMISSION = 0.0003


def run_single(
    ohlcv: pd.DataFrame,
    trend_period: int = 200,
    entry_period: int = 50,
    band_pct: float = 5.0,
    slope_lookback: int = 20,
    commission: float = _COMMISSION,
    return_equity: bool = False,
    market_ohlcv: pd.DataFrame | None = None,
) -> dict:
    """단일 파라미터 백테스트.

    Args:
        ohlcv: DataFrame (index=DatetimeIndex, columns: close, high, low)
        return_equity: True면 결과에 equity_curve 포함
        market_ohlcv: 코스피 등 시장 지수 OHLCV. 제공 시 Layer1 regime gate 적용.

    Returns:
        compute_metrics 결과 dict + params + (선택적) equity_curve
    """
    market_gate = None
    if market_ohlcv is not None:
        from backtest.pullback_signal import regime_gate as _regime_gate
        market_gate = _regime_gate(market_ohlcv["close"], trend_period, slope_lookback)
    sig = compute_signals(ohlcv, trend_period, entry_period, band_pct, slope_lookback,
                          market_gate=market_gate)
    close = ohlcv["close"]
    high  = ohlcv["high"]
    low   = ohlcv["low"]

    acc = Account(commission=commission)
    in_pos = False

    for i in range(len(ohlcv)):
        dt  = str(ohlcv.index[i].date())
        c   = close.iloc[i]
        mid = (high.iloc[i] + low.iloc[i]) / 2

        if pd.isna(c):
            acc.record_equity(0)
            continue

        if in_pos and sig["exit"].iloc[i]:
            acc.sell(dt, mid, fraction_of_holdings=1.0, reason="trend_break")
            in_pos = False

        if not in_pos and sig["entry"].iloc[i]:
            acc.buy(dt, mid, fraction_of_cash=1.0)
            in_pos = True

        acc.record_equity(c)

    # 마지막날 강제 청산
    if in_pos:
        last_dt  = str(ohlcv.index[-1].date())
        last_mid = (high.iloc[-1] + low.iloc[-1]) / 2
        acc.sell(last_dt, last_mid, fraction_of_holdings=1.0, reason="end_of_period")

    metrics = compute_metrics(acc.trades, acc.equity_curve, close)
    metrics["params"] = {
        "trend_period": trend_period,
        "entry_period": entry_period,
        "band_pct":     band_pct,
        "slope_lookback": slope_lookback,
    }
    if return_equity:
        metrics["equity_curve"] = acc.equity_curve
    return metrics


def sweep_params(
    ohlcv: pd.DataFrame,
    grid: list[dict],
    commission: float = _COMMISSION,
) -> list[dict]:
    """파라미터 그리드 스윕.

    Args:
        grid: [{"trend_period": int, "entry_period": int, "band_pct": float}, ...]

    Returns:
        각 파라미터 조합의 run_single 결과 리스트 (calmar 내림차순)
    """
    results = []
    for p in grid:
        r = run_single(
            ohlcv,
            trend_period=p.get("trend_period", 200),
            entry_period=p.get("entry_period", 50),
            band_pct=p.get("band_pct", 5.0),
            slope_lookback=p.get("slope_lookback", 20),
            commission=commission,
        )
        results.append(r)
    return sorted(results, key=lambda x: x["calmar"], reverse=True)


def split_periods(
    ohlcv: pd.DataFrame,
    years: int = 5,
) -> list[pd.DataFrame]:
    """ohlcv를 years 단위로 분할.

    Returns:
        분할된 DataFrame 리스트 (마지막 조각은 짧을 수 있음)
    """
    days = int(years * 252)
    chunks = []
    start = 0
    while start < len(ohlcv):
        end = min(start + days, len(ohlcv))
        chunk = ohlcv.iloc[start:end]
        if len(chunk) > 0:
            chunks.append(chunk)
        start += days
    return chunks
