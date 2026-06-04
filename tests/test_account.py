# tests/test_account.py
"""Account 프리미티브 단위 테스트 — 회계 정확성 검증."""
import pandas as pd
import pytest
from backtest.account import Account


# ── 헬퍼 ──────────────────────────────────────────────────────


def mid(high, low):
    return (high + low) / 2


# ── 테스트 ─────────────────────────────────────────────────────


def test_buy_uses_cash_correctly():
    """매수 후 cash = 0 (전액 투입), shares > 0."""
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.buy('2020-01-01', mid_price=10_000, fraction_of_cash=1.0)
    assert abs(acc.cash) < 1e-6
    assert abs(acc.shares - 100.0) < 1e-6  # 1_000_000 / 10_000


def test_buy_commission_reduces_shares():
    """수수료 반영 시 shares 감소."""
    acc = Account(initial_capital=1_000_000, commission=0.0003)
    acc.buy('2020-01-01', mid_price=10_000, fraction_of_cash=1.0)
    expected_qty = 1_000_000 / (10_000 * 1.0003)
    assert abs(acc.shares - expected_qty) < 1e-6


def test_sell_full_updates_cash():
    """전량 매도 후 shares=0, cash에 수익 반영."""
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.buy('2020-01-01', mid_price=10_000, fraction_of_cash=1.0)
    acc.sell('2020-01-02', mid_price=12_000, fraction_of_holdings=1.0, reason='exit')
    assert abs(acc.shares) < 1e-9
    assert abs(acc.cash - 1_200_000) < 1e-3  # +20% 수익
    assert len(acc.trades) == 1
    assert acc.trades[0]['pnl_pct'] == pytest.approx(20.0, abs=0.01)


def test_mtm_equity_reflects_unrealized():
    """보유 중 미실현 손익이 equity에 반영됨 (record_equity 기준)."""
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.buy('2020-01-01', mid_price=10_000)
    # 주가 -20% 시 equity도 -20%
    acc.record_equity(close=8_000)
    assert abs(acc.equity_curve[-1] - 800_000) < 1


def test_mdd_reflects_unrealized_loss():
    """MDD가 미실현 손실 구간을 반영 — 버그 #1 회귀 방지."""
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.record_equity(10_000)   # 포지션 없음 (cash = 1M, close 무관)
    # 실제로는 cash만 있으므로 equity = cash = 1M
    acc2 = Account(initial_capital=1_000_000, commission=0)
    acc2.record_equity(1_000_000)   # 진입 전
    acc2.buy('2020-01-02', mid_price=10_000)
    acc2.record_equity(10_000)      # 진입 직후 (equity ≈ 1M)
    acc2.record_equity(8_000)       # -20% 미실현 손실 (equity ≈ 800K)
    acc2.record_equity(12_000)      # 회복

    eq = pd.Series(acc2.equity_curve)
    mdd = ((eq - eq.cummax()) / eq.cummax() * 100).min()
    assert mdd < -15.0  # 미실현 -20%가 MDD에 반영돼야 함


def test_partial_sell_equity_invariant():
    """파셜 익절 후 총 equity는 거의 동일 — 버그 #1 회귀 방지.

    기존 버그: _partial_sell 에서 capital *= proceeds/cost 로 전체 자본이 잘못 곱해짐.
    올바른 동작: 일부 매도해도 equity (cash + shares×price) 는 수수료만큼만 감소.
    """
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.buy('2020-01-01', mid_price=10_000)

    equity_before = acc.equity(close=15_000)  # +50% 시점
    acc.sell('2020-01-02', mid_price=15_000, fraction_of_holdings=0.10)
    equity_after = acc.equity(close=15_000)

    # 수수료 0이면 equity 완전 동일
    assert abs(equity_after - equity_before) < 1e-3


def test_partial_sell_with_commission():
    """수수료 있을 때 파셜 익절 후 equity 감소는 수수료 이내."""
    acc = Account(initial_capital=1_000_000, commission=0.0003)
    acc.buy('2020-01-01', mid_price=10_000)
    equity_before = acc.equity(close=15_000)
    acc.sell('2020-01-02', mid_price=15_000, fraction_of_holdings=0.10)
    equity_after = acc.equity(close=15_000)
    # 감소분 = 수수료 비용만큼 (0.03% × 매도금액)
    sold_value = 0.10 * acc.shares / (1 - 0.10) * 15_000  # 근사
    assert equity_before - equity_after < sold_value * 0.001  # 0.1% 이내


def test_two_day_split_avg_entry():
    """2일 분할 진입 후 avg_entry_price가 가중평균과 일치."""
    acc = Account(initial_capital=1_000_000, commission=0)

    # Day1: 50% 매수 @ 10,000 → qty = 50
    acc.buy('2020-01-01', mid_price=10_000, fraction_of_cash=0.5)
    qty1 = 50.0

    # Day2: 나머지 100% 매수 @ 11,000 → qty = 500,000/11,000 ≈ 45.45
    acc.buy('2020-01-02', mid_price=11_000, fraction_of_cash=1.0)
    qty2 = 500_000 / 11_000

    expected_avg = (qty1 * 10_000 + qty2 * 11_000) / (qty1 + qty2)
    assert abs(acc.avg_entry_price - expected_avg) < 0.01
    assert abs(acc.cash) < 1e-3


def test_half_position_stop_loss():
    """T+1에 1/2 매수 후 T+2에 전량 손절 → cash 정확성."""
    acc = Account(initial_capital=1_000_000, commission=0)

    # T+1: 50% 매수 @ 10,000 (qty=50)
    acc.buy('2020-01-01', mid_price=10_000, fraction_of_cash=0.5)

    # T+2: 전량 손절 @ 9,500
    acc.sell('2020-01-02', mid_price=9_500, fraction_of_holdings=1.0, reason='stop_loss')

    # cash = 남은 50만 + 50주 × 9500 = 500_000 + 475_000 = 975_000
    assert abs(acc.shares) < 1e-9
    assert abs(acc.cash - 975_000) < 1e-3
    assert acc.trades[0]['exit_reason'] == 'stop_loss'
    assert acc.trades[0]['pnl_pct'] == pytest.approx(-5.0, abs=0.01)


def test_position_resets_after_full_close():
    """전량 청산 후 상태 리셋 — 다음 진입 준비."""
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.buy('2020-01-01', mid_price=10_000)
    acc.sell('2020-01-02', mid_price=11_000, reason='exit')

    assert not acc.in_position
    assert acc.avg_entry_price == 0.0

    # 두 번째 진입 정상 동작
    acc.buy('2020-01-03', mid_price=9_000)
    assert acc.in_position
    assert abs(acc.avg_entry_price - 9_000) < 1e-6


def test_profit_level_tracking():
    """익절 레벨 hit 추적 — 같은 레벨 두 번 안 팜."""
    acc = Account(initial_capital=1_000_000, commission=0)
    acc.buy('2020-01-01', mid_price=10_000)

    assert not acc.profit_level_hit(10.0)
    acc.mark_profit_level(10.0)
    assert acc.profit_level_hit(10.0)
    assert not acc.profit_level_hit(25.0)

    # 전량 청산 후 레벨 리셋
    acc.sell('2020-01-02', mid_price=11_000)
    assert not acc.profit_level_hit(10.0)
