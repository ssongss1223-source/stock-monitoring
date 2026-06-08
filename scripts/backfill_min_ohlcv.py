"""
60분봉 yfinance 백필 스크립트

ohlcv_min 테이블에 yfinance 1h 데이터 (최대 730일) 를 INSERT OR IGNORE.
실패 종목은 1회 자동 재시도.

Usage:
    python scripts/backfill_min_ohlcv.py
    python scripts/backfill_min_ohlcv.py --from 005930   # 특정 종목부터 재개
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

_PERIOD = "730d"   # yfinance 1h 최대 허용 기간
_SOURCE = "yfinance"
_INSERT_COLS = ["ticker", "dt", "open", "high", "low", "close", "volume", "amount", "source"]


def fetch_1h(ticker: str) -> pd.DataFrame:
    """.KS 우선 시도, 없으면 .KQ. 실패 시 빈 DataFrame."""
    for suffix in [".KS", ".KQ"]:
        try:
            df = yf.download(
                f"{ticker}{suffix}",
                period=_PERIOD,
                interval="1h",
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
        except Exception as e:
            logger.debug("%s%s 조회 오류: %s", ticker, suffix, e)
    return pd.DataFrame()


def prep(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance DataFrame → ohlcv_min INSERT용 스키마."""
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df.index.name = "dt"
    df = df.reset_index()

    df["dt"] = pd.to_datetime(df["dt"])
    if df["dt"].dt.tz is not None:
        df["dt"] = df["dt"].dt.tz_convert("Asia/Seoul").dt.tz_localize(None)

    df["ticker"] = ticker
    df["amount"] = None
    df["source"] = _SOURCE
    df = df[df["close"] > 0].copy()
    return df[[c for c in _INSERT_COLS if c in df.columns]]


def insert_ignore(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    conn.register("_min_batch", df)
    conn.execute(f"""
        INSERT OR IGNORE INTO ohlcv_min ({', '.join(_INSERT_COLS)})
        SELECT {', '.join(_INSERT_COLS)} FROM _min_batch
    """)
    return len(df)


def process_ticker(conn, ticker: str) -> bool:
    """단일 종목 처리. 성공 True, 실패 False."""
    raw = fetch_1h(ticker)
    if raw.empty:
        logger.warning("  %s: 데이터 없음", ticker)
        return False

    prepped = prep(raw, ticker)
    if prepped.empty:
        logger.warning("  %s: 유효 데이터 없음", ticker)
        return False

    n = insert_ignore(conn, prepped)
    logger.info("  %s: %d행 (%s ~ %s)",
                ticker, n, prepped["dt"].min(), prepped["dt"].max())
    return True


def run(from_ticker: str | None) -> None:
    conn = get_conn()

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM universe_daily ORDER BY ticker"
    ).fetchall()]

    # 2024-01-01 이전 데이터가 이미 있으면 백필 완료로 간주
    done = set(r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ohlcv_min WHERE dt < '2024-01-01'"
    ).fetchall())

    todo = [t for t in tickers if t not in done]
    if from_ticker:
        todo = [t for t in todo if t >= from_ticker]

    logger.info("총 %d종목 | 완료: %d | 남은: %d", len(tickers), len(done), len(todo))

    failed: list[str] = []

    for i, ticker in enumerate(todo, 1):
        logger.info("[%d/%d] %s ...", i, len(todo), ticker)
        ok = process_ticker(conn, ticker)
        if not ok:
            failed.append(ticker)
        if i % 50 == 0:
            conn.commit()
            logger.info("=== %d종목 처리 완료 ===", i)
        time.sleep(0.3)

    conn.commit()

    # 1회 자동 재시도
    if failed:
        logger.info("실패 %d개 재시도 (10초 대기)...", len(failed))
        time.sleep(10)
        still_failed: list[str] = []
        for i, ticker in enumerate(failed, 1):
            logger.info("[재시도 %d/%d] %s ...", i, len(failed), ticker)
            ok = process_ticker(conn, ticker)
            if not ok:
                still_failed.append(ticker)
            time.sleep(1.0)
        conn.commit()
        if still_failed:
            logger.warning("재시도 후도 실패 %d개: %s", len(still_failed), still_failed)
        else:
            logger.info("재시도 전부 성공!")

    conn.close()
    logger.info("백필 완료!")


def main() -> None:
    p = argparse.ArgumentParser(description="60분봉 yfinance 백필 (ohlcv_min)")
    p.add_argument("--from", dest="from_ticker", default=None,
                   help="이 종목코드부터 재개 (예: 035720)")
    args = p.parse_args()
    run(args.from_ticker)


if __name__ == "__main__":
    main()
