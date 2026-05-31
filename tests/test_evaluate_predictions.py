import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.evaluate_predictions import compute_metrics, _prec_at_k


# ── _prec_at_k 테스트 ────────────────────────────────────────────────────────

def test_prec_at_k_basic():
    group = pd.DataFrame({"prob": [0.9, 0.7, 0.5, 0.3], "actual": [1.0, 0.0, 1.0, 0.0]})
    # 상위 2개: prob 0.9(actual=1), 0.7(actual=0) → precision = 0.5
    assert _prec_at_k(group, k=2) == pytest.approx(0.5)


def test_prec_at_k_all_positive():
    group = pd.DataFrame({"prob": [0.9, 0.8], "actual": [1.0, 1.0]})
    assert _prec_at_k(group, k=2) == pytest.approx(1.0)


def test_prec_at_k_larger_than_group():
    """k가 그룹 크기보다 크면 전체 사용."""
    group = pd.DataFrame({"prob": [0.9, 0.8], "actual": [1.0, 0.0]})
    assert _prec_at_k(group, k=10) == pytest.approx(0.5)


# ── compute_metrics K 리스트 테스트 ──────────────────────────────────────────

def _make_df(n_dates=5, n_tickers=30):
    """라벨 하나에 대한 최소 테스트 데이터."""
    rows = []
    for d in range(n_dates):
        for t in range(n_tickers):
            rows.append({
                "signal_date": pd.Timestamp(f"2026-05-{d+1:02d}"),
                "ticker": f"{t:06d}",
                "label": "5d_5pct_clean",
                "prob": (t + 1) / n_tickers,
                "actual": 1.0 if t >= n_tickers - 5 else 0.0,
            })
    return pd.DataFrame(rows)


def test_compute_metrics_returns_columns_for_all_k():
    df = _make_df()
    base_rates = {"5d_5pct_clean": 5 / 30}
    result = compute_metrics(df, base_rates, top_ks=[5, 10, 20, 30])
    assert "prec_at_5" in result.columns
    assert "prec_at_10" in result.columns
    assert "prec_at_20" in result.columns
    assert "prec_at_30" in result.columns
    assert "lift_at_5" in result.columns
    assert "lift_at_30" in result.columns


def test_compute_metrics_prec_values_are_not_null():
    df = _make_df()
    base_rates = {"5d_5pct_clean": 5 / 30}
    result = compute_metrics(df, base_rates, top_ks=[5, 10])
    row = result[result["label"] == "5d_5pct_clean"].iloc[0]
    assert pd.notna(row["prec_at_5"])
    assert pd.notna(row["prec_at_10"])


def test_compute_metrics_smaller_k_higher_precision():
    """상위 5개가 상위 10개보다 precision이 높거나 같아야 한다 (양성이 상위에 집중)."""
    df = _make_df()
    base_rates = {"5d_5pct_clean": 5 / 30}
    result = compute_metrics(df, base_rates, top_ks=[5, 10])
    row = result[result["label"] == "5d_5pct_clean"].iloc[0]
    assert row["prec_at_5"] >= row["prec_at_10"]
