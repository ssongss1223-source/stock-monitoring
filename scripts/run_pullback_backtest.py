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
ROLLING_THRESHOLD = 20    # 이 미만이면 추세 구간 신호로 허용


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
    """Layer2 rolling window 진입 빈도 필터 — threshold 그리드서치.

    종목을 1번만 순회하면서 threshold 10~60을 내부에서 동시 처리.
    DB 조회·신호 계산: 208번 (threshold당 반복 없음).
    """
    from backtest.pullback_signal import compute_signals

    universe = load_universe(conn, min_days=5000)
    thresholds = list(range(10, 65, 5))  # 10, 15, 20, ..., 60

    print(f"\n{'#'*60}")
    print(f"# P5 rolling window threshold 그리드서치")
    print(f"# trailing {ROLLING_LOOKBACK}일 내 entry 횟수 기준, threshold {thresholds[0]}~{thresholds[-1]} 스윕")
    print(f"# 대상: {len(universe)}종목")
    print(f"{'#'*60}")

    # threshold별 집계: {thr: {"base": n, "filt": n, "total": n}}
    counts: dict[int, dict] = {t: {"base": 0, "filt": 0, "total": 0} for t in thresholds}

    for u in universe:
        ticker = u["ticker"]
        df = conn.execute(
            "SELECT date, open, high, low, close FROM ohlcv_daily "
            "WHERE ticker = ? ORDER BY date", [ticker],
        ).df()
        if df.empty or len(df) < ROLLING_LOOKBACK + 200:
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")

        sig = compute_signals(df, 200, 50, 5.0, 20)
        rolling_cnt = (
            sig["entry"].astype(int)
            .shift(1).fillna(0)
            .rolling(ROLLING_LOOKBACK, min_periods=1)
            .sum()
        )

        m_base = run_single(df)
        calmar_base = m_base["calmar"]

        for thr in thresholds:
            entry_mask = sig["entry"] & (rolling_cnt < thr)
            m_filt = run_single(df, entry_mask=entry_mask)
            counts[thr]["total"] += 1
            if calmar_base >= 0.2:
                counts[thr]["base"] += 1
            if m_filt["calmar"] >= 0.2:
                counts[thr]["filt"] += 1

    print(f"\n  {'threshold':>10} {'P3생존':>10} {'P5생존':>10} {'생존율':>8} {'개선':>6}")
    print("  " + "-"*50)

    best = None
    for thr in thresholds:
        c = counts[thr]
        n = c["total"]
        if n == 0:
            continue
        rate = c["filt"] / n
        delta = c["filt"] - c["base"]
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        print(f"  {thr:>10}  {c['base']:>5}/{n}  {c['filt']:>5}/{n}  "
              f"{rate:>7.1%}  {arrow}{delta:>+4}")
        if best is None or c["filt"] > best["filt"]:
            best = {**c, "threshold": thr}

    n = best["total"]
    print(f"\n  [최적] threshold={best['threshold']}  "
          f"P5={best['filt']}/{n}={best['filt']/n:.1%}  "
          f"(P3={best['base']}/{n}={best['base']/n:.1%})")
    verdict = "PASS — 개선됨" if best["filt"] > best["base"] else "FAIL — 개선 없음"
    print(f"  판정: {verdict}")


