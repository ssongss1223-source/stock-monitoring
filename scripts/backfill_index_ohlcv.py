#!/usr/bin/env python3
"""market_index에 코스피(1001)/코스닥(2001) 20년치 백필."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pykrx import stock

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn, init_db

# Load .env file
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

INDICES = {"1001": "KOSPI", "2001": "KOSDAQ"}
START = "20050103"
END   = pd.Timestamp.today().strftime("%Y%m%d")


def fetch_index(ticker: str) -> pd.DataFrame:
    df = stock.get_index_ohlcv_by_date(START, END, ticker)
    if df is None or df.empty:
        raise RuntimeError(f"pykrx 반환 없음: {ticker}")
    df = df.rename(columns={
        "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume", "거래대금": "amount",
        "상장시가총액": "market_cap",
    })
    df.index.name = "date"
    df.index = pd.to_datetime(df.index)
    df["ticker"] = ticker
    return df[["ticker", "open", "high", "low", "close", "volume", "amount", "market_cap"]]


def backfill(conn) -> None:
    for ticker, name in INDICES.items():
        print(f"[{ticker}] {name} 수집 중 ({START}~{END})...", flush=True)
        df = fetch_index(ticker)
        df = df.reset_index()  # date 컬럼으로
        conn.register("_idx_rows", df)
        result = conn.execute("""
            INSERT OR REPLACE INTO market_index
                (ticker, date, open, high, low, close, volume, amount, market_cap)
            SELECT ticker, CAST(date AS DATE), open, high, low, close,
                   CAST(volume AS BIGINT), amount, CAST(market_cap AS BIGINT)
            FROM _idx_rows
        """)
        print(f"  → {len(df)}행 처리 ({ticker})")


if __name__ == "__main__":
    init_db()
    conn = get_conn()
    try:
        backfill(conn)
    finally:
        conn.close()
    print("완료.")
