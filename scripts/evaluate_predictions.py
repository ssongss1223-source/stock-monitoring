"""
P5 라이브 예측 평가 — signal_xgb_probs × universe_daily 라벨

지표:
  Brier score   — 확률 보정 품질 (낮을수록 좋음)
  Prec@K        — 확률 상위 K 종목 중 실제 양성 비율 (기본: K=[5,10,20,30])
  Lift@K        — Prec@K / 전체 양성 비율 (베이스라인 대비 배수)

Usage:
    sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py
    sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py --save
    sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py --from-date 2026-05-01
    sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py --top-k 5 10 20
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from data.db import get_conn

# signal_xgb_probs.label 값 → universe_daily 컬럼명 (label_ 접두사 추가)
_ALL_LABELS = [
    "3d_3pct_clean", "3d_5pct_clean", "3d_10pct_clean",
    "5d_3pct_clean", "5d_5pct_clean", "5d_10pct_clean",
    "10d_3pct_clean", "10d_5pct_clean", "10d_10pct_clean",
    "first_3d_3pct", "first_3d_5pct", "first_3d_10pct",
    "first_5d_3pct", "first_5d_5pct", "first_5d_10pct",
    "first_10d_3pct", "first_10d_5pct", "first_10d_10pct",
]

# 라벨별 최소 hold 기간 (outcome 확인까지 대기해야 하는 거래일 수)
def _get_hold_days(label: str) -> int:
    """라벨에서 일수 추출 (예: '3d_3pct_clean' → 3, 'first_5d_3pct' → 5)."""
    parts = label.split("_")
    for part in parts:
        if part.endswith("d"):
            return int(part[:-1])
    return 0

_LABEL_HOLD = {k: _get_hold_days(k) for k in _ALL_LABELS}


def _label_col(label: str) -> str:
    return f"label_{label}"


def _load_data(from_date: str, to_date: str) -> pd.DataFrame:
    """signal_xgb_probs와 universe_daily 라벨을 조인해 long-format DataFrame 반환."""
    label_cols = [_label_col(l) for l in _ALL_LABELS]
    cols_sql = ", ".join(f"u.{c}" for c in label_cols)

    conn = get_conn(read_only=True)
    try:
        df = conn.execute(f"""
            SELECT
                s.signal_date,
                s.ticker,
                s.label,
                s.ensemble_prob AS prob,
                {cols_sql}
            FROM signal_xgb_probs s
            JOIN universe_daily u
                ON s.ticker = u.ticker
               AND s.signal_date = u.date
            WHERE s.signal_date >= CAST('{from_date}' AS DATE)
              AND s.signal_date <= CAST('{to_date}' AS DATE)
        """).df()
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame()

    # wide → long: 각 행에 해당 라벨의 actual 값만 추출
    def _get_actual(row):
        col = _label_col(row["label"])
        if col in row:
            return row[col]
        return None

    df["actual"] = df.apply(_get_actual, axis=1)

    # label 컬럼 외 불필요 컬럼 제거
    return df[["signal_date", "ticker", "label", "prob", "actual"]].copy()


def _load_base_rates(from_date: str, to_date: str) -> dict[str, float]:
    """universe_daily에서 날짜 범위 내 라벨별 양성 비율 계산."""
    label_cols = [_label_col(l) for l in _ALL_LABELS]
    avgs_sql = ", ".join(f"AVG(CAST({c} AS DOUBLE)) AS {c}" for c in label_cols)

    conn = get_conn(read_only=True)
    try:
        row = conn.execute(f"""
            SELECT {avgs_sql}
            FROM universe_daily
            WHERE date >= CAST('{from_date}' AS DATE)
              AND date <= CAST('{to_date}' AS DATE)
        """).fetchone()
        desc = conn.execute(f"SELECT {avgs_sql} FROM universe_daily WHERE FALSE").description
    finally:
        conn.close()

    if row is None:
        return {}

    return {
        col[0].replace("label_", ""): (val or 0.0)
        for col, val in zip(desc, row)
    }


def _prec_at_k(group: pd.DataFrame, k: int) -> float | None:
    """한 날짜 내 상위 K 종목의 Precision."""
    labeled = group.dropna(subset=["actual"])
    if labeled.empty:
        return None
    top = labeled.nlargest(min(k, len(labeled)), "prob")
    return float(top["actual"].mean())


def compute_metrics(df: pd.DataFrame, base_rates: dict[str, float], top_ks: list[int] = None) -> pd.DataFrame:
    """라벨별 Brier / Prec@K / Lift@K 계산. top_ks 리스트의 각 K에 대해 열 생성."""
    if top_ks is None:
        top_ks = [5, 10, 20, 30]

    records = []
    for label in _ALL_LABELS:
        sub = df[df["label"] == label].copy()
        sub_labeled = sub.dropna(subset=["actual"])

        n_dates = sub_labeled["signal_date"].nunique()
        n_pairs = len(sub_labeled)

        if n_pairs == 0:
            record: dict = {
                "label": label, "n_dates": 0, "n_pairs": 0,
                "brier": None, "base_rate": base_rates.get(label),
            }
            for k in top_ks:
                record[f"prec_at_{k}"] = None
                record[f"lift_at_{k}"] = None
            records.append(record)
            continue

        brier = float(((sub_labeled["prob"] - sub_labeled["actual"]) ** 2).mean())
        base = base_rates.get(label)

        record = {
            "label": label, "n_dates": n_dates, "n_pairs": n_pairs,
            "brier": round(brier, 4),
            "base_rate": round(base, 4) if base else None,
        }

        for k in top_ks:
            prec_per_date = (
                sub_labeled.groupby("signal_date")
                .apply(lambda g, _k=k: _prec_at_k(g, _k))
                .dropna()
            )
            prec = float(prec_per_date.mean()) if not prec_per_date.empty else None
            lift = (prec / base) if (prec is not None and base and base > 0) else None
            record[f"prec_at_{k}"] = round(prec, 4) if prec is not None else None
            record[f"lift_at_{k}"] = round(lift, 2) if lift is not None else None

        records.append(record)

    return pd.DataFrame(records)


def print_report(metrics: pd.DataFrame, from_date: str, to_date: str) -> None:
    top_ks = sorted([int(c.replace("prec_at_", "")) for c in metrics.columns if c.startswith("prec_at_")])

    header = f"{'라벨':22s} {'N일':>4s} {'N건':>6s}  {'Brier':>6s}"
    for k in top_ks:
        header += f"  {'P@' + str(k):>6s}"
    for k in top_ks:
        header += f"  {'L@' + str(k):>5s}"
    header += f"  {'Base':>5s}"

    print(f"\n=== P5 라이브 예측 평가 ({from_date} ~ {to_date}) ===\n")
    print(header)
    print("-" * len(header))

    for _, row in metrics.iterrows():
        label = str(row["label"])
        n_dates = int(row["n_dates"]) if pd.notna(row["n_dates"]) else 0
        n_pairs = int(row["n_pairs"]) if pd.notna(row["n_pairs"]) else 0
        brier = f"{row['brier']:.4f}" if pd.notna(row.get("brier")) else "  N/A "
        base = f"{row['base_rate']:.2%}" if pd.notna(row.get("base_rate")) else " N/A "

        line = f"{label:22s} {n_dates:>4d} {n_pairs:>6d}  {brier:>6s}"
        for k in top_ks:
            v = row.get(f"prec_at_{k}")
            line += f"  {f'{v:.2%}':>6s}" if pd.notna(v) else "    N/A"
        for k in top_ks:
            v = row.get(f"lift_at_{k}")
            line += f"  {f'{v:.2f}x':>5s}" if pd.notna(v) else "   N/A"
        line += f"  {base:>5s}"
        print(line)

    print()
    labeled = metrics[metrics["n_pairs"] > 0]
    if not labeled.empty:
        avg_brier = labeled["brier"].mean()
        k_summary = "  ".join(
            f"P@{k}={labeled[f'prec_at_{k}'].mean():.2%}  L@{k}={labeled[f'lift_at_{k}'].mean():.2f}x"
            for k in top_ks if f"prec_at_{k}" in labeled.columns
        )
        print(f"평균 (라벨 있는 {len(labeled)}개): Brier={avg_brier:.4f}  {k_summary}")


def save_to_db(metrics: pd.DataFrame, eval_date: str) -> None:
    conn = get_conn()
    try:
        for _, row in metrics.iterrows():
            if row["n_pairs"] == 0:
                continue
            conn.execute("""
                INSERT OR REPLACE INTO evaluation_history
                    (eval_date, label_key, model_type, oof_auc, prec_at_10, prec_at_20, brier)
                VALUES (CAST(? AS DATE), ?, ?, NULL, ?, ?, ?)
            """, [
                eval_date,
                str(row["label"]),
                "live_ensemble",
                row.get("prec_at_10"),
                row.get("prec_at_20"),
                row.get("brier"),
            ])
        print(f"evaluation_history 저장 완료 (eval_date={eval_date}, live_ensemble)")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="P5 라이브 예측 평가")
    parser.add_argument("--from-date", default=None, help="평가 시작일 YYYY-MM-DD (기본: 30일 전)")
    parser.add_argument("--to-date", default=None, help="평가 종료일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument(
        "--top-k", type=int, nargs="+", default=[5, 10, 20, 30],
        help="Precision@K의 K값 목록 (기본: 5 10 20 30)",
    )
    parser.add_argument("--save", action="store_true", help="결과를 evaluation_history에 저장")
    args = parser.parse_args()

    today = date.today().isoformat()
    to_date = args.to_date or today
    from_date = args.from_date or (date.today() - timedelta(days=30)).isoformat()

    print(f"데이터 로드 중 ({from_date} ~ {to_date})...")
    df = _load_data(from_date, to_date)

    if df.empty:
        print("평가할 데이터 없음 — signal_xgb_probs와 universe_daily 라벨 조인 결과 0건")
        print("(universe_daily 라벨이 아직 채워지지 않았거나 신호 없음)")
        sys.exit(0)

    labeled = df.dropna(subset=["actual"])
    print(f"전체 예측 {len(df):,}건 / 라벨 확인 가능 {len(labeled):,}건 / 날짜 {labeled['signal_date'].nunique()}일")

    if labeled.empty:
        print("라벨 확인 가능한 데이터 없음 (universe_daily 라벨이 NULL)")
        sys.exit(0)

    base_rates = _load_base_rates(from_date, to_date)
    metrics = compute_metrics(df, base_rates, top_ks=args.top_k)
    print_report(metrics, from_date, to_date)

    if args.save:
        save_to_db(metrics, today)


if __name__ == "__main__":
    main()
