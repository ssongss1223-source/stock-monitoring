#!/usr/bin/env python3
"""
Track C label builder — computes 9 label candidates for backtest_labels_c table.

Usage:
  python scripts/labeler_c.py --build [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  python scripts/labeler_c.py --filter
  python scripts/labeler_c.py --check
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Must run from /opt/stock-monitor (project root)
sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn

_START_DEFAULT = "2023-06-07"

_ALL_LABELS = [
    "label_3d_5pct_first",
    "label_3d_10pct_first_c",
    "label_5d_7pct_first",
    "label_5d_10pct_first_c",
    "label_2d_5pct_first",
    "label_3d_bb_upper_break",
    "label_3d_range_breakout_20d",
    "label_5d_range_breakout_20d",
    "label_3d_recover_pullback",
]


def _label_first_to_hit(
    closes: list[float],
    entry: float,
    win_pct: float,
    stop_pct: float,
) -> Optional[bool]:
    """
    True if win hits before stop; False if stop hits first or neither.
    None if entry is invalid.
    """
    if not closes or entry <= 0:
        return None
    win_price = entry * (1 + win_pct)
    stop_price = entry * (1 - stop_pct)
    for c in closes:
        if c >= win_price:
            return True
        if c <= stop_price:
            return False
    return False


def _label_ticker(
    ticker_hist: pd.DataFrame,
    signal_date: date,
    atr: Optional[float],
) -> dict:
    """
    Compute all per-ticker labels and metrics.

    Args:
        ticker_hist: DataFrame with [date, open, high, low, close, volume] sorted by date
        signal_date: the label signal date
        atr: ATR value from universe_features_daily.atr_14 (or None)

    Returns:
        dict with entry_price, return_2d/3d/5d, max_drawdown_2d/3d/5d, and all labels
    """
    result = {}

    # Separate past (signal_date and before) and future
    past = ticker_hist[ticker_hist["date"] <= signal_date]
    future = ticker_hist[ticker_hist["date"] > signal_date]

    # Entry price = T+1 open
    entry_price = future["open"].iloc[0] if len(future) >= 1 else None
    result["entry_price"] = entry_price

    # If entry is invalid, skip all labels
    if entry_price is None or entry_price <= 0:
        for label in _ALL_LABELS:
            result[label] = None
        result["return_2d"] = None
        result["return_3d"] = None
        result["return_5d"] = None
        result["max_drawdown_2d"] = None
        result["max_drawdown_3d"] = None
        result["max_drawdown_5d"] = None
        return result

    # Compute returns and drawdowns
    future_closes = future["close"].values

    if len(future_closes) >= 2:
        result["return_2d"] = (future_closes[1] - entry_price) / entry_price
        result["max_drawdown_2d"] = np.min(
            (future_closes[:2] - entry_price) / entry_price
        )
    else:
        result["return_2d"] = None
        result["max_drawdown_2d"] = None

    if len(future_closes) >= 3:
        result["return_3d"] = (future_closes[2] - entry_price) / entry_price
        result["max_drawdown_3d"] = np.min(
            (future_closes[:3] - entry_price) / entry_price
        )
    else:
        result["return_3d"] = None
        result["max_drawdown_3d"] = None

    if len(future_closes) >= 5:
        result["return_5d"] = (future_closes[4] - entry_price) / entry_price
        result["max_drawdown_5d"] = np.min(
            (future_closes[:5] - entry_price) / entry_price
        )
    else:
        result["return_5d"] = None
        result["max_drawdown_5d"] = None

    # Past and future closes for technical labels
    past_closes = past["close"].values
    past_highs = past["high"].values

    # ── Group 1: First-to-hit labels ──
    result["label_2d_5pct_first"] = _label_first_to_hit(
        future_closes[:2].tolist(), entry_price, 0.05, 0.025
    )
    result["label_3d_5pct_first"] = _label_first_to_hit(
        future_closes[:3].tolist(), entry_price, 0.05, 0.035
    )
    result["label_3d_10pct_first_c"] = _label_first_to_hit(
        future_closes[:3].tolist(), entry_price, 0.10, 0.04
    )
    result["label_5d_7pct_first"] = _label_first_to_hit(
        future_closes[:5].tolist(), entry_price, 0.07, 0.04
    )
    result["label_5d_10pct_first_c"] = _label_first_to_hit(
        future_closes[:5].tolist(), entry_price, 0.10, 0.05
    )

    # ── Group 2: Technical breakout labels ──

    # BB upper break (3d, 5d)
    # BB 상단을 오늘 종가 제외한 직전 20일 기준으로 계산.
    # 오늘 이미 BB 상단을 넘은 종목은 NULL — "돌파 전 → 돌파" 케이스만 의미있음.
    past_excl = past_closes[:-1]  # 오늘 종가 제외
    if len(past_excl) >= 20:
        last_20_excl = past_excl[-20:]
        bb_upper_prev = last_20_excl.mean() + 2 * last_20_excl.std(ddof=1)
        today_close = past_closes[-1]

        if today_close >= bb_upper_prev:
            result["label_3d_bb_upper_break"] = None
            result["label_5d_bb_upper_break"] = None
        else:
            result["label_3d_bb_upper_break"] = (
                True
                if len(future_closes) >= 3 and np.max(future_closes[:3]) > bb_upper_prev
                else (False if len(future_closes) >= 3 else None)
            )
            result["label_5d_bb_upper_break"] = (
                True
                if len(future_closes) >= 5 and np.max(future_closes[:5]) > bb_upper_prev
                else (False if len(future_closes) >= 5 else None)
            )
    else:
        result["label_3d_bb_upper_break"] = None
        result["label_5d_bb_upper_break"] = None

    # Range breakout (20d)
    if len(past_highs) >= 20:
        high_20d = np.max(past_highs[-20:])
        result["label_3d_range_breakout_20d"] = (
            True
            if len(future_closes) >= 3 and np.max(future_closes[:3]) > high_20d
            else (False if len(future_closes) >= 3 else None)
        )
        result["label_5d_range_breakout_20d"] = (
            True
            if len(future_closes) >= 5 and np.max(future_closes[:5]) > high_20d
            else (False if len(future_closes) >= 5 else None)
        )
    else:
        result["label_3d_range_breakout_20d"] = None
        result["label_5d_range_breakout_20d"] = None

    # Pullback recovery: prior 5d return <= -5%, then +5% in 3d
    if len(past_closes) >= 6:
        ret_5d_prior = past_closes[-1] / past_closes[-6] - 1
        if ret_5d_prior <= -0.05:
            result["label_3d_recover_pullback"] = _label_first_to_hit(
                future_closes[:3].tolist(), entry_price, 0.05, 0.035
            )
        else:
            result["label_3d_recover_pullback"] = None
    else:
        result["label_3d_recover_pullback"] = None

    return result


def _process_date(conn, signal_date: date) -> list[dict]:
    """
    Process all tickers for a given signal_date.
    Compute per-ticker labels, then cross-sectional/excess labels.
    Return list of row dicts for insert.
    """
    signal_date_str = signal_date.isoformat()

    # Get tickers in universe_daily for this date
    tickers_query = f"""
        SELECT DISTINCT ticker
        FROM universe_daily
        WHERE date = '{signal_date_str}'
        ORDER BY ticker
    """
    tickers = [row[0] for row in conn.execute(tickers_query).fetchall()]

    if not tickers:
        return []

    # Load ohlcv_daily for all tickers covering signal_date +/- 365 days
    lookback_date = (signal_date - timedelta(days=365)).isoformat()
    lookahead_date = (signal_date + timedelta(days=7)).isoformat()

    ohlcv_query = f"""
        SELECT ticker, date, open, high, low, close, volume
        FROM ohlcv_daily
        WHERE date >= '{lookback_date}' AND date <= '{lookahead_date}'
        ORDER BY ticker, date
    """
    ohlcv_df = conn.execute(ohlcv_query).df()
    if ohlcv_df.empty:
        return []

    # Convert date to date object
    ohlcv_df["date"] = pd.to_datetime(ohlcv_df["date"]).dt.date

    # Load ATR for this date
    atr_query = f"""
        SELECT ticker, atr_14
        FROM universe_features_daily
        WHERE date = '{signal_date_str}'
    """
    atr_rows = conn.execute(atr_query).fetchall()
    atr_dict = {row[0]: row[1] for row in atr_rows}

    # Pre-index ohlcv by ticker to avoid O(N²) per-ticker filtering
    ohlcv_by_ticker = {t: grp.reset_index(drop=True) for t, grp in ohlcv_df.groupby("ticker")}

    # Compute per-ticker labels
    ticker_results = {}

    for ticker in tickers:
        ticker_hist = ohlcv_by_ticker.get(ticker)
        if ticker_hist is None or len(ticker_hist) < 1:
            continue

        atr = atr_dict.get(ticker)
        result = _label_ticker(ticker_hist, signal_date, atr)
        ticker_results[ticker] = result

    # Build output rows
    rows = []
    for ticker, result in ticker_results.items():
        row = {
            "signal_date": signal_date,
            "ticker": ticker,
            "entry_price": result["entry_price"],
            "return_2d": result["return_2d"],
            "return_3d": result["return_3d"],
            "return_5d": result["return_5d"],
            "max_drawdown_2d": result["max_drawdown_2d"],
            "max_drawdown_3d": result["max_drawdown_3d"],
            "max_drawdown_5d": result["max_drawdown_5d"],
        }
        for label in _ALL_LABELS:
            row[label] = result[label]
        rows.append(row)

    return rows


def cmd_build(start_str: Optional[str], end_str: Optional[str], target_labels: Optional[list] = None) -> None:
    """Build labels for date range. If target_labels given, UPDATE those columns only."""
    if target_labels:
        invalid = [l for l in target_labels if l not in _ALL_LABELS]
        if invalid:
            print(f"Unknown labels: {invalid}\nValid: {_ALL_LABELS}")
            return

    conn = get_conn()

    # Determine date range
    if start_str:
        start_date = date.fromisoformat(start_str)
    else:
        start_date = date.fromisoformat(_START_DEFAULT)

    if end_str:
        end_date = date.fromisoformat(end_str)
    else:
        # Yesterday or last trading day in ohlcv_daily
        result = conn.execute(
            "SELECT MAX(date) FROM ohlcv_daily"
        ).fetchone()
        end_date = result[0] if result[0] else date.today() - timedelta(days=1)

    # Get all trading dates in range from universe_daily
    trading_dates_query = f"""
        SELECT DISTINCT date
        FROM universe_daily
        WHERE date >= '{start_date.isoformat()}' AND date <= '{end_date.isoformat()}'
        ORDER BY date
    """
    trading_dates = [
        row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        for row in conn.execute(trading_dates_query).fetchall()
    ]

    print(
        f"Processing {len(trading_dates)} dates from {start_date} to {end_date}"
    )

    def _b(v):
        """Convert numpy.bool_ / np.bool to Python bool or None for DuckDB."""
        if v is None:
            return None
        return bool(v)

    if target_labels:
        # UPDATE 모드: 지정된 컬럼만 갱신
        set_clause = ", ".join(f"{l}=?" for l in target_labels)
        update_sql = f"UPDATE backtest_labels_c SET {set_clause} WHERE signal_date=? AND ticker=?"

        def _to_update_values(row):
            return tuple(_b(row[l]) for l in target_labels) + (row["signal_date"], row["ticker"])

        total_inserted = 0
        batch_rows = []
        for idx, signal_date in enumerate(trading_dates):
            rows = _process_date(conn, signal_date)
            batch_rows.extend(rows)

            if (idx + 1) % 50 == 0 or (idx + 1) == len(trading_dates):
                if batch_rows:
                    conn.executemany(update_sql, [_to_update_values(r) for r in batch_rows])
                    conn.commit()
                    total_inserted += len(batch_rows)
                    batch_rows = []
                print(f"  Processed {idx + 1}/{len(trading_dates)} dates, updated {total_inserted} rows total")
    else:
        # 전체 INSERT OR REPLACE 모드
        insert_sql = """
            INSERT OR REPLACE INTO backtest_labels_c (
                signal_date, ticker, entry_price, return_2d, return_3d, return_5d,
                max_drawdown_2d, max_drawdown_3d, max_drawdown_5d,
                label_3d_5pct_first, label_3d_10pct_first_c,
                label_5d_7pct_first, label_5d_10pct_first_c, label_2d_5pct_first,
                label_3d_bb_upper_break, label_3d_range_breakout_20d,
                label_5d_range_breakout_20d, label_3d_recover_pullback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        def _to_values(row):
            return (
                row["signal_date"], row["ticker"], row["entry_price"],
                row["return_2d"], row["return_3d"], row["return_5d"],
                row["max_drawdown_2d"], row["max_drawdown_3d"], row["max_drawdown_5d"],
                _b(row["label_3d_5pct_first"]), _b(row["label_3d_10pct_first_c"]),
                _b(row["label_5d_7pct_first"]), _b(row["label_5d_10pct_first_c"]), _b(row["label_2d_5pct_first"]),
                _b(row["label_3d_bb_upper_break"]), _b(row["label_3d_range_breakout_20d"]),
                _b(row["label_5d_range_breakout_20d"]), _b(row["label_3d_recover_pullback"]),
            )

        total_inserted = 0
        batch_rows = []
        for idx, signal_date in enumerate(trading_dates):
            rows = _process_date(conn, signal_date)
            batch_rows.extend(rows)

            if (idx + 1) % 50 == 0 or (idx + 1) == len(trading_dates):
                if batch_rows:
                    conn.executemany(insert_sql, [_to_values(r) for r in batch_rows])
                    conn.commit()
                    total_inserted += len(batch_rows)
                    batch_rows = []
                print(f"  Processed {idx + 1}/{len(trading_dates)} dates, inserted {total_inserted} rows total")

    print(f"Done. Total inserted: {total_inserted} rows into backtest_labels_c")
    conn.close()


def cmd_filter() -> None:
    """Compute positive rate per label (excludes NULLs from denominator)."""
    conn = get_conn()

    # Build per-label expressions: positive rate = TRUE count / non-null count
    label_exprs = ",\n            ".join(
        f"SUM(CASE WHEN {lbl} THEN 1 ELSE 0 END) AS pos_{lbl},\n"
        f"            COUNT({lbl}) AS cnt_{lbl}"
        for lbl in _ALL_LABELS
    )
    query = f"""
        SELECT COUNT(*) AS total, {label_exprs}
        FROM backtest_labels_c
        WHERE signal_date >= '{_START_DEFAULT}'
    """
    result = conn.execute(query).fetchone()
    conn.close()

    if not result:
        print("No data in backtest_labels_c")
        return

    total = result[0]
    print(f"\nLabel Positive Rates (non-null denominator) — total rows: {total}")
    print(f"{'Label':42} {'Rate':>7}  {'Non-null':>10}  Filter")
    print("-" * 72)
    col = 1
    for lbl in _ALL_LABELS:
        pos = result[col]
        cnt = result[col + 1]
        col += 2
        if cnt and cnt > 0:
            rate = 100.0 * pos / cnt
            passes = "YES" if 5 <= rate <= 45 else "NO "
            print(f"{lbl:42} {rate:6.2f}%  {cnt:>10}  {passes}")
        else:
            print(f"{lbl:42}      N/A  {0:>10}  ---")


def cmd_check() -> None:
    """Print summary stats."""
    conn = get_conn()

    total = conn.execute(
        "SELECT COUNT(*) FROM backtest_labels_c"
    ).fetchone()[0]

    date_range = conn.execute(
        "SELECT MIN(signal_date), MAX(signal_date) FROM backtest_labels_c"
    ).fetchone()

    n_tickers = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM backtest_labels_c"
    ).fetchone()[0]

    n_dates = conn.execute(
        "SELECT COUNT(DISTINCT signal_date) FROM backtest_labels_c"
    ).fetchone()[0]

    print("\nbacktest_labels_c Summary")
    print("-" * 60)
    print(f"Total rows: {total}")
    if date_range[0] and date_range[1]:
        print(f"Date range: {date_range[0]} to {date_range[1]}")
    print(f"Unique tickers: {n_tickers}")
    print(f"Unique dates: {n_dates}")

    # Null counts per label
    print("\nNull counts per label:")
    for label in _ALL_LABELS:
        null_count = conn.execute(
            f"SELECT COUNT(*) FROM backtest_labels_c WHERE {label} IS NULL"
        ).fetchone()[0]
        print(f"  {label}: {null_count}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Track C label builder")
    parser.add_argument("--build", action="store_true", help="Build labels for date range")
    parser.add_argument("--filter", action="store_true", help="Compute positive rate per label")
    parser.add_argument("--check", action="store_true", help="Print summary stats")
    parser.add_argument("--start", help="Start date for --build (YYYY-MM-DD)", default=None)
    parser.add_argument("--end", help="End date for --build (YYYY-MM-DD)", default=None)
    parser.add_argument("--labels", help="Comma-separated labels to rebuild (UPDATE only, e.g. label_3d_bb_upper_break)", default=None)

    args = parser.parse_args()

    if args.build:
        target_labels = [l.strip() for l in args.labels.split(",")] if args.labels else None
        cmd_build(args.start, args.end, target_labels)
    elif args.filter:
        cmd_filter()
    elif args.check:
        cmd_check()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
