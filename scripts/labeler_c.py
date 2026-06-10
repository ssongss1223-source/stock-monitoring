#!/usr/bin/env python3
"""
Track C label builder — computes 22 label candidates for backtest_labels_c table.

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
    "label_3d_trend_start_atr",
    "label_5d_7pct_first",
    "label_5d_10pct_first_c",
    "label_2d_5pct_first",
    "label_1d_5pct_first",
    "label_3d_return_top10pct",
    "label_3d_return_top20pct",
    "label_5d_return_top10pct",
    "label_5d_return_top20pct",
    "label_3d_market_excess_top20pct",
    "label_3d_sector_excess_top30pct",
    "label_5d_market_excess_top20pct",
    "label_5d_sector_excess_top20pct",
    "label_5d_dual_excess",
    "label_3d_bb_upper_break",
    "label_3d_range_breakout_20d",
    "label_3d_bb_squeeze_breakout",
    "label_5d_bb_squeeze_breakout",
    "label_5d_range_breakout_20d",
    "label_5d_ma20_reclaim_trend",
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


def _compute_bb_series(
    closes: np.ndarray,
    window: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute rolling 20-day BB upper, lower, mid for a closes array.
    Returns (bb_upper, bb_lower, bb_mid) arrays with NaN where insufficient data.
    """
    n = len(closes)
    bb_upper = np.full(n, np.nan)
    bb_lower = np.full(n, np.nan)
    bb_mid = np.full(n, np.nan)

    for i in range(window - 1, n):
        w = closes[i - window + 1 : i + 1]
        m = w.mean()
        s = w.std(ddof=1)
        bb_mid[i] = m
        bb_upper[i] = m + 2 * s
        bb_lower[i] = m - 2 * s

    return bb_upper, bb_lower, bb_mid


