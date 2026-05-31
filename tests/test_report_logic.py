import pytest
from dataclasses import dataclass, field
from typing import Optional


# BuySignal 최소 mock (실제 import 대신 — DB 없이 테스트)
@dataclass
class _Signal:
    ticker: str = "000000"
    name: str = "테스트"
    grade: str = "B"
    total_score: int = 17
    trend_score: int = 6
    volume_score: int = 7
    pattern: Optional[str] = None
    current_price: float = 10000.0
    stop_loss: float = 9500.0
    target_price: float = 11000.0
    risk_reward: float = 2.0
    pattern_score: int = 0
    ensemble_prob: Optional[float] = 0.5
    best_label: Optional[str] = "5d_5pct_clean"
    target_is_resistance: bool = False
    market: str = "KOSPI"
    mktcap_rank: Optional[int] = None
    market_cap: Optional[float] = None
    label_probs: dict = field(default_factory=dict)


# ── _is_large_cap 테스트 ─────────────────────────────────────────────────────

def _is_large_cap(s) -> bool:
    return (s.market_cap or 0) >= 5e12


def test_large_cap_above_5jo():
    s = _Signal(market_cap=6e12)
    assert _is_large_cap(s) is True


def test_large_cap_exactly_5jo():
    s = _Signal(market_cap=5e12)
    assert _is_large_cap(s) is True


def test_large_cap_below_5jo():
    s = _Signal(market_cap=4.9e12)
    assert _is_large_cap(s) is False


def test_large_cap_none():
    s = _Signal(market_cap=None)
    assert _is_large_cap(s) is False


def test_large_cap_zero():
    s = _Signal(market_cap=0.0)
    assert _is_large_cap(s) is False
