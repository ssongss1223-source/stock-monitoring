"""
백테스트 라벨러 — (ticker, signal_date) → 선도 수익 라벨 계산.

기본값: 3일 보유, 3% 수익 목표. 두 파라미터 모두 호출 시 변경 가능.

라벨 정의:
  entry_price  = T+1 시가
  max_high     = T+1 ~ T+hold_days 최고가
  min_low      = T+1 ~ T+hold_days 최저가
  close_n      = T+hold_days 종가
  label        = 1 if max_high >= entry_price * (1 + target_return)
  max_drawdown = (min_low - entry_price) / entry_price
  return_n     = (close_n - entry_price) / entry_price
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

import pandas as pd

from data.db import get_conn

logger = logging.getLogger(__name__)

_LABEL_COLS = [
    "signal_date", "ticker", "hold_days", "target_return",
    "entry_price", "max_high", "min_low", "close_n",
    "label", "max_drawdown", "return_n",
]


def label_one(
    df_daily: pd.DataFrame,
    signal_date: str | date,
    hold_days: int = 3,
    target_return: float = 0.03,
) -> dict | None:
    """
    단일 (ticker, signal_date) 라벨 계산.

    Args:
        df_daily: OhlcvStore가 반환한 DataFrame (DatetimeIndex, Open/High/Low/Close 컬럼)
        signal_date: 신호 발생일 (T). T+1 시가를 진입가로 사용.
        hold_days: 보유 기간 (기본 3거래일)
        target_return: 목표 수익률 (기본 0.03 = 3%)

    Returns:
        라벨 dict, 또는 데이터 부족 시 None
    """
    sd = pd.Timestamp(signal_date)
    future = df_daily[df_daily.index > sd]

    if len(future) < hold_days:
        return None

    window = future.iloc[:hold_days]
    entry_price = float(window["Open"].iloc[0])

    if entry_price <= 0:
        return None

    max_high = float(window["High"].max())
    min_low = float(window["Low"].min())
    close_n = float(window["Close"].iloc[-1])

    return {
        "entry_price": entry_price,
        "max_high": max_high,
        "min_low": min_low,
        "close_n": close_n,
        "label": int(max_high >= entry_price * (1 + target_return)),
        "max_drawdown": (min_low - entry_price) / entry_price,
        "return_n": (close_n - entry_price) / entry_price,
    }


def label_batch(
    pairs: list[tuple[str, str | date]],
    hold_days: int = 3,
    target_return: float = 0.03,
) -> pd.DataFrame:
    """
    (ticker, signal_date) 리스트 → 라벨 DataFrame.

    DuckDB에서 일봉을 읽어 각 (ticker, date) 라벨을 계산.
    계산 불가 행(데이터 부족)은 결과에서 제외됨.

    Returns:
        columns: signal_date, ticker, hold_days, target_return,
                 entry_price, max_high, min_low, close_n,
                 label, max_drawdown, return_n
    """
    conn = get_conn(read_only=True)
    try:
        tickers = list({t for t, _ in pairs})
        placeholders = ", ".join("?" * len(tickers))
        df_all = conn.execute(
            f"SELECT ticker, date, open, high, low, close "
            f"FROM ohlcv_daily WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
            tickers,
        ).df()
    finally:
        conn.close()

    df_all["date"] = pd.to_datetime(df_all["date"])

    rows = []
    for ticker, signal_date in pairs:
        df = df_all[df_all["ticker"] == ticker].set_index("date")
        df = df.rename(columns={"open": "Open", "high": "High",
                                 "low": "Low", "close": "Close"})
        result = label_one(df, signal_date, hold_days, target_return)
        if result is not None:
            rows.append({
                "signal_date": pd.Timestamp(signal_date).date(),
                "ticker": ticker,
                "hold_days": hold_days,
                "target_return": target_return,
                **result,
            })

    return pd.DataFrame(rows, columns=_LABEL_COLS) if rows else pd.DataFrame(columns=_LABEL_COLS)


def save_labels(labels: pd.DataFrame) -> None:
    """라벨 DataFrame을 backtest_labels 테이블에 upsert."""
    if labels.empty:
        return
    conn = get_conn()
    try:
        conn.register("_lbl", labels)
        conn.execute(f"""
            INSERT OR REPLACE INTO backtest_labels
            SELECT {', '.join(_LABEL_COLS)} FROM _lbl
        """)
    finally:
        conn.close()


def summarize(labels: pd.DataFrame) -> None:
    """라벨 결과 요약 출력."""
    if labels.empty:
        print("라벨 없음")
        return

    n = len(labels)
    win_rate = labels["label"].mean()
    avg_return = labels["return_n"].mean()
    avg_dd = labels["max_drawdown"].mean()
    survivable = labels[labels["max_drawdown"] >= -0.05]["label"].mean()

    params = f"hold_days={labels['hold_days'].iloc[0]}, target_return={labels['target_return'].iloc[0]:.1%}"
    print(f"[{params}]  신호 {n}건")
    print(f"  승률 (label=1):        {win_rate:.1%}")
    print(f"  손절 생존 후 승률:      {survivable:.1%}  (max_drawdown >= -5%)")
    print(f"  평균 {labels['hold_days'].iloc[0]}일 수익:      {avg_return:.2%}")
    print(f"  평균 최대 낙폭:        {avg_dd:.2%}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="백테스트 라벨 계산")
    p.add_argument("--tickers", nargs="+", help="종목 코드 목록 (미지정 시 DB 전체 종목)")
    p.add_argument("--date", default=None, help="신호 날짜 YYYY-MM-DD (미지정 시 최근 거래일)")
    p.add_argument("--days", type=int, default=3, help="보유 기간 (기본 3)")
    p.add_argument("--pct", type=float, default=0.03, help="목표 수익률 (기본 0.03)")
    p.add_argument("--save", action="store_true", help="결과를 DB에 저장")
    return p.parse_args()


def _latest_trade_date(conn) -> date:
    row = conn.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
    return row[0]


def _all_tickers(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ohlcv_daily").fetchall()]


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    args = _parse_args()

    conn = get_conn(read_only=True)
    try:
        signal_date = date.fromisoformat(args.date) if args.date else _latest_trade_date(conn)
        tickers = args.tickers or _all_tickers(conn)
    finally:
        conn.close()

    print(f"신호일: {signal_date}, 종목: {len(tickers)}개, "
          f"hold_days={args.days}, target_return={args.pct:.1%}")

    pairs = [(t, signal_date) for t in tickers]
    labels = label_batch(pairs, hold_days=args.days, target_return=args.pct)
    summarize(labels)

    if args.save:
        save_labels(labels)
        print(f"\nDB 저장 완료: {len(labels)}건")


if __name__ == "__main__":
    main()
