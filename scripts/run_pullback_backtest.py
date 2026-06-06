#!/usr/bin/env python3
"""P1~P5 풀백 타이밍 엔진 백테스트 실행.

사용:
  python scripts/run_pullback_backtest.py --phase p1
  python scripts/run_pullback_backtest.py --phase p3
  python scripts/run_pullback_backtest.py --phase p5
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

# P5 rolling window 파라미터
ROLLING_LOOKBACK = 500    # trailing 영업일 ≈ 2년
ROLLING_THRESHOLD = 8     # 이 미만이면 추세 구간 신호로 허용


def load_universe(conn, min_days: int = 5000) -> list[dict]:
    """ohlcv_daily에서 min_days 이상 데이터가 있는 종목 로드."""
    rows = conn.execute("""
        SELECT o.ticker, COALESCE(t.name, o.ticker) AS name, t.market, t.market_cap
        FROM ohlcv_daily o
        LEFT JOIN ticker_master t ON o.ticker = t.ticker
        GROUP BY o.ticker, t.name, t.market, t.market_cap
        HAVING COUNT(DISTINCT o.date) >= ?
        ORDER BY COALESCE(t.market_cap, 0) DESC
    """, [min_days]).fetchall()
    return [
        {"ticker": r[0], "name": r[1], "market": r[2] or "UNKNOWN", "market_cap": r[3] or 0}
        for r in rows
    ]


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
    """고정 규칙(200/50/5%)을 5000일+ 전체 종목에 무튜닝 전이."""
    universe = load_universe(conn, min_days=5000)
    kospi_cnt = sum(1 for u in universe if u["market"] == "KOSPI")
    kosdaq_cnt = sum(1 for u in universe if u["market"] == "KOSDAQ")

    print(f"\n{'#'*55}")
    print(f"# P3 무튜닝 전이 시험 ({len(universe)}종목, 고정 200/50/5%)")
    print(f"# KOSPI {kospi_cnt}종목, KOSDAQ {kosdaq_cnt}종목")
    print(f"{'#'*55}")

    rows = []
    for u in universe:
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
        rows.append({"ticker": ticker, "name": name, "market": u["market"], **m})

    rows.sort(key=lambda x: x["calmar"], reverse=True)
    print(f"\n  {'종목':<18} {'시장':<7} {'Calmar':>7} {'CAGR':>7} {'MDD':>7} {'거래':>5}")
    print("  " + "-"*55)
    for r in rows:
        flag = "✓" if r["calmar"] >= 0.2 else "✗"
        print(f"  {flag} {r['name']:<16} {r['market']:<7} {r['calmar']:>7.3f} "
              f"{r['cagr']:>6.1f}% {r['mdd']:>6.1f}% {r['total_trades']:>5}")

    survive = sum(1 for r in rows if r["calmar"] >= 0.2)
    print(f"\n  생존율(Calmar>=0.2): {survive}/{len(rows)} = {survive/len(rows):.1%}")

    kospi_survive = sum(1 for r in rows if r["calmar"] >= 0.2 and r["market"] == "KOSPI")
    kospi_n = sum(1 for r in rows if r["market"] == "KOSPI")
    print(f"  KOSPI 생존율: {kospi_survive}/{kospi_n} = {kospi_survive/kospi_n:.1%}" if kospi_n else "")

    verdict = "PASS — 무튜닝 전이 성공" if survive / len(rows) >= 0.6 else "FAIL — 추가 분석 필요"
    print(f"  판정: {verdict}")


def phase_p4(conn) -> None:
    """Layer1 regime gate 효과 검증 — KOSPI gate ON일 때만 개별주 신호 허용."""
    universe = load_universe(conn, min_days=5000)
    kospi = load_index("1001", conn)
    if kospi is None:
        print("KOSPI 데이터 없음 — P0 먼저 실행하세요")
        return

    print(f"\n{'#'*55}")
    print(f"# P4 Layer1 regime gate (KOSPI 200d) + 무튜닝 전이")
    print(f"# P3 vs P4 생존율(Calmar>=0.2) 비교 — {len(universe)}종목")
    print(f"{'#'*55}")

    rows = []
    for u in universe:
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
        m_no_gate   = run_single(df)
        m_with_gate = run_single(df, market_ohlcv=kospi)
        rows.append({
            "ticker": ticker, "name": name, "market": u["market"],
            "calmar_p3": m_no_gate["calmar"],
            "calmar_p4": m_with_gate["calmar"],
            "cagr_p4":   m_with_gate["cagr"],
            "mdd_p4":    m_with_gate["mdd"],
            "trades_p4": m_with_gate["total_trades"],
        })

    rows.sort(key=lambda x: x["calmar_p4"], reverse=True)
    print(f"\n  {'종목':<18} {'시장':<7} {'P3':>7} {'P4':>7} {'변화':>7} {'CAGR':>7} {'거래':>5}")
    print("  " + "-"*65)
    for r in rows:
        delta = r["calmar_p4"] - r["calmar_p3"]
        flag  = "✓" if r["calmar_p4"] >= 0.2 else "✗"
        arrow = "↑" if delta > 0.02 else ("↓" if delta < -0.02 else "→")
        print(f"  {flag} {r['name']:<16} {r['market']:<7} {r['calmar_p3']:>7.3f} "
              f"{r['calmar_p4']:>7.3f} {arrow}{delta:>+6.3f} {r['cagr_p4']:>6.1f}%  {r['trades_p4']:>5}")

    survive_p3 = sum(1 for r in rows if r["calmar_p3"] >= 0.2)
    survive_p4 = sum(1 for r in rows if r["calmar_p4"] >= 0.2)
    n = len(rows)
    print(f"\n  P3 생존율: {survive_p3}/{n} = {survive_p3/n:.1%}")
    print(f"  P4 생존율: {survive_p4}/{n} = {survive_p4/n:.1%}  "
          f"({'개선' if survive_p4 > survive_p3 else '악화' if survive_p4 < survive_p3 else '동일'})")


def phase_p5(conn) -> None:
    """Layer2 rolling window 진입 빈도 필터 (lookahead-free 재설계).

    각 신호 발생 시점 t에서:
      - 과거 ROLLING_LOOKBACK 영업일 내 entry 횟수 < ROLLING_THRESHOLD 이면 허용
      - 횟수 >= ROLLING_THRESHOLD 이면 횡보 구간으로 판단, 해당 신호 제외

    → 미래 거래 결과를 참조하지 않으므로 in-sample 편향 없음.
    """
    from backtest.pullback_signal import compute_signals

    universe = load_universe(conn, min_days=5000)

    print(f"\n{'#'*55}")
    print(f"# P5 rolling window 진입 빈도 필터 (lookahead-free)")
    print(f"# trailing {ROLLING_LOOKBACK}일 내 entry 횟수 < {ROLLING_THRESHOLD}인 신호만 허용")
    print(f"# 대상: {len(universe)}종목")
    print(f"{'#'*55}")

    rows = []
    for u in universe:
        ticker = u["ticker"]
        name   = u["name"]
        df = conn.execute(
            "SELECT date, open, high, low, close FROM ohlcv_daily "
            "WHERE ticker = ? ORDER BY date", [ticker],
        ).df()
        if df.empty or len(df) < ROLLING_LOOKBACK + 200:
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")

        sig = compute_signals(df, 200, 50, 5.0, 20)

        # 각 시점 t에서 (t-ROLLING_LOOKBACK ~ t-1) 구간의 entry 횟수
        rolling_cnt = (
            sig["entry"].astype(int)
            .shift(1).fillna(0)
            .rolling(ROLLING_LOOKBACK, min_periods=1)
            .sum()
        )
        entry_mask = sig["entry"] & (rolling_cnt < ROLLING_THRESHOLD)

        m_base = run_single(df)
        m_filt = run_single(df, entry_mask=entry_mask)

        rows.append({
            "ticker":       ticker,
            "name":         name,
            "market":       u["market"],
            "trades_base":  m_base["total_trades"],
            "trades_filt":  m_filt["total_trades"],
            "calmar_base":  m_base["calmar"],
            "calmar_filt":  m_filt["calmar"],
            "cagr_filt":    m_filt["cagr"],
            "mdd_filt":     m_filt["mdd"],
        })

    rows.sort(key=lambda x: x["calmar_filt"], reverse=True)

    print(f"\n  {'종목':<18} {'시장':<7} {'기본거래':>8} {'필터거래':>8} "
          f"{'Calmar전':>9} {'Calmar후':>9} {'변화':>7}")
    print("  " + "-"*75)
    for r in rows:
        delta = r["calmar_filt"] - r["calmar_base"]
        arrow = "↑" if delta > 0.01 else ("↓" if delta < -0.01 else "→")
        flag  = "✓" if r["calmar_filt"] >= 0.2 else "✗"
        print(f"  {flag} {r['name']:<16} {r['market']:<7} {r['trades_base']:>8} "
              f"{r['trades_filt']:>8} {r['calmar_base']:>9.3f} {r['calmar_filt']:>9.3f} "
              f"{arrow}{delta:>+6.3f}")

    n = len(rows)
    base_survive = sum(1 for r in rows if r["calmar_base"] >= 0.2)
    filt_survive = sum(1 for r in rows if r["calmar_filt"] >= 0.2)
    improve      = sum(1 for r in rows if r["calmar_filt"] > r["calmar_base"])

    print(f"\n  P3 기준 (필터없음): {base_survive}/{n} = {base_survive/n:.1%}")
    print(f"  P5 rolling window:  {filt_survive}/{n} = {filt_survive/n:.1%}")
    print(f"  Calmar 개선 종목:   {improve}/{n} = {improve/n:.1%}")

    kospi_rows = [r for r in rows if r["market"] == "KOSPI"]
    kosdaq_rows = [r for r in rows if r["market"] == "KOSDAQ"]
    if kospi_rows:
        ks = sum(1 for r in kospi_rows if r["calmar_filt"] >= 0.2)
        print(f"  KOSPI 생존율:  {ks}/{len(kospi_rows)} = {ks/len(kospi_rows):.1%}")
    if kosdaq_rows:
        kq = sum(1 for r in kosdaq_rows if r["calmar_filt"] >= 0.2)
        print(f"  KOSDAQ 생존율: {kq}/{len(kosdaq_rows)} = {kq/len(kosdaq_rows):.1%}")

    verdict = "PASS — 개선됨" if filt_survive > base_survive else "FAIL — 개선 없음"
    print(f"  판정: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["p1", "p2", "p3", "p4", "p5", "all"], default="p1")
    args = parser.parse_args()

    conn = get_conn(read_only=True)
    try:
        if args.phase in ("p1", "all"):
            phase_p1(conn)
        if args.phase in ("p2", "all"):
            phase_p2(conn)
        if args.phase in ("p3", "all"):
            phase_p3(conn)
        if args.phase in ("p4", "all"):
            phase_p4(conn)
        if args.phase in ("p5", "all"):
            phase_p5(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