def _label_ticker(
    ticker_hist: pd.DataFrame,
    signal_date: date,
    atr: Optional[float],
) -> dict:
    """
    Compute all per-ticker labels and metrics.

    Args:
        ticker_hist: DataFrame with [date, open, high, low, close] sorted by date
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
    result["label_1d_5pct_first"] = _label_first_to_hit(
        future_closes[:1].tolist(), entry_price, 0.05, 0.025
    )
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

    # ATR-based label
    if atr and atr > 0 and len(future_closes) >= 3:
        result["label_3d_trend_start_atr"] = _label_first_to_hit(
            future_closes[:3].tolist(),
            entry_price,
            1.2 * atr / entry_price,
            1.0 * atr / entry_price,
        )
    else:
        result["label_3d_trend_start_atr"] = None

    # ── Group 2: Technical breakout labels ──

    # BB upper break (3d, 5d)
    if len(past_closes) >= 20:
        _, _, bb_mid = _compute_bb_series(past_closes)
        # Get last 20 closes
        last_20_closes = past_closes[-20:]
        bb_mid_today = last_20_closes.mean()
        bb_std = last_20_closes.std(ddof=1)
        bb_upper_today = bb_mid_today + 2 * bb_std

        result["label_3d_bb_upper_break"] = (
            True
            if len(future_closes) >= 3 and np.max(future_closes[:3]) > bb_upper_today
            else (False if len(future_closes) >= 3 else None)
        )
        result["label_5d_bb_upper_break"] = (
            True
            if len(future_closes) >= 5 and np.max(future_closes[:5]) > bb_upper_today
            else (False if len(future_closes) >= 5 else None)
        )
    else:
        result["label_3d_bb_upper_break"] = None
        result["label_5d_bb_upper_break"] = None

    # BB squeeze (3d, 5d)
    if len(past_closes) >= 252 and len(future_closes) >= 3:
        bb_upper_series, bb_lower_series, bb_mid_series = _compute_bb_series(
            past_closes
        )
        # Current squeeze metrics
        bb_width_today = (bb_upper_series[-1] - bb_lower_series[-1]) / bb_mid_series[-1]

        # Get all bb_width values from past 252 days
        bb_widths = (
            (bb_upper_series - bb_lower_series) / bb_mid_series
        )  # array with NaNs
        bb_widths_valid = bb_widths[~np.isnan(bb_widths)]

        if len(bb_widths_valid) >= 50:  # min 40 days required per spec
            p30 = np.percentile(bb_widths_valid, 30)
            is_squeeze = bb_width_today <= p30
            bb_upper_today = bb_upper_series[-1]

            result["label_3d_bb_squeeze_breakout"] = (
                True
                if is_squeeze
                and len(future_closes) >= 3
                and np.max(future_closes[:3]) > bb_upper_today
                else (
                    False
                    if is_squeeze and len(future_closes) >= 3
                    else None
                )
            )
            result["label_5d_bb_squeeze_breakout"] = (
                True
                if is_squeeze
                and len(future_closes) >= 5
                and np.max(future_closes[:5]) > bb_upper_today
                else (
                    False
                    if is_squeeze and len(future_closes) >= 5
                    else None
                )
            )
        else:
            result["label_3d_bb_squeeze_breakout"] = None
            result["label_5d_bb_squeeze_breakout"] = None
    else:
        result["label_3d_bb_squeeze_breakout"] = None
        result["label_5d_bb_squeeze_breakout"] = None

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

    # MA20 reclaim trend (5d)
    if len(past_closes) >= 20 and len(future_closes) >= 5:
        ma20_today = past_closes[-20:].mean()
        close_today = past_closes[-1]
        near_ma20 = abs(close_today / ma20_today - 1) <= 0.03

        # MA20 at T+5: mean of closes from T-14 to T+5 (20 days)
        # T-14 is past_closes[-14], T+5 is future_closes[4]
        if len(past_closes) >= 14:
            ma20_at_t5 = (
                np.concatenate([past_closes[-14:], future_closes[:6]]).mean()
            )
            return_5d_val = result["return_5d"]
            result["label_5d_ma20_reclaim_trend"] = (
                True
                if near_ma20 and future_closes[4] > ma20_at_t5 and return_5d_val > 0
                else (
                    False
                    if near_ma20
                    else None
                )
            )
        else:
            result["label_5d_ma20_reclaim_trend"] = None
    else:
        result["label_5d_ma20_reclaim_trend"] = None

    # Cross-sectional and excess labels computed later (placeholder None)
    result["label_3d_return_top10pct"] = None
    result["label_3d_return_top20pct"] = None
    result["label_5d_return_top10pct"] = None
    result["label_5d_return_top20pct"] = None
    result["label_3d_market_excess_top20pct"] = None
    result["label_3d_sector_excess_top30pct"] = None
    result["label_5d_market_excess_top20pct"] = None
    result["label_5d_sector_excess_top20pct"] = None
    result["label_5d_dual_excess"] = None

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
        SELECT ticker, date, open, high, low, close
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

    # Load sector info for cross-sectional calculations
    sector_query = """
        SELECT ticker, sector
        FROM ticker_master
    """
    sector_rows = conn.execute(sector_query).fetchall()
    sector_dict = {row[0]: row[1] for row in sector_rows}

    # Load KOSPI data for market excess
    kospi_query = f"""
        SELECT date, close
        FROM market_index
        WHERE ticker = '1001' AND date >= '{signal_date_str}' AND date <= '{lookahead_date}'
        ORDER BY date
    """
    kospi_df = conn.execute(kospi_query).df()
    if not kospi_df.empty:
        kospi_df["date"] = pd.to_datetime(kospi_df["date"]).dt.date
    kospi_by_date = {row[0]: row[1] for row in kospi_df.itertuples(index=False)}

    # Compute per-ticker labels
    ticker_results = {}

    for ticker in tickers:
        ticker_hist = ohlcv_df[ohlcv_df["ticker"] == ticker].reset_index(drop=True)
        if len(ticker_hist) < 1:
            continue

        atr = atr_dict.get(ticker)
        result = _label_ticker(ticker_hist, signal_date, atr)
        ticker_results[ticker] = result

    # Compute cross-sectional rank labels (return_3d, return_5d)
    returns_3d = [
        r["return_3d"]
        for r in ticker_results.values()
        if r["return_3d"] is not None
    ]
    returns_5d = [
        r["return_5d"]
        for r in ticker_results.values()
        if r["return_5d"] is not None
    ]

    if returns_3d:
        p90_3d = np.percentile(returns_3d, 90)
        p80_3d = np.percentile(returns_3d, 80)
    else:
        p90_3d = p80_3d = None

    if returns_5d:
        p90_5d = np.percentile(returns_5d, 90)
        p80_5d = np.percentile(returns_5d, 80)
    else:
        p90_5d = p80_5d = None

    for ticker, result in ticker_results.items():
        if result["return_3d"] is not None and p90_3d is not None:
            result["label_3d_return_top10pct"] = result["return_3d"] >= p90_3d
            result["label_3d_return_top20pct"] = result["return_3d"] >= p80_3d
        if result["return_5d"] is not None and p90_5d is not None:
            result["label_5d_return_top10pct"] = result["return_5d"] >= p90_5d
            result["label_5d_return_top20pct"] = result["return_5d"] >= p80_5d

    # Compute market excess labels
    # KOSPI return: base = close at signal_date, T+3 = 3rd future close, T+5 = 5th future close
    kospi_base = kospi_by_date.get(signal_date)

    # Find T+3 and T+5 dates (3 and 5 trading days after signal_date)
    if kospi_base:
        kospi_closes_after = sorted(
            [d for d in kospi_by_date.keys() if d > signal_date]
        )

        if len(kospi_closes_after) >= 3:
            kospi_t3_close = kospi_by_date[kospi_closes_after[2]]
            kospi_ret_3d = (kospi_t3_close - kospi_base) / kospi_base
        else:
            kospi_ret_3d = None

        if len(kospi_closes_after) >= 5:
            kospi_t5_close = kospi_by_date[kospi_closes_after[4]]
            kospi_ret_5d = (kospi_t5_close - kospi_base) / kospi_base
        else:
            kospi_ret_5d = None

        # Compute excess returns
        excess_3d = []
        excess_5d = []
        for ticker, result in ticker_results.items():
            if kospi_ret_3d is not None and result["return_3d"] is not None:
                excess_3d.append(result["return_3d"] - kospi_ret_3d)
            if kospi_ret_5d is not None and result["return_5d"] is not None:
                excess_5d.append(result["return_5d"] - kospi_ret_5d)

        if excess_3d:
            p80_excess_3d = np.percentile(excess_3d, 80)
        else:
            p80_excess_3d = None

        if excess_5d:
            p80_excess_5d = np.percentile(excess_5d, 80)
        else:
            p80_excess_5d = None

        # Apply market excess labels
        for ticker, result in ticker_results.items():
            if (
                kospi_ret_3d is not None
                and result["return_3d"] is not None
                and p80_excess_3d is not None
            ):
                exc_3d = result["return_3d"] - kospi_ret_3d
                result["label_3d_market_excess_top20pct"] = exc_3d >= p80_excess_3d

            if (
                kospi_ret_5d is not None
                and result["return_5d"] is not None
                and p80_excess_5d is not None
            ):
                exc_5d = result["return_5d"] - kospi_ret_5d
                result["label_5d_market_excess_top20pct"] = exc_5d >= p80_excess_5d

            # Dual excess (both, not percentile)
            if (
                kospi_ret_5d is not None
                and result["return_5d"] is not None
                and result["return_5d"] > kospi_ret_5d
            ):
                sector = sector_dict.get(ticker)
                # Compute sector avg for this date/sector
                sector_returns_5d = [
                    r["return_5d"]
                    for t, r in ticker_results.items()
                    if sector_dict.get(t) == sector and r["return_5d"] is not None
                ]
                if sector_returns_5d:
                    sector_avg_5d = np.mean(sector_returns_5d)
                    result["label_5d_dual_excess"] = (
                        result["return_5d"] > kospi_ret_5d
                        and result["return_5d"] > sector_avg_5d
                    )

    # Compute sector excess labels
    for ticker, result in ticker_results.items():
        sector = sector_dict.get(ticker)
        if not sector:
            continue

        # Compute sector avg for 3d and 5d
        sector_returns_3d = [
            r["return_3d"]
            for t, r in ticker_results.items()
            if sector_dict.get(t) == sector and r["return_3d"] is not None
        ]
        sector_returns_5d = [
            r["return_5d"]
            for t, r in ticker_results.items()
            if sector_dict.get(t) == sector and r["return_5d"] is not None
        ]

        sector_avg_3d = (
            np.mean(sector_returns_3d) if sector_returns_3d else None
        )
        sector_avg_5d = (
            np.mean(sector_returns_5d) if sector_returns_5d else None
        )

        # Sector excess: return - sector_avg
        if result["return_3d"] is not None and sector_avg_3d is not None:
            sector_excess_3d = result["return_3d"] - sector_avg_3d
        else:
            sector_excess_3d = None

        if result["return_5d"] is not None and sector_avg_5d is not None:
            sector_excess_5d = result["return_5d"] - sector_avg_5d
        else:
            sector_excess_5d = None

        # Compute percentile ranks for sector excess
        all_sector_excess_3d = []
        all_sector_excess_5d = []

        for t, r in ticker_results.items():
            t_sector = sector_dict.get(t)
            if not t_sector:
                continue
            t_sector_avg_3d = np.mean(
                [
                    r2["return_3d"]
                    for t2, r2 in ticker_results.items()
                    if sector_dict.get(t2) == t_sector and r2["return_3d"] is not None
                ]
            ) if [
                r2["return_3d"]
                for t2, r2 in ticker_results.items()
                if sector_dict.get(t2) == t_sector and r2["return_3d"] is not None
            ] else None
            t_sector_avg_5d = np.mean(
                [
                    r2["return_5d"]
                    for t2, r2 in ticker_results.items()
                    if sector_dict.get(t2) == t_sector and r2["return_5d"] is not None
                ]
            ) if [
                r2["return_5d"]
                for t2, r2 in ticker_results.items()
                if sector_dict.get(t2) == t_sector and r2["return_5d"] is not None
            ] else None

            if r["return_3d"] is not None and t_sector_avg_3d is not None:
                all_sector_excess_3d.append(r["return_3d"] - t_sector_avg_3d)
            if r["return_5d"] is not None and t_sector_avg_5d is not None:
                all_sector_excess_5d.append(r["return_5d"] - t_sector_avg_5d)

        if all_sector_excess_3d:
            p70_sector_excess_3d = np.percentile(all_sector_excess_3d, 70)
            if (
                sector_excess_3d is not None
                and sector_excess_3d >= p70_sector_excess_3d
            ):
                result["label_3d_sector_excess_top30pct"] = True
            elif sector_excess_3d is not None:
                result["label_3d_sector_excess_top30pct"] = False

        if all_sector_excess_5d:
            p80_sector_excess_5d = np.percentile(all_sector_excess_5d, 80)
            if (
                sector_excess_5d is not None
                and sector_excess_5d >= p80_sector_excess_5d
            ):
                result["label_5d_sector_excess_top20pct"] = True
            elif sector_excess_5d is not None:
                result["label_5d_sector_excess_top20pct"] = False

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


def cmd_build(start_str: Optional[str], end_str: Optional[str]) -> None:
    """Build labels for date range."""
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

    insert_sql = """
        INSERT OR REPLACE INTO backtest_labels_c (
            signal_date, ticker, entry_price, return_2d, return_3d, return_5d,
            max_drawdown_2d, max_drawdown_3d, max_drawdown_5d,
            label_3d_5pct_first, label_3d_10pct_first_c, label_3d_trend_start_atr,
            label_5d_7pct_first, label_5d_10pct_first_c, label_2d_5pct_first, label_1d_5pct_first,
            label_3d_return_top10pct, label_3d_return_top20pct,
            label_5d_return_top10pct, label_5d_return_top20pct,
            label_3d_market_excess_top20pct, label_3d_sector_excess_top30pct,
            label_5d_market_excess_top20pct, label_5d_sector_excess_top20pct,
            label_5d_dual_excess,
            label_3d_bb_upper_break, label_3d_range_breakout_20d, label_3d_bb_squeeze_breakout,
            label_5d_bb_squeeze_breakout, label_5d_range_breakout_20d, label_5d_ma20_reclaim_trend
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def _to_values(row):
        return (
            row["signal_date"], row["ticker"], row["entry_price"],
            row["return_2d"], row["return_3d"], row["return_5d"],
            row["max_drawdown_2d"], row["max_drawdown_3d"], row["max_drawdown_5d"],
            row["label_3d_5pct_first"], row["label_3d_10pct_first_c"], row["label_3d_trend_start_atr"],
            row["label_5d_7pct_first"], row["label_5d_10pct_first_c"], row["label_2d_5pct_first"],
            row["label_1d_5pct_first"],
            row["label_3d_return_top10pct"], row["label_3d_return_top20pct"],
            row["label_5d_return_top10pct"], row["label_5d_return_top20pct"],
            row["label_3d_market_excess_top20pct"], row["label_3d_sector_excess_top30pct"],
            row["label_5d_market_excess_top20pct"], row["label_5d_sector_excess_top20pct"],
            row["label_5d_dual_excess"],
            row["label_3d_bb_upper_break"], row["label_3d_range_breakout_20d"],
            row["label_3d_bb_squeeze_breakout"], row["label_5d_bb_squeeze_breakout"],
            row["label_5d_range_breakout_20d"], row["label_5d_ma20_reclaim_trend"],
        )

    # Process each date, flush every 50 dates to avoid holding all rows in memory
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

    args = parser.parse_args()

    if args.build:
        cmd_build(args.start, args.end)
    elif args.filter:
        cmd_filter()
    elif args.check:
        cmd_check()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
