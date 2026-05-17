"""
ML 피처 엔지니어링 — signal_history × ohlcv_daily × backtest_labels → feature_matrix.parquet

v1 피처 (27개):
  signal_history: vol_score, total_score, grade_S/A/B, trend_score, pattern_score, risk_reward
  pattern 원-핫 (4개), scoring_version 원-핫 (2개)
  ohlcv_daily: per, pbr, div_yield, foreign_exh_rate, short_ratio, volume, amount, market_cap, turnover_rate
  rolling: foreign_net_5d, inst_net_5d, log_avg_volume_20d, hist_volatility_20d, avg_foreign_exh_rate_20d

v2 추가 피처 (~28개):
  MA 기반: close_to_20ma_ratio, close_to_60ma_ratio, close_to_52w_high
           close_to_5ma_ratio, ma_cross_5_20
  기술적:  rsi_14, bb_position, obv_slope_5d
  캔들:    high_low_ratio, body_ratio
  공매도:  short_balance_ratio, short_volume_ratio_5d, short_balance_change_5d
  거래량:  volume_surge_ratio, amount_surge_ratio
  모멘텀:  price_momentum_3d, price_momentum_10d
  수급:    foreign_net_20d, inst_net_20d, combined_net_5d, foreign_exh_change_5d
  재무:    roe_proxy
  시장:    kospi_return_20d, kospi_return_5d, kospi_above_ma60,
           market_volatility_20d, relative_strength_5d

Usage:
    python scripts/feature_engineering.py
    python scripts/feature_engineering.py --output data/fm_v2.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# 프로젝트 루트를 path에 추가 (scripts/ 에서 직접 실행 시)
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn

_HOLD_DAYS = [3, 5, 10]
_TARGET_PCTS = [3, 5, 10]
_PATTERNS = ["cup_handle", "falling_box_breakout", "triangle_convergence", "bb_squeeze"]


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


def _parse_features_col(series: pd.Series) -> pd.DataFrame:
    """signal_history.features JSON 컬럼 → 개별 컬럼."""
    def _parse(raw) -> dict:
        if isinstance(raw, str):
            f = json.loads(raw)
        elif isinstance(raw, dict):
            f = raw
        else:
            f = {}
        return {
            "total_score": f.get("total_score", 0),
            "trend_score": f.get("trend_score", 0),
            "pattern_score": f.get("pattern_score", 0),
            "risk_reward": f.get("risk_reward", 0.0),
            "pattern": f.get("pattern"),
        }
    return series.apply(_parse).apply(pd.Series)


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
        # 1. signal_history
        df_sig = conn.execute(
            "SELECT signal_date, ticker, vol_score, grade, features, scoring_version FROM signal_history"
        ).df()

        # 2. ohlcv_daily: signal_date 기준 스냅샷 + rolling 5일 합산
        df_roll = conn.execute("""
            SELECT
                ticker, date,
                volume, amount, market_cap,
                per, pbr, div_yield, foreign_exh_rate, short_ratio,
                CASE WHEN market_cap > 0 THEN amount / market_cap ELSE NULL END AS turnover_rate,
                SUM(foreign_net) OVER (
                    PARTITION BY ticker ORDER BY date
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                ) AS foreign_net_5d,
                SUM(inst_net) OVER (
                    PARTITION BY ticker ORDER BY date
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                ) AS inst_net_5d
            FROM ohlcv_daily
        """).df()

        # 3. ticker 특성 피처 (종목별 특성 인코딩)
        df_ticker = conn.execute("""
            WITH ret AS (
                SELECT ticker, date, volume, foreign_exh_rate,
                       close / NULLIF(LAG(close, 1) OVER (PARTITION BY ticker ORDER BY date), 0) - 1
                           AS daily_ret
                FROM ohlcv_daily
            )
            SELECT ticker, date,
                   LN(NULLIF(AVG(volume) OVER w, 0))          AS log_avg_volume_20d,
                   STDDEV_POP(daily_ret) OVER w * SQRT(252)    AS hist_volatility_20d,
                   AVG(foreign_exh_rate) OVER w                AS avg_foreign_exh_rate_20d
            FROM ret
            WINDOW w AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING)
        """).df()

        # 4. 업종 (ticker_master.sector) — 데이터 없으면 빈 DataFrame
        df_sector = conn.execute(
            "SELECT ticker, sector FROM ticker_master WHERE sector IS NOT NULL"
        ).df()

        # 5. backtest_labels
        df_lbl = conn.execute("SELECT * FROM backtest_labels").df()

        # 6. v2 피처
        df_v2 = _build_v2_ohlcv_features(conn)
        df_mkt = _build_market_features(conn)
    finally:
        conn.close()

    if df_sig.empty:
        print("signal_history가 비어 있습니다.")
        return pd.DataFrame()

    if df_lbl.empty:
        print("backtest_labels가 비어 있습니다. 먼저 labeler를 실행하세요.")
        return pd.DataFrame()

    # --- features JSON 파싱 ---
    feat_cols = _parse_features_col(df_sig["features"])
    df_sig = pd.concat([df_sig.drop(columns=["features"]), feat_cols], axis=1)

    # --- 날짜 타입 통일 ---
    df_sig["signal_date"] = pd.to_datetime(df_sig["signal_date"])
    df_roll["date"] = pd.to_datetime(df_roll["date"])
    df_lbl["signal_date"] = pd.to_datetime(df_lbl["signal_date"])

    # --- ticker 특성 피처 날짜 타입 통일 ---
    df_ticker["date"] = pd.to_datetime(df_ticker["date"])

    # --- v2 피처 날짜 타입 통일 ---
    df_v2["date"] = pd.to_datetime(df_v2["date"])
    df_mkt["date"] = pd.to_datetime(df_mkt["date"])

    # --- signal_history × ohlcv_daily JOIN ---
    df = df_sig.merge(
        df_roll,
        left_on=["ticker", "signal_date"],
        right_on=["ticker", "date"],
        how="inner",
    ).drop(columns=["date"])

    # --- ticker 특성 피처 JOIN ---
    df = df.merge(
        df_ticker,
        left_on=["ticker", "signal_date"],
        right_on=["ticker", "date"],
        how="left",
    ).drop(columns=["date"])

    # --- 업종 JOIN (데이터 있을 때만) ---
    if not df_sector.empty:
        df = df.merge(df_sector, on="ticker", how="left")

    # --- v2 OHLCV 피처 JOIN ---
    df = df.merge(
        df_v2,
        left_on=["ticker", "signal_date"],
        right_on=["ticker", "date"],
        how="left",
    ).drop(columns=["date"], errors="ignore")

    # --- v2 시장 피처 JOIN ---
    df = df.merge(
        df_mkt,
        left_on="signal_date",
        right_on="date",
        how="left",
    ).drop(columns=["date"], errors="ignore")

    # --- 파생 피처 ---
    # 상대강도 = 종목 5일 수익률 - KOSPI 5일 수익률
    if "stock_ret_5d" in df.columns and "kospi_return_5d" in df.columns:
        df["relative_strength_5d"] = df["stock_ret_5d"] - df["kospi_return_5d"]
        df = df.drop(columns=["stock_ret_5d"])  # kospi_return_5d는 독립 피처로 유지
    # 외국인+기관 합산 5일 수급
    if "foreign_net_5d" in df.columns and "inst_net_5d" in df.columns:
        df["combined_net_5d"] = df["foreign_net_5d"] + df["inst_net_5d"]

    # --- 유동성 필터 (NULL은 필터 통과, 실값이 있을 때만 임계값 적용) ---
    before = len(df)
    vol_ok = df["volume"].fillna(0) >= min_volume
    amt_ok = df["amount"].isna() | (df["amount"] >= min_amount)
    df = df[vol_ok & amt_ok]
    null_amt = int(df["amount"].isna().sum())
    print(f"유동성 필터: {before}건 → {len(df)}건 "
          f"(volume≥{min_volume:,}, amount≥{min_amount:,.0f}원)"
          + (f"  ※ amount NULL {null_amt}건 (backfill 필요)" if null_amt else ""))

    # --- backtest_labels JOIN ---
    df = df.merge(df_lbl, on=["ticker", "signal_date"], how="inner")
    print(f"backtest_labels JOIN 후: {len(df)}건")

    if df.empty:
        return df

    # --- grade 원-핫 ---
    for g in ["S", "A", "B"]:
        df[f"grade_{g}"] = (df["grade"] == g).astype(int)
    df = df.drop(columns=["grade"])

    # --- pattern 원-핫 ---
    for pat in _PATTERNS:
        df[f"pattern_{pat}"] = (df["pattern"] == pat).astype(int)
    df = df.drop(columns=["pattern"])

    # --- scoring_version 원-핫 ---
    for v in ["live_v1", "live_v2"]:
        df[f"sv_{v}"] = (df["scoring_version"] == v).astype(int)
    df = df.drop(columns=["scoring_version"])

    # --- 업종 원-핫 (데이터 있을 때만) ---
    if "sector" in df.columns:
        sector_dummies = pd.get_dummies(df["sector"], prefix="sector").astype(int)
        df = pd.concat([df.drop(columns=["sector"]), sector_dummies], axis=1)

    # --- 라벨 9개 동적 생성 ---
    for d in _HOLD_DAYS:
        for pct in _TARGET_PCTS:
            df[f"label_{d}d_{pct}pct"] = (
                df[f"max_close_{d}d"] >= df["entry_price"] * (1 + pct / 100)
            ).astype(int)

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
    print(f"\n라벨 positive rate:")
    print(f"{'':16s} {'3일':>7s} {'5일':>7s} {'10일':>7s}")
    for pct in _TARGET_PCTS:
        rates = [f"{df[f'label_{d}d_{pct}pct'].mean():.1%}" for d in _HOLD_DAYS]
        print(f"  +{pct}% 달성:      {rates[0]:>7s} {rates[1]:>7s} {rates[2]:>7s}")


if __name__ == "__main__":
    main()
