# backtest/sma_optimizer_v2.py
"""진짜 Walk-forward 옵티마이저 — train에서 파라미터 선택 → test에만 적용."""
from __future__ import annotations

import pandas as pd

from backtest.sma_strategies import run_sma_breakout, run_pullback
from backtest.sma_metrics import compute_metrics, combine_50_50
from backtest.sma_config import WF_TRAIN_YEARS, WF_TEST_YEARS, WF_STEP_YEARS, FIXED


def build_wf_windows(
    ohlcv: pd.DataFrame,
    train_years: int = WF_TRAIN_YEARS,
    test_years: int  = WF_TEST_YEARS,
    step_years: int  = WF_STEP_YEARS,
) -> list[dict]:
    """Walk-forward 윈도우 목록 생성."""
    train_days = int(train_years * 252)
    test_days  = int(test_years  * 252)
    step_days  = int(step_years  * 252)

    windows = []
    start = 0
    while start + train_days + test_days <= len(ohlcv):
        te = start + train_days
        ts = te + test_days
        windows.append({
            "train": ohlcv.iloc[start:te],
            "test":  ohlcv.iloc[te:ts],
            "train_start": ohlcv.index[start],
            "train_end":   ohlcv.index[te - 1],
            "test_start":  ohlcv.index[te],
            "test_end":    ohlcv.index[ts - 1],
        })
        start += step_days
    return windows


def _best_breakout_params(
    train: pd.DataFrame,
    param_grid: list[dict],
    min_trades: int,
) -> dict | None:
    """train 구간에서 SMA breakout Calmar 최고 파라미터 반환."""
    best_calmar = -1e9
    best_params = None
    for p in param_grid:
        trades, eq = run_sma_breakout(train, p["sma_period"])
        if len([t for t in trades if t.get("fraction", 1.0) >= 0.99]) < min_trades:
            continue
        m = compute_metrics(trades, eq, train["close"])
        if m["calmar"] > best_calmar:
            best_calmar = m["calmar"]
            best_params = p
    return best_params


def _best_pullback_params(
    train: pd.DataFrame,
    param_grid: list[dict],
    min_trades: int,
) -> dict | None:
    """train 구간에서 pullback Calmar 최고 파라미터 반환."""
    best_calmar = -1e9
    best_params = None
    for p in param_grid:
        trades, eq = run_pullback(
            train,
            p["sma_period"],
            p["pullback_sma_delta"],
            p["stop_loss_pct"],
        )
        if len([t for t in trades if t.get("fraction", 1.0) >= 0.99]) < min_trades:
            continue
        m = compute_metrics(trades, eq, train["close"])
        if m["calmar"] > best_calmar:
            best_calmar = m["calmar"]
            best_params = p
    return best_params


