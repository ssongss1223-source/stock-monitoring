"""
백테스트 라벨러 — (ticker, signal_date) → 선도 수익 wide-format 라벨 계산.

보유 기간 3/5/10일 × 목표 수익률 3/5/10%의 9가지 조합을 단일 패스로 계산.
라벨(label_Xd_Ypct)은 feature_engineering.py에서 동적으로 파생.

라벨 정의:
  entry_price      = T+1 시가
  max_close_Xd     = T+1 ~ T+X 최고 종가 (EOD 매도로 달성 가능한 수익 기준)
  max_drawdown_Xd  = (T+1~X 최저가 - entry_price) / entry_price
  return_Xd        = (T+X 종가 - entry_price) / entry_price
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import pandas as pd

from data.db import get_conn

logger = logging.getLogger(__name__)

_HOLD_DAYS = [3, 5, 10]

# c2: 해당 기간 내 종가가 목표 이상인 날 >= 2일 (3d_10pct 제외 — 달성률 2.9%)
_C2_COMBOS = [(3, 3), (3, 5), (5, 3), (5, 5), (5, 10), (10, 3), (10, 5), (10, 10)]

_LABEL_COLS = [
    "signal_date", "ticker", "entry_price",
    "max_close_3d", "max_close_5d", "max_close_10d",
    "max_drawdown_3d", "max_drawdown_5d", "max_drawdown_10d",
    "return_3d", "return_5d", "return_10d",
    *[f"c2_{d}d_{p}pct" for d, p in _C2_COMBOS],
]


def label_one(df_daily: pd.DataFrame, signal_date: str | date) -> dict | None:
    """
    단일 (ticker, signal_date) wide-format 라벨 계산.

    Args:
        df_daily: DatetimeIndex DataFrame (Open/High/Low/Close 컬럼)
        signal_date: 신호 발생일 (T). T+1 시가를 진입가로 사용.

    Returns:
        wide-format dict, 또는 미래 데이터 부족(< 10거래일) 시 None
    """
    sd = pd.Timestamp(signal_date)
    future = df_daily[df_daily.index > sd]

    if len(future) < max(_HOLD_DAYS):
        return None

    window = future.iloc[:max(_HOLD_DAYS)]  # T+1 ~ T+10 슬라이스 1회
    entry_price = float(window["Open"].iloc[0])

    if entry_price <= 0:
        return None

    result: dict = {"entry_price": entry_price}
    for d in _HOLD_DAYS:
        w = window.iloc[:d]
        max_close = float(w["Close"].max())
        min_low = float(w["Low"].min())
        close_n = float(w["Close"].iloc[-1])
        result[f"max_close_{d}d"] = max_close
        result[f"max_drawdown_{d}d"] = (min_low - entry_price) / entry_price
        result[f"return_{d}d"] = (close_n - entry_price) / entry_price

    for d, pct in _C2_COMBOS:
        w = window.iloc[:d]
        threshold = entry_price * (1 + pct / 100)
        result[f"c2_{d}d_{pct}pct"] = int((w["Close"] >= threshold).sum())

    return result


def label_batch(pairs: list[tuple[str, str | date]]) -> pd.DataFrame:
    """
    (ticker, signal_date) 리스트 → wide-format 라벨 DataFrame.

    DuckDB에서 일봉을 읽어 각 (ticker, date)의 3/5/10일 라벨을 단일 패스로 계산.
    미래 데이터가 10거래일 미만인 행은 제외됨.

    Returns:
        columns: signal_date, ticker, entry_price,
                 max_close_3d/5d/10d, max_drawdown_3d/5d/10d, return_3d/5d/10d
    """
    if not pairs:
        return pd.DataFrame(columns=_LABEL_COLS)

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
        result = label_one(df, signal_date)
        if result is not None:
            rows.append({
                "signal_date": pd.Timestamp(signal_date).date(),
                "ticker": ticker,
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
    """wide-format 라벨 결과 요약 출력."""
    if labels.empty:
        print("라벨 없음")
        return

    n = len(labels)
    print(f"신호 {n}건\n")
    print(f"{'':16s} {'3일':>7s} {'5일':>7s} {'10일':>7s}")
    for pct in [3, 5, 10]:
        rates = []
        for d in _HOLD_DAYS:
            achieved = (labels[f"max_close_{d}d"] >= labels["entry_price"] * (1 + pct / 100)).mean()
            rates.append(f"{achieved:.1%}")
        print(f"  +{pct}% 달성률:     {rates[0]:>7s} {rates[1]:>7s} {rates[2]:>7s}")

    print()
    avg_rets = [f"{labels[f'return_{d}d'].mean():.2%}" for d in _HOLD_DAYS]
    print(f"  평균 수익률:     {avg_rets[0]:>7s} {avg_rets[1]:>7s} {avg_rets[2]:>7s}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="백테스트 라벨 계산 (wide format)")
    p.add_argument("--tickers", nargs="+", help="종목 코드 목록 (미지정 시 DB 전체 종목)")
    p.add_argument("--date", default=None, help="신호 날짜 YYYY-MM-DD (미지정 시 최근 거래일)")
    p.add_argument("--all", action="store_true", dest="all_signals",
                   help="signal_history 전체 (ticker, signal_date) 라벨 생성")
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
        if args.all_signals:
            rows = conn.execute(
                "SELECT ticker, signal_date FROM signal_history ORDER BY signal_date, ticker"
            ).fetchall()
            pairs = [(r[0], r[1]) for r in rows]
            print(f"signal_history에서 {len(pairs)}건 로드")
        else:
            signal_date = date.fromisoformat(args.date) if args.date else _latest_trade_date(conn)
            tickers = args.tickers or _all_tickers(conn)
            pairs = [(t, signal_date) for t in tickers]
            print(f"신호일: {signal_date}, 종목: {len(tickers)}개")
    finally:
        conn.close()

    labels = label_batch(pairs)
    summarize(labels)

    if args.save:
        save_labels(labels)
        print(f"\nDB 저장 완료: {len(labels)}건")


if __name__ == "__main__":
    main()
