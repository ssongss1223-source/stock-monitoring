#!/usr/bin/env python3
"""
기존 ohlcv_daily 신규 컬럼 소급 수집 + market_index 초기 적재.
  - per / pbr / eps / bps / div_yield
  - foreign_exh_rate
  - short_volume / short_ratio

실행: python -m scripts.backfill_new_cols
옵션: --from TICKER  (중단 후 특정 종목부터 재개)
      --dry-run      (변경 없이 대상 목록만 출력)

예상 소요 시간: 359종목 기준 약 15~25분
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from dotenv import load_dotenv
load_dotenv()

from pykrx import stock as krx
from data.db import get_conn, init_db
from data.store import MarketIndexStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SLEEP = 0.4  # API 호출 간격(초) — KRX 서버 부하 방지


def _update(conn, df: pd.DataFrame, col_map: dict, ticker: str) -> int:
    """DataFrame → DuckDB UPDATE.
    col_map = {ohlcv_daily_col: df_original_col}
    """
    if df is None or df.empty:
        return 0

    df = df.copy()
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "_date"
    df = df.reset_index()
    df["_ticker"] = ticker

    # 한글 컬럼명을 SQL-safe 이름으로 변환
    rename = {src: f"_v{i}" for i, (_, src) in enumerate(col_map.items())}
    df = df.rename(columns=rename)

    conn.register("_upd", df)
    set_clause = ", ".join(
        f"{col} = _upd._v{i}" for i, (col, _) in enumerate(col_map.items())
    )
    conn.execute(f"""
        UPDATE ohlcv_daily
        SET {set_clause}
        FROM _upd
        WHERE ohlcv_daily.ticker = _upd._ticker
          AND ohlcv_daily.date   = CAST(_upd._date AS DATE)
    """)
    return len(df)


def backfill_ticker(conn, ticker: str, start_str: str, end_str: str) -> None:
    # 1. Fundamental (PER/PBR/EPS/BPS/DIV)
    try:
        df = krx.get_market_fundamental(start_str, end_str, ticker)
        n = _update(conn, df, {
            "per": "PER", "pbr": "PBR",
            "eps": "EPS", "bps": "BPS", "div_yield": "DIV",
        }, ticker)
        logger.debug("%s fundamental %d건", ticker, n)
    except Exception as e:
        logger.warning("%s fundamental 실패: %s", ticker, e)
    time.sleep(SLEEP)

    # 2. 외국인 한도소진률
    try:
        df = krx.get_exhaustion_rates_of_foreign_investment_by_date(
            start_str, end_str, ticker
        )
        n = _update(conn, df, {"foreign_exh_rate": "한도소진률"}, ticker)
        logger.debug("%s 외국인한도소진률 %d건", ticker, n)
    except Exception as e:
        logger.warning("%s 외국인한도소진률 실패: %s", ticker, e)
    time.sleep(SLEEP)

    # 3. 공매도 거래량
    try:
        df = krx.get_shorting_volume_by_date(start_str, end_str, ticker)
        n = _update(conn, df, {"short_volume": "공매도", "short_ratio": "비중"}, ticker)
        logger.debug("%s 공매도거래량 %d건", ticker, n)
    except Exception as e:
        logger.warning("%s 공매도거래량 실패: %s", ticker, e)
    time.sleep(SLEEP)


def main() -> None:
    parser = argparse.ArgumentParser(description="ohlcv_daily 신규 컬럼 소급 수집")
    parser.add_argument("--from", dest="from_ticker", default=None,
                        help="이 종목코드부터 재개 (중단 후 재시작용)")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 변경 없이 대상 목록만 출력")
    args = parser.parse_args()

    init_db()
    conn = get_conn()

    rows = conn.execute("""
        SELECT ticker, MIN(date) AS min_date, MAX(date) AS max_date
        FROM ohlcv_daily
        GROUP BY ticker
        ORDER BY ticker
    """).fetchall()

    if args.dry_run:
        print(f"대상 종목: {len(rows)}개")
        for r in rows[:10]:
            print(f"  {r[0]}  {r[1]} ~ {r[2]}")
        print("  ...")
        conn.close()
        return

    # --from 옵션: 지정 종목부터 시작
    if args.from_ticker:
        rows = [(t, s, e) for t, s, e in rows if t >= args.from_ticker]
        logger.info("%s 부터 재개, 남은 종목: %d개", args.from_ticker, len(rows))

    total = len(rows)
    logger.info("소급 시작: %d종목 × 3 API = 약 %d분 예상",
                total, total * 3 * SLEEP // 60 + 1)

    for i, (ticker, min_date, max_date) in enumerate(rows, 1):
        start_str = min_date.strftime("%Y%m%d")
        end_str = max_date.strftime("%Y%m%d")
        logger.info("[%d/%d] %s  (%s ~ %s)", i, total, ticker, start_str, end_str)
        backfill_ticker(conn, ticker, start_str, end_str)

    conn.close()
    logger.info("ohlcv_daily 신규 컬럼 소급 완료")

    # market_index 초기 수집 (비어 있으면 900일치 자동 소급)
    logger.info("market_index (KOSPI/KOSDAQ) 초기 수집...")
    MarketIndexStore.fetch_and_update()
    logger.info("전체 완료")


if __name__ == "__main__":
    main()
