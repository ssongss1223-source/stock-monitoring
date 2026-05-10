"""
ML 피처 엔지니어링 — signal_history × ohlcv_daily × backtest_labels → feature_matrix.parquet

출력 컬럼:
  - signal_history: vol_score, grade_S/A/B (원-핫), trend_score, pattern_score, risk_reward
  - pattern 원-핫 (cup_handle / falling_box_breakout / triangle_convergence / bb_squeeze)
  - ohlcv_daily (signal_date 기준): per, pbr, div_yield, foreign_exh_rate, short_ratio,
      volume, amount, market_cap, turnover_rate
  - rolling (signal_date 이전 5거래일): foreign_net_5d, inst_net_5d
  - backtest_labels: entry_price, max_high_Xd, max_drawdown_Xd, return_Xd
  - 라벨 9개: label_3d_3pct ~ label_10d_10pct

Usage:
    python scripts/feature_engineering.py
    python scripts/feature_engineering.py --min_volume 30000 --min_amount 300000000
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
            "trend_score": f.get("trend_score", 0),
            "pattern_score": f.get("pattern_score", 0),
            "risk_reward": f.get("risk_reward", 0.0),
            "pattern": f.get("pattern"),
        }
    return series.apply(_parse).apply(pd.Series)


def build_feature_matrix(min_volume: int, min_amount: float) -> pd.DataFrame:
    conn = get_conn(read_only=True)
    try:
        # 1. signal_history
        df_sig = conn.execute(
            "SELECT signal_date, ticker, vol_score, grade, features FROM signal_history"
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

        # 3. backtest_labels
        df_lbl = conn.execute("SELECT * FROM backtest_labels").df()
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

    # --- signal_history × ohlcv_daily JOIN ---
    df = df_sig.merge(
        df_roll,
        left_on=["ticker", "signal_date"],
        right_on=["ticker", "date"],
        how="inner",
    ).drop(columns=["date"])

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

    # --- 라벨 9개 동적 생성 ---
    for d in _HOLD_DAYS:
        for pct in _TARGET_PCTS:
            df[f"label_{d}d_{pct}pct"] = (
                df[f"max_high_{d}d"] >= df["entry_price"] * (1 + pct / 100)
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
