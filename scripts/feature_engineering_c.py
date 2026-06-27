"""
Track C 피처 엔지니어링 — 71개 일봉 + 15개 intraday + 3개 크로스섹셔널 → data/fm_c.parquet

Features:
    _DAILY_FEAT_COLS  (55): universe_features_daily + universe_daily 기존 피처
    _DAILY_EXTRA_COLS (16): ohlcv_daily 추가 파생 피처 (MACD, Stoch, CMF 등)
    _INTRADAY_FEAT_COLS (15): ohlcv_min 60분봉 집계 피처
    _RANK_FEAT_COLS    (3): 날짜 내 크로스섹셔널 순위 피처 (rsi/volume/return 백분위)

Usage:
    python scripts/feature_engineering_c.py --mode train [--output data/fm_c.parquet]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn

_DEFAULT_OUTPUT = "data/fm_c.parquet"
_TRAIN_START = "2023-06-07"

_UD_COLS = {
    "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
    "ma20_ratio", "ma60_ratio", "ma5_ratio",
}

_DAILY_FEAT_COLS = [
    # Section A (21)
    "ma_cross_5_20", "obv_slope_5d", "high_low_ratio", "body_ratio",
    "short_balance_ratio", "short_volume_ratio_5d", "short_balance_change_5d",
    "volume_surge_ratio", "amount_surge_ratio",
    "price_momentum_3d", "price_momentum_10d",
    "inst_net_20d", "foreign_exh_change_5d", "roe_proxy",
    "relative_strength_5d", "combined_net_5d",
    "kospi_above_ma60", "market_volatility_20d",
    "grade_S", "grade_A", "grade_B",
    # Section B (10)
    "bb_width", "atr_14", "atr_ratio_60d",
    "volume_zscore_20d", "amount_zscore_20d",
    "rs_20d", "rs_rank_pct", "market_breadth",
    "breakout_distance_20d", "box_tightness_20d",
    # Section C (17)
    "breakout_distance_60d", "breakout_distance_120d",
    "range_80d_pct", "distance_from_ma224",
    "up_days_5d", "gap_percent", "opening_strength", "intraday_close_strength",
    "recovery_from_low_80d", "volume_acceleration", "volume_dryup_ratio",
    "retracement_ratio", "pullback_depth",
    "bb_width_pct_252", "turnover_rank_pct", "amount_rank_pct", "volatility_rank_pct",
    # universe_daily (7)
    "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
    "ma20_ratio", "ma60_ratio", "ma5_ratio",
]

# ohlcv_daily에서 새로 파생하는 16개 추가 피처
_DAILY_EXTRA_COLS = [
    # 캔들 미시구조
    "lower_shadow_ratio", "upper_shadow_ratio",
    # 오실레이터
    "stoch_k_14",
    "macd_hist", "macd_hist_slope_3d",
    "rsi_slope_3d", "bullish_divergence",
    # 변동성 / 추세
    "bb_expansion_rate",
    "rs_60d", "rs_acceleration",
    "dist_from_52w_low",
    "price_accel_5d",
    # 거래량 심층
    "cmf_20d", "vol_up_ratio_20d", "pv_correlation_20d",
    # 연속성
    "consecutive_up_days_10d",
]

_RANK_FEAT_COLS = ["rsi_rank_pct", "vol_5d_rank_pct", "ret_5d_rank_pct"]

# 소스 컬럼 → 순위 컬럼 매핑
_RANK_SOURCES = {
    "rsi_rank_pct": "rsi_14",
    "vol_5d_rank_pct": "volume_zscore_20d",
    "ret_5d_rank_pct": "ma5_ratio",
}


def add_rank_features(df: pd.DataFrame, date_col: str | None = "signal_date") -> pd.DataFrame:
    """날짜 내 크로스섹셔널 순위 피처(0~1 백분위) 추가.

    date_col=None: 전체 df를 단일 그룹으로 처리 (일일 추론 단일 날짜 모드).
    """
    df = df.copy()
    for new_col, src_col in _RANK_SOURCES.items():
        if src_col not in df.columns:
            df[new_col] = np.nan
            continue
        if date_col and date_col in df.columns:
            df[new_col] = df.groupby(date_col)[src_col].rank(pct=True)
        else:
            df[new_col] = df[src_col].rank(pct=True)
    return df


_INTRADAY_FEAT_COLS = [
    "vwap_close_ratio",
    "vol_front_ratio", "vol_tail_ratio", "vol_mid_ratio",
    "am_return", "pm_return", "am_pm_return_diff",
    "intraday_range_ratio", "open_to_high_ratio", "open_to_low_ratio",
    "intraday_close_strength_min",
    "vol_front_ratio_5d", "vwap_consistency_5d",
    "open_1h_return",       # 첫 1시간봉 수익률
    "vol_concentration_5d", # 5일 평균 최대 단일 시간봉 거래량 비중
]


# ─────────────────────────────────────────────────────────────
#  Intraday features
# ─────────────────────────────────────────────────────────────

def compute_intraday_features(df_min: pd.DataFrame) -> pd.DataFrame:
    """60분봉 DataFrame → 일별 intraday 피처 DataFrame.

    Args:
        df_min: columns = [ticker, dt, open, high, low, close, volume, amount]

    Returns:
        columns = [ticker, date] + _INTRADAY_FEAT_COLS
    """
    df = df_min.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    df["date"] = df["dt"].dt.date
    df["hour"] = df["dt"].dt.hour
    df = df.sort_values(["ticker", "dt"])

    records = []
    for (ticker, date_), g in df.groupby(["ticker", "date"]):
        total_vol = float(g["volume"].sum())
        if total_vol == 0:
            continue

        g_sorted = g.sort_values("dt")
        vwap = float((g["close"] * g["volume"]).sum()) / total_vol
        day_open  = float(g_sorted["open"].iloc[0])
        day_close = float(g_sorted["close"].iloc[-1])
        day_high  = float(g["high"].max())
        day_low   = float(g["low"].min())

        front_vol = float(g[g["hour"] <= 10]["volume"].sum())
        tail_vol  = float(g[g["hour"] >= 14]["volume"].sum())
        mid_vol   = max(0.0, total_vol - front_vol - tail_vol)

        am_g = g_sorted[g_sorted["hour"] < 12]
        pm_g = g_sorted[g_sorted["hour"] >= 12]
        am_open  = float(am_g["open"].iloc[0])   if not am_g.empty else day_open
        am_close = float(am_g["close"].iloc[-1])  if not am_g.empty else day_open
        pm_open  = float(pm_g["open"].iloc[0])   if not pm_g.empty else am_close
        pm_close = float(pm_g["close"].iloc[-1])  if not pm_g.empty else am_close

        am_ret = (am_close - am_open) / am_open if am_open > 0 else 0.0
        pm_ret = (pm_close - pm_open) / pm_open if pm_open > 0 else 0.0

        # open_1h_return: 9시봉 close / 시가 - 1
        bar_9 = g_sorted[g_sorted["hour"] == 9]
        if not bar_9.empty:
            open_1h_ret = float(bar_9["close"].iloc[0]) / day_open - 1 if day_open > 0 else 0.0
        else:
            open_1h_ret = (float(g_sorted["close"].iloc[0]) / day_open - 1) if day_open > 0 else 0.0

        # vol_concentration: 시간대별 거래량 중 최대 비중
        hourly_vol = g.groupby("hour")["volume"].sum()
        vol_conc = float(hourly_vol.max()) / total_vol if total_vol > 0 else 0.0

        records.append({
            "ticker": ticker,
            "date": date_,
            "vwap_close_ratio": (day_close - vwap) / vwap if vwap > 0 else 0.0,
            "vol_front_ratio": front_vol / total_vol,
            "vol_tail_ratio":  tail_vol  / total_vol,
            "vol_mid_ratio":   mid_vol   / total_vol,
            "am_return":           am_ret,
            "pm_return":           pm_ret,
            "am_pm_return_diff":   am_ret - pm_ret,
            "intraday_range_ratio": (day_high - day_low) / day_open if day_open > 0 else 0.0,
            "open_to_high_ratio":   (day_high - day_open) / day_open if day_open > 0 else 0.0,
            "open_to_low_ratio":    (day_low  - day_open) / day_open if day_open > 0 else 0.0,
            "intraday_close_strength_min": (
                (day_close - day_low) / (day_high - day_low)
                if day_high > day_low else 0.5
            ),
            "open_1h_return":         open_1h_ret,
            "_vol_concentration_raw": vol_conc,
        })

    if not records:
        return pd.DataFrame(columns=["ticker", "date"] + _INTRADAY_FEAT_COLS)

    result = pd.DataFrame(records)
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["ticker", "date"]).reset_index(drop=True)

    result["vol_front_ratio_5d"] = result.groupby("ticker")["vol_front_ratio"].transform(
        lambda x: x.rolling(5, min_periods=1).mean()
    )
    result["vwap_consistency_5d"] = result.groupby("ticker")["vwap_close_ratio"].transform(
        lambda x: (x > 0).astype(float).rolling(5, min_periods=1).mean()
    )
    result["vol_concentration_5d"] = result.groupby("ticker")["_vol_concentration_raw"].transform(
        lambda x: x.rolling(5, min_periods=1).mean()
    )

    return result[["ticker", "date"] + _INTRADAY_FEAT_COLS]


# ─────────────────────────────────────────────────────────────
#  추가 일봉 피처 (순수 pandas — 테스트 가능)
# ─────────────────────────────────────────────────────────────

def _max_consec_up(arr: np.ndarray) -> float:
    """rolling().apply용: 배열 내 최대 연속 상승 일수."""
    max_c = 0
    c = 0
    for v in arr:
        if v > 0:
            c += 1
            if c > max_c:
                max_c = c
        else:
            c = 0
    return float(max_c)


def _compute_extra_features_from_df(
    df_ohlcv: pd.DataFrame,
    df_mkt_60d: pd.DataFrame,
) -> pd.DataFrame:
    """ohlcv_daily DataFrame → 16개 추가 파생 피처 (rs_acceleration 제외).

    Args:
        df_ohlcv: columns = [ticker, date(Timestamp), open, high, low, close, volume]
        df_mkt_60d: columns = [date(Timestamp), kospi_ret_60d]

    Returns:
        columns = [ticker, date] + _DAILY_EXTRA_COLS except rs_acceleration
        rs_acceleration 은 build_fm_c 에서 rs_20d JOIN 후 계산
    """
    results = []

    for ticker, g in df_ohlcv.groupby("ticker"):
        g = g.sort_values("date").copy().reset_index(drop=True)

        hl = (g["high"] - g["low"]).replace(0, np.nan)
        upper_body = g[["open", "close"]].max(axis=1)
        lower_body = g[["open", "close"]].min(axis=1)

        # 캔들 그림자
        g["lower_shadow_ratio"] = (lower_body - g["low"]) / hl
        g["upper_shadow_ratio"] = (g["high"] - upper_body) / hl

        # Stochastic K(14)
        low14  = g["low"].rolling(14, min_periods=1).min()
        high14 = g["high"].rolling(14, min_periods=1).max()
        stoch_denom = (high14 - low14).replace(0, np.nan)
        g["stoch_k_14"] = (g["close"] - low14) / stoch_denom * 100

        # MACD (12, 26, 9)
        ema12  = g["close"].ewm(span=12, adjust=False).mean()
        ema26  = g["close"].ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        g["macd_hist"]          = macd - signal
        g["macd_hist_slope_3d"] = g["macd_hist"].diff(3)

        # RSI 기반 파생
        delta = g["close"].diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        g["rsi_slope_3d"] = rsi.diff(3)

        # Bullish divergence: 가격 낮은데 RSI 높아짐 (10d 기준)
        g["bullish_divergence"] = (
            (g["close"] < g["close"].shift(10)) & (rsi > rsi.shift(10))
        ).astype(int)

        # BB 확장률 (현재 BB폭 / 5일 전 BB폭 - 1)
        ma20  = g["close"].rolling(20, min_periods=1).mean()
        std20 = g["close"].rolling(20, min_periods=1).std(ddof=0)
        bb_w  = 4 * std20 / ma20.replace(0, np.nan)
        g["bb_expansion_rate"] = bb_w / bb_w.shift(5).replace(0, np.nan) - 1

        # RS 60d (시장 조정은 merge 후 처리)
        g["rs_60d_stock"] = g["close"].pct_change(60)

        # 52주 저점 대비 거리
        low252 = g["low"].rolling(252, min_periods=60).min()
        g["dist_from_52w_low"] = g["close"] / low252.replace(0, np.nan) - 1

        # 가격 가속도 (3d 모멘텀 변화율)
        pm3d = g["close"].pct_change(3)
        g["price_accel_5d"] = pm3d - pm3d.shift(5)

        # CMF (20d)
        mfm = ((g["close"] - g["low"]) - (g["high"] - g["close"])) / hl
        mfv = mfm.fillna(0) * g["volume"]
        vol20 = g["volume"].rolling(20, min_periods=1).sum().replace(0, np.nan)
        g["cmf_20d"] = mfv.rolling(20, min_periods=1).sum() / vol20

        # 상승일 거래량 비중 (20d)
        up_mask = (g["close"] > g["close"].shift(1)).astype(float)
        g["vol_up_ratio_20d"] = (
            (g["volume"] * up_mask).rolling(20, min_periods=1).sum() / vol20
        )

        # 가격-거래량 상관 (20d)
        daily_ret = g["close"].pct_change()
        g["pv_correlation_20d"] = daily_ret.rolling(20, min_periods=10).corr(g["volume"])

        # 최대 연속 상승일 (10d 윈도우)
        up_flag = (g["close"] > g["close"].shift(1)).astype(float).fillna(0)
        g["consecutive_up_days_10d"] = up_flag.rolling(10, min_periods=1).apply(
            _max_consec_up, raw=True
        )

        keep = [
            "ticker", "date",
            "lower_shadow_ratio", "upper_shadow_ratio", "stoch_k_14",
            "macd_hist", "macd_hist_slope_3d", "rsi_slope_3d", "bullish_divergence",
            "bb_expansion_rate", "rs_60d_stock", "dist_from_52w_low",
            "price_accel_5d", "cmf_20d", "vol_up_ratio_20d", "pv_correlation_20d",
            "consecutive_up_days_10d",
        ]
        results.append(g[keep])

    if not results:
        return pd.DataFrame()

    df_extra = pd.concat(results, ignore_index=True)

    # rs_60d = 종목 60일수익률 - KOSPI 60일수익률
    df_mkt = df_mkt_60d.copy()
    df_mkt["date"] = pd.to_datetime(df_mkt["date"])
    df_extra = df_extra.merge(df_mkt, on="date", how="left")
    df_extra["rs_60d"] = df_extra["rs_60d_stock"] - df_extra["kospi_ret_60d"].fillna(0)
    df_extra = df_extra.drop(columns=["rs_60d_stock", "kospi_ret_60d"])

    return df_extra


def compute_extra_daily_features(conn, start_date: str = _TRAIN_START) -> pd.DataFrame:
    """DB에서 ohlcv_daily + market_index 로드 후 추가 일봉 피처 계산.

    Returns:
        start_date 이후 행만 포함. columns = [ticker, date] + (extra, except rs_acceleration)
    """
    # 252일 rolling을 위해 300일 여유 포함
    hist_start = str((pd.Timestamp(start_date) - pd.Timedelta(days=300)).date())

    df = conn.execute("""
        SELECT ticker, date, open, high, low, close, volume
        FROM ohlcv_daily
        WHERE date >= CAST(? AS DATE)
        ORDER BY ticker, date
    """, [hist_start]).df()
    df["date"] = pd.to_datetime(df["date"])

    # KOSPI 60일 수익률 (window function — 중첩 없음)
    df_mkt = conn.execute("""
        SELECT date,
               close / NULLIF(LAG(close, 60) OVER (ORDER BY date), 0) - 1 AS kospi_ret_60d
        FROM market_index
        WHERE ticker = '1001'
        ORDER BY date
    """).df()
    df_mkt["date"] = pd.to_datetime(df_mkt["date"])

    df_result = _compute_extra_features_from_df(df, df_mkt)
    return df_result[df_result["date"] >= pd.Timestamp(start_date)].reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
#  Feature matrix 빌드 (train mode)
# ─────────────────────────────────────────────────────────────

def build_fm_c(output_path: str = _DEFAULT_OUTPUT) -> pd.DataFrame:
    """Track C 피처 매트릭스를 빌드하여 parquet으로 저장."""
    conn = get_conn(read_only=True)
    try:
        # 1. 라벨
        df_labels = conn.execute("""
            SELECT * FROM backtest_labels_c
            WHERE signal_date >= CAST(? AS DATE)
        """, [_TRAIN_START]).df()
        print(f"[labels] {len(df_labels):,}행")
        if df_labels.empty:
            raise RuntimeError("backtest_labels_c 비어 있음 — labeler_c 먼저 실행 필요")

        # 2. 기존 일봉 피처 (universe_features_daily + universe_daily)
        ufd_cols = [c for c in _DAILY_FEAT_COLS if c not in _UD_COLS]
        ufd_sql  = ", ".join(f"ufd.{c}" for c in ufd_cols)
        ud_sql   = ", ".join(f"ud.{c}" for c in sorted(_UD_COLS))
        df_feats = conn.execute(f"""
            SELECT ufd.date, ufd.ticker, {ufd_sql}, {ud_sql}
            FROM universe_features_daily ufd
            LEFT JOIN universe_daily ud
              ON ud.date = ufd.date AND ud.ticker = ufd.ticker
            WHERE ufd.date >= CAST(? AS DATE)
        """, [_TRAIN_START]).df()
        print(f"[daily feats] {len(df_feats):,}행, {len(df_feats.columns)}컬럼")

        # 3. 추가 일봉 피처
        print("[extra daily] 계산 중...")
        df_extra = compute_extra_daily_features(conn, _TRAIN_START)
        print(f"[extra daily] {len(df_extra):,}행")

        # 4. Intraday 원본 (60분봉)
        df_min = conn.execute("""
            SELECT ticker, dt, open, high, low, close, volume, amount
            FROM ohlcv_min
            WHERE CAST(dt AS DATE) >= CAST(? AS DATE)
        """, [_TRAIN_START]).df()
        print(f"[ohlcv_min] {len(df_min):,}행 로드")
    finally:
        conn.close()

    df_intraday = compute_intraday_features(df_min)
    print(f"[intraday] {len(df_intraday):,}행")

    # 날짜 타입 통일
    df_labels["signal_date"] = pd.to_datetime(df_labels["signal_date"])
    df_feats["date"]         = pd.to_datetime(df_feats["date"])
    df_extra["date"]         = pd.to_datetime(df_extra["date"])
    df_intraday["date"]      = pd.to_datetime(df_intraday["date"])

    # JOIN
    df = df_labels.merge(
        df_feats.rename(columns={"date": "signal_date"}),
        on=["signal_date", "ticker"], how="left",
    )
    df = df.merge(
        df_extra.rename(columns={"date": "signal_date"}),
        on=["signal_date", "ticker"], how="left",
    )
    df = df.merge(
        df_intraday.rename(columns={"date": "signal_date"}),
        on=["signal_date", "ticker"], how="left",
    )

    # rs_acceleration = rs_20d - rs_60d
    if "rs_20d" in df.columns and "rs_60d" in df.columns:
        df["rs_acceleration"] = df["rs_20d"] - df["rs_60d"]

    # 크로스섹셔널 순위 피처 (signal_date 그룹 내 백분위)
    df = add_rank_features(df, date_col="signal_date")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    print(
        f"[done] fm_c.parquet 저장: {len(df):,}행 × {len(df.columns)}컬럼 → {output_path}"
    )
    return df


# ─────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Track C 피처 엔지니어링")
    p.add_argument("--mode", choices=["train"], default="train")
    p.add_argument("--output", default=_DEFAULT_OUTPUT)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.mode == "train":
        build_fm_c(args.output)


if __name__ == "__main__":
    main()
