#!/usr/bin/env python3
# scripts/run_sma_backtest_v2.py
"""SMA 백테스트 v2 실행 — 올바른 회계 + 2계좌 + 진짜 WF."""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from itertools import product
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.sma_config import UNIVERSE, FIXED, WF_TRAIN_YEARS, WF_TEST_YEARS, WF_STEP_YEARS
from backtest.sma_optimizer_v2 import run_walkforward, aggregate_wf_results
from backtest.sma_reporter_v2 import format_report, format_summary
from data.db import get_conn


# ── 파라미터 그리드 (v2) ──────────────────────────────────────

BREAKOUT_GRID = [
    {"sma_period": p}
    for p in range(50, 310, 10)  # 26개
]

PULLBACK_GRID = [
    {"sma_period": p, "pullback_sma_delta": d, "stop_loss_pct": s}
    for p, d, s in product(range(50, 310, 10), [5, 10, 15, 20, 25], [5.0, 8.0])
]  # 26 × 5 × 2 = 260개


# ── 데이터 로드 ───────────────────────────────────────────────

def _load_ohlcv_parquet(ticker: str) -> pd.DataFrame | None:
    """dry-run용: FinanceDataReader로 샘플 데이터 로드 (로컬 DB 없을 때)."""
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start="2015-01-01")
        df = df.rename(columns={c: c.lower() for c in df.columns})
        for col in ("close", "high", "low"):
            if col not in df.columns:
                return None
        df.index = pd.to_datetime(df.index)
        return df[["close", "high", "low"]].dropna()
    except Exception as e:
        print(f"  [dry-run 데이터 로드 실패: {e}]")
        return None


def load_ohlcv(ticker: str, conn) -> pd.DataFrame | None:
    """ohlcv_daily에서 OHLCV 로드."""
    df = conn.execute(
        "SELECT date, open, high, low, close FROM ohlcv_daily "
        "WHERE ticker = ? ORDER BY date",
        [ticker],
    ).df()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df


# ── DB 저장 ───────────────────────────────────────────────────

def save_results(
    ticker: str,
    run_id: str,
    run_date: date,
    window_results: list[dict],
    conn,
) -> None:
    """window별 결과를 sma_backtest_v2에 저장."""
    rows = []
    for w in window_results:
        for strategy in ("sma_breakout", "pullback", "combined"):
            m = w[strategy]["metrics"]
            p = w[strategy].get("params") or {}
            rows.append({
                "run_id":             run_id,
                "ticker":             ticker,
                "run_date":           run_date,
                "strategy":           strategy,
                "is_walkforward":     w["is_walkforward"],
                "window_start":       str(w["test_start"].date()),
                "window_end":         str(w["test_end"].date()),
                "sma_period":         p.get("sma_period"),
                "pullback_sma_delta": p.get("pullback_sma_delta"),
                "stop_loss_pct":      p.get("stop_loss_pct"),
                "calmar":             m["calmar"],
                "cagr":               m["cagr"],
                "mdd":                m["mdd"],
                "win_rate":           m.get("win_rate", 0.0),
                "profit_factor":      m.get("profit_factor", 0.0),
                "ev":                 m.get("ev", 0.0),
                "total_trades":       m["total_trades"],
                "vs_buyhold":         m["vs_buyhold"],
            })

    if not rows:
        return

    df = pd.DataFrame(rows)
    conn.register("_v2_rows", df)
    conn.execute("""
        INSERT OR REPLACE INTO sma_backtest_v2
            (run_id, ticker, run_date, strategy, is_walkforward,
             window_start, window_end, sma_period, pullback_sma_delta, stop_loss_pct,
             calmar, cagr, mdd, win_rate, profit_factor, ev, total_trades, vs_buyhold)
        SELECT
            run_id, ticker, CAST(run_date AS DATE), strategy, is_walkforward,
            CAST(window_start AS DATE), CAST(window_end AS DATE),
            sma_period, pullback_sma_delta, stop_loss_pct,
            calmar, cagr, mdd, win_rate, profit_factor, ev, total_trades, vs_buyhold
        FROM _v2_rows
    """)


# ── 텔레그램 전송 ─────────────────────────────────────────────

def send_telegram(msg: str) -> None:
    try:
        import os
        from dotenv import load_dotenv
        import requests
        load_dotenv()
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_BACKTEST_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            print("[텔레그램 미설정 — 출력만]")
            print(msg)
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=10)
    except Exception as e:
        print(f"[텔레그램 전송 실패: {e}]")
        print(msg)


# ── 메인 ─────────────────────────────────────────────────────

def main(dry_run: bool = False, tickers: list[str] | None = None) -> None:
    run_date = date.today()
    run_id = f"{run_date.isoformat()}_{hashlib.md5(str(run_date).encode()).hexdigest()[:6]}"

    universe = [u for u in UNIVERSE if not tickers or u["ticker"] in tickers]
    if dry_run:
        universe = universe[:1]
        print(f"[dry-run] {universe[0]['name']} 1종목만 실행")

    if not dry_run:
        from data.db import init_db
        init_db()

    conn = get_conn() if not dry_run else None

    try:

        summary_rows = []

        for u in universe:
            ticker = u["ticker"]
            name   = u["name"]
            print(f"\n[{ticker}] {name} 처리 중...", flush=True)

            ohlcv = load_ohlcv(ticker, conn) if conn else _load_ohlcv_parquet(ticker)
            if ohlcv is None or len(ohlcv) < 252:
                print(f"  → 데이터 부족, 스킵")
                continue

            window_results = run_walkforward(
                ohlcv,
                breakout_grid=BREAKOUT_GRID,
                pullback_grid=PULLBACK_GRID,
                fixed=FIXED,
            )

            if not window_results:
                print(f"  → WF 결과 없음, 스킵")
                continue

            agg = aggregate_wf_results(window_results)
            if conn:
                save_results(ticker, run_id, run_date, window_results, conn)

            # 대표 파라미터 (첫 번째 윈도우 기준)
            best_bo = window_results[0]["sma_breakout"]["params"]
            best_pb = window_results[0]["pullback"]["params"]

            msg = format_report(ticker, name, agg, best_bo, best_pb)
            print(msg)
            send_telegram(msg)

            summary_rows.append({
                "ticker":           ticker,
                "name":             name,
                "combined_calmar":  agg["combined"]["calmar"],
                "combined_vs_bh":   agg["combined"]["vs_buyhold"],
            })

        # 전체 요약
        if summary_rows:
            summary = format_summary(summary_rows)
            print("\n" + summary)
            send_telegram(summary)

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="1종목만 테스트")
    parser.add_argument("--tickers", nargs="+", help="특정 종목만 실행")
    args = parser.parse_args()
    main(dry_run=args.dry_run, tickers=args.tickers)
