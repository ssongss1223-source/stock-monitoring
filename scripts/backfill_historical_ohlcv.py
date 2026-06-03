"""
20년치 OHLCV 백필 스크립트 (yfinance)

ohlcv_daily 테이블에 2005-01-01 ~ 2023-11-20 구간을 추가.
이미 해당 구간 데이터가 있는 종목은 스킵 (INSERT OR IGNORE).
.KS suffix 우선 시도, 없으면 .KQ 시도.

Usage:
    python scripts/backfill_historical_ohlcv.py
    python scripts/backfill_historical_ohlcv.py --from 005930   # 특정 종목부터 재개
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

START_DATE = "2005-01-01"
END_DATE   = "2023-11-20"  # 기존 데이터 시작(2023-11-21) 하루 전

_INSERT_COLS = [
    "ticker", "date", "open", "high", "low", "close", "volume",
]


def fetch_ohlcv(ticker: str) -> pd.DataFrame:
    """.KS 우선 시도, 없으면 .KQ. 실패 시 빈 DataFrame."""
    for suffix in [".KS", ".KQ"]:
        try:
            df = yf.download(
                f"{ticker}{suffix}",
                start=START_DATE,
                end=END_DATE,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                continue
            # MultiIndex 컬럼 처리 (yfinance 버전에 따라 다름)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception as e:
            logger.debug("%s%s 조회 오류: %s", ticker, suffix, e)
    return pd.DataFrame()


def prep(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance DataFrame → INSERT용 스키마."""
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["ticker"] = ticker
    df = df[df["close"] > 0].copy()
    # 필요 컬럼만 선택
    return df[[c for c in _INSERT_COLS if c in df.columns]]


def insert_ignore(conn, df: pd.DataFrame) -> int:
    """INSERT OR IGNORE — 기존 데이터 덮어쓰지 않음."""
    if df.empty:
        return 0
    cols = [c for c in _INSERT_COLS if c in df.columns]
    conn.register("_hist", df[cols])
    conn.execute(f"""
        INSERT OR IGNORE INTO ohlcv_daily ({', '.join(cols)})
        SELECT {', '.join(cols)} FROM _hist
    """)
    return len(df)


def run(from_ticker: str | None) -> None:
    conn = get_conn()

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM universe_daily ORDER BY ticker"
    ).fetchall()]

    # 이미 2020년 이전 데이터가 있으면 충분히 백필된 것으로 간주
    done = set(r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ohlcv_daily WHERE date < '2020-01-01'"
    ).fetchall())

    todo = [t for t in tickers if t not in done]
    if from_ticker:
        todo = [t for t in todo if t >= from_ticker]

    logger.info("총 %d종목 | 완료: %d | 남은: %d", len(tickers), len(done), len(todo))

    total_rows = 0
    failed: list[str] = []

    for i, ticker in enumerate(todo, 1):
        logger.info("[%d/%d] %s ...", i, len(todo), ticker)

        raw = fetch_ohlcv(ticker)
        if raw.empty:
            logger.warning("  %s: 데이터 없음", ticker)
            failed.append(ticker)
            time.sleep(0.5)
            continue

        prepped = prep(raw, ticker)
        if prepped.empty:
            logger.warning("  %s: 유효 데이터 없음", ticker)
            failed.append(ticker)
            continue

        n = insert_ignore(conn, prepped)
        total_rows += n
        logger.info("  %s: %d행 추가 (%s ~ %s)",
                    ticker, n,
                    prepped["date"].min(), prepped["date"].max())

        if i % 50 == 0:
            conn.commit()
            logger.info("=== %d종목 완료, 누적 %d행 ===", i, total_rows)

        time.sleep(0.2)  # yfinance rate limiting 방지

    conn.commit()
    conn.close()

    logger.info("완료! 총 %d행 추가", total_rows)
    if failed:
        logger.warning("데이터 없는 종목 %d개: %s", len(failed), failed)


def main() -> None:
    p = argparse.ArgumentParser(description="20년치 OHLCV 백필 (yfinance)")
    p.add_argument("--from", dest="from_ticker", default=None,
                   help="이 종목코드부터 재개 (예: 035720)")
    args = p.parse_args()
    run(args.from_ticker)


if __name__ == "__main__":
    main()
