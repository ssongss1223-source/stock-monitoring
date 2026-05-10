"""
ohlcv_daily amount/market_cap/shares 소급 UPDATE.

기존 행에 NULL로 남아있는 거래대금·시가총액·상장주식수를 pykrx로 재조회해 채움.
이미 값이 있는 행은 건너뜀.

Usage:
    python scripts/backfill_amount.py
    python scripts/backfill_amount.py --from 005930   # 특정 종목부터 재개
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd
from pykrx import stock

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fetch_cap(ticker: str, start: str, end: str) -> pd.DataFrame:
    """pykrx get_market_cap_by_date → amount, market_cap, shares 반환."""
    try:
        df = stock.get_market_cap_by_date(start, end, ticker)
        if df.empty:
            return pd.DataFrame()
        # pykrx 컬럼명 매핑 (버전별 차이 대응)
        col_map = {}
        for c in df.columns:
            if '시가총액' in c:
                col_map[c] = 'market_cap'
            elif '거래대금' in c:
                col_map[c] = 'amount'
            elif '상장주식수' in c or '상장 주식수' in c:
                col_map[c] = 'shares'
        df = df.rename(columns=col_map)
        keep = [c for c in ['market_cap', 'amount', 'shares'] if c in df.columns]
        if not keep:
            return pd.DataFrame()
        df = df[keep].copy()
        df.index.name = 'date'
        df = df.reset_index()
        df['date'] = pd.to_datetime(df['date']).dt.date
        return df
    except Exception as e:
        logger.warning("%s 조회 실패: %s", ticker, e)
        return pd.DataFrame()


def run(from_ticker: str | None) -> None:
    conn = get_conn(read_only=True)
    # amount가 NULL인 종목만 대상
    rows = conn.execute("""
        SELECT ticker, MIN(date) AS min_d, MAX(date) AS max_d
        FROM ohlcv_daily
        WHERE amount IS NULL
        GROUP BY ticker
        ORDER BY ticker
    """).fetchall()
    conn.close()

    tickers = [(r[0], str(r[1]), str(r[2])) for r in rows]
    if from_ticker:
        tickers = [(t, s, e) for t, s, e in tickers if t >= from_ticker]

    logger.info("amount NULL 종목: %d개", len(tickers))

    for i, (ticker, min_d, max_d) in enumerate(tickers):
        start_str = min_d.replace('-', '')
        end_str   = max_d.replace('-', '')
        logger.info("[%d/%d] %s (%s~%s)", i + 1, len(tickers), ticker, min_d, max_d)

        df_cap = _fetch_cap(ticker, start_str, end_str)
        if df_cap.empty:
            logger.warning("%s: 데이터 없음, 건너뜀", ticker)
            time.sleep(1)
            continue

        df_cap['ticker'] = ticker
        cols = ['ticker', 'date'] + [c for c in ['amount', 'market_cap', 'shares'] if c in df_cap.columns]
        df_cap = df_cap[cols]

        set_clauses = ', '.join(
            f"{c} = _tmp.{c}" for c in cols if c not in ('ticker', 'date')
        )
        conn_w = get_conn()
        conn_w.register('_tmp', df_cap)
        conn_w.execute(f"""
            UPDATE ohlcv_daily
            SET {set_clauses}
            FROM _tmp
            WHERE ohlcv_daily.ticker = _tmp.ticker
              AND ohlcv_daily.date   = CAST(_tmp.date AS DATE)
        """)
        conn_w.close()

        time.sleep(0.3)  # API 부하 방지

    logger.info("amount 소급 완료")


def main() -> None:
    p = argparse.ArgumentParser(description="ohlcv_daily amount/market_cap 소급 UPDATE")
    p.add_argument('--from', dest='from_ticker', default=None,
                   help='이 종목코드부터 재개')
    args = p.parse_args()
    run(args.from_ticker)


if __name__ == '__main__':
    main()