def phase_p5_grid2d(conn) -> None:
    """LOOKBACK × threshold 2D 그리드서치 — 종목 1회 순회로 전체 조합 처리.

    LOOKBACK: 최근 N일간 raw entry 신호 수 카운트 기간
    threshold: 카운트 < threshold 일 때만 진입 허용
    평가: TP율(P3 생존 유지) - FP율(P3 실패 중 오구제) 기준
    """
    from backtest.pullback_signal import compute_signals

    universe = load_universe(conn, min_days=5000)
    LOOKBACKS  = [50, 100, 150, 200, 300, 500]
    THRESHOLDS = [2, 5, 8, 10, 15, 20, 25, 30, 40, 50, 60]

    print(f"\n{'#'*65}")
    print(f"# P5 2D 그리드서치: LOOKBACK × threshold")
    print(f"# LOOKBACK: {LOOKBACKS}")
    print(f"# threshold: {THRESHOLDS}")
    print(f"# 대상: {len(universe)}종목  조합: {len(LOOKBACKS)*len(THRESHOLDS)}개")
    print(f"{'#'*65}")

    results = {
        lb: {thr: {"base": 0, "tp": 0, "fn": 0, "fp": 0, "total": 0}
             for thr in THRESHOLDS}
        for lb in LOOKBACKS
    }

    for i, u in enumerate(universe, 1):
        ticker = u["ticker"]
        df = conn.execute(
            "SELECT date, open, high, low, close FROM ohlcv_daily "
            "WHERE ticker = ? ORDER BY date", [ticker],
        ).df()
        if df.empty or len(df) < 700:
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")

        sig = compute_signals(df, 200, 50, 5.0, 20)
        calmar_base = run_single(df)["calmar"]
        p3_good = calmar_base >= 0.5

        for lb in LOOKBACKS:
            rolling_cnt = (
                sig["entry"].astype(int)
                .shift(1).fillna(0)
                .rolling(lb, min_periods=1)
                .sum()
            )
            for thr in THRESHOLDS:
                entry_mask = sig["entry"] & (rolling_cnt < thr)
                calmar_filt = run_single(df, entry_mask=entry_mask)["calmar"]
                p5_good = calmar_filt >= 0.5

                r = results[lb][thr]
                r["total"] += 1
                if p3_good:
                    r["base"] += 1
                    if p5_good:
                        r["tp"] += 1
                    else:
                        r["fn"] += 1
                else:
                    if p5_good:
                        r["fp"] += 1

        if i % 20 == 0:
            print(f"  [{i}/{len(universe)}] 진행 중...")

    # ── TP율 요약 테이블 ─────────────────────────────────────────────
    n_p3 = results[LOOKBACKS[0]][THRESHOLDS[0]]["base"]
    print(f"\nP3 baseline: {n_p3}개 생존 ({n_p3/results[LOOKBACKS[0]][THRESHOLDS[0]]['total']:.1%})\n")
    print("  [TP율 — P3 생존 중 rolling도 생존 비율]")
    print(f"  {'LB':>5}" + "".join(f" {t:>5}" for t in THRESHOLDS))
    print("  " + "-" * (6 + 6 * len(THRESHOLDS)))
    for lb in LOOKBACKS:
        row = f"  {lb:>5}"
        for thr in THRESHOLDS:
            r = results[lb][thr]
            rate = r["tp"] / r["base"] if r["base"] else 0
            row += f" {rate:>4.0%} "
        print(row)

    # ── 상위 10 조합 (TP율 - FP율 기준) ─────────────────────────────
    combos = []
    for lb in LOOKBACKS:
        for thr in THRESHOLDS:
            r = results[lb][thr]
            if r["base"] == 0:
                continue
            n_not_p3 = r["total"] - r["base"]
            tp_rate = r["tp"] / r["base"]
            fp_rate = r["fp"] / n_not_p3 if n_not_p3 else 0
            score = tp_rate - fp_rate
            combos.append((score, lb, thr, r["tp"], r["fn"], r["fp"], tp_rate, fp_rate))
    combos.sort(reverse=True)

    print(f"\n  [상위 10 조합]")
    print(f"  {'LB':>5} {'THR':>5} {'TP':>4} {'FN':>4} {'FP':>4} {'TP율':>7} {'FP율':>7} {'score':>7}")
    print("  " + "-" * 52)
    for row in combos[:10]:
        score, lb, thr, tp, fn, fp, tp_rate, fp_rate = row
        print(f"  {lb:>5} {thr:>5} {tp:>4} {fn:>4} {fp:>4} {tp_rate:>7.1%} {fp_rate:>7.1%} {score:>7.3f}")

    score, lb, thr, tp, fn, fp, tp_rate, fp_rate = combos[0]
    print(f"\n  [최적] LOOKBACK={lb}, threshold={thr}")
    print(f"         TP={tp}  FN={fn}  FP={fp}  TP율={tp_rate:.1%}  FP율={fp_rate:.1%}")
    if tp_rate >= 0.7 and fp_rate <= 0.1:
        verdict = "PASS — rolling window 유효"
    elif tp_rate >= 0.5:
        verdict = "부분 유효 — 추가 분석 필요"
    else:
        verdict = "FAIL — Option B 전환 검토"
    print(f"  판정: {verdict}")


