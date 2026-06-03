#!/usr/bin/env python3
# scripts/run_sma_backtest.py
"""SMA 백테스팅 통합 실행.

Usage:
    # 전체 23종목 실행
    python scripts/run_sma_backtest.py

    # 특정 종목만
    python scripts/run_sma_backtest.py --tickers 005930 000660

    # 파라미터 조합 수 확인
    python scripts/run_sma_backtest.py --dry-run
"""
from __future__ import annotations
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_conn
from backtest.sma_config import UNIVERSE, FIXED, all_param_combinations, MIN_YEARS_FOR_WF
from backtest.sma_optimizer import run_grid_search, find_best_params
from backtest.sma_backtester import run_backtest
from backtest.sma_reporter import save_results, format_report, send_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_close(ticker: str):
    """종목의 종가 시리즈 로드."""
    import pandas as pd
    conn = get_conn(read_only=True)
    try:
        df = conn.execute(
            "SELECT date, close FROM ohlcv_daily WHERE ticker=? AND close>0 ORDER BY date",
            [ticker]
        ).df()
    finally:
        conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"]


def run_ticker(ticker: str, name: str, param_combinations: list[dict]) -> None:
    """단일 종목 백테스팅 실행."""
    logger.info("[%s] %s 데이터 로딩...", ticker, name)
    close = load_close(ticker)
    if close.empty or len(close) < 200:
        logger.warning("[%s] 데이터 부족 (%d행), 스킵", ticker, len(close))
        return

    n_years = len(close) / 252
    is_wf = n_years >= MIN_YEARS_FOR_WF
    logger.info("[%s] %.1f년 데이터, Walk-forward=%s", ticker, n_years, is_wf)

    results = run_grid_search(close, param_combinations, FIXED)
    if results.empty:
        logger.warning("[%s] 결과 없음 (거래 수 부족)", ticker)
        return

    save_results(ticker, results, date.today())
    logger.info("[%s] 결과 저장 완료 (%d행)", ticker, len(results))

    best = find_best_params(results)
    if best is None:
        logger.warning("[%s] 최적 파라미터 없음", ticker)
        return

    _, metrics = run_backtest(close, best, FIXED)

    msg = format_report(ticker, name, best, metrics, is_walkforward=is_wf)
    logger.info("[%s] 리포트:\n%s", ticker, msg)
    send_telegram(msg)


def main() -> None:
    """메인 진입점."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*", help="특정 종목 코드 지정")
    parser.add_argument("--dry-run", action="store_true", help="파라미터 수 확인만")
    args = parser.parse_args()

    combos = all_param_combinations()
    logger.info("파라미터 조합: %d개", len(combos))

    if args.dry_run:
        print(f"파라미터 조합: {len(combos)}개")
        print(f"대상 종목: {len(UNIVERSE)}개")
        print(f"총 백테스트: {len(combos) * len(UNIVERSE)}개 (Walk-forward 윈도우 미포함)")
        return

    universe = UNIVERSE
    if args.tickers:
        universe = [u for u in UNIVERSE if u["ticker"] in args.tickers]

    for stock in universe:
        try:
            run_ticker(stock["ticker"], stock["name"], combos)
        except Exception as e:
            logger.error("[%s] 오류: %s", stock["ticker"], e)


if __name__ == "__main__":
    main()
