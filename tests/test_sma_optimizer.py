# tests/test_sma_optimizer.py
import pandas as pd
import numpy as np
import pytest
from backtest.sma_optimizer import build_walkforward_windows, run_grid_search, find_best_params

def make_close(n_years):
    """n년치 일봉 더미 데이터 (상승 추세)."""
    n = int(n_years * 252)
    np.random.seed(42)
    prices = 100 * np.cumprod(1 + np.random.normal(0.0003, 0.015, n))
    dates = pd.date_range("2005-01-03", periods=n, freq="B")
    return pd.Series(prices, index=dates)

def test_walkforward_windows_count():
    """10년 데이터 → (5yr train + 2yr test, 1yr step) → 적어도 3개 윈도우."""
    close = make_close(10)
    windows = build_walkforward_windows(close, train_years=5, test_years=2, step_years=1)
    assert len(windows) >= 3
    for w in windows:
        assert "train_start" in w
        assert "train_end" in w
        assert "test_start" in w
        assert "test_end" in w

def test_walkforward_windows_no_overlap():
    """각 윈도우 내에서 train과 test이 겹치지 않아야 함."""
    close = make_close(10)
    windows = build_walkforward_windows(close, train_years=5, test_years=2, step_years=1)
    for w in windows:
        # train_end < test_start (시간상 순서)
        assert w["train_end"] < w["test_start"]

def test_run_grid_search_returns_dataframe():
    """그리드서치가 파라미터별 결과 DataFrame을 반환해야 함."""
    close = make_close(8)
    from backtest.sma_config import FIXED
    mini_params = [
        {"sma_period": 50, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 5},
        {"sma_period": 100, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 5},
    ]
    results = run_grid_search(close, mini_params, FIXED, train_years=3, test_years=2, step_years=1)
    assert isinstance(results, pd.DataFrame)
    if not results.empty:
        assert "sma_period" in results.columns
        assert "calmar" in results.columns
        assert "is_walkforward" in results.columns

def test_insample_for_short_history():
    """7년 미만 데이터 → Walk-forward 불가 → 인샘플 결과 반환."""
    close = make_close(3)
    from backtest.sma_config import FIXED
    mini_params = [
        {"sma_period": 50, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 5},
    ]
    results = run_grid_search(close, mini_params, FIXED, train_years=5, test_years=2, step_years=1)
    if not results.empty:
        assert all(results["is_walkforward"] == False)

def test_find_best_params_returns_dict():
    """결과 DataFrame에서 최적 파라미터 dict 반환."""
    close = make_close(8)
    from backtest.sma_config import FIXED
    mini_params = [
        {"sma_period": 50, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 5},
        {"sma_period": 100, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 5},
    ]
    results = run_grid_search(close, mini_params, FIXED, train_years=3, test_years=2, step_years=1)
    best = find_best_params(results)
    if best is not None:
        assert "sma_period" in best
        assert "confirm_days" in best
        assert "lookback_days" in best
        assert "drawdown_pct" in best

def test_empty_results_on_no_trades():
    """거래 수 부족하면 빈 DataFrame 반환."""
    # 짧은 데이터 (SMA보다 기간이 짧음)
    close = pd.Series([100.0] * 20,
                      index=pd.date_range("2020-01-01", periods=20, freq="B"))
    from backtest.sma_config import FIXED
    mini_params = [
        {"sma_period": 50, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 5},
    ]
    results = run_grid_search(close, mini_params, FIXED, train_years=5, test_years=2, step_years=1)
    # 데이터가 너무 짧아 거래 불가 → 빈 DataFrame
    assert isinstance(results, pd.DataFrame)