def run_walkforward(
    ohlcv: pd.DataFrame,
    breakout_grid: list[dict],
    pullback_grid: list[dict],
    fixed: dict = FIXED,
    train_years: int = WF_TRAIN_YEARS,
    test_years: int  = WF_TEST_YEARS,
    step_years: int  = WF_STEP_YEARS,
) -> list[dict]:
    """진짜 Walk-forward: train 파라미터 선택 → test 성과만 기록.

    Returns:
        list of window results, each containing:
          - sma_breakout: {params, trades, equity, metrics}
          - pullback: {params, trades, equity, metrics}
          - combined: {equity, metrics}
          - window info (test_start, test_end, is_walkforward)
    """
    n_years = len(ohlcv) / 252
    min_years = train_years + test_years
    min_trades = fixed.get("min_trades", 5)
    results = []

    if n_years < min_years:
        # 데이터 부족 → in-sample 폴백
        bo_params = _best_breakout_params(ohlcv, breakout_grid, min_trades)
        pb_params = _best_pullback_params(ohlcv, pullback_grid, min_trades)

        if bo_params:
            bo_t, bo_eq = run_sma_breakout(ohlcv, bo_params["sma_period"])
        else:
            bo_t, bo_eq = [], [1.0] * len(ohlcv)

        if pb_params:
            pb_t, pb_eq = run_pullback(
                ohlcv, pb_params["sma_period"],
                pb_params["pullback_sma_delta"], pb_params["stop_loss_pct"],
            )
        else:
            pb_t, pb_eq = [], [1.0] * len(ohlcv)

        comb_eq = combine_50_50(bo_eq, pb_eq)
        results.append({
            "is_walkforward": False,
            "test_start": ohlcv.index[0],
            "test_end":   ohlcv.index[-1],
            "sma_breakout": {
                "params": bo_params, "trades": bo_t, "equity": bo_eq,
                "metrics": compute_metrics(bo_t, bo_eq, ohlcv["close"]),
            },
            "pullback": {
                "params": pb_params, "trades": pb_t, "equity": pb_eq,
                "metrics": compute_metrics(pb_t, pb_eq, ohlcv["close"]),
            },
            "combined": {
                "equity": comb_eq,
                "metrics": compute_metrics([], comb_eq, ohlcv["close"]),
            },
        })
        return results

    windows = build_wf_windows(ohlcv, train_years, test_years, step_years)
    for w in windows:
        train = w["train"]
        test  = w["test"]

        # train에서 최적 파라미터 선택
        bo_params = _best_breakout_params(train, breakout_grid, min_trades)
        pb_params = _best_pullback_params(train, pullback_grid, min_trades)

        # test 구간에 적용
        if bo_params:
            bo_t, bo_eq = run_sma_breakout(test, bo_params["sma_period"])
        else:
            bo_t, bo_eq = [], [1.0] * len(test)

        if pb_params:
            pb_t, pb_eq = run_pullback(
                test, pb_params["sma_period"],
                pb_params["pullback_sma_delta"], pb_params["stop_loss_pct"],
            )
        else:
            pb_t, pb_eq = [], [1.0] * len(test)

        comb_eq = combine_50_50(bo_eq, pb_eq)

        results.append({
            "is_walkforward": True,
            "test_start": w["test_start"],
            "test_end":   w["test_end"],
            "sma_breakout": {
                "params":  bo_params,
                "trades":  bo_t,
                "equity":  bo_eq,
                "metrics": compute_metrics(bo_t, bo_eq, test["close"]),
            },
            "pullback": {
                "params":  pb_params,
                "trades":  pb_t,
                "equity":  pb_eq,
                "metrics": compute_metrics(pb_t, pb_eq, test["close"]),
            },
            "combined": {
                "equity":  comb_eq,
                "metrics": compute_metrics([], comb_eq, test["close"]),
            },
        })

    return results


def aggregate_wf_results(window_results: list[dict]) -> dict[str, dict]:
    """WF 윈도우 결과 집계 → 전략별 최종 지표 (평균 calmar 등).

    Returns:
        {"sma_breakout": metrics, "pullback": metrics, "combined": metrics}
    """
    import numpy as np

    def _agg(key: str) -> dict:
        calmar_list  = [w[key]["metrics"]["calmar"]  for w in window_results if w[key]["metrics"]]
        cagr_list    = [w[key]["metrics"]["cagr"]    for w in window_results if w[key]["metrics"]]
        mdd_list     = [w[key]["metrics"]["mdd"]     for w in window_results if w[key]["metrics"]]
        vs_bh_list   = [w[key]["metrics"]["vs_buyhold"] for w in window_results if w[key]["metrics"]]
        trades_list  = [w[key]["metrics"]["total_trades"] for w in window_results if w[key]["metrics"]]

        return {
            "calmar":       round(float(np.mean(calmar_list)),  4) if calmar_list  else 0.0,
            "cagr":         round(float(np.mean(cagr_list)),    2) if cagr_list    else 0.0,
            "mdd":          round(float(np.mean(mdd_list)),     2) if mdd_list     else 0.0,
            "vs_buyhold":   round(float(np.mean(vs_bh_list)),   2) if vs_bh_list   else 0.0,
            "total_trades": int(sum(trades_list)),
            "n_windows":    len(window_results),
            "is_walkforward": window_results[0]["is_walkforward"] if window_results else False,
        }

    return {
        "sma_breakout": _agg("sma_breakout"),
        "pullback":     _agg("pullback"),
        "combined":     _agg("combined"),
    }
