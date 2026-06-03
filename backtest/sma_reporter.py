# backtest/sma_reporter.py
"""SMA 백테스트 결과 저장 및 텔레그램 리포트."""
from __future__ import annotations
import logging
import os
from datetime import date

import pandas as pd

from data.db import get_conn

logger = logging.getLogger(__name__)

_TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
_BACKTEST_CHAT_ID = os.getenv("TELEGRAM_BACKTEST_CHAT_ID", "")


def save_results(ticker: str, results: pd.DataFrame, run_date: date) -> None:
    """Walk-forward / 인샘플 결과를 DuckDB에 저장."""
    if results.empty:
        return
    df = results.copy()
    df["ticker"]   = ticker
    df["run_date"] = run_date

    cols = [
        "ticker", "run_date", "sma_period", "confirm_days", "lookback_days",
        "drawdown_pct", "is_walkforward", "window_start", "window_end",
        "calmar", "cagr", "mdd", "win_rate", "profit_factor", "ev",
        "total_trades", "vs_buyhold",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None

    conn = get_conn()
    try:
        conn.register("_sma_res", df[cols])
        conn.execute("""
            INSERT OR REPLACE INTO sma_backtest_results
            SELECT ticker, run_date, sma_period, confirm_days, lookback_days,
                   drawdown_pct, is_walkforward, window_start, window_end,
                   calmar, cagr, mdd, win_rate, profit_factor, ev,
                   total_trades, vs_buyhold
            FROM _sma_res
        """)
        conn.commit()
    finally:
        conn.close()


def format_report(
    ticker: str,
    name: str,
    best_params: dict,
    metrics: dict,
    is_walkforward: bool,
) -> str:
    """텔레그램 메시지 포맷."""
    wf_tag = "Walk-forward 검증" if is_walkforward else "⚠️ 인샘플 참고용"
    return (
        f"📊 [{name} {ticker}] 최적 전략\n"
        f"SMA {best_params['sma_period']}일 | "
        f"확인 {best_params['confirm_days']}일 | "
        f"낙폭 -{best_params['drawdown_pct']}% "
        f"({best_params['lookback_days']}일 고점)\n\n"
        f"Calmar  {metrics['calmar']:.2f}  |  CAGR  {metrics['cagr']:.1f}%\n"
        f"MDD  {metrics['mdd']:.1f}%  |  승률  {metrics['win_rate']:.0f}%\n"
        f"손익비  {metrics['profit_factor']:.1f}:1  |  EV  {metrics['ev']:+.1f}%\n"
        f"Buy&Hold 대비  {metrics['vs_buyhold']:+.0f}%  |  "
        f"거래 {metrics['total_trades']}회\n\n"
        f"📌 {wf_tag}"
    )


def send_telegram(message: str) -> bool:
    """텔레그램 새 채널로 메시지 전송."""
    if not _TELEGRAM_TOKEN or not _BACKTEST_CHAT_ID:
        logger.warning("TELEGRAM_BACKTEST_CHAT_ID 미설정 — 콘솔 출력만")
        print(message)
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": _BACKTEST_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error("텔레그램 전송 실패: %s", e)
        return False
