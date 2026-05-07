"""
패턴 학습 백테스트 CLI

사용법:
  python backtest/pattern_backtest.py --ticker 005930
  python backtest/pattern_backtest.py --ticker 005930 --test-start 20250401
  python backtest/pattern_backtest.py --ticker 005930 --threshold 0.03
"""

import argparse
import io
import sys
from datetime import date, timedelta
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows 콘솔 UTF-8 출력
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import numpy as np
from pykrx import stock

from agents.pattern_learning import (
    FUTURE_DAYS,
    SUCCESS_THRESHOLD,
    TOP_K,
    WINDOW_CANDIDATES,
    StockPatternLearner,
    _build_corpus,
    _col,
    _fetch_investor,
    _make_vector,
    _optimize_window,
)


def run_backtest(
    ticker: str,
    train_days: int = 400,
    threshold: float = SUCCESS_THRESHOLD,
    future_days: int = FUTURE_DAYS,
    test_start_date: str | None = None,
) -> None:
    print(f"\n종목 코드: {ticker} — 데이터 조회 중...")

    today = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1500)).strftime("%Y%m%d")

    try:
        df = stock.get_market_ohlcv_by_date(start, today, ticker)
    except Exception as e:
        print(f"OHLCV 조회 실패: {e}")
        return

    if df is None or df.empty:
        print("데이터 없음")
        return

    try:
        name = stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        name = ticker

    close = _col(df, ("종가", "Close"))
    vol = _col(df, ("거래량", "Volume"))
    investor_df = _fetch_investor(ticker, len(close))
    has_investor = investor_df is not None
    dim_label = "4ch" if has_investor else "2ch"

    # 훈련/테스트 분리: test_start_date 우선, 없으면 train_days
    if test_start_date:
        import pandas as pd
        cutoff = pd.Timestamp(test_start_date)
        split_idx = int((close.index < cutoff).sum())
        if split_idx < 60:
            print(f"훈련 데이터 부족: {split_idx}행")
            return
        print(f"테스트 시작일: {test_start_date} → 인덱스 {split_idx} (훈련 {split_idx}거래일)")
        train_days = split_idx
    elif len(df) < train_days + 60:
        print(f"데이터 부족: {len(df)}행 (최소 {train_days + 60}행 필요)")
        return

    train_close = close.iloc[:train_days]
    train_vol = vol.iloc[:train_days] if vol is not None else None
    train_inv = investor_df.iloc[:train_days] if investor_df is not None else None

    # 윈도우 최적화
    print("윈도우 최적화 중...")
    optimal_w = _optimize_window(train_close, train_vol, train_inv)

    # 훈련 코퍼스 구성
    corpus, labels, _ = _build_corpus(train_close, train_vol, train_inv, optimal_w)
    if corpus is None or len(corpus) < TOP_K:
        print(f"훈련 패턴 부족 ({len(corpus) if corpus is not None else 0}개)")
        return

    # _build_corpus의 rets(peak return)를 threshold로 재라벨링
    corpus, _, raw_rets = _build_corpus(train_close, train_vol, train_inv, optimal_w)
    labels = (raw_rets >= threshold * 100).astype(np.float32)

    success_n = int(labels.sum())
    total_n = len(labels)
    print(f"[진단] 훈련 코퍼스: {total_n}개 패턴, 성공(+{threshold*100:.0f}%/{future_days}일) {success_n}개 ({success_n/total_n*100:.1f}%)")
    print(f"[진단] 등급 기준: HIGH>=65% / MEDIUM>=50% / LOW>=35% (top-{TOP_K} 이웃)")

    # 테스트 구간 평가
    test_start = train_days
    grade_stats: dict[str, dict] = {
        g: {"total": 0, "success": 0, "returns": []}
        for g in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")
    }

    first_test_date = str(close.index[test_start].date()) if test_start < len(close) else "N/A"
    last_test_date = "N/A"
    for i in range(test_start, len(close) - optimal_w - future_days):
        q = _make_vector(close, vol, investor_df, optimal_w, start_idx=i)
        if q is None:
            continue

        norms = np.linalg.norm(corpus, axis=1)
        qn = np.linalg.norm(q)
        if qn < 1e-10:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            sims = (corpus @ q) / (norms * qn)
        sims = np.nan_to_num(sims, nan=-1.0)
        top_k_idx = np.argsort(sims)[-TOP_K:]

        confidence = float(np.mean(labels[top_k_idx]))
        similar_count = int(np.sum(labels[top_k_idx]))
        grade = _grade(confidence, similar_count)

        future = close.iloc[i + optimal_w: i + optimal_w + future_days]
        base = float(close.iloc[i + optimal_w - 1])
        peak = float(future.max()) if base > 0 else base
        ret = (peak - base) / base * 100.0 if base > 0 else 0.0
        success = 1 if peak >= base * (1 + threshold) else 0

        grade_stats[grade]["total"] += 1
        grade_stats[grade]["success"] += success
        grade_stats[grade]["returns"].append(ret)
        last_test_date = str(close.index[i].date())

    total_tested = sum(v["total"] for v in grade_stats.values())
    if total_tested == 0:
        print("테스트 포지션 없음")
        return

    date_start = first_test_date
    date_end = last_test_date

    print(f"\n{'═' * 55}")
    print(f"패턴 백테스트 결과: {ticker} ({name})  선택 윈도우: W={optimal_w}  패턴채널: {dim_label}")
    print(f"기간: {date_start} ~ {date_end} | 테스트 윈도우: {total_tested}개")
    print(f"{'─' * 55}")
    print(f"{'등급':<12} {'샘플':>6} {'성공':>6} {'정밀도':>8} {'평균수익률(5일)':>15}")

    for grade in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT"):
        s = grade_stats[grade]
        n = s["total"]
        if n == 0:
            continue
        succ = s["success"]
        prec = succ / n * 100
        avg_ret = float(np.mean(s["returns"])) if s["returns"] else 0.0
        ret_str = f"+{avg_ret:.1f}%" if avg_ret >= 0 else f"{avg_ret:.1f}%"
        if grade == "INSUFFICIENT":
            print(f"{grade:<12} {n:>6}    {'—':>4}      {'—':>6}          {'—':>8}")
        else:
            print(f"{grade:<12} {n:>6} {succ:>6} {prec:>7.1f}% {ret_str:>14}")

    # 전체 통계 (INSUFFICIENT 제외)
    rated = [g for g in ("HIGH", "MEDIUM", "LOW") if grade_stats[g]["total"] > 0]
    total_rated = sum(grade_stats[g]["total"] for g in rated)
    total_success = sum(grade_stats[g]["success"] for g in rated)
    overall_prec = total_success / total_rated * 100 if total_rated > 0 else 0.0
    random_baseline = 50.0

    print(f"{'─' * 55}")
    print(f"전체 성공률: {overall_prec:.1f}% ({total_success}/{total_rated})  기준치(랜덤): ~{random_baseline:.0f}%")
    print(f"{'═' * 55}\n")