def phase_p5_diag(conn) -> None:
    """rolling window 효과 분해: threshold=55, LOOKBACK 100/250/500 비교.

    2x2 분해: P3 생존 여부 × rolling window(threshold=55) 생존 여부
    - TP: P3 good & rolling good  (rolling이 유지)
    - FN: P3 good & rolling bad   (rolling이 탈락시킴)
    - FP: P3 bad  & rolling good  (rolling이 구제)
    - TN: P3 bad  & rolling bad
    """
    from backtest.pullback_signal import compute_signals

    universe = load_universe(conn, min_days=5000)
    THR = 55
    LOOKBACKS = [100, 250, 500]

    print(f"\n{'#'*60}")
    print(f"# P5 진단 — threshold={THR}, LOOKBACK 100/250/500 비교")
    print(f"# 대상: {len(universe)}종목")
    print(f"{'#'*60}")

    for lb in LOOKBACKS:
        tp: list[tuple] = []  # (name, calmar_base, calmar_filt)
        fn: list[tuple] = []
        fp: list[tuple] = []
        tn: list[tuple] = []

        for u in universe:
            ticker = u["ticker"]
            name = u["name"]
            df = conn.execute(
                "SELECT date, open, high, low, close FROM ohlcv_daily "
                "WHERE ticker = ? ORDER BY date", [ticker],
            ).df()
            if df.empty or len(df) < lb + 200:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

            sig = compute_signals(df, 200, 50, 5.0, 20)
            rolling_cnt = (
                sig["entry"].astype(int)
                .shift(1).fillna(0)
                .rolling(lb, min_periods=1)
                .sum()
            )

            m_base = run_single(df)
            calmar_base = m_base["calmar"]
            entry_mask = sig["entry"] & (rolling_cnt < THR)
            m_filt = run_single(df, entry_mask=entry_mask)
            calmar_filt = m_filt["calmar"]

            rec = (name, calmar_base, calmar_filt)
            if calmar_base >= 0.2 and calmar_filt >= 0.2:
                tp.append(rec)
            elif calmar_base >= 0.2 and calmar_filt < 0.2:
                fn.append(rec)
            elif calmar_base < 0.2 and calmar_filt >= 0.2:
                fp.append(rec)
            else:
                tn.append(rec)

        n_p3 = len(tp) + len(fn)
        total = len(tp) + len(fn) + len(fp) + len(tn)

        print(f"\n=== LOOKBACK={lb}일 ===")
        print(f"  P3 생존 {n_p3}개 중:")
        avg_tp = sum(x[2] for x in tp) / len(tp) if tp else 0.0
        avg_fn = sum(x[2] for x in fn) / len(fn) if fn else 0.0
        print(f"    TP (rolling도 생존): {len(tp)}개  Calmar avg={avg_tp:.3f}")
        print(f"    FN (rolling이 탈락): {len(fn)}개  Calmar avg={avg_fn:.3f}")
        if fn:
            top_fn = sorted(fn, key=lambda x: x[1], reverse=True)[:8]
            print(f"      탈락: {', '.join(f'{x[0]}({x[1]:.2f})' for x in top_fn)}")
        print(f"  P3 실패 {total - n_p3}개 중:")
        avg_fp = sum(x[2] for x in fp) / len(fp) if fp else 0.0
        print(f"    FP (rolling이 구제): {len(fp)}개  Calmar avg={avg_fp:.3f}")
        if fp:
            top_fp = sorted(fp, key=lambda x: x[2], reverse=True)[:5]
            print(f"      구제: {', '.join(f'{x[0]}({x[2]:.2f})' for x in top_fp)}")
        if n_p3:
            print(f"  P3 생존 유지율: {len(tp)}/{n_p3} = {len(tp)/n_p3:.1%}")
        base_avg = sum(x[1] for x in tp + fn) / n_p3 if n_p3 else 0.0
        filt_n = len(tp) + len(fp)
        filt_avg = sum(x[2] for x in tp + fp) / filt_n if filt_n else 0.0
        print(f"  Calmar avg: P3 생존({base_avg:.3f}) → rolling 생존({filt_avg:.3f})")


