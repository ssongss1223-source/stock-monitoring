#!/usr/bin/env python3
"""
P3 backfill: universe_predictions + universe_outcomes 소급 반영.

universe_predictions: universe_daily의 pred_* 컬럼 → long format INSERT
universe_outcomes:    ohlcv_daily로 전체 유니버스 raw measurement 소급 계산

실행: sudo -u stock python3 scripts/backfill_p3.py
옵션:
  --skip-predictions   universe_predictions 소급 건너뜀
  --skip-outcomes      universe_outcomes 소급 건너뜀
  --from-date YYYY-MM-DD  outcomes 소급 시작일 (기본: 전체)
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from data.db import get_conn
from backtest.labeler import compute_outcomes_universe, _HOLD_DAYS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_PRED_LABELS = [
    "3d_3pct_clean", "3d_5pct_clean", "3d_10pct_clean",
    "5d_3pct_clean", "5d_5pct_clean", "5d_10pct_clean",
    "10d_3pct_clean", "10d_5pct_clean", "10d_10pct_clean",
    "first_3d_3pct", "first_3d_5pct", "first_3d_10pct",
    "first_5d_3pct", "first_5d_5pct", "first_5d_10pct",
    "first_10d_3pct", "first_10d_5pct", "first_10d_10pct",
]


def backfill_predictions() -> None:
    """universe_daily pred_* 컬럼 → universe_predictions (long format)."""
    logger.info("=== universe_predictions 소급 시작 ===")

    pred_cols = [f"pred_{lbl}" for lbl in _PRED_LABELS]
    select_cols = ", ".join(["date", "ticker"] + pred_cols)

    conn_r = get_conn(read_only=True)
    try:
        # pred 값이 하나라도 있는 날짜만 읽음
        df = conn_r.execute(
            f"SELECT {select_cols} FROM universe_daily WHERE pred_3d_3pct_clean IS NOT NULL"
        ).df()
    finally:
        conn_r.close()

    if df.empty:
        logger.info("universe_daily에 pred_* 데이터 없음 — 건너뜀")
        return

    logger.info("universe_daily 읽음: %d행", len(df))

    # wide → long melt
    df_long = df.melt(
        id_vars=["date", "ticker"],
        value_vars=pred_cols,
        var_name="col_name",
        value_name="prob",
    ).dropna(subset=["prob"])
    df_long["model_type"] = "ensemble"
    df_long["label"] = df_long["col_name"].str.removeprefix("pred_")
    df_long = df_long[["date", "ticker", "model_type", "label", "prob"]]

    logger.info("long format 변환: %d행", len(df_long))

    conn = get_conn()
    try:
        conn.register("_pred_long", df_long)
        conn.execute("""
            INSERT OR REPLACE INTO universe_predictions
                (date, ticker, model_type, label, prob)
            SELECT date, ticker, model_type, label, prob
            FROM _pred_long
        """)
        logger.info("universe_predictions INSERT 완료: %d행", len(df_long))
    finally:
        conn.close()


def backfill_outcomes(from_date: str | None = None) -> None:
    """전체 유니버스 raw measurement → universe_outcomes 소급."""
    logger.info("=== universe_outcomes 소급 시작 ===")

    conn_r = get_conn(read_only=True)
    try:
        latest_ohlcv = conn_r.execute(
            "SELECT MAX(date) FROM ohlcv_daily"
        ).fetchone()[0]
        if latest_ohlcv is None:
            logger.error("ohlcv_daily 데이터 없음")
            return

        # outcomes 계산 가능한 최대 예측일 (최신 OHLC 기준 10 거래일 이전)
        cutoff = conn_r.execute(f"""
            SELECT MIN(date) FROM (
                SELECT DISTINCT date FROM ohlcv_daily
                WHERE date <= CAST('{latest_ohlcv}' AS DATE)
                ORDER BY date DESC LIMIT {max(_HOLD_DAYS) + 1}
            )
        """).fetchone()[0]
        if cutoff is None:
            logger.error("거래일 데이터 부족")
            return

        # universe_daily에서 소급 대상 날짜 목록
        query = "SELECT DISTINCT date FROM universe_daily WHERE date <= CAST(? AS DATE)"
        params = [str(cutoff)]
        if from_date:
            query += " AND date >= CAST(? AS DATE)"
            params.append(from_date)
        query += " ORDER BY date"
        dates = [str(r[0]) for r in conn_r.execute(query, params).fetchall()]

        # 이미 저장된 날짜 제외
        done = {str(r[0]) for r in conn_r.execute(
            "SELECT DISTINCT date FROM universe_outcomes"
        ).fetchall()}
    finally:
        conn_r.close()

    todo = [d for d in dates if d not in done]
    logger.info("소급 대상: %d일 (이미 완료 %d일, 기준일 %s)", len(todo), len(done), cutoff)

    for i, d in enumerate(todo, 1):
        n = compute_outcomes_universe(d)
        if i % 20 == 0 or i == len(todo):
            logger.info("[%d/%d] %s → %d행 INSERT", i, len(todo), d, n)

    logger.info("universe_outcomes 소급 완료: 총 %d일 처리", len(todo))


def main() -> None:
    parser = argparse.ArgumentParser(description="P3 backfill")
    parser.add_argument("--skip-predictions", action="store_true")
    parser.add_argument("--skip-outcomes", action="store_true")
    parser.add_argument("--from-date", default=None, help="outcomes 소급 시작일 YYYY-MM-DD")
    args = parser.parse_args()

    if not args.skip_predictions:
        backfill_predictions()

    if not args.skip_outcomes:
        backfill_outcomes(args.from_date)

    logger.info("=== P3 backfill 완료 ===")


if __name__ == "__main__":
    main()