def _grade(confidence: float, similar_count: int) -> str:
    if confidence >= 0.65 and similar_count >= 5:
        return "HIGH"
    if confidence >= 0.50 and similar_count >= 3:
        return "MEDIUM"
    if confidence >= 0.35 and similar_count >= 2:
        return "LOW"
    return "INSUFFICIENT"


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description="종목별 패턴 학습 백테스트")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", help="종목 코드 (예: 005930)")
    group.add_argument("--watchlist", action="store_true", help="data/watchlist.json 전체 종목 실행")
    parser.add_argument("--train-days", type=int, default=400, help="훈련 기간 거래일 수 (기본: 400)")
    parser.add_argument("--threshold", type=float, default=SUCCESS_THRESHOLD, help=f"성공 기준 수익률 (기본: {SUCCESS_THRESHOLD})")
    parser.add_argument("--future-days", type=int, default=FUTURE_DAYS, help=f"미래 관측 기간 (기본: {FUTURE_DAYS})")
    parser.add_argument("--test-start", type=str, default=None, help="테스트 시작일 YYYYMMDD (예: 20250401)")
    args = parser.parse_args()

    if args.watchlist:
        watchlist_path = Path(__file__).parent.parent / "data" / "watchlist.json"
        tickers = json.loads(watchlist_path.read_text(encoding="utf-8")).get("stocks", [])
        if not tickers:
            print("watchlist.json에 종목이 없습니다.")
            sys.exit(1)
        print(f"watchlist 종목 {len(tickers)}개: {tickers}")
        for t in tickers:
            run_backtest(t, args.train_days, args.threshold, args.future_days, args.test_start)
    else:
        run_backtest(args.ticker, args.train_days, args.threshold, args.future_days, args.test_start)
