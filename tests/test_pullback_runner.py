# tests/test_pullback_runner.py
import pandas as pd
import numpy as np
import pytest
from backtest.pullback_runner import run_single, sweep_params, split_periods


def make_trending_ohlcv(n: int = 300) -> pd.DataFrame:
    """n일 단조 상승 OHLCV."""
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    closes = pd.Series([100.0 + i * 0.5 for i in range(n)], index=idx)
    return pd.DataFrame({
        "close": closes,
        "high":  closes * 1.005,
        "low":   closes * 0.995,
    })


# ── run_single ───────────────────────────────────────────────────

def test_run_single_returns_metrics_keys():
    df = make_trending_ohlcv(300)
    result = run_single(df)
    for key in ("calmar", "cagr", "mdd", "win_rate", "total_trades", "vs_buyhold"):
        assert key in result, f"키 누락: {key}"


def test_run_single_no_trade_flat_market():
    """횡보장(추세 없음) → regime_gate 미발동 → 거래 0."""
    idx = pd.date_range("2010-01-01", periods=300, freq="B")
    closes = pd.Series([100.0] * 300, index=idx)
    df = pd.DataFrame({"close": closes, "high": closes * 1.001, "low": closes * 0.999})
    result = run_single(df)
    assert result["total_trades"] == 0


def test_run_single_equity_curve_length():
    """equity_curve 길이 = OHLCV 행 수."""
    df = make_trending_ohlcv(300)
    result = run_single(df, return_equity=True)
    assert len(result["equity_curve"]) == len(df)


# ── sweep_params ─────────────────────────────────────────────────

def test_sweep_params_returns_list():
    df = make_trending_ohlcv(300)
    grid = [
        {"trend_period": 150, "entry_period": 40, "band_pct": 5.0},
        {"trend_period": 200, "entry_period": 50, "band_pct": 5.0},
    ]
    results = sweep_params(df, grid)
    assert len(results) == 2
    for r in results:
        assert "calmar" in r
        assert "params" in r


# ── split_periods ─────────────────────────────────────────────────

def test_split_periods_n_chunks():
    df = make_trending_ohlcv(1260)  # ~5년
    chunks = split_periods(df, years=1)
    assert len(chunks) == 5


def test_split_periods_each_chunk_has_data():
    df = make_trending_ohlcv(1260)
    chunks = split_periods(df, years=1)
    for chunk in chunks:
        assert len(chunk) > 0
