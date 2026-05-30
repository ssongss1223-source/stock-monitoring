"""
데이터 품질 검증 — Phase 1

3개 섹션을 차례로 점검하고, [OK] / [WARN] / [FAIL] 라인 출력.
exit code: 0=정상, 1=경고, 2=실패.

Usage:
    sudo -u stock ./.venv/bin/python scripts/verify_data_quality.py
    sudo -u stock ./.venv/bin/python scripts/verify_data_quality.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn

# ────────────────────────────────────────────────────────────────
# 임계치 — 운영하며 조정. 기준 근거는 docs/architecture-roadmap.md D2
# ────────────────────────────────────────────────────────────────
EXPECTED_UNIVERSE_SIZE = 351
UNIVERSE_TOLERANCE = 20            # ±20종목 허용
NULL_WARN_PCT = 5.0                # 피처 NULL 비율 5% 초과 시 경고
NULL_FAIL_PCT = 30.0               # 30% 초과 시 실패
LABEL_CUTOFF_DAYS = 15             # T+15 경과 행은 라벨 채워야 함
LABEL_COVERAGE_WARN_PCT = 95.0     # T+15 경과 행의 라벨 채움 비율
LABEL_COVERAGE_FAIL_PCT = 50.0

# 점검할 핵심 피처 (전부 점검하면 노이즈 많음. 학습에 쓰는 것 위주)
FEATURE_NULL_CHECKS = [
    ("universe_features_daily", "bb_width"),
    ("universe_features_daily", "atr_14"),
    ("universe_features_daily", "volume_zscore_20d"),
    ("universe_features_daily", "amount_zscore_20d"),
    ("universe_features_daily", "rs_20d"),
    ("universe_features_daily", "ma_cross_5_20"),
    ("universe_features_daily", "obv_slope_5d"),
    ("universe_features_daily", "price_momentum_10d"),
    ("universe_daily", "rsi_14"),
    ("universe_daily", "bb_position"),
    ("universe_daily", "turnover_rate"),
    ("universe_daily", "foreign_net_20d"),
]

# 점검할 라벨 (clean / first 대표만)
LABEL_CHECKS = [
    "label_3d_3pct_clean",
    "label_5d_5pct_clean",
    "label_10d_10pct_clean",
    "label_first_3d_3pct",
    "label_first_5d_5pct",
]


# ────────────────────────────────────────────────────────────────
# 결과 추적
# ────────────────────────────────────────────────────────────────

class Result:
    def __init__(self) -> None:
        self.ok = 0
        self.warn = 0
        self.fail = 0
        self.lines: list[str] = []

    def add(self, level: str, section: str, msg: str) -> None:
        self.lines.append(f"[{level}] {section}: {msg}")
        if level == "OK":
            self.ok += 1
        elif level == "WARN":
            self.warn += 1
        elif level == "FAIL":
            self.fail += 1

    def exit_code(self) -> int:
        if self.fail:
            return 2
        if self.warn:
            return 1
        return 0


# ────────────────────────────────────────────────────────────────
# Section 1 — 적재 완전성
# ────────────────────────────────────────────────────────────────

def check_ingestion(conn, result: Result) -> None:
    """최신 거래일에 핵심 테이블이 기대 행수만큼 적재됐는지."""
    section = "ingestion"

    row = conn.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
    if not row or row[0] is None:
        result.add("FAIL", section, "ohlcv_daily 비어있음")
        return
    latest = row[0]
    result.add("OK", section, f"최신 거래일 = {latest}")

    for table in ("ohlcv_daily", "universe_daily", "universe_features_daily"):
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE date = ?", [latest]
        ).fetchone()[0]
        diff = abs(n - EXPECTED_UNIVERSE_SIZE)
        if n == 0:
            result.add("FAIL", section, f"{table}({latest}) 0건")
        elif diff > UNIVERSE_TOLERANCE:
            result.add(
                "WARN", section,
                f"{table}({latest}) {n}건 (기대 {EXPECTED_UNIVERSE_SIZE}±{UNIVERSE_TOLERANCE})",
            )
        else:
            result.add("OK", section, f"{table}({latest}) {n}건")


# ────────────────────────────────────────────────────────────────
# Section 2 — NULL 비율
# ────────────────────────────────────────────────────────────────

def check_null_rates(conn, result: Result) -> None:
    """최신 거래일 행에서 핵심 피처 NULL 비율 측정."""
    section = "null"

    row = conn.execute("SELECT MAX(date) FROM universe_features_daily").fetchone()
    if not row or row[0] is None:
        result.add("FAIL", section, "universe_features_daily 비어있음")
        return
    latest = row[0]

    for table, col in FEATURE_NULL_CHECKS:
        try:
            n_total, n_null = conn.execute(
                f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {col} IS NULL) "
                f"FROM {table} WHERE date = ?",
                [latest],
            ).fetchone()
        except Exception as e:
            result.add("FAIL", section, f"{table}.{col} 조회 실패: {e}")
            continue
        if n_total == 0:
            continue
        pct = 100.0 * n_null / n_total
        msg = f"{table}.{col} {pct:.1f}% ({n_null}/{n_total})"
        if pct >= NULL_FAIL_PCT:
            result.add("FAIL", section, msg)
        elif pct >= NULL_WARN_PCT:
            result.add("WARN", section, msg)
        else:
            result.add("OK", section, msg)


# ────────────────────────────────────────────────────────────────
# Section 3 — 라벨 채움 진행
# ────────────────────────────────────────────────────────────────

def check_label_coverage(conn, result: Result) -> None:
    """T+15 경과한 universe_daily 행에 라벨이 채워졌는지."""
    section = "label"

    cutoff = (date.today() - timedelta(days=LABEL_CUTOFF_DAYS)).isoformat()

    row = conn.execute(
        "SELECT COUNT(*) FROM universe_daily WHERE date <= CAST(? AS DATE) "
        "AND date >= CAST(? AS DATE) - INTERVAL '30 days'",
        [cutoff, cutoff],
    ).fetchone()
    eligible = row[0] if row else 0
    if eligible == 0:
        result.add("WARN", section, f"T+15 경과(≤{cutoff}) 행이 없음 — 데이터 부족")
        return

    for label_col in LABEL_CHECKS:
        try:
            n_labeled = conn.execute(
                f"SELECT COUNT(*) FROM universe_daily "
                f"WHERE date <= CAST(? AS DATE) "
                f"AND date >= CAST(? AS DATE) - INTERVAL '30 days' "
                f"AND {label_col} IS NOT NULL",
                [cutoff, cutoff],
            ).fetchone()[0]
        except Exception as e:
            result.add("FAIL", section, f"{label_col} 조회 실패: {e}")
            continue
        pct = 100.0 * n_labeled / eligible
        msg = f"{label_col} {pct:.1f}% ({n_labeled}/{eligible})"
        if pct < LABEL_COVERAGE_FAIL_PCT:
            result.add("FAIL", section, msg)
        elif pct < LABEL_COVERAGE_WARN_PCT:
            result.add("WARN", section, msg)
        else:
            result.add("OK", section, msg)


# ────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="데이터 품질 검증")
    parser.add_argument("--verbose", action="store_true", help="OK 항목도 모두 출력")
    args = parser.parse_args()

    result = Result()
    conn = get_conn(read_only=True)
    try:
        check_ingestion(conn, result)
        check_null_rates(conn, result)
        check_label_coverage(conn, result)
    finally:
        conn.close()

    for line in result.lines:
        if not args.verbose and line.startswith("[OK]"):
            continue
        print(line)

    print(
        f"\n=== 요약: OK {result.ok} / WARN {result.warn} / FAIL {result.fail} ==="
    )
    return result.exit_code()


if __name__ == "__main__":
    sys.exit(main())
