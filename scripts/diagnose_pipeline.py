#!/usr/bin/env python3
"""ML 파이프라인 DB 상태 진단.

VM에서 실행:
  sudo -u stock .venv/bin/python3 scripts/diagnose_pipeline.py

출력 항목:
  - ohlcv_daily 최신 날짜
  - universe_daily 라벨 최근 10일 현황
  - signal_xgb_probs label 유형별 날짜 범위
  - Prec@K 평가 가능 여부 (겹치는 날짜 수)
  - 모델 버전 (최근 3개)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn


def main() -> None:
    conn = get_conn(read_only=True)

    print("=" * 60)
    print("ML 파이프라인 상태 진단")
    print("=" * 60)

    # 1. ohlcv_daily 최신 날짜
    row = conn.execute(
        "SELECT MAX(date), COUNT(DISTINCT date) FROM ohlcv_daily"
    ).fetchone()
    print(f"\n[ohlcv_daily]")
    print(f"  최신 날짜: {row[0]}  총 {row[1]}일")

    # 2. universe_daily 라벨 최근 10일
    print(f"\n[universe_daily 라벨 — 최근 10일]")
    rows = conn.execute("""
        SELECT date,
               COUNT(*) AS n,
               COUNT(label_3d_3pct_clean)  AS lbl_3d,
               COUNT(label_10d_10pct_clean) AS lbl_10d
        FROM universe_daily
        WHERE date >= CAST('2026-05-01' AS DATE)
        GROUP BY date
        ORDER BY date DESC
        LIMIT 10
    """).fetchall()
    if not rows:
        print("  데이터 없음")
    else:
        print(f"  {'날짜':<12} {'종목수':>6} {'lbl_3d':>8} {'lbl_10d':>9}")
        for r in rows:
            print(f"  {str(r[0]):<12} {r[1]:>6} {r[2]:>8} {r[3]:>9}")

    # 3. signal_xgb_probs label 유형별 범위
    print(f"\n[signal_xgb_probs label 유형별 날짜 범위]")
    rows = conn.execute("""
        SELECT label,
               MIN(signal_date) AS min_d,
               MAX(signal_date) AS max_d,
               COUNT(*)         AS n
        FROM signal_xgb_probs
        GROUP BY label
        ORDER BY label
    """).fetchall()
    if not rows:
        print("  데이터 없음")
    else:
        for r in rows:
            print(f"  {str(r[0]):<32}  {r[1]} ~ {r[2]}  ({r[3]}행)")

    # 4. Prec@K 평가 가능 여부
    print(f"\n[Prec@K 평가 가능 여부]")
    overlap = conn.execute("""
        SELECT COUNT(DISTINCT p.signal_date)
        FROM signal_xgb_probs p
        JOIN universe_daily u
          ON p.ticker = u.ticker
         AND p.signal_date = CAST(u.date AS DATE)
        WHERE (p.label LIKE '%_clean' OR p.label LIKE 'first_%')
          AND u.label_3d_3pct_clean IS NOT NULL
    """).fetchone()[0]

    if overlap >= 1:
        print(f"  평가 가능: {overlap}일 교집합 존재")
        sample = conn.execute("""
            SELECT DISTINCT p.signal_date
            FROM signal_xgb_probs p
            JOIN universe_daily u
              ON p.ticker = u.ticker
             AND p.signal_date = CAST(u.date AS DATE)
            WHERE (p.label LIKE '%_clean' OR p.label LIKE 'first_%')
              AND u.label_3d_3pct_clean IS NOT NULL
            ORDER BY p.signal_date
            LIMIT 5
        """).fetchall()
        for r in sample:
            print(f"    {r[0]}")
        print()
        print("  실행 명령:")
        print("    sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py"
              " --top-k 5 10 20 30 --from-date 2026-05-22 --save")
    else:
        print("  평가 불가: 겹치는 날짜 없음")
        sig = conn.execute("""
            SELECT MIN(signal_date), MAX(signal_date)
            FROM signal_xgb_probs
            WHERE label LIKE '%_clean' OR label LIKE 'first_%'
        """).fetchone()
        lbl = conn.execute("""
            SELECT MAX(date)
            FROM universe_daily
            WHERE label_3d_3pct_clean IS NOT NULL
        """).fetchone()
        print(f"  signal (_clean/first_*): {sig[0]} ~ {sig[1]}")
        print(f"  universe_daily 라벨 최신: {lbl[0]}")
        if sig[0] and lbl[0] and str(sig[0]) >= str(lbl[0]):
            print("  → 라벨이 signal보다 뒤처짐")
            print("  → 평일 17:30 KST 파이프라인 실행 후 재진단")

    # 5. 모델 버전
    models_dir = Path(__file__).parent.parent / "models"
    if models_dir.exists():
        versions = sorted(d.name for d in models_dir.iterdir() if d.is_dir())
        if versions:
            print(f"\n[모델 버전 — 최근 3개]")
            for v in versions[-3:]:
                print(f"  {v}")

    conn.close()
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
