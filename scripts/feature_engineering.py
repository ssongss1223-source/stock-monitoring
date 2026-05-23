"""
ML 피처 엔지니어링 — universe_daily → feature_matrix.parquet

Source: universe_daily (2024-01 ~, 약 215k+ 행)
  - pre-computed 피처: MA비율(4), RSI, BB, 변동성, 52w고점비율, 수급(5d/20d), 코스피, vol_score
  - 라벨 29개: basic 9 + c2 8 + clean 9 + first_touch 3
  - 보조 피처: ohlcv_daily → OBV slope, 가격모멘텀, 캔들, 공매도, 거래대금 surge
  - 시장 피처: kospi_above_ma60, market_volatility_20d

Usage:
    python scripts/feature_engineering.py
    python scripts/feature_engineering.py --output data/fm_v2.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# 프로젝트 루트를 path에 추가 (scripts/ 에서 직접 실행 시)
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn

_UD_OVERLAP = {
    "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
    "close_to_20ma_ratio", "close_to_60ma_ratio", "close_to_5ma_ratio",
}

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
        "--min_volume", type=int, default=50_000,
        choices=[30_000, 50_000, 100_000],
        help="최소 거래량 (주). 기본 5만주",
    )
    p.add_argument(
        "--min_amount", type=float, default=500_000_000,
        choices=[300_000_000, 500_000_000, 1_000_000_000],
        help="최소 거래대금 (원). 기본 5억원",
    )
    p.add_argument(
        "--output", default="data/feature_matrix.parquet",
        help="출력 경로 (기본: data/feature_matrix.parquet)",
    )
    return p.parse_args()


def _build_v2_ohlcv_features(conn) -> pd.DataFrame:
    """v2 기술적 피처 전체.

    v2 기존 (13개): MA 비율, RSI, BB, OBV slope, 공매도 비율, volume/amount surge, 5일 수익률
    v2 신규 (12개): price_momentum_3d/10d, close_to_5ma_ratio, ma_cross_5_20,
                    high_low_ratio, body_ratio, amount_surge_ratio,
                    foreign_net_20d, inst_net_20d, foreign_exh_change_5d,
                    roe_proxy, short_balance_change_5d
    """
    return conn.execute("""
        WITH
        ma AS (
            SELECT ticker, date, close, open, high, low, volume, amount,
                   AVG(close)  OVER w5     AS ma5,
                   AVG(close)  OVER w20    AS ma20,
                   AVG(close)  OVER w60    AS ma60,
                   MAX(high)   OVER w252   AS high_52w,
                   STDDEV_POP(close) OVER w20  AS std20,
                   AVG(volume) OVER w20_lag AS avg_vol_20d,
                   AVG(amount) OVER w20_lag AS avg_amt_20d
            FROM ohlcv_daily
            WINDOW
                w5      AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
                w20     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                w60     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),
                w252    AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                w20_lag AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
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
                   close / NULLIF(LAG(close,10) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS price_momentum_10d
            FROM ohlcv_daily
        ),
        flows AS (
            SELECT ticker, date,
                   SUM(foreign_net) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS foreign_net_20d,
                   SUM(inst_net)    OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS inst_net_20d,
                   foreign_exh_rate - LAG(foreign_exh_rate, 5) OVER (PARTITION BY ticker ORDER BY date) AS foreign_exh_change_5d
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
        )
        SELECT
            ma.ticker, ma.date,
            -- ── v2 기존: MA 비율 ──────────────────────────────────────────────
            ma.close / NULLIF(ma.ma20, 0) - 1                            AS close_to_20ma_ratio,
            ma.close / NULLIF(ma.ma60, 0) - 1                            AS close_to_60ma_ratio,
            ma.close / NULLIF(ma.high_52w, 0) - 1                        AS close_to_52w_high,
            -- ── v2 신규: MA5 기반 ─────────────────────────────────────────────
            ma.close / NULLIF(ma.ma5, 0) - 1                             AS close_to_5ma_ratio,
            CASE WHEN ma.ma5 >= ma.ma20 THEN 1 ELSE 0 END                AS ma_cross_5_20,
            -- ── v2 기존: BB, RSI, OBV ─────────────────────────────────────────
            (ma.close - (ma.ma20 - 2*ma.std20)) / NULLIF(4*ma.std20, 0) AS bb_position,
            CASE WHEN rsi_avg.avg_loss = 0 THEN 100.0
                 ELSE 100 - 100 / (1 + rsi_avg.avg_gain / NULLIF(rsi_avg.avg_loss, 0))
            END                                                           AS rsi_14,
            obv_slope.obv_slope_5d,
            -- ── v2 신규: 캔들 특성 ───────────────────────────────────────────
            (ma.high - ma.low) / NULLIF(ma.close, 0)                     AS high_low_ratio,
            (ma.close - ma.open) / NULLIF(ma.high - ma.low, 0)          AS body_ratio,
            -- ── v2 기존: 공매도 ──────────────────────────────────────────────
            sr.short_balance / NULLIF(sr.shares, 0)                      AS short_balance_ratio,
            sr.short_volume_ratio_5d,
            -- ── v2 신규: 공매도 잔고 변화 ────────────────────────────────────
            short_chg.short_balance_change_5d,
            -- ── v2 기존: 거래량 surge ────────────────────────────────────────
            ma.volume / NULLIF(ma.avg_vol_20d, 0)                        AS volume_surge_ratio,
            -- ── v2 신규: 거래대금 surge ──────────────────────────────────────
            ma.amount / NULLIF(ma.avg_amt_20d, 0)                        AS amount_surge_ratio,
            -- ── v2 신규: 가격 모멘텀 ─────────────────────────────────────────
            ret.price_momentum_3d,
            ret.price_momentum_10d,
            -- ── v2 신규: 수급 중기 ───────────────────────────────────────────
            flows.foreign_net_20d,
            flows.inst_net_20d,
            flows.foreign_exh_change_5d,
            -- ── v2 신규: 재무 품질 ───────────────────────────────────────────
            valuation.roe_proxy,
            -- ── 상대강도 계산용 (feature_matrix에서 제거됨) ─────────────────
            ret.stock_ret_5d
        FROM ma
        JOIN rsi_avg    ON ma.ticker = rsi_avg.ticker    AND ma.date = rsi_avg.date
        JOIN obv_slope  ON ma.ticker = obv_slope.ticker  AND ma.date = obv_slope.date
        JOIN short_roll sr ON ma.ticker = sr.ticker      AND ma.date = sr.date
        JOIN ret        ON ma.ticker = ret.ticker        AND ma.date = ret.date
        JOIN flows      ON ma.ticker = flows.ticker      AND ma.date = flows.date
        JOIN short_chg  ON ma.ticker = short_chg.ticker  AND ma.date = short_chg.date
        JOIN valuation  ON ma.ticker = valuation.ticker  AND ma.date = valuation.date
    """).df()


def _build_market_features(conn) -> pd.DataFrame:
    """시장 피처: kospi_return_20d/5d, kospi_above_ma60, market_volatility_20d."""
    return conn.execute("""
        WITH kospi_ret AS (
            SELECT date, close,
                   close / NULLIF(LAG(close,1)  OVER (ORDER BY date), 0) - 1 AS daily_ret,
                   close / NULLIF(LAG(close,5)  OVER (ORDER BY date), 0) - 1 AS kospi_return_5d,
                   close / NULLIF(LAG(close,20) OVER (ORDER BY date), 0) - 1 AS kospi_return_20d
            FROM market_index WHERE ticker = '1001'
        )
        SELECT date,
               kospi_return_20d,
               kospi_return_5d,
               CASE WHEN close >= AVG(close) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
                    THEN 1 ELSE 0 END                                          AS kospi_above_ma60,
               STDDEV_POP(daily_ret) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                   * SQRT(252)                                                 AS market_volatility_20d
        FROM kospi_ret
        ORDER BY date
    """).df()


def build_feature_matrix(min_volume: int, min_amount: float) -> pd.DataFrame:
    conn = get_conn(read_only=True)
    try:
        # 1. universe_daily — pre-computed 피처 + 29개 라벨 (라벨 있는 행만)
        label_sel = ", ".join(_LABEL_COLS)
        df = conn.execute(f"""
            SELECT
                date AS signal_date, ticker, close, volume, market_cap,
                per, pbr, turnover_rate,
                ma5_ratio, ma20_ratio, ma60_ratio, ma120_ratio,
                rsi_14, bb_position, hist_vol_20d, close_to_52w_high,
                foreign_net_5d, inst_net_5d, foreign_net_20d, volume_surge_5d,
                kospi_ret_5d  AS kospi_return_5d,
                kospi_ret_20d AS kospi_return_20d,
                vol_score_approx, grade_approx,
                {label_sel}
            FROM universe_daily
            WHERE label_3d_3pct_clean IS NOT NULL
        """).df()

        # 2. backtest_labels LEFT JOIN — max_close_* (Return@K 평가 메트릭용, 약 8% 행만 채워짐)
        df_bl = conn.execute("""
            SELECT signal_date, ticker,
                   max_close_3d, max_close_5d, max_close_10d,
                   max_drawdown_3d, max_drawdown_5d, max_drawdown_10d,
                   return_3d, return_5d, return_10d
            FROM backtest_labels
        """).df()

        # 3. v2 기술 피처
        df_v2 = _build_v2_ohlcv_features(conn)

        # 4. 시장 피처 (kospi_above_ma60, market_volatility_20d 추가)
        df_mkt = _build_market_features(conn)
    finally:
        conn.close()

    if df.empty:
        print("universe_daily에 라벨 있는 행이 없습니다.")
        return pd.DataFrame()

    # --- 날짜 타입 통일 ---
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df_bl["signal_date"] = pd.to_datetime(df_bl["signal_date"])
    df_v2["date"] = pd.to_datetime(df_v2["date"])
    df_mkt["date"] = pd.to_datetime(df_mkt["date"])

    # --- backtest_labels LEFT JOIN (Return@K 메트릭용 — 없으면 NaN) ---
    df = df.merge(df_bl, on=["ticker", "signal_date"], how="left")

    # --- v2 피처 JOIN (universe_daily와 중복 컬럼 제거 후) ---
    df_v2 = df_v2.drop(columns=[c for c in _UD_OVERLAP if c in df_v2.columns])
    df = df.merge(
        df_v2,
        left_on=["ticker", "signal_date"],
        right_on=["ticker", "date"],
        how="left",
    ).drop(columns=["date"], errors="ignore")

    # --- 시장 피처 JOIN (kospi_return_5d/20d는 이미 universe_daily에 있으므로 제외) ---
    df_mkt = df_mkt.drop(columns=["kospi_return_5d", "kospi_return_20d"], errors="ignore")
    df = df.merge(
        df_mkt,
        left_on="signal_date",
        right_on="date",
        how="left",
    ).drop(columns=["date"], errors="ignore")

    # --- 파생 피처 ---
    if "stock_ret_5d" in df.columns and "kospi_return_5d" in df.columns:
        df["relative_strength_5d"] = df["stock_ret_5d"] - df["kospi_return_5d"]
        df = df.drop(columns=["stock_ret_5d"])
    if "foreign_net_5d" in df.columns and "inst_net_5d" in df.columns:
        df["combined_net_5d"] = df["foreign_net_5d"] + df["inst_net_5d"]

    # --- 유동성 필터 (close * volume = amount proxy) ---
    before = len(df)
    vol_ok = df["volume"].fillna(0) >= min_volume
    amt_proxy = df["close"] * df["volume"]
    amt_ok = amt_proxy.isna() | (amt_proxy >= min_amount)
    df = df[vol_ok & amt_ok]
    print(f"유동성 필터: {before:,}건 → {len(df):,}건 "
          f"(volume≥{min_volume:,}, amount_proxy≥{min_amount:,.0f}원)")

    if df.empty:
        return df

    # --- grade 원-핫 ---
    for g in ["S", "A", "B"]:
        df[f"grade_{g}"] = (df["grade_approx"] == g).astype(int)
    df = df.drop(columns=["grade_approx"])

    return df


def main() -> None:
    args = _parse_args()

    print(f"피처 엔지니어링 시작 (min_volume={args.min_volume:,}, min_amount={args.min_amount:,.0f}원)")
    df = build_feature_matrix(args.min_volume, args.min_amount)

    if df.empty:
        print("출력할 데이터 없음. 종료.")
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"\n저장 완료: {out}  shape={df.shape}")
    print(f"기간: {df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")
    print(f"\n라벨 positive rate:")

    label_groups = [
        ("clean 9",       [f"label_{d}d_{p}pct_clean" for d in [3, 5, 10] for p in [3, 5, 10]]),
        ("first_touch 9", [f"label_first_{d}d_{p}pct" for d in [3, 5, 10] for p in [3, 5, 10]]),
    ]
    for group_name, cols in label_groups:
        print(f"  [{group_name}]")
        for col in cols:
            if col in df.columns:
                rate = df[col].mean()
                n = int(df[col].sum())
                print(f"    {col:<32s} {rate:>6.1%}  (n={n:,})")


if __name__ == "__main__":
    main()
