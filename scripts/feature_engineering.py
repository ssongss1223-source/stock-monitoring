"""
ML 피처 엔지니어링

--mode build : ohlcv_daily → 계산 → universe_features_daily INSERT (백필/갱신)
               feature_matrix.parquet 도 함께 생성
--mode train : universe_daily JOIN universe_features_daily → feature_matrix.parquet 생성
               (재계산 없이 DB 읽기만 → 수 분 완료)

Usage:
    python scripts/feature_engineering.py --mode build
    python scripts/feature_engineering.py --mode train
    python scripts/feature_engineering.py --mode build --output data/fm_v2.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn, init_db

# universe_daily 에 이미 있어서 universe_features_daily 에는 저장하지 않는 컬럼
_UD_COLS = {
    "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
    "close_to_20ma_ratio", "close_to_60ma_ratio", "close_to_5ma_ratio",
}

# Section A + B 컬럼 (학습에 사용)
_FEAT_TRAIN_COLS = [
    # Section A
    "ma_cross_5_20", "obv_slope_5d", "high_low_ratio", "body_ratio",
    "short_balance_ratio", "short_volume_ratio_5d", "short_balance_change_5d",
    "volume_surge_ratio", "amount_surge_ratio",
    "price_momentum_3d", "price_momentum_10d",
    "inst_net_20d", "foreign_exh_change_5d", "roe_proxy",
    "relative_strength_5d", "combined_net_5d",
    "kospi_above_ma60", "market_volatility_20d",
    "grade_S", "grade_A", "grade_B",
    # Section B
    "bb_width", "atr_14", "atr_ratio_60d",
    "volume_zscore_20d", "amount_zscore_20d",
    "rs_20d", "rs_rank_pct", "market_breadth",
    "breakout_distance_20d", "box_tightness_20d",
]

# Section C 컬럼 (DB 저장만, 학습 미포함)
_FEAT_LAYER2_COLS = [
    "breakout_distance_60d", "breakout_distance_120d",
    "range_80d_pct", "distance_from_ma224",
    "up_days_5d",
    "gap_percent", "opening_strength", "intraday_close_strength",
    "recovery_from_low_80d",
    "volume_acceleration", "volume_dryup_ratio",
    "retracement_ratio", "pullback_depth",
]

_LABEL_COLS = [
    "entry_price",
    "label_3d_3pct_clean", "label_3d_5pct_clean", "label_3d_10pct_clean",
    "label_5d_3pct_clean", "label_5d_5pct_clean", "label_5d_10pct_clean",
    "label_10d_3pct_clean", "label_10d_5pct_clean", "label_10d_10pct_clean",
    "label_first_3d_3pct", "label_first_3d_5pct", "label_first_3d_10pct",
    "label_first_5d_3pct", "label_first_5d_5pct", "label_first_5d_10pct",
    "label_first_10d_3pct", "label_first_10d_5pct", "label_first_10d_10pct",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ML 피처 엔지니어링")
    p.add_argument(
        "--mode", choices=["build", "train"], default="build",
        help="build: ohlcv→계산→DB저장+parquet | train: DB읽기→parquet",
    )
    p.add_argument(
        "--min_volume", type=int, default=50_000,
        help="최소 거래량 (학습 필터, train 모드에서만 적용)",
    )
    p.add_argument(
        "--min_amount", type=float, default=500_000_000,
        help="최소 거래대금 proxy (학습 필터, train 모드에서만 적용)",
    )
    p.add_argument(
        "--output", default="data/feature_matrix.parquet",
        help="출력 경로 (기본: data/feature_matrix.parquet)",
    )
    return p.parse_args()


# ──────────────────────────────────────────────────────────────────
#  BUILD MODE: ohlcv_daily → 계산
# ──────────────────────────────────────────────────────────────────

def _compute_all_features(conn) -> pd.DataFrame:
    """전체 피처 계산 (Section A + B + C). 학습/비학습 구분 없이 전체 반환."""
    return conn.execute("""
        WITH
        ma AS (
            SELECT ticker, date, open, high, low, close, volume, amount,
                   AVG(close)  OVER w5     AS ma5,
                   AVG(close)  OVER w20    AS ma20,
                   AVG(close)  OVER w60    AS ma60,
                   AVG(close)  OVER w120   AS ma120,
                   AVG(close)  OVER w224   AS ma224,
                   MAX(high)   OVER w252   AS high_52w,
                   MAX(high)   OVER w20    AS high_20d,
                   MAX(high)   OVER w60    AS high_60d,
                   MAX(high)   OVER w120   AS high_120d,
                   MIN(low)    OVER w80    AS low_80d,
                   MAX(high)   OVER w80    AS high_80d,
                   STDDEV_POP(close) OVER w20     AS std20,
                   AVG(close)  OVER w20_lag        AS ma20_lag5,
                   AVG(close)  OVER w60_lag        AS ma60_lag10,
                   AVG(volume) OVER w20_lag_vol    AS avg_vol_20d,
                   AVG(volume) OVER w5_cur         AS avg_vol_5d,
                   AVG(volume) OVER w60_cur        AS avg_vol_60d,
                   STDDEV_POP(volume) OVER w20_lag_vol AS std_vol_20d,
                   AVG(amount) OVER w20_lag_vol    AS avg_amt_20d,
                   STDDEV_POP(amount) OVER w20_lag_vol AS std_amt_20d,
                   LAG(close, 1) OVER (PARTITION BY ticker ORDER BY date) AS prev_close,
                   LAG(open,  1) OVER (PARTITION BY ticker ORDER BY date) AS prev_open
            FROM ohlcv_daily
            WINDOW
                w5      AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4   PRECEDING AND CURRENT ROW),
                w20     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19  PRECEDING AND CURRENT ROW),
                w60     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59  PRECEDING AND CURRENT ROW),
                w80     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 79  PRECEDING AND CURRENT ROW),
                w120    AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW),
                w224    AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 223 PRECEDING AND CURRENT ROW),
                w252    AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                w5_cur  AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4   PRECEDING AND CURRENT ROW),
                w60_cur AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59  PRECEDING AND CURRENT ROW),
                w20_lag     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 25 PRECEDING AND 6  PRECEDING),
                w60_lag     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 70 PRECEDING AND 11 PRECEDING),
                w20_lag_vol AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1  PRECEDING)
        ),
        tr AS (
            SELECT ticker, date,
                   GREATEST(
                       high - low,
                       ABS(high - prev_close),
                       ABS(low  - prev_close)
                   ) AS true_range
            FROM ma
        ),
        atr AS (
            SELECT ticker, date,
                   AVG(true_range) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr_14
            FROM tr
        ),
        atr2 AS (
            SELECT ticker, date, atr_14,
                   AVG(atr_14) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS atr_60d_mean
            FROM atr
        ),
        rsi_raw AS (
            SELECT ticker, date,
                   GREATEST(close - LAG(close,1) OVER (PARTITION BY ticker ORDER BY date), 0) AS gain,
                   GREATEST(LAG(close,1) OVER (PARTITION BY ticker ORDER BY date) - close, 0) AS loss
            FROM ohlcv_daily
        ),
        rsi_avg AS (
            SELECT ticker, date,
                   AVG(gain) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain,
                   AVG(loss) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss
            FROM rsi_raw
        ),
        obv_dir AS (
            SELECT ticker, date, volume,
                   SIGN(close - LAG(close,1) OVER (PARTITION BY ticker ORDER BY date)) AS dir
            FROM ohlcv_daily
        ),
        obv_val AS (
            SELECT ticker, date,
                   SUM(volume * dir) OVER (PARTITION BY ticker ORDER BY date) AS obv
            FROM obv_dir
        ),
        obv_slope AS (
            SELECT ticker, date,
                   (obv - LAG(obv,5) OVER (PARTITION BY ticker ORDER BY date))
                       / NULLIF(ABS(LAG(obv,5) OVER (PARTITION BY ticker ORDER BY date)), 0) AS obv_slope_5d
            FROM obv_val
        ),
        short_roll AS (
            SELECT ticker, date, short_balance, shares,
                   AVG(short_ratio) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
                       AS short_volume_ratio_5d
            FROM ohlcv_daily
        ),
        ret AS (
            SELECT ticker, date,
                   close / NULLIF(LAG(close,3)  OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS price_momentum_3d,
                   close / NULLIF(LAG(close,5)  OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS stock_ret_5d,
                   close / NULLIF(LAG(close,10) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS price_momentum_10d,
                   close / NULLIF(LAG(close,20) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS stock_ret_20d
            FROM ohlcv_daily
        ),
        flows AS (
            SELECT ticker, date,
                   SUM(foreign_net) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS foreign_net_20d,
                   SUM(inst_net)    OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS inst_net_20d,
                   foreign_exh_rate - LAG(foreign_exh_rate, 5) OVER (PARTITION BY ticker ORDER BY date) AS foreign_exh_change_5d,
                   SUM(foreign_net) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS foreign_net_5d,
                   SUM(inst_net)    OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS inst_net_5d
            FROM ohlcv_daily
        ),
        short_chg AS (
            SELECT ticker, date,
                   (short_balance - LAG(short_balance, 5) OVER (PARTITION BY ticker ORDER BY date))
                       / NULLIF(ABS(LAG(short_balance, 5) OVER (PARTITION BY ticker ORDER BY date)), 0)
                       AS short_balance_change_5d
            FROM ohlcv_daily
        ),
        valuation AS (
            SELECT ticker, date,
                   CASE WHEN bps > 0 THEN CAST(eps AS DOUBLE) / NULLIF(bps, 0) ELSE NULL END AS roe_proxy
            FROM ohlcv_daily
        ),
        up_dir AS (
            SELECT ticker, date,
                   CASE WHEN close > LAG(close,1) OVER (PARTITION BY ticker ORDER BY date) THEN 1 ELSE 0 END AS up_flag
            FROM ohlcv_daily
        ),
        up_days AS (
            SELECT ticker, date,
                   SUM(up_flag) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS up_days_5d
            FROM up_dir
        )
        SELECT
            ma.ticker, ma.date,
            -- ── Section A: v2 on-the-fly ──────────────────────────────────
            CASE WHEN ma.ma5 >= ma.ma20 THEN 1 ELSE 0 END                AS ma_cross_5_20,
            obv_slope.obv_slope_5d,
            (ma.high - ma.low) / NULLIF(ma.close, 0)                     AS high_low_ratio,
            (ma.close - ma.open) / NULLIF(ma.high - ma.low, 0)          AS body_ratio,
            sr.short_balance / NULLIF(sr.shares, 0)                      AS short_balance_ratio,
            sr.short_volume_ratio_5d,
            short_chg.short_balance_change_5d,
            ma.volume / NULLIF(ma.avg_vol_20d, 0)                        AS volume_surge_ratio,
            ma.amount / NULLIF(ma.avg_amt_20d, 0)                        AS amount_surge_ratio,
            ret.price_momentum_3d,
            ret.price_momentum_10d,
            flows.inst_net_20d,
            flows.foreign_exh_change_5d,
            valuation.roe_proxy,
            -- relative_strength_5d, combined_net_5d: 시장 피처 JOIN 후 계산 (pandas)
            -- kospi_above_ma60, market_volatility_20d: 시장 피처 JOIN 후 (pandas)
            -- grade_S/A/B: universe_daily JOIN 후 (pandas)
            -- ── Section B: Tier 1 신규 ────────────────────────────────────
            4 * ma.std20 / NULLIF(ma.ma20, 0)                            AS bb_width,
            atr2.atr_14,
            atr2.atr_14 / NULLIF(atr2.atr_60d_mean, 0)                   AS atr_ratio_60d,
            (ma.volume - ma.avg_vol_20d) / NULLIF(ma.std_vol_20d, 0)    AS volume_zscore_20d,
            (ma.amount - ma.avg_amt_20d) / NULLIF(ma.std_amt_20d, 0)    AS amount_zscore_20d,
            -- rs_20d, rs_rank_pct, market_breadth: 시장 피처 JOIN 후 계산 (pandas)
            ma.close / NULLIF(ma.high_20d, 0) - 1                       AS breakout_distance_20d,
            ma.std20 / NULLIF(ma.ma20, 0)                               AS box_tightness_20d,
            -- ── Section C: Layer2 raw ─────────────────────────────────────
            ma.close / NULLIF(ma.high_60d, 0) - 1                       AS breakout_distance_60d,
            ma.close / NULLIF(ma.high_120d, 0) - 1                      AS breakout_distance_120d,
            (ma.high_80d - ma.low_80d) / NULLIF(ma.close, 0)           AS range_80d_pct,
            ma.close / NULLIF(ma.ma224, 0) - 1                          AS distance_from_ma224,
            up_days.up_days_5d,
            (ma.open - ma.prev_close) / NULLIF(ma.prev_close, 0)        AS gap_percent,
            (ma.open - ma.low) / NULLIF(ma.high - ma.low, 0)           AS opening_strength,
            (ma.close - ma.low) / NULLIF(ma.high - ma.low, 0)          AS intraday_close_strength,
            (ma.close - ma.low_80d) / NULLIF(ma.low_80d, 0)            AS recovery_from_low_80d,
            ma.avg_vol_5d / NULLIF(ma.avg_vol_20d, 0)                   AS volume_acceleration,
            ma.avg_vol_5d / NULLIF(ma.avg_vol_60d, 0)                   AS volume_dryup_ratio,
            (ma.close - ma.low_80d) / NULLIF(ma.high_80d - ma.low_80d, 0) AS retracement_ratio,
            (ma.high_80d - ma.close) / NULLIF(ma.high_80d, 0)          AS pullback_depth,
            -- 중간 계산값 (pandas에서 파생 피처 계산에 사용)
            ret.stock_ret_5d,
            ret.stock_ret_20d,
            flows.foreign_net_5d,
            flows.inst_net_5d
        FROM ma
        JOIN atr2        ON ma.ticker = atr2.ticker      AND ma.date = atr2.date
        JOIN rsi_avg     ON ma.ticker = rsi_avg.ticker   AND ma.date = rsi_avg.date
        JOIN obv_slope   ON ma.ticker = obv_slope.ticker AND ma.date = obv_slope.date
        JOIN short_roll sr ON ma.ticker = sr.ticker      AND ma.date = sr.date
        JOIN ret         ON ma.ticker = ret.ticker       AND ma.date = ret.date
        JOIN flows       ON ma.ticker = flows.ticker     AND ma.date = flows.date
        JOIN short_chg   ON ma.ticker = short_chg.ticker AND ma.date = short_chg.date
        JOIN valuation   ON ma.ticker = valuation.ticker AND ma.date = valuation.date
        JOIN up_days     ON ma.ticker = up_days.ticker   AND ma.date = up_days.date
    """).df()


def _build_market_features(conn) -> pd.DataFrame:
    return conn.execute("""
        WITH kospi_ret AS (
            SELECT date, close,
                   close / NULLIF(LAG(close,1)  OVER (ORDER BY date), 0) - 1 AS daily_ret,
                   close / NULLIF(LAG(close,5)  OVER (ORDER BY date), 0) - 1 AS kospi_return_5d,
                   close / NULLIF(LAG(close,20) OVER (ORDER BY date), 0) - 1 AS kospi_return_20d
            FROM market_index WHERE ticker = '1001'
        )
        SELECT date,
               kospi_return_5d,
               kospi_return_20d,
               CASE WHEN close >= AVG(close) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
                    THEN 1 ELSE 0 END                                          AS kospi_above_ma60,
               STDDEV_POP(daily_ret) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                   * SQRT(252)                                                 AS market_volatility_20d
        FROM kospi_ret
        ORDER BY date
    """).df()


def _add_derived_and_cross_sectional(df: pd.DataFrame, df_mkt: pd.DataFrame) -> pd.DataFrame:
    """pandas에서 처리하는 파생 피처 및 cross-sectional 피처."""
    df["date"] = pd.to_datetime(df["date"])
    df_mkt["date"] = pd.to_datetime(df_mkt["date"])
    df = df.merge(df_mkt, on="date", how="left")

    # relative_strength_5d / 20d
    df["relative_strength_5d"] = df["stock_ret_5d"] - df["kospi_return_5d"]
    df["rs_20d"] = df["stock_ret_20d"] - df["kospi_return_20d"]

    # combined_net_5d
    if "foreign_net_5d" in df.columns and "inst_net_5d" in df.columns:
        df["combined_net_5d"] = df["foreign_net_5d"] + df["inst_net_5d"]

    # rs_rank_pct: date별 rs_20d percentile rank
    df["rs_rank_pct"] = df.groupby("date")["rs_20d"].rank(pct=True)

    # market_breadth: date별 상승종목 비율 (stock_ret_5d > 0 기준)
    def _breadth(g):
        return (g > 0).sum() / len(g) if len(g) > 0 else float("nan")
    breadth_map = df.groupby("date")["stock_ret_5d"].transform(_breadth)
    df["market_breadth"] = breadth_map

    df = df.drop(columns=["stock_ret_5d", "stock_ret_20d",
                           "kospi_return_5d", "kospi_return_20d",
                           "foreign_net_5d", "inst_net_5d"], errors="ignore")
    return df


def _save_features_to_db(df: pd.DataFrame) -> None:
    """universe_features_daily에 INSERT OR REPLACE."""
    feat_cols = _FEAT_TRAIN_COLS + _FEAT_LAYER2_COLS
    # grade_S/A/B 는 build 모드에서 universe_daily JOIN 없이 없을 수 있음 → 있을 때만
    all_cols = ["date", "ticker"] + [c for c in feat_cols if c in df.columns]
    df_ins = df[all_cols].copy()
    df_ins["date"] = df_ins["date"].astype(str)

    conn = get_conn(read_only=False)
    try:
        conn.execute("DELETE FROM universe_features_daily WHERE date >= ?",
                     [df_ins["date"].min()])
        conn.execute("INSERT INTO universe_features_daily SELECT * FROM df_ins")
        print(f"  universe_features_daily INSERT: {len(df_ins):,}행")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────
#  BUILD MODE 실행
# ──────────────────────────────────────────────────────────────────

def run_build(output_path: str) -> None:
    """ohlcv → 계산 → DB 저장 + parquet 생성."""
    init_db()  # universe_features_daily 테이블 생성 (마이그레이션 적용)
    print("=== BUILD MODE: 피처 계산 → universe_features_daily 저장 ===")
    conn = get_conn(read_only=True)
    try:
        print("  ohlcv_daily → 피처 계산 중 (SQL window functions, 수 분 소요)...")
        df = _compute_all_features(conn)
        df_mkt = _build_market_features(conn)
    finally:
        conn.close()

    print(f"  SQL 계산 완료: {len(df):,}행")
    df = _add_derived_and_cross_sectional(df, df_mkt)

    # grade_S/A/B 는 universe_daily에서 가져와야 함 → 별도 join
    conn_r = get_conn(read_only=True)
    try:
        df_grade = conn_r.execute("""
            SELECT date, ticker, grade_approx FROM universe_daily
        """).df()
    finally:
        conn_r.close()

    df_grade["date"] = pd.to_datetime(df_grade["date"])
    df = df.merge(df_grade, on=["date", "ticker"], how="left")
    for g in ["S", "A", "B"]:
        df[f"grade_{g}"] = (df["grade_approx"] == g).astype("Int8")
    df = df.drop(columns=["grade_approx"], errors="ignore")

    print("  DB INSERT 중...")
    _save_features_to_db(df)

    # parquet도 함께 생성 (train 모드 없이 바로 학습 가능하도록)
    _build_and_save_parquet(df, output_path)


# ──────────────────────────────────────────────────────────────────
#  TRAIN MODE 실행
# ──────────────────────────────────────────────────────────────────

def run_train(min_volume: int, min_amount: float, output_path: str) -> None:
    """universe_daily JOIN universe_features_daily → feature_matrix.parquet (재계산 없음)."""
    print("=== TRAIN MODE: DB 읽기 → feature_matrix.parquet ===")
    label_sel = ", ".join(f"ud.{c}" for c in _LABEL_COLS)
    feat_sel = ", ".join(f"uf.{c}" for c in _FEAT_TRAIN_COLS if c not in ("grade_S", "grade_A", "grade_B"))

    conn = get_conn(read_only=True)
    try:
        df = conn.execute(f"""
            SELECT
                ud.date AS signal_date, ud.ticker,
                ud.close, ud.volume, ud.market_cap, ud.per, ud.pbr, ud.turnover_rate,
                ud.ma5_ratio, ud.ma20_ratio, ud.ma60_ratio, ud.ma120_ratio,
                ud.rsi_14, ud.bb_position, ud.hist_vol_20d, ud.close_to_52w_high,
                ud.foreign_net_5d, ud.inst_net_5d, ud.foreign_net_20d, ud.volume_surge_5d,
                ud.kospi_ret_5d AS kospi_return_5d,
                ud.kospi_ret_20d AS kospi_return_20d,
                ud.vol_score_approx,
                {label_sel},
                uf.grade_S, uf.grade_A, uf.grade_B,
                {feat_sel}
            FROM universe_daily ud
            JOIN universe_features_daily uf
              ON ud.date = uf.date AND ud.ticker = uf.ticker
            WHERE ud.label_3d_3pct_clean IS NOT NULL
        """).df()

        df_bl = conn.execute("""
            SELECT signal_date, ticker,
                   max_close_3d, max_close_5d, max_close_10d,
                   max_drawdown_3d, max_drawdown_5d, max_drawdown_10d,
                   return_3d, return_5d, return_10d
            FROM backtest_labels
        """).df()
    finally:
        conn.close()

    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df_bl["signal_date"] = pd.to_datetime(df_bl["signal_date"])
    df = df.merge(df_bl, on=["ticker", "signal_date"], how="left")

    _build_and_save_parquet(df, output_path, mode="train",
                             min_volume=min_volume, min_amount=min_amount)


def _build_and_save_parquet(df: pd.DataFrame, output_path: str,
                             mode: str = "build",
                             min_volume: int = 50_000,
                             min_amount: float = 500_000_000) -> None:
    if df.empty:
        print("출력할 데이터 없음.")
        return

    # build 모드: date 컬럼을 signal_date로 통일 (train_models.py 호환)
    if "date" in df.columns and "signal_date" not in df.columns:
        df = df.rename(columns={"date": "signal_date"})
    date_col = "signal_date" if "signal_date" in df.columns else "date"

    if mode == "train":
        # 유동성 필터 (train 모드에서만)
        before = len(df)
        vol_ok = df["volume"].fillna(0) >= min_volume
        amt_proxy = df["close"] * df["volume"]
        amt_ok = amt_proxy.isna() | (amt_proxy >= min_amount)
        df = df[vol_ok & amt_ok]
        print(f"  유동성 필터: {before:,}건 → {len(df):,}건")
    else:
        # build 모드: 라벨 있는 행만 parquet에 포함
        label_col = "label_3d_3pct_clean"
        if label_col in df.columns:
            before = len(df)
            df = df[df[label_col].notna()]
            print(f"  라벨 필터: {before:,}건 → {len(df):,}건")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"\n저장 완료: {out}  shape={df.shape}")
    if date_col in df.columns:
        print(f"기간: {df[date_col].min()} ~ {df[date_col].max()}")

    label_groups = [
        ("clean 9",       [f"label_{d}d_{p}pct_clean" for d in [3, 5, 10] for p in [3, 5, 10]]),
        ("first_touch 9", [f"label_first_{d}d_{p}pct" for d in [3, 5, 10] for p in [3, 5, 10]]),
    ]
    print("\n라벨 positive rate:")
    for group_name, cols in label_groups:
        print(f"  [{group_name}]")
        for col in cols:
            if col in df.columns:
                rate = df[col].mean()
                n = int(df[col].sum())
                print(f"    {col:<32s} {rate:>6.1%}  (n={n:,})")


def main() -> None:
    args = _parse_args()
    if args.mode == "build":
        run_build(args.output)
    else:
        run_train(args.min_volume, args.min_amount, args.output)


if __name__ == "__main__":
    main()
