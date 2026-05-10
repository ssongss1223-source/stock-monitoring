"""
feature별 lift 분석 — 여러 날짜 샘플링

lift = P(label=1 | feature=True) / P(label=1) (기준선 대비 상대 승률)

Usage:
    python -m backtest.validator --days 3 --pct 0.03
    python -m backtest.validator --days 3 --pct 0.03 --dates 2026-04-01,2026-04-08
"""
import argparse
import logging
from datetime import date, timedelta

import pandas as pd

from agents.technical_analysis import _detect_pattern, _ichimoku_flags, _ma_flags
from backtest.labeler import label_batch
from data.db import get_conn

logger = logging.getLogger(__name__)

LOOKBACK = 300

BOOL_FEATURES = [
    "price_above_20ma", "price_above_60ma", "price_above_120ma", "price_above_240ma",
    "ma20_above_ma60", "ma60_above_ma120", "ma120_uptrend", "near_52w_high_5pct",
    "ichimoku_triple_positive", "ichimoku_cloud_support", "ichimoku_cloud_break", "ichimoku_dead_cross",
    "has_pattern", "volume_surge",
]


def sample_dates(n: int = 12, exclude_recent_biz: int = 7) -> list[str]:
    """최근 3개월 중 매주 목요일 n개 추출. 최근 영업일 제외."""
    cutoff = date.today()
    count = 0
    while count < exclude_recent_biz:
        cutoff -= timedelta(days=1)
        if cutoff.weekday() < 5:
            count += 1

    start = cutoff - timedelta(days=90)
    thursdays = []
    d = start
    while d <= cutoff:
        if d.weekday() == 3:
            thursdays.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    if len(thursdays) <= n:
        return thursdays
    step = len(thursdays) / n
    return [thursdays[int(i * step)] for i in range(n)]


def _load_all_ohlcv(tickers: list[str] | None = None) -> pd.DataFrame:
    conn = get_conn(read_only=True)
    try:
        if tickers:
            placeholders = ", ".join("?" * len(tickers))
            df = conn.execute(
                f"SELECT ticker, date, open, high, low, close, volume "
                f"FROM ohlcv_daily WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
                tickers,
            ).df()
        else:
            df = conn.execute(
                "SELECT ticker, date, open, high, low, close, volume FROM ohlcv_daily ORDER BY ticker, date"
            ).df()
    finally:
        conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def _compute_features(df_slice: pd.DataFrame) -> dict:
    """일봉 slice (시간순 정렬) → feature dict."""
    close = df_slice["close"].astype(float).reset_index(drop=True)
    high = df_slice["high"].astype(float).reset_index(drop=True)
    low = df_slice["low"].astype(float).reset_index(drop=True)
    vol = df_slice["volume"].astype(float).reset_index(drop=True)

    feat = {}
    feat.update(_ma_flags(close))
    feat.update(_ichimoku_flags(close, high, low))

    pattern = _detect_pattern(close, high, low, vol)
    feat["has_pattern"] = pattern is not None

    if len(vol) >= 21:
        avg20 = float(vol.iloc[-21:-1].mean())
        ratio = float(vol.iloc[-1]) / avg20 if avg20 > 0 else 1.0
    else:
        ratio = 1.0
    feat["volume_surge"] = ratio >= 2.0

    return feat


def build_matrix(
    dates: list[str],
    hold_days: int,
    target_pct: float,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """날짜 리스트 × 종목 → feature + label 행렬. tickers=None이면 전체 종목."""
    df_all = _load_all_ohlcv(tickers)
    groups = {t: g.reset_index(drop=True) for t, g in df_all.groupby("ticker")}
    tickers_actual = list(groups.keys())

    rows = []
    for d in dates:
        d_ts = pd.Timestamp(d)

        pairs = [(t, d) for t in tickers_actual]
        labels_df = label_batch(pairs)
        if labels_df.empty:
            logger.warning("레이블 없음: %s", d)
            continue
        # wide format → hold_days 기준 max_high로 label 파생
        high_col = f"max_high_{hold_days}d" if hold_days in (3, 5, 10) else "max_high_3d"
        labels_df["label"] = (
            labels_df[high_col] >= labels_df["entry_price"] * (1 + target_pct)
        ).astype(int)
        label_map = dict(zip(labels_df["ticker"], labels_df["label"]))

        n_ok = 0
        for ticker in tickers_actual:
            label = label_map.get(ticker)
            if label is None:
                continue
            g = groups[ticker]
            past = g[g["date"] <= d_ts].tail(LOOKBACK)
            if len(past) < 60:
                continue

            feat = _compute_features(past)
            feat["signal_date"] = d
            feat["ticker"] = ticker
            feat["label"] = int(label)
            rows.append(feat)
            n_ok += 1

        logger.info("%s: %d종목 처리", d, n_ok)

    return pd.DataFrame(rows)


def compute_lift(df: pd.DataFrame) -> pd.DataFrame:
    base_rate = df["label"].mean()
    results = []

    for feat in BOOL_FEATURES:
        if feat not in df.columns:
            continue
        mask = df[feat].astype(bool)
        n_true, n_false = int(mask.sum()), int((~mask).sum())
        if n_true < 10 or n_false < 10:
            continue

        p_true = df.loc[mask, "label"].mean()
        p_false = df.loc[~mask, "label"].mean()
        lift = p_true / base_rate if base_rate > 0 else 0.0
        results.append({
            "feature": feat,
            "n_true": n_true,
            "win_rate(T)": round(p_true, 4),
            "win_rate(F)": round(p_false, 4),
            "lift": round(lift, 3),
        })

    print(f"\n기준선 승률: {base_rate:.1%}  |  분석 샘플: {len(df):,}건\n")
    return pd.DataFrame(results).sort_values("lift", ascending=False).reset_index(drop=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="feature별 lift 분석")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--pct", type=float, default=0.03)
    p.add_argument("--n-dates", type=int, default=12)
    p.add_argument("--dates", help="comma-separated YYYY-MM-DD")
    p.add_argument("--universe", choices=["live"], default=None,
                   help="live: 운영 유니버스(100~105종목)로 한정. 미지정 시 전체 종목.")
    args = p.parse_args()

    dates = args.dates.split(",") if args.dates else sample_dates(args.n_dates)
    print(f"분석 날짜 ({len(dates)}개): {dates}")

    universe_tickers: list[str] | None = None
    if args.universe == "live":
        from agents.universe_manager import UniverseManager
        universe_tickers = [t for t, _ in UniverseManager().get_universe()]
        print(f"운영 유니버스 {len(universe_tickers)}종목으로 한정")

    matrix = build_matrix(dates, args.days, args.pct, tickers=universe_tickers)
    if matrix.empty:
        print("분석 가능한 데이터 없음")
        return

    lift_df = compute_lift(matrix)
    print(lift_df.to_string(index=False))


if __name__ == "__main__":
    main()