def phase_p6_wf(conn) -> None:
    """Option B: Walk-Forward Expanding Window calibration split.

    최소 7년 calibration → 이후 매년 test.
    calib 기간에서 Calmar >= 0.5인 종목만 test 연도에 백테스트.
    선별 종목 vs 비선별 종목 test 성과를 비교해 calibration 예측력 검증.
    """
    import datetime

    universe = load_universe(conn, min_days=5000)
    MIN_CALIB_YEARS = 7
    CALMAR_THRESH = 0.5

    # 전체 데이터 날짜 범위 파악
    date_range = conn.execute(
        "SELECT MIN(date), MAX(date) FROM ohlcv_daily"
    ).fetchone()
    data_start = pd.Timestamp(date_range[0])
    data_end   = pd.Timestamp(date_range[1])

    first_test_year = data_start.year + MIN_CALIB_YEARS
    last_test_year  = data_end.year - 1  # 마지막 연도는 test 불완전할 수 있음

    print(f"\n{'#'*65}")
    print(f"# P6 Walk-Forward Expanding Window")
    print(f"# 데이터: {data_start.date()} ~ {data_end.date()}")
    print(f"# 최소 calibration: {MIN_CALIB_YEARS}년  선별 기준: Calmar >= {CALMAR_THRESH}")
    print(f"# test 연도: {first_test_year} ~ {last_test_year}  ({last_test_year - first_test_year + 1}회)")
    print(f"# 대상: {len(universe)}종목")
    print(f"{'#'*65}")

    # WF 결과 집계
    wf_rows = []  # (year, n_sel, sel_calmar_med, nosel_calmar_med, better)

    for test_year in range(first_test_year, last_test_year + 1):
        calib_end   = pd.Timestamp(f"{test_year - 1}-12-31")
        test_start  = pd.Timestamp(f"{test_year}-01-01")
        test_end    = pd.Timestamp(f"{test_year}-12-31")

        sel_calmars   = []  # calibration 통과 종목의 test 성과
        nosel_calmars = []  # calibration 탈락 종목의 test 성과

        for u in universe:
            ticker = u["ticker"]
            df = conn.execute(
                "SELECT date, open, high, low, close FROM ohlcv_daily "
                "WHERE ticker = ? ORDER BY date", [ticker],
            ).df()
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

            df_calib = df[df.index <= calib_end]
            df_test  = df[(df.index >= test_start) & (df.index <= test_end)]

            # calibration: 최소 MIN_CALIB_YEARS년치 데이터 필요
            min_calib_days = MIN_CALIB_YEARS * 200  # 영업일 기준 (252 × 7 ≈ 1764, 여유있게 200×7)
            if len(df_calib) < min_calib_days or len(df_test) < 50:
                continue

            calmar_calib = run_single(df_calib)["calmar"]
            calmar_test  = run_single(df_test)["calmar"]

            if calmar_calib >= CALMAR_THRESH:
                sel_calmars.append(calmar_test)
            else:
                nosel_calmars.append(calmar_test)

        if not sel_calmars:
            continue

        sel_med   = sorted(sel_calmars)[len(sel_calmars) // 2]
        nosel_med = sorted(nosel_calmars)[len(nosel_calmars) // 2] if nosel_calmars else float("nan")
        better    = sel_med > nosel_med if nosel_calmars else True
        wf_rows.append((test_year, len(sel_calmars), sel_med, nosel_med, better))

        arrow = "✓" if better else "✗"
        print(f"  {arrow} {test_year}: 선별={len(sel_calmars)}종목  "
              f"선별Calmar중앙값={sel_med:.3f}  비선별={nosel_med:.3f}")

    # 전체 요약
    if not wf_rows:
        print("  결과 없음")
        return

    n_better = sum(1 for r in wf_rows if r[4])
    all_sel   = [r[2] for r in wf_rows]
    all_nosel = [r[3] for r in wf_rows if r[3] == r[3]]  # nan 제외

    print(f"\n{'='*55}")
    print(f"  WF 요약 ({len(wf_rows)}개 연도)")
    print(f"  선별 종목이 비선별보다 우수한 연도: {n_better}/{len(wf_rows)} = {n_better/len(wf_rows):.1%}")
    overall_sel   = sorted(all_sel)[len(all_sel) // 2]
    overall_nosel = sorted(all_nosel)[len(all_nosel) // 2] if all_nosel else float("nan")
    print(f"  전체 Calmar 중앙값: 선별={overall_sel:.3f}  비선별={overall_nosel:.3f}")
    if n_better / len(wf_rows) >= 0.6 and overall_sel > overall_nosel:
        verdict = "PASS — calibration 선별력 유효"
    elif n_better / len(wf_rows) >= 0.5:
        verdict = "부분 유효 — 추가 분석 필요"
    else:
        verdict = "FAIL — calibration 예측력 없음"
    print(f"  판정: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=["p1", "p2", "p3", "p4", "p5", "p5_diag", "p5_grid2d", "p6_wf", "all"],
        default="p1",
    )
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
        if args.phase == "p5_diag":
            phase_p5_diag(conn)
        if args.phase == "p5_grid2d":
            phase_p5_grid2d(conn)
        if args.phase == "p6_wf":
            phase_p6_wf(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
