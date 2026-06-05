#!/usr/bin/env python3
"""P1~P3 풀백 타이밍 엔진 백테스트 실행.

사용:
  python scripts/run_pullback_backtest.py --phase p1
  python scripts/run_pullback_backtest.py --phase p2
  python scripts/run_pullback_backtest.py --phase p3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.pullback_runner import run_single, sweep_params, split_periods
from data.db import get_conn

INDEX_NAMES = {"1001": "KOSPI", "2001": "KOSDAQ"}

# 파라미터 그리드 — robustness 스윕용 (P2)
SWEEP_GRID = [
    {"trend_period": tp, "entry_period": ep, "band_pct": bp}
    for tp in range(150, 260, 10)   # 150~250, 11값
    for ep in range(30, 80, 10)     # 30~70, 5값
    for bp in [3.0, 5.0, 7.0]       # 3값
]  # 11 × 5 × 3 = 165 조합


def load_index(ticker: str, conn) -> pd.DataFrame | None:
    df = conn.execute(
        "SELECT date, open, high, low, close FROM market_index "
        "WHERE ticker = ? ORDER BY date",
        [ticker],
    ).df()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def print_metrics(label: str, m: dict) -> None:
    print(f"\n{'='*55}")
    print(f" {label}")
    print(f"{'='*55}")
    print(f"  Calmar : {m['calmar']:>8.3f}   (목표: BH 대비 우위)")
    print(f"  CAGR   : {m['cagr']:>7.2f}%")
    print(f"  MaxDD  : {m['mdd']:>7.2f}%")
    print(f"  WinRate: {m['win_rate']:>7.1f}%  (참고용)")
    print(f"  Trades : {m['total_trades']:>7d}")
    print(f"  vsBH   : {m['vs_buyhold']:>7.2f}%  (참고용)")


def phase_p1(conn) -> None:
    """단일 파라미터, 기본값(200/50/5%) + 베이스라인(200d 단순교차) 비교."""
    for ticker, name in INDEX_NAMES.items():
        ohlcv = load_index(ticker, conn)
        if ohlcv is None:
            print(f"[{ticker}] 데이터 없음 — P0 먼저 실행하세요")
            continue

        print(f"\n{'#'*55}")
        print(f"# {name} ({ticker}) — {ohlcv.index[0].date()} ~ {ohlcv.index[-1].date()}")
        print(f"# 전체 {len(ohlcv)}일")

        bh_cagr = ((ohlcv["close"].iloc[-1] / ohlcv["close"].iloc[0]) **
                   (252 / len(ohlcv)) - 1) * 100
        print(f"\n  Buy&Hold CAGR: {bh_cagr:.2f}%")

        baseline = run_single(ohlcv, trend_period=200, entry_period=200,
                              band_pct=0.5, slope_lookback=1)
        print_metrics("베이스라인 (200d 교차)", baseline)

        pullback = run_single(ohlcv, trend_period=200, entry_period=50, band_pct=5.0)
        print_metrics("눌림 전략 (200/50/5%)", pullback)

        improvement = pullback["calmar"] - baseline["calmar"]
        print(f"\n  눌림 Calmar 개선: {improvement:+.3f}")


def phase_p2(conn) -> None:
    """파라미터 대역 스윕 + 5년 구간 분할."""
    for ticker, name in INDEX_NAMES.items():
        ohlcv = load_index(ticker, conn)
        if ohlcv is None:
            continue

        print(f"\n{'#'*55}")
        print(f"# {name} ROBUSTNESS 스윕 ({len(SWEEP_GRID)}조합)")

        results = sweep_params(ohlcv, SWEEP_GRID)
        calmars = [r["calmar"] for r in results]
        pos_ratio = sum(1 for c in calmars if c > 0) / len(calmars)
        print(f"\n  전체기간: 양수Calmar비율={pos_ratio:.1%}  "
              f"중앙값={sorted(calmars)[len(calmars)//2]:.3f}  "
              f"상위10%={sorted(calmars)[int(len(calmars)*0.9)]:.3f}")
        best = results[0]
        print(f"  최고: calmar={best['calmar']:.3f}  params={best['params']}")

        print(f"\n  [5년 구간별 기본 파라미터(200/50/5%) 성과]")
        chunks = split_periods(ohlcv, years=5)
        for chunk in chunks:
            y_start = chunk.index[0].year
            y_end   = chunk.index[-1].year
            m = run_single(chunk)
            flag = "✓" if m["calmar"] > 0 else "✗"
            print(f"  {flag} {y_start}-{y_end}: calmar={m['calmar']:.3f}  "
                  f"cagr={m['cagr']:.1f}%  mdd={m['mdd']:.1f}%")


def phase_p3(conn) -> None:
    """고정 규칙(200/50/5%)을 개별주 23종목에 무튜닝 전이."""
    from backtest.sma_config import UNIVERSE
    print(f"\n{'#'*55}")
    print(f"# P3 무튜닝 전이 시험 (23종목, 고정 200/50/5%)")
    print(f"{'#'*55}")

    rows = []
    for u in UNIVERSE:
        ticker = u["ticker"]
        name   = u["name"]
        df = conn.execute(
            "SELECT date, open, high, low, close FROM ohlcv_daily "
            "WHERE ticker = ? ORDER BY date", [ticker],
        ).df()
        if df.empty or len(df) < 300:
            print(f"  [{ticker}] {name}: 데이터 부족, 스킵")
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        m = run_single(df)
        rows.append({"ticker": ticker, "name": name, **m})

    rows.sort(key=lambda x: x["calmar"], reverse=True)
    print(f"\n  {'종목':<18} {'Calmar':>7} {'CAGR':>7} {'MDD':>7} {'거래':>5}")
    print("  " + "-"*48)
    for r in rows:
        flag = "✓" if r["calmar"] >= 0.2 else "✗"
        print(f"  {flag} {r['name']:<16} {r['calmar']:>7.3f} "
              f"{r['cagr']:>6.1f}% {r['mdd']:>6.1f}% {r['total_trades']:>5}")

    survive = sum(1 for r in rows if r["calmar"] >= 0.2)
    print(f"\n  생존율(Calmar>=0.2): {survive}/{len(rows)} = {survive/len(rows):.1%}")
    print(f"  판정: {'PASS — 무튜닝 전이 성공' if survive/len(rows) >= 0.6 else 'FAIL — 추가 분석 필요'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["p1", "p2", "p3", "all"], default="p1")
    args = parser.parse_args()

    conn = get_conn(read_only=True)
    try:
        if args.phase in ("p1", "all"):
            phase_p1(conn)
        if args.phase in ("p2", "all"):
            phase_p2(conn)
        if args.phase in ("p3", "all"):
            phase_p3(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
