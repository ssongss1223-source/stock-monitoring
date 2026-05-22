"""
universe_daily 역사 데이터 백필 스크립트.

1단계: SQL CTE로 start~end 구간 전종목 일봉 피쳐 INSERT OR REPLACE
       (trend_score / vol_score_live = NULL — live orchestrator가 채움)
2단계: label_one()으로 date <= today-15일 행 17개 라벨 채우기

실행 예:
  python scripts/build_historical_matrix.py
  python scripts/build_historical_matrix.py --start 2024-06-01
  python scripts/build_historical_matrix.py --skip-features   # 라벨만 재처리
  python scripts/build_historical_matrix.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta

import pandas as pd

from data.db import get_conn
from backtest.labeler import label_one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_DEFAULT_START = "2024-01-01"

_LABEL_COLS = [
    "entry_price",
    "label_3d_3pct", "label_3d_5pct", "label_3d_10pct",
    "label_5d_3pct", "label_5d_5pct", "label_5d_10pct",
    "label_10d_3pct", "label_10d_5pct", "label_10d_10pct",
    "label_3d_3pct_c2", "label_3d_5pct_c2",
    "label_5d_3pct_c2", "label_5d_5pct_c2", "label_5d_10pct_c2",
    "label_10d_3pct_c2", "label_10d_5pct_c2", "label_10d_10pct_c2",
]


def _build_feature_sql(start: str, end: str, select_only: bool = False) -> str:
    """피쳐 INSERT SQL 반환. select_only=True 이면 SELECT COUNT(*) 래퍼 포함."""
    lookback = (date.fromisoformat(start) - timedelta(days=365)).isoformat()

    insert_header = """
        INSERT OR REPLACE INTO universe_daily (
            date, ticker,
            close, volume, market_cap, per, pbr, turnover_rate,
            trend_score,
            ma5_ratio, ma20_ratio, ma60_ratio, ma120_ratio,
            rsi_14, bb_position, hist_vol_20d, close_to_52w_high,
            foreign_net_5d, inst_net_5d, foreign_net_20d, volume_surge_5d,
            kospi_ret_5d, kospi_ret_20d,
            vol_score_approx, vol_score_live,
            grade_approx, grade_live
        )
    """ if not select_only else ""

    body = f"""
        WITH
        hist AS (
            SELECT ticker, date,
                   CAST(close AS DOUBLE)                         AS close,
                   CAST(volume AS DOUBLE)                        AS volume,
                   market_cap, per, pbr,
                   COALESCE(CAST(shares AS BIGINT), 0)           AS shares,
                   COALESCE(CAST(foreign_net AS DOUBLE), 0.0)    AS foreign_net,
                   COALESCE(CAST(inst_net AS DOUBLE), 0.0)       AS inst_net
            FROM ohlcv_daily
            WHERE date >= CAST('{lookback}' AS DATE)
              AND ticker IN (
                  SELECT DISTINCT ticker FROM ohlcv_daily
                  WHERE date BETWEEN CAST('{start}' AS DATE) AND CAST('{end}' AS DATE)
              )
        ),
        roll AS (
            SELECT ticker, date, close, volume, market_cap, per, pbr, shares,
                   foreign_net, inst_net,
                   CASE WHEN shares > 0 THEN volume / shares ELSE NULL END AS turnover_rate,
                   close / NULLIF(AVG(close) OVER w5,   0)  AS ma5_ratio,
                   close / NULLIF(AVG(close) OVER w20,  0)  AS ma20_ratio,
                   close / NULLIF(AVG(close) OVER w60,  0)  AS ma60_ratio,
                   close / NULLIF(AVG(close) OVER w120, 0)  AS ma120_ratio,
                   close / NULLIF(MAX(close) OVER w252, 0)  AS close_to_52w_high,
                   AVG(close)    OVER w20  AS sma20,
                   STDDEV(close) OVER w20  AS std20,
                   volume / NULLIF(
                       AVG(volume) OVER (
                           PARTITION BY ticker ORDER BY date
                           ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                       ), 0
                   )                                          AS volume_surge_5d,
                   SUM(foreign_net) OVER w5   AS foreign_net_5d,
                   SUM(inst_net)    OVER w5   AS inst_net_5d,
                   SUM(foreign_net) OVER w20  AS foreign_net_20d,
                   QUANTILE_CONT(
                       CASE WHEN shares > 0 THEN volume / shares ELSE NULL END, 0.8
                   ) OVER w60  AS turnover_p80
            FROM hist
            WINDOW
                w5   AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4   PRECEDING AND CURRENT ROW),
                w20  AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19  PRECEDING AND CURRENT ROW),
                w60  AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59  PRECEDING AND CURRENT ROW),
                w120 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW),
                w252 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
        ),
        pdiff AS (
            SELECT ticker, date,
                   close - LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS d
            FROM hist
        ),
        rsi AS (
            SELECT ticker, date,
                   100.0 - 100.0 / (
                       1 + AVG(GREATEST(d, 0)) OVER (
                               PARTITION BY ticker ORDER BY date
                               ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
                         / NULLIF(AVG(GREATEST(-d, 0)) OVER (
                               PARTITION BY ticker ORDER BY date
                               ROWS BETWEEN 13 PRECEDING AND CURRENT ROW), 0)
                   ) AS rsi_14
            FROM pdiff
        ),
        lret AS (
            SELECT ticker, date,
                   ln(close / NULLIF(LAG(close) OVER (PARTITION BY ticker ORDER BY date), 0)) AS lr
            FROM hist
        ),
        hvol AS (
            SELECT ticker, date,
                   STDDEV(lr) OVER (
                       PARTITION BY ticker ORDER BY date
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS hist_vol_20d
            FROM lret
        ),
        kospi AS (
            SELECT date,
                   close / NULLIF(LAG(close, 5)  OVER (ORDER BY date), 0) - 1 AS kospi_ret_5d,
                   close / NULLIF(LAG(close, 20) OVER (ORDER BY date), 0) - 1 AS kospi_ret_20d
            FROM market_index WHERE ticker = '1001'
        ),
        combined AS (
            SELECT r.ticker, r.date,
                   r.close, CAST(r.volume AS BIGINT) AS volume,
                   r.market_cap, r.per, r.pbr, r.turnover_rate,
                   r.ma5_ratio, r.ma20_ratio, r.ma60_ratio, r.ma120_ratio, r.close_to_52w_high,
                   CASE WHEN r.std20 > 0
                        THEN (r.close - (r.sma20 - 2 * r.std20)) / (4 * r.std20)
                        ELSE 0.5 END                             AS bb_position,
                   hv.hist_vol_20d, rs.rsi_14,
                   r.foreign_net_5d, r.inst_net_5d, r.foreign_net_20d, r.volume_surge_5d,
                   k.kospi_ret_5d, k.kospi_ret_20d,
                   CAST(
                       CASE WHEN r.volume_surge_5d >= 2.0 THEN 5 ELSE 0 END
                       + CASE WHEN r.turnover_rate IS NOT NULL
                                   AND r.turnover_p80 IS NOT NULL
                                   AND r.turnover_rate >= r.turnover_p80 THEN 3 ELSE 0 END
                       + CASE WHEN r.foreign_net_5d > 0 THEN 2 ELSE 0 END
                   AS SMALLINT)                                  AS vol_score_approx
            FROM roll r
            LEFT JOIN rsi  rs USING (ticker, date)
            LEFT JOIN hvol hv USING (ticker, date)
            LEFT JOIN kospi k  USING (date)
        )
    """

    if select_only:
        return body + f"""
        SELECT COUNT(*) FROM combined c
        WHERE c.date BETWEEN CAST('{start}' AS DATE) AND CAST('{end}' AS DATE)
        """

    return insert_header + body + f"""
        SELECT
            c.date, c.ticker,
            c.close, c.volume, c.market_cap, c.per, c.pbr, c.turnover_rate,
            NULL::SMALLINT  AS trend_score,
            c.ma5_ratio, c.ma20_ratio, c.ma60_ratio, c.ma120_ratio,
            c.rsi_14, c.bb_position, c.hist_vol_20d, c.close_to_52w_high,
            c.foreign_net_5d, c.inst_net_5d, c.foreign_net_20d, c.volume_surge_5d,
            c.kospi_ret_5d, c.kospi_ret_20d,
            c.vol_score_approx, NULL::SMALLINT AS vol_score_live,
            CASE WHEN c.vol_score_approx >= 8 THEN 'S'
                 WHEN c.vol_score_approx >= 5 THEN 'A'
                 WHEN c.vol_score_approx >= 2 THEN 'B'
                 ELSE NULL END                  AS grade_approx,
            NULL::VARCHAR   AS grade_live
        FROM combined c
        WHERE c.date BETWEEN CAST('{start}' AS DATE) AND CAST('{end}' AS DATE)
    """


def insert_features(start: str, end: str, dry_run: bool = False) -> None:
    """start~end 구간 전종목 일봉 피쳐를 universe_daily에 INSERT OR REPLACE."""
    if dry_run:
        conn = get_conn(read_only=True)
        try:
            count = conn.execute(_build_feature_sql(start, end, select_only=True)).fetchone()[0]
            print(f"[dry-run] 삽입 예정: {count:,}행 ({start} ~ {end})")
        finally:
            conn.close()
        return

    logger.info("피쳐 INSERT 시작: %s ~ %s", start, end)
    t0 = time.time()
    conn = get_conn()
    try:
        conn.execute(_build_feature_sql(start, end))
        elapsed = time.time() - t0
        logger.info("피쳐 INSERT 완료 (%.1f초)", elapsed)
    except Exception:
        logger.exception("피쳐 INSERT 실패")
        raise
    finally:
        conn.close()


def update_labels(cutoff_days: int = 15, dry_run: bool = False) -> None:
    """universe_daily 에서 label_3d_3pct IS NULL 이고 date <= today-cutoff_days 인 행 라벨 채우기."""
    cutoff = (date.today() - timedelta(days=cutoff_days)).isoformat()

    conn_r = get_conn(read_only=True)
    try:
        rows = conn_r.execute("""
            SELECT ticker, date::VARCHAR FROM universe_daily
            WHERE label_3d_3pct IS NULL
              AND date <= CAST(? AS DATE)
            ORDER BY date, ticker
        """, [cutoff]).fetchall()
    finally:
        conn_r.close()

    if not rows:
        logger.info("라벨 업데이트 대상 없음")
        return

    logger.info("라벨 대상: %d행 (cutoff %s)", len(rows), cutoff)

    if dry_run:
        print(f"[dry-run] 라벨 업데이트 예정: {len(rows):,}행 (cutoff {cutoff})")
        return

    # ohlcv_daily 전체 로드 (label_one 에 필요한 미래 데이터 포함)
    tickers = list({r[0] for r in rows})
    placeholders = ", ".join("?" * len(tickers))
    conn_r = get_conn(read_only=True)
    try:
        df_all = conn_r.execute(
            f"SELECT ticker, date, open, high, low, close FROM ohlcv_daily "
            f"WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
            tickers,
        ).df()
    finally:
        conn_r.close()

    df_all["date"] = pd.to_datetime(df_all["date"])

    labeled_rows: list[dict] = []
    skipped = 0
    for ticker, date_str in rows:
        df = df_all[df_all["ticker"] == ticker].set_index("date")
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        result = label_one(df, date_str)
        if result is not None:
            labeled_rows.append({"ticker": ticker, "date": date_str, **result})
        else:
            skipped += 1

    logger.info("라벨 계산 완료: %d건 성공, %d건 미래데이터 부족으로 스킵", len(labeled_rows), skipped)

    if not labeled_rows:
        return

    df_labels = pd.DataFrame(labeled_rows)
    set_clause = ", ".join(f"ud.{col} = s.{col}" for col in _LABEL_COLS)

    conn = get_conn()
    try:
        conn.register("_lbls", df_labels)
        conn.execute(f"""
            UPDATE universe_daily ud
            SET {set_clause}
            FROM _lbls s
            WHERE ud.date = CAST(s.date AS DATE) AND ud.ticker = s.ticker
        """)
        logger.info("라벨 UPDATE 완료: %d건", len(labeled_rows))
    except Exception:
        logger.exception("라벨 UPDATE 실패")
        raise
    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="universe_daily 역사 데이터 백필")
    p.add_argument("--start", default=_DEFAULT_START, help="시작일 YYYY-MM-DD (기본 2024-01-01)")
    p.add_argument("--end",   default=date.today().isoformat(), help="종료일 YYYY-MM-DD (기본 오늘)")
    p.add_argument("--skip-features", action="store_true", help="피쳐 INSERT 건너뜀")
    p.add_argument("--skip-labels",   action="store_true", help="라벨 UPDATE 건너뜀")
    p.add_argument("--dry-run", action="store_true", help="DB 변경 없이 건수만 출력")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if not args.skip_features:
        insert_features(args.start, args.end, dry_run=args.dry_run)

    if not args.skip_labels:
        update_labels(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
