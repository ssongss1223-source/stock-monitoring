# tests/test_sma_signal.py
import pandas as pd
import numpy as np
import pytest
from backtest.sma_signal import compute_sma, compute_rolling_high, get_entry_signals

def make_close(values):
    return pd.Series(values, dtype=float)

def test_compute_sma_basic():
    close = make_close([10, 20, 30, 40, 50])
    sma = compute_sma(close, period=3)
    assert pd.isna(sma.iloc[0])
    assert pd.isna(sma.iloc[1])
    assert sma.iloc[2] == pytest.approx(20.0)
    assert sma.iloc[4] == pytest.approx(40.0)

def test_compute_rolling_high():
    close = make_close([10, 30, 20, 25, 15])
    high = compute_rolling_high(close, lookback=3)
    assert pd.isna(high.iloc[0])
    assert pd.isna(high.iloc[1])
    assert high.iloc[2] == 30.0   # max(10,30,20)
    assert high.iloc[3] == 30.0   # max(30,20,25)
    assert high.iloc[4] == 25.0   # max(20,25,15)

def test_entry_signal_sma_breakout():
    # 어제 SMA 아래, 오늘 위 → 'sma_breakout'
    # close: [5, 5, 5, 40, 50]
    # SMA(3): [NaN, NaN, 5, 16.67, 31.67]
    # idx 2: close=5 >= sma=5 (처음 True) → breakout at idx 2
    # idx 3: close=40 >= sma=16.67 (이미 True) → no signal
    close = make_close([5, 5, 5, 40, 50])
    signals = get_entry_signals(close, sma_period=3, lookback_days=10, drawdown_pct=5.0)
    assert signals.iloc[2] == 'sma_breakout'

def test_entry_signal_pullback():
    # 눌림목: sma < close AND close <= rolling_high*(1-pct)
    close4 = pd.Series([100, 100, 100, 150, 127], dtype=float)
    # SMA(3) at idx4 = (100+150+127)/3 = 125.7, close=127 > sma ✓
    # rolling_high(3) at idx4 = max(100,150,127)=150
    # 127 <= 150*(1-0.15)=127.5 → True → pullback
    signals4 = get_entry_signals(close4, sma_period=3, lookback_days=3, drawdown_pct=15.0)
    assert signals4.iloc[4] == 'pullback'

def test_breakout_takes_priority_on_crossover_day():
    """SMA 돌파일에는 pullback 조건이 충족되더라도 breakout 반환."""
    # idx4에서 SMA 돌파(breakout) + 낙폭 조건 동시 가능성 확인
    # close = [100, 100, 100, 50, 85]
    # SMA(3) at idx3 = (100+100+50)/3 = 83.3, close=50 < sma → no signal at idx3
    # SMA(3) at idx4 = (100+50+85)/3 = 78.3, close=85 > sma ✓ → breakout at idx4
    # rolling_high(3) at idx4 = max(100,50,85) = 100
    # pullback 조건: 85 <= 100*(1-0.10)=90? → True (drawdown_pct=10)
    # BUT: is_breakout=True at idx4 → is_pullback requires ~is_breakout → False
    # 결과: sma_breakout (not pullback)
    close = make_close([100.0, 100.0, 100.0, 50.0, 85.0])
    signals = get_entry_signals(close, sma_period=3, lookback_days=3, drawdown_pct=10.0)
    # idx4: breakout 발생, pullback 조건도 충족되지만 breakout이 우선
    assert signals.iloc[4] == 'sma_breakout'

    # 반면 idx5(다음날)에는 pullback 가능 — breakout이 아니므로
    close2 = make_close([100.0, 100.0, 100.0, 50.0, 85.0, 83.0])
    signals2 = get_entry_signals(close2, sma_period=3, lookback_days=3, drawdown_pct=10.0)
    # idx5: SMA(3)=(50+85+83)/3=72.7, close=83>sma ✓, NOT a breakout (was above sma at idx4)
    # rolling_high(3) at idx5 = max(50,85,83) = 85
    # 83 <= 85*(1-0.10)=76.5? → No → still no pullback
    # idx5는 already-above-sma이고 pullback 조건 미충족 → None
    assert signals2.iloc[5] is None or pd.isna(signals2.iloc[5])
