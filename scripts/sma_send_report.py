#!/usr/bin/env python3
"""DB에 저장된 SMA 백테스트 결과로 텔레그램 리포트 전송 (재전송용).

Usage:
    python scripts/sma_send_report.py [--date YYYY-MM-DD]
"""
from __future__ import annotations
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn
from backtest.sma_config import UNIVERSE, FIXED
from backtest.sma_optimizer import find_best_params
from backtest.sma_backtester import run_backtest
from backtest.sma_reporter import format_report, send_telegram


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()), help="결과 날짜 (기본: 오늘)")
    args = parser.parse_args()

    ok, fail = 0, 0
    for stock in UNIVERSE:
        ticker, name = stock["ticker"], stock["name"]

        conn = get_conn(read_only=True)
        results = conn.execute(
            "SELECT * FROM sma_backtest_results WHERE ticker=? AND run_date=?",
            [ticker, args.date],
        ).df()
        conn.close()

        if results.empty:
            print(f"[{ticker}] 결과 없음, 스킵")
            continue

        best = find_best_params(results)
        if best is None:
            print(f"[{ticker}] 최적 파라미터 없음, 스킵")
            continue

        conn2 = get_conn(read_only=True)
        df = conn2.execute(
            "SELECT date, close FROM ohlcv_daily WHERE ticker=? AND close>0 ORDER BY date",
            [ticker],
        ).df()
        conn2.close()
        df["date"] = pd.to_datetime(df["date"])
        close = df.set_index("date")["close"]

        _, metrics = run_backtest(close, best, FIXED)
        is_wf = bool(results["is_walkforward"].any())
        msg = format_report(ticker, name, best, metrics, is_walkforward=is_wf)
        sent = send_telegram(msg)
        if sent:
            ok += 1
            print(f"[{ticker}] {name} SMA{best['sma_period']} Calmar={metrics['calmar']:.2f} -> OK")
        else:
            fail += 1
            print(f"[{ticker}] {name} -> FAIL")

    print(f"\n완료: 성공 {ok} / 실패 {fail}")


if __name__ == "__main__":
    main()
