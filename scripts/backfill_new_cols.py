#!/usr/bin/env python3
"""
OHLCV 초기 적재 + 신규 컬럼 소급 수집 + market_index 초기 적재.

동작:
  1. 유니버스 종목 중 ohlcv_daily에 없는 종목 → OhlcvStore로 900일치 풀 적재
  2. ohlcv_daily에 있는 종목 → per/pbr/eps/bps/div_yield/foreign_exh_rate/short_volume 소급 UPDATE
  3. market_index (KOSPI/KOSDAQ) 초기 적재

실행: python -m scripts.backfill_new_cols
옵션: --from TICKER  (중단 후 특정 종목부터 재개)
      --dry-run      (변경 없이 대상 목록만 출력)

예상 소요 시간: 101종목 기준 약 30~50분
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
from data.store import MarketIndexStore, OhlcvStore
from agents.universe_manager import UniverseManager

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
    parser = argparse.ArgumentParser(description="OHLCV 초기 적재 + 신규 컬럼 소급")
    parser.add_argument("--from", dest="from_ticker", default=None,
                        help="이 종목코드부터 재개 (중단 후 재시작용)")
    parser.add_argument("--dry-run", action="store_true",
                        help="변경 없이 대상 목록만 출력")
    args = parser.parse_args()

    init_db()

    # 유니버스에서 전체 종목 목록 수집
    universe = UniverseManager().get_universe()
    all_tickers = sorted({ticker for ticker, _ in universe})
    logger.info("유니버스 종목: %d개", len(all_tickers))

    if args.from_ticker:
        all_tickers = [t for t in all_tickers if t >= args.from_ticker]
        logger.info("--from %s 이후 재개: %d개", args.from_ticker, len(all_tickers))

    if args.dry_run:
        print(f"대상 종목: {len(all_tickers)}개")
        for t in all_tickers[:10]:
            print(f"  {t}")
        print("  ...")
        return

    # 현재 DB에 있는 종목 확인
    conn = get_conn()
    existing = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT ticker FROM ohlcv_daily"
        ).fetchall()
    }
    conn.close()

    new_tickers = [t for t in all_tickers if t not in existing]
    old_tickers = [t for t in all_tickers if t in existing]
    logger.info("신규 적재 필요: %d종목 / 컬럼 소급 필요: %d종목",
                len(new_tickers), len(old_tickers))

    # ── 1단계: 신규 종목 OHLCV + 전체 컬럼 풀 적재 ────────────────────────
    for i, ticker in enumerate(new_tickers, 1):
        logger.info("[신규 %d/%d] %s OHLCV 900일 적재 중...", i, len(new_tickers), ticker)
        try:
            OhlcvStore.fetch_and_update_daily(ticker)
        except Exception as e:
            logger.warning("%s OHLCV 실패: %s", ticker, e)

    # ── 2단계: 기존 종목 신규 컬럼 소급 UPDATE ─────────────────────────────
    if old_tickers:
        conn = get_conn()
        rows = conn.execute("""
            SELECT ticker, MIN(date) AS min_date, MAX(date) AS max_date
            FROM ohlcv_daily
            WHERE ticker = ANY(?)
            GROUP BY ticker ORDER BY ticker
        """, [old_tickers]).fetchall()
        conn.close()

        logger.info("컬럼 소급 시작: %d종목", len(rows))
        conn = get_conn()
        for i, (ticker, min_date, max_date) in enumerate(rows, 1):
            start_str = min_date.strftime("%Y%m%d")
            end_str = max_date.strftime("%Y%m%d")
            logger.info("[소급 %d/%d] %s (%s~%s)", i, len(rows), ticker, start_str, end_str)
            backfill_ticker(conn, ticker, start_str, end_str)
        conn.close()

    logger.info("ohlcv_daily 적재 완료")

    # ── 3단계: market_index (이미 채워져 있으면 오늘치만 시도) ─────────────
    logger.info("market_index 갱신...")
    try:
        MarketIndexStore.fetch_and_update()
    except Exception as e:
        logger.warning("market_index 갱신 실패 (장중이면 정상): %s", e)
    logger.info("전체 완료")


if __name__ == "__main__":
    main()
