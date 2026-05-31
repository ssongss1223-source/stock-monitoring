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


# ── _passes_gate 테스트 ──────────────────────────────────────────────────────

_GATE_VOLUME_MIN = 7
_GATE_TREND_MIN = 6


def _passes_gate(s) -> bool:
    return s.volume_score >= _GATE_VOLUME_MIN and s.trend_score >= _GATE_TREND_MIN


def test_gate_passes_exactly_at_minimum():
    s = _Signal(volume_score=7, trend_score=6)
    assert _passes_gate(s) is True


def test_gate_fails_volume_below():
    s = _Signal(volume_score=6, trend_score=6)
    assert _passes_gate(s) is False


def test_gate_fails_trend_below():
    s = _Signal(volume_score=7, trend_score=5)
    assert _passes_gate(s) is False


def test_gate_fails_ml_only_signal():
    """ML-only 신호는 score=0 이므로 항상 게이트 미달."""
    s = _Signal(grade="ML", volume_score=0, trend_score=0)
    assert _passes_gate(s) is False


# ── _pick_group 정렬 테스트 ──────────────────────────────────────────────────

_LABEL_AUC = {"5d_5pct_clean": 0.59, "10d_3pct_clean": 0.54}


def _sort_key(item):
    s, prob, label = item
    auc = _LABEL_AUC.get(label, 0.5)
    return (-prob * auc, -s.volume_score, -s.trend_score)


def _pick_group(bucket, used, n=3):
    available = [v for v in bucket.values() if v[0].ticker not in used]
    gated = sorted([v for v in available if _passes_gate(v[0])], key=_sort_key)
    top = gated[:n]
    if len(top) < n:
        picked = {s.ticker for s, _, _ in top}
        ungated = [v for v in available if not _passes_gate(v[0]) and v[0].ticker not in picked]
        top += sorted(ungated, key=_sort_key)[:n - len(top)]
    return top


def test_pick_group_higher_ml_prob_first():
    """AUC가중 확률 높은 종목이 먼저 와야 한다."""
    s_low = _Signal(ticker="A", volume_score=8, trend_score=7, ensemble_prob=0.40)
    s_high = _Signal(ticker="B", volume_score=8, trend_score=7, ensemble_prob=0.70)
    bucket = {
        "A": (s_low, 0.40, "5d_5pct_clean"),
        "B": (s_high, 0.70, "5d_5pct_clean"),
    }
    result = _pick_group(bucket, used=set())
    assert result[0][0].ticker == "B"


def test_pick_group_gate_fail_goes_to_fallback():
    """게이트 미달 종목은 gated pool 소진 후에만 나온다."""
    s_gated = _Signal(ticker="A", volume_score=8, trend_score=7, ensemble_prob=0.50)
    s_ungated = _Signal(ticker="B", volume_score=3, trend_score=3, ensemble_prob=0.99)
    bucket = {
        "A": (s_gated, 0.50, "5d_5pct_clean"),
        "B": (s_ungated, 0.99, "5d_5pct_clean"),
    }
    result = _pick_group(bucket, used=set())
    # gated 종목이 먼저, ungated는 fallback
    assert result[0][0].ticker == "A"
    assert result[1][0].ticker == "B"


def test_pick_group_respects_used_set():
    """used에 있는 ticker는 반환하지 않는다."""
    s1 = _Signal(ticker="A", volume_score=9, trend_score=8)
    s2 = _Signal(ticker="B", volume_score=8, trend_score=7)
    bucket = {"A": (s1, 0.7, "5d_5pct_clean"), "B": (s2, 0.6, "5d_5pct_clean")}
    result = _pick_group(bucket, used={"A"})
    tickers = [s.ticker for s, _, _ in result]
    assert "A" not in tickers
    assert "B" in tickers


def test_pick_group_fallback_fills_when_gated_insufficient():
    """게이트 통과 종목이 n개 미만이면 ungated로 보충한다."""
    s_gated = _Signal(ticker="A", volume_score=8, trend_score=7)
    s_ungated1 = _Signal(ticker="B", volume_score=2, trend_score=2)
    s_ungated2 = _Signal(ticker="C", volume_score=1, trend_score=1)
    bucket = {
        "A": (s_gated, 0.6, "5d_5pct_clean"),
        "B": (s_ungated1, 0.5, "5d_5pct_clean"),
        "C": (s_ungated2, 0.4, "5d_5pct_clean"),
    }
    result = _pick_group(bucket, used=set(), n=3)
    assert len(result) == 3
    assert result[0][0].ticker == "A"
