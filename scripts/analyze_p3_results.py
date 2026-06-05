#!/usr/bin/env python3
"""P3 종목별 특성 분석 — GOOD/FAIL 그룹 비교.

regime_on_ratio, bh_cagr, volatility, trade_count 등으로
추세주(GOOD) vs 박스권주(FAIL) 패턴을 찾는다.

사용:
  python scripts/analyze_p3_results.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.pullback_signal import regime_gate, compute_signals
from backtest.pullback_runner import run_single
from backtest.sma_config import UNIVERSE
from data.db import get_conn

CALMAR_THRESHOLD = 0.2
TREND_PERIOD = 200
ENTRY_PERIOD = 50
BAND_PCT = 5.0
SLOPE_LOOKBACK = 20


def compute_stock_features(df: pd.DataFrame) -> dict:
    close = df["close"]
    n = len(df)

    gate = regime_gate(close, TREND_PERIOD, SLOPE_LOOKBACK)
    regime_on_ratio = gate.sum() / n

    daily_ret = close.pct_change().dropna()
    volatility = daily_ret.std() * (252 ** 0.5)  # 연간화

    bh_cagr = ((close.iloc[-1] / close.iloc[0]) ** (252 / n) - 1) * 100

    return {
        "regime_on_ratio": regime_on_ratio,
        "volatility_ann":  volatility * 100,
        "bh_cagr":         bh_cagr,
        "years":           n / 252,
    }


def main() -> None:
    conn = get_conn(read_only=True)
    rows = []
    try:
        for u in UNIVERSE:
            ticker = u["ticker"]
            name   = u["name"]
            df = conn.execute(
                "SELECT date, open, high, low, close FROM ohlcv_daily "
                "WHERE ticker = ? ORDER BY date", [ticker],
            ).df()
            if df.empty or len(df) < 300:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

            m = run_single(df)
            feat = compute_stock_features(df)
            rows.append({
                "ticker":           ticker,
                "name":             name,
                "calmar":           m["calmar"],
                "good":             m["calmar"] >= CALMAR_THRESHOLD,
                "trades":           m["total_trades"],
                "mdd":              m["mdd"],
                **feat,
            })
    finally:
        conn.close()

    rows.sort(key=lambda x: x["calmar"], reverse=True)

    print(f"\n{'#'*65}")
    print(f"# P3 종목별 특성 분석 ({len(rows)}종목)")
    print(f"{'#'*65}")
    header = f"  {'종목':<18} {'판정':>4} {'Calmar':>7} {'추세ON%':>7} {'BH_CAGR':>8} {'Vol%':>6} {'거래':>5} {'MDD':>7}"
    print(header)
    print("  " + "-" * 68)
    for r in rows:
        flag = "GOOD" if r["good"] else "FAIL"
        print(f"  {r['name']:<18} {flag:>4} {r['calmar']:>7.3f} "
              f"{r['regime_on_ratio']:>6.1%} {r['bh_cagr']:>7.1f}% "
              f"{r['volatility_ann']:>5.1f}% {r['trades']:>5} {r['mdd']:>6.1f}%")

    good = [r for r in rows if r["good"]]
    fail = [r for r in rows if not r["good"]]

    def avg(lst, key):
        return sum(x[key] for x in lst) / len(lst) if lst else 0.0

    print(f"\n{'='*65}")
    print(f"  GOOD({len(good)}종목) vs FAIL({len(fail)}종목) 평균 비교")
    print(f"{'='*65}")
    metrics = [
        ("추세ON 비율",  "regime_on_ratio", "{:.1%}"),
        ("BH CAGR",      "bh_cagr",         "{:.1f}%"),
        ("연간 변동성",  "volatility_ann",   "{:.1f}%"),
        ("거래수",       "trades",           "{:.1f}"),
        ("MDD",          "mdd",              "{:.1f}%"),
        ("Calmar",       "calmar",           "{:.3f}"),
    ]
    for label, key, fmt in metrics:
        g = avg(good, key)
        f = avg(fail, key)
        print(f"  {label:<12}  GOOD={fmt.format(g):>8}  FAIL={fmt.format(f):>8}")

    print(f"\n[해석 힌트]")
    print(f"  추세ON 비율이 높을수록 → 장기 상승 추세 지속 → GOOD 경향")
    print(f"  BH CAGR 높을수록 → 구조적 상승 종목 → GOOD 경향")
    print(f"  거래수 많을수록 → regime 잦은 ON/OFF → 박스권 → FAIL 경향")


if __name__ == "__main__":
    main()
