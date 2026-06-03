# backtest/sma_optimizer.py
"""Walk-forward 그리드서치 — 최적 파라미터 탐색."""
from __future__ import annotations
import pandas as pd
from backtest.sma_backtester import run_backtest
from backtest.sma_config import WF_TRAIN_YEARS, WF_TEST_YEARS, WF_STEP_YEARS, FIXED


def build_walkforward_windows(
    close: pd.Series,
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
    while start + train_days + test_days <= len(close):
        train_end  = start + train_days
        test_end   = train_end + test_days
        windows.append({
            "train_start": close.index[start],
            "train_end":   close.index[train_end - 1],
            "test_start":  close.index[train_end],
            "test_end":    close.index[test_end - 1],
            "train_slice": close.iloc[start:train_end],
            "test_slice":  close.iloc[train_end:test_end],
        })
        start += step_days
    return windows


def run_grid_search(
    close: pd.Series,
    param_combinations: list[dict],
    fixed: dict = FIXED,
    train_years: int = WF_TRAIN_YEARS,
    test_years: int  = WF_TEST_YEARS,
    step_years: int  = WF_STEP_YEARS,
) -> pd.DataFrame:
    """파라미터 조합별 Walk-forward 성과 계산.

    데이터 (train_years + test_years)년 미만 → 인샘플 백테스트 (is_walkforward=False).
    """
    n_years = len(close) / 252
    min_years = train_years + test_years

    rows = []

    if n_years < min_years:
        for params in param_combinations:
            _, metrics = run_backtest(close, params, fixed)
            if metrics["total_trades"] < fixed["min_trades"]:
                continue
            rows.append({**params, **metrics, "is_walkforward": False,
                         "window_start": close.index[0], "window_end": close.index[-1]})
    else:
        windows = build_walkforward_windows(close, train_years, test_years, step_years)
        for params in param_combinations:
            for w in windows:
                _, metrics = run_backtest(w["test_slice"], params, fixed)
                if metrics["total_trades"] < fixed["min_trades"]:
                    continue
                rows.append({
                    **params, **metrics,
                    "is_walkforward": True,
                    "window_start": w["test_start"],
                    "window_end":   w["test_end"],
                })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def find_best_params(results: pd.DataFrame) -> dict | None:
    """Walk-forward 검증 결과에서 평균 Calmar 최고 파라미터 반환."""
    if results is None or results.empty:
        return None
    param_cols = ["sma_period", "confirm_days", "lookback_days", "drawdown_pct"]
    wf_results = results[results["is_walkforward"] == True]
    avg = (
        wf_results.groupby(param_cols)["calmar"].mean().reset_index()
        if not wf_results.empty
        else results.groupby(param_cols)["calmar"].mean().reset_index()
    )
    if avg.empty:
        return None
    best_row = avg.loc[avg["calmar"].idxmax()]
    params = best_row[param_cols].to_dict()
    params["sma_period"]    = int(params["sma_period"])
    params["confirm_days"]  = int(params["confirm_days"])
    params["lookback_days"] = int(params["lookback_days"])
    return params
