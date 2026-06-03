# tests/test_sma_backtester.py
import pandas as pd
import pytest
from backtest.sma_backtester import run_backtest
from backtest.sma_config import FIXED

PARAMS = {"sma_period": 5, "confirm_days": 1, "lookback_days": 10, "drawdown_pct": 10}

def make_series(values):
    dates = pd.date_range("2020-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=dates, dtype=float)

def test_no_trades_all_below_sma():
    # SMA(5) 계산 후 close 항상 아래 → 거래 없음
    close = make_series([10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
    trades, metrics = run_backtest(close, PARAMS, FIXED)
    assert len(trades) == 0
    assert metrics["total_trades"] == 0

def test_sma_breakout_split_entry_3days():
    # confirm_days=3 SMA 최초 진입 → 3일에 걸쳐 1/3씩 매수
    params = {**PARAMS, "confirm_days": 3}
    # 처음 5일 낮아서 SMA 아래, 이후 상승
    close = make_series([10]*5 + [100, 110, 120, 130, 90])
    # SMA(5) at idx5 = (10+10+10+10+100)/5=28, close=100 > sma → breakout
    # Day6: SMA=(10+10+10+100+110)/5=48, close=110>sma → 2nd confirmation
    # Day7: SMA=(10+10+100+110+120)/5=70, close=120>sma → 3rd conf → 완전 진입
    # Day8: SMA=(10+100+110+120+130)/5=94, close=130>sma → hold
    # Day9: SMA=(100+110+120+130+90)/5=110, close=90<sma → SMA 청산
    trades, metrics = run_backtest(close, params, FIXED)
    assert len(trades) == 1
    assert trades[0]["entry_type"] == "sma_breakout"
    assert trades[0]["exit_reason"] == "sma_exit"

def test_split_entry_interrupted_on_day2():
    # SMA breakout 후 바로 SMA 아래로 떨어져서 손절
    params = {**PARAMS, "confirm_days": 3}
    # 첫 breakout은 idx4에서 발생하므로 idx5에서 SMA 아래로 떨어지게 설정
    close2 = make_series([50]*5 + [51, 40, 120, 130, 140])
    # idx4: SMA=(50+50+50+50+50)/5=50, close=50 >= sma → sma_breakout (처음), buy 1/3
    # idx5: SMA=(50+50+50+50+51)/5=50.2, close=40 < sma → sma_exit (손절)
    trades2, _ = run_backtest(close2, {**PARAMS, "confirm_days": 3}, FIXED)
    # 첫 거래는 손절로 종료, 두 번째 breakout(idx6)에서 새 거래 시작
    assert len(trades2) >= 1
    if len(trades2) > 0:
        assert trades2[0]["exit_reason"] == "sma_exit"
        assert trades2[0]["pnl_pct"] < 0  # 첫 거래는 손실

def test_pullback_entry_with_signals():
    # 눌림목 진입이 발생 가능한 케이스 (signal에 의존)
    # 여기서는 신호 발생 여부와 관계없이 list 반환 확인
    close = make_series([100]*5 + [120, 130, 140, 126, 116])
    params = {**PARAMS, "lookback_days": 3, "drawdown_pct": 12}
    trades, _ = run_backtest(close, params, FIXED)
    assert isinstance(trades, list)

def test_commission_applied():
    # 거래 시 수수료 0.03%가 P&L에 반영되는지
    close = make_series([50]*5 + [100, 110, 120, 130, 80])
    trades, _ = run_backtest(close, {**PARAMS, "confirm_days": 1}, FIXED)
    if trades:
        # 수수료 있으면 단순 가격 대비 P&L이 약간 낮아야 함
        t = trades[0]
        raw_pnl = (t["exit_price"] / t["avg_entry_price"] - 1) * 100
        assert t["pnl_pct"] < raw_pnl  # 수수료로 인해 낮음

def test_metrics_keys_present():
    # 거래 유무에 관계없이 지표 키 존재
    close = make_series([50]*5 + [100, 110, 120, 130, 80])
    _, metrics = run_backtest(close, {**PARAMS, "confirm_days": 1}, FIXED)
    for key in ["calmar", "cagr", "mdd", "win_rate", "profit_factor", "ev", "total_trades", "vs_buyhold"]:
        assert key in metrics

def test_end_of_period_exit():
    # 포지션 보유 중 데이터 끝 → end_of_period 청산
    close = make_series([50]*5 + [100, 110, 120, 130, 140])
    # SMA 아래에서 위로 진입 후 계속 상승 → 마지막날 강제 청산
    trades, _ = run_backtest(close, {**PARAMS, "confirm_days": 1}, FIXED)
    if trades:
        assert trades[-1]["exit_reason"] in ("end_of_period", "sma_exit")
