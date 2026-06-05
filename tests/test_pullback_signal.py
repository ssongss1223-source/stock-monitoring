# tests/test_pullback_signal.py
import pandas as pd
import numpy as np
import pytest
from backtest.pullback_signal import (
    regime_gate,
    pullback_entry,
    exit_signal,
    compute_signals,
)


def make_ohlcv(closes: list[float]) -> pd.DataFrame:
    """최소 OHLCV DataFrame 생성 (테스트용). high=close*1.01, low=close*0.99."""
    idx = pd.date_range("2010-01-01", periods=len(closes), freq="B")
    closes = pd.Series(closes, dtype=float, index=idx)
    return pd.DataFrame({
        "close": closes,
        "high":  closes * 1.01,
        "low":   closes * 0.99,
    }, index=idx)


# ── regime_gate ─────────────────────────────────────────────────

def test_regime_on_when_above_rising_ma():
    """종가 > MA200이고 MA200이 상승 중이면 True."""
    closes = list(range(1, 250))
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    assert gate.iloc[-1] == True


def test_regime_off_when_below_ma():
    """종가 < MA200이면 False."""
    closes = list(range(249, 0, -1))
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    assert gate.iloc[-1] == False


def test_regime_nan_before_warmup():
    """trend_period 미달 구간은 False (NaN 아님)."""
    df = make_ohlcv([100.0] * 50)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    assert gate.iloc[0] == False
    assert gate.iloc[49] == False


# ── pullback_entry ───────────────────────────────────────────────

def test_pullback_entry_at_mid_ma():
    """추세 ON + 종가가 MA50 ±band 내 → True."""
    base = list(range(1, 251))
    pullback = [base[-1] * 0.96] * 10
    closes = base + pullback
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    entry = pullback_entry(df["close"], gate, entry_period=50, band_pct=5.0)
    assert entry.iloc[250:].any()


def test_no_entry_when_regime_off():
    """추세 OFF면 진입 신호 없음."""
    closes = list(range(249, 0, -1))
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    entry = pullback_entry(df["close"], gate, entry_period=50, band_pct=5.0)
    assert not entry.any()


# ── exit_signal ──────────────────────────────────────────────────

def test_exit_when_below_trend_ma():
    """종가 < MA200이면 청산 신호."""
    closes = list(range(1, 251)) + [50.0] * 5
    df = make_ohlcv(closes)
    exits = exit_signal(df["close"], trend_period=200)
    assert exits.iloc[252:].any()


# ── compute_signals (통합) ───────────────────────────────────────

def test_compute_signals_returns_correct_columns():
    closes = list(range(1, 260))
    df = make_ohlcv(closes)
    sig = compute_signals(df)
    for col in ("regime", "entry", "exit"):
        assert col in sig.columns, f"컬럼 누락: {col}"


def test_no_entry_and_exit_same_day():
    """같은 날 진입+청산 신호 동시 발생 불가."""
    closes = list(range(1, 260))
    df = make_ohlcv(closes)
    sig = compute_signals(df)
    both = sig["entry"] & sig["exit"]
    assert not both.any(), "진입·청산 동시 발생"
