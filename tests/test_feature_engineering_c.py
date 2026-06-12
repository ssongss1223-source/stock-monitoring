"""Track C 피처 엔지니어링 단위 테스트."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_ohlcv_min(ticker="A005930", n_days=5, bars_per_day=7):
    """n_days × bars_per_day 60분봉 DataFrame."""
    rows = []
    base = pd.Timestamp("2024-01-02 09:00:00")
    for d in range(n_days):
        for b in range(bars_per_day):
            dt = base + pd.Timedelta(days=d, hours=b)
            price = 100 + d + b * 0.1
            rows.append({
                "ticker": ticker, "dt": dt,
                "open": price, "high": price * 1.005,
                "low": price * 0.995, "close": price,
                "volume": 10000 + b * 1000,
                "amount": (10000 + b * 1000) * price,
            })
    return pd.DataFrame(rows)


def _make_ohlcv_daily(ticker="A005930", n_days=30, seed=42):
    """n_days 일봉 DataFrame."""
    dates = pd.date_range("2024-01-02", periods=n_days, freq="B")
    rng = np.random.default_rng(seed)
    closes = 100.0 * np.cumprod(1 + rng.standard_normal(n_days) * 0.01)
    return pd.DataFrame({
        "ticker": ticker,
        "date": dates,
        "open": closes * 0.999,
        "high": closes * 1.005,
        "low": closes * 0.994,
        "close": closes,
        "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
    })


# ─────────────────────────────────────────────
#  Intraday 피처
# ─────────────────────────────────────────────

def test_intraday_features_returns_df():
    from scripts.feature_engineering_c import compute_intraday_features
    result = compute_intraday_features(_make_ohlcv_min())
    assert isinstance(result, pd.DataFrame)
    assert "ticker" in result.columns
    assert "date" in result.columns


def test_intraday_features_columns():
    from scripts.feature_engineering_c import compute_intraday_features, _INTRADAY_FEAT_COLS
    result = compute_intraday_features(_make_ohlcv_min())
    for col in _INTRADAY_FEAT_COLS:
        assert col in result.columns, f"Missing intraday feature: {col}"


def test_vwap_close_ratio_direction():
    from scripts.feature_engineering_c import compute_intraday_features
    rows = _make_ohlcv_min(n_days=1, bars_per_day=7).to_dict("records")
    rows[-1]["close"] = 200.0  # 마지막 bar close를 대폭 높임 → close > VWAP
    result = compute_intraday_features(pd.DataFrame(rows))
    assert result.iloc[0]["vwap_close_ratio"] > 0


def test_vol_ratios_sum_to_one():
    from scripts.feature_engineering_c import compute_intraday_features
    result = compute_intraday_features(_make_ohlcv_min(n_days=1, bars_per_day=7))
    total = (
        result["vol_front_ratio"].iloc[0]
        + result["vol_mid_ratio"].iloc[0]
        + result["vol_tail_ratio"].iloc[0]
    )
    assert abs(total - 1.0) < 1e-6


def test_open_1h_return_positive_when_first_bar_rises():
    from scripts.feature_engineering_c import compute_intraday_features
    rows = _make_ohlcv_min(n_days=1, bars_per_day=7).to_dict("records")
    rows[0]["open"] = 100.0
    rows[0]["close"] = 105.0   # 첫 시간 bar +5%
    result = compute_intraday_features(pd.DataFrame(rows))
    assert result.iloc[0]["open_1h_return"] > 0


def test_vol_concentration_5d_between_0_and_1():
    from scripts.feature_engineering_c import compute_intraday_features
    result = compute_intraday_features(_make_ohlcv_min(n_days=5, bars_per_day=7))
    valid = result["vol_concentration_5d"].dropna()
    assert (valid >= 0).all() and (valid <= 1).all()


# ─────────────────────────────────────────────
#  추가 일봉 피처
# ─────────────────────────────────────────────

def _make_extra_inputs(n=30, seed=0):
    """_compute_extra_features_from_df 입력용 fixture."""
    df = _make_ohlcv_daily(n_days=n, seed=seed)
    df_mkt = pd.DataFrame({
        "date": df["date"],
        "kospi_ret_60d": [0.01] * n,
    })
    return df, df_mkt


def test_extra_features_columns():
    from scripts.feature_engineering_c import _compute_extra_features_from_df, _DAILY_EXTRA_COLS
    df, df_mkt = _make_extra_inputs(n=30)
    result = _compute_extra_features_from_df(df, df_mkt)
    # rs_acceleration은 build_fm_c 에서 계산하므로 여기선 없어도 됨
    expected = [c for c in _DAILY_EXTRA_COLS if c != "rs_acceleration"]
    for col in expected:
        assert col in result.columns, f"Missing extra feature: {col}"


def test_lower_shadow_ratio_value():
    from scripts.feature_engineering_c import _compute_extra_features_from_df
    # open=101, high=105, low=95, close=102
    # lower_body = min(101,102) = 101 → lower_shadow = (101-95)/(105-95) = 0.6
    n = 5
    df = pd.DataFrame({
        "ticker": ["A"] * n,
        "date": pd.date_range("2024-01-02", periods=n, freq="B"),
        "open":   [101.0] * n,
        "high":   [105.0] * n,
        "low":    [95.0]  * n,
        "close":  [102.0] * n,
        "volume": [100_000.0] * n,
    })
    df_mkt = pd.DataFrame({"date": df["date"], "kospi_ret_60d": [0.0] * n})
    result = _compute_extra_features_from_df(df, df_mkt)
    assert abs(result["lower_shadow_ratio"].iloc[-1] - 0.6) < 1e-6


def test_stoch_k_within_0_100():
    from scripts.feature_engineering_c import _compute_extra_features_from_df
    df, df_mkt = _make_extra_inputs(n=30)
    result = _compute_extra_features_from_df(df, df_mkt)
    valid = result["stoch_k_14"].dropna()
    assert len(valid) > 0
    assert (valid >= 0).all() and (valid <= 100).all()


def test_consecutive_up_days_nonneg():
    from scripts.feature_engineering_c import _compute_extra_features_from_df
    df, df_mkt = _make_extra_inputs(n=30)
    result = _compute_extra_features_from_df(df, df_mkt)
    valid = result["consecutive_up_days_10d"].dropna()
    assert (valid >= 0).all()
    assert (valid <= 10).all()


def test_cmf_range():
    from scripts.feature_engineering_c import _compute_extra_features_from_df
    df, df_mkt = _make_extra_inputs(n=30)
    result = _compute_extra_features_from_df(df, df_mkt)
    valid = result["cmf_20d"].dropna()
    # CMF는 이론적으로 -1 ~ 1 범위
    assert (valid >= -1.1).all() and (valid <= 1.1).all()
