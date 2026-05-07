"""
feature 조합 sweep + walk-forward validation

Usage:
    python -m backtest.optimizer --days 3 --pct 0.03
"""
import argparse
import itertools
import logging

import pandas as pd

from backtest.validator import build_matrix, sample_dates

logger = logging.getLogger(__name__)

# validator lift 결과 기준 내림차순 (순서가 sweep 우선순위에 영향)
LIFT_ORDER = [
    "ichimoku_triple_positive",
    "near_52w_high_5pct",
    "price_above_120ma",
    "price_above_240ma",
    "ma20_above_ma60",
    "ma120_uptrend",
    "price_above_60ma",
    "ma60_above_ma120",
    "volume_surge",
    "price_above_20ma",
    "has_pattern",
    "ichimoku_cloud_support",
]


def _apply_combo(df: pd.DataFrame, combo: tuple[str, ...]) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for f in combo:
        mask &= df[f].astype(bool)
    return mask


def sweep(df: pd.DataFrame, top_n: int = 6, min_samples: int = 30) -> pd.DataFrame:
    """top_n개 feature의 1~3개 AND 조합 sweep."""
    features = [f for f in LIFT_ORDER if f in df.columns][:top_n]
    base_rate = df["label"].mean()
    rows = []

    for size in (1, 2, 3):
        for combo in itertools.combinations(features, size):
            mask = _apply_combo(df, combo)
            n = int(mask.sum())
            if n < min_samples:
                continue
            win_rate = float(df.loc[mask, "label"].mean())
            rows.append({
                "combo": " & ".join(combo),
                "n": n,
                "coverage": round(n / len(df), 3),
                "win_rate": round(win_rate, 4),
                "lift": round(win_rate / base_rate if base_rate > 0 else 0, 3),
            })

    return pd.DataFrame(rows).sort_values("lift", ascending=False).reset_index(drop=True)


def walk_forward(
    matrix: pd.DataFrame,
    dates: list[str],
    n_train: int = 8,
    top_n: int = 6,
    min_samples: int = 20,
) -> pd.DataFrame:
    """
    이미 빌드된 matrix를 날짜 기준으로 분할해 walk-forward 평가.
    fold 1: dates[:n_train] 학습, dates[n_train:] 검증
    fold 2: dates[2:n_train+2] 학습, dates[n_train+2:] 검증 (날짜 충분 시)
    """
    n = len(dates)
    folds = []
    if n > n_train:
        folds.append((dates[:n_train], dates[n_train:]))
    if n >= n_train + 4:
        folds.append((dates[2:n_train + 2], dates[n_train + 2:]))
    if not folds:
        folds = [(dates[:max(n - 2, 1)], dates[max(n - 2, 1):])]

    results = []
    for fold_i, (train_dates, test_dates) in enumerate(folds, 1):
        train_df = matrix[matrix["signal_date"].isin(train_dates)]
        test_df = matrix[matrix["signal_date"].isin(test_dates)]

        print(f"\n[Fold {fold_i}] 학습 {len(train_dates)}일 / 검증 {len(test_dates)}일")
        print(f"  학습: {train_dates[0]} ~ {train_dates[-1]}  ({len(train_df):,}건)")
        print(f"  검증: {test_dates[0]} ~ {test_dates[-1]}  ({len(test_df):,}건)")

        sweep_df = sweep(train_df, top_n, min_samples)
        if sweep_df.empty:
            print("  유효 combo 없음")
            continue

        best_combo = tuple(sweep_df.iloc[0]["combo"].split(" & "))
        is_lift = sweep_df.iloc[0]["lift"]
        is_win = sweep_df.iloc[0]["win_rate"]
        print(f"  Best IS: {' & '.join(best_combo)}  win={is_win:.1%}  lift={is_lift:.3f}")

        oos_mask = _apply_combo(test_df, best_combo)
        oos_n = int(oos_mask.sum())
        oos_base = float(test_df["label"].mean())
        oos_win = float(test_df.loc[oos_mask, "label"].mean()) if oos_n > 0 else 0.0
        oos_lift = oos_win / oos_base if oos_base > 0 else 0.0
        print(f"  OOS:     n={oos_n}  win={oos_win:.1%}  lift={oos_lift:.3f}  (base={oos_base:.1%})")

        results.append({
            "fold": fold_i,
            "combo": " & ".join(best_combo),
            "is_win": round(is_win, 4),
            "is_lift": round(is_lift, 3),
            "oos_n": oos_n,
            "oos_win": round(oos_win, 4),
            "oos_lift": round(oos_lift, 3),
        })

    return pd.DataFrame(results)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="feature 조합 sweep + walk-forward")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--pct", type=float, default=0.03)
    p.add_argument("--n-dates", type=int, default=12)
    p.add_argument("--top-features", type=int, default=6)
    p.add_argument("--min-samples", type=int, default=30)
    args = p.parse_args()

    dates = sample_dates(args.n_dates)
    print(f"날짜 ({len(dates)}개): {dates}")

    matrix = build_matrix(dates, args.days, args.pct)
    base_rate = matrix["label"].mean()
    print(f"\n전체 샘플: {len(matrix):,}건  기준선: {base_rate:.1%}")

    print("\n─── 전체 기간 combination sweep (top 15) ───")
    sweep_df = sweep(matrix, args.top_features, args.min_samples)
    print(sweep_df.head(15).to_string(index=False))

    print("\n─── Walk-forward validation ───")
    wf_df = walk_forward(matrix, dates, top_n=args.top_features, min_samples=args.min_samples)
    if not wf_df.empty:
        print("\n요약:")
        print(wf_df.to_string(index=False))


if __name__ == "__main__":
    main()
