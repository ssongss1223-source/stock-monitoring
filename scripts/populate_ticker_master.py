#!/usr/bin/env python3
"""ticker_master 채우기 — KOSPI/KOSDAQ 구분 + 시총 + 종목명.

VM에서 실행:
  sudo -u stock .venv/bin/python3 scripts/populate_ticker_master.py

출력:
  KOSPI/KOSDAQ별 종목수, 시총 상위 10
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn

try:
    from pykrx import stock as krx
except ImportError:
    print("ERROR: pykrx 미설치 — pip install pykrx")
    sys.exit(1)


def main() -> None:
    conn = get_conn()

    conn.execute(
        "ALTER TABLE ticker_master ADD COLUMN IF NOT EXISTS market_cap BIGINT DEFAULT 0"
    )

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ohlcv_daily ORDER BY ticker"
    ).fetchall()]
    print(f"ohlcv_daily 종목수: {len(tickers)}")

    today = date.today().strftime("%Y%m%d")

    print("pykrx 시장 목록 조회 중...")
    kospi_set = set(krx.get_market_ticker_list(today, market="KOSPI"))
    kosdaq_set = set(krx.get_market_ticker_list(today, market="KOSDAQ"))
    print(f"  KOSPI {len(kospi_set)}종목, KOSDAQ {len(kosdaq_set)}종목")

    print("시총 데이터 조회 중...")
    cap_map: dict[str, int] = {}
    for market_name in ("KOSPI", "KOSDAQ"):
        try:
            df = krx.get_market_cap_by_ticker(today, market=market_name)
            cap_col = next((c for c in ("시가총액", "Mktcap") if c in df.columns), None)
            if cap_col:
                for t, row in df.iterrows():
                    cap_map[t] = int(row[cap_col])
        except Exception as e:
            print(f"  {market_name} 시총 조회 실패: {e}")
    print(f"  시총 정보 {len(cap_map)}종목")

    rows = []
    market_counts: dict[str, int] = {"KOSPI": 0, "KOSDAQ": 0, "UNKNOWN": 0}

    for ticker in tickers:
        if ticker in kospi_set:
            market = "KOSPI"
        elif ticker in kosdaq_set:
            market = "KOSDAQ"
        else:
            market = "UNKNOWN"

        try:
            name = krx.get_market_ticker_name(ticker) or ticker
        except Exception:
            name = ticker

        cap = cap_map.get(ticker, 0)
        market_counts[market] += 1
        rows.append((ticker, name, market, cap))

    conn.execute("DELETE FROM ticker_master")
    conn.executemany(
        "INSERT INTO ticker_master (ticker, name, market, sector, listed_date, market_cap) "
        "VALUES (?, ?, ?, NULL, NULL, ?)",
        rows,
    )

    print(f"\n총 {len(rows)}종목 저장")
    for m, cnt in market_counts.items():
        print(f"  {m}: {cnt}종목")

    top10 = conn.execute("""
        SELECT ticker, name, market, market_cap
        FROM ticker_master
        WHERE market_cap >= 1
        ORDER BY market_cap DESC
        LIMIT 10
    """).fetchall()
    print("\n[시총 상위 10]")
    for i, r in enumerate(top10, 1):
        print(f"  {i:2}. {r[1]:<20} {r[2]:<8} {r[3]:>15,}")

    conn.close()


if __name__ == "__main__":
    main()
