"""
lr_base OOF 계산 + 최종 모델 학습.

기존 XGB/LGBM/ET OOF(oof_predictions.parquet)를 재활용하고
LR base만 새로 계산한다. 상관계수 출력 후 최종 모델 저장.

Usage:
    python scripts/compute_lr_base.py
    python scripts/compute_lr_base.py --feature-matrix data/feature_matrix.parquet
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

_TARGETS = [
    "label_3d_3pct_clean", "label_3d_5pct_clean", "label_3d_10pct_clean",
    "label_5d_3pct_clean", "label_5d_5pct_clean", "label_5d_10pct_clean",
    "label_10d_3pct_clean", "label_10d_5pct_clean", "label_10d_10pct_clean",
    "label_first_3d_3pct", "label_first_3d_5pct", "label_first_3d_10pct",
    "label_first_5d_3pct", "label_first_5d_5pct", "label_first_5d_10pct",
    "label_first_10d_3pct", "label_first_10d_5pct", "label_first_10d_10pct",
]

_DROP = {
    "signal_date", "ticker", "entry_price", "close",
    "max_close_3d", "max_close_5d", "max_close_10d",
    "max_drawdown_3d", "max_drawdown_5d", "max_drawdown_10d",
    "return_3d", "return_5d", "return_10d",
    "c2_3d_3pct", "c2_3d_5pct",
    "c2_5d_3pct", "c2_5d_5pct", "c2_5d_10pct",
    "c2_10d_3pct", "c2_10d_5pct", "c2_10d_10pct",
}

_BASE_OOF_PREFIXES = ["xgb", "lgbm", "et"]


def _feature_cols(df: pd.DataFrame) -> list[str]:
    label_cols = {c for c in df.columns if c.startswith("label_")}
    return [c for c in df.columns if c not in _DROP and c not in label_cols]


def _lr_base_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> tuple[list[float], np.ndarray]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_aucs: list[float] = []
    oof = np.full(len(X), np.nan)
    for tr_idx, va_idx in tscv.split(X):
        X_tr = X.iloc[tr_idx].fillna(0)
        X_va = X.iloc[va_idx].fillna(0)
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_va_s = scaler.transform(X_va)
        m = LogisticRegression(C=0.1, max_iter=1000, class_weight="balanced", random_state=42)
        m.fit(X_tr_s, y_tr)
        prob = m.predict_proba(X_va_s)[:, 1]
        oof[va_idx] = prob
        fold_aucs.append(roc_auc_score(y_va, prob))
    return fold_aucs, oof


def _lr_base_final(X: pd.DataFrame, y: pd.Series) -> tuple:
    X_f = X.fillna(0)
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_f)
    m = LogisticRegression(C=0.1, max_iter=1000, class_weight="balanced", random_state=42)
    m.fit(X_s, y)
    return m, scaler, list(X.columns)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--feature-matrix", default="data/feature_matrix.parquet")
    args = p.parse_args()

    df = pd.read_parquet(args.feature_matrix)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df.sort_values("signal_date").reset_index(drop=True)

    fcols = _feature_cols(df)
    X = df[fcols]

    oof_path = Path("data/oof_predictions.parquet")
    oof_df = pd.read_parquet(oof_path) if oof_path.exists() else df[["signal_date", "ticker"]].copy()

    out_dir = Path("data/models")
    out_dir.mkdir(exist_ok=True)

    print(f"샘플: {len(df):,}건  피처: {len(fcols)}개")
    print(f"기간: {df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")
    print()

    corr_summary: list[dict] = []

    for target in _TARGETS:
        label_key = target.replace("label_", "")

        if target not in df.columns:
            print(f"  {label_key}: 라벨 컬럼 없음 — 건너뜀")
            continue

        y = df[target]

        print(f"{'='*60}")
        print(f"  {target}  (positive={y.mean():.1%})")

        # lr_base CV
        fold_aucs, lr_base_oof = _lr_base_cv(X, y)
        auc = float(np.mean(fold_aucs))
        print(f"  LR_B  fold AUC: {auc:.4f} ± {np.std(fold_aucs):.4f}")

        oof_df[f"lr_base_oof_{label_key}"] = lr_base_oof

        # 기존 OOF와 상관계수
        row: dict = {"label": label_key, "lr_base_auc": auc}
        corr_parts = []
        for prefix in _BASE_OOF_PREFIXES:
            col = f"{prefix}_oof_{label_key}"
            if col in oof_df.columns:
                oa = oof_df[col].values
                ob = lr_base_oof
                valid = ~(np.isnan(oa) | np.isnan(ob))
                if valid.sum() > 100:
                    r = float(np.corrcoef(oa[valid], ob[valid])[0, 1])
                    row[f"corr_lr_base_{prefix}"] = round(r, 3)
                    corr_parts.append(f"lr_base↔{prefix}:{r:.3f}")
        if corr_parts:
            print(f"  OOF 상관: {' | '.join(corr_parts)}")

        # xgb↔lgbm 등 기존 모델 간 상관도 출력 (참고용)
        for i, a in enumerate(_BASE_OOF_PREFIXES):
            for b in _BASE_OOF_PREFIXES[i+1:]:
                ca, cb = f"{a}_oof_{label_key}", f"{b}_oof_{label_key}"
                if ca in oof_df.columns and cb in oof_df.columns:
                    oa, ob = oof_df[ca].values, oof_df[cb].values
                    valid = ~(np.isnan(oa) | np.isnan(ob))
                    if valid.sum() > 100:
                        r = float(np.corrcoef(oa[valid], ob[valid])[0, 1])
                        row[f"corr_{a}_{b}"] = round(r, 3)

        corr_summary.append(row)

        # 최종 모델 학습 + 저장
        lr_base_m, lr_base_scaler, lr_base_cols = _lr_base_final(X, y)
        joblib.dump({"model": lr_base_m, "scaler": lr_base_scaler, "feat_cols": lr_base_cols},
                    str(out_dir / f"lr_base_label_{label_key}.pkl"))
        print(f"  저장: lr_base_label_{label_key}.pkl")
        print()

    # OOF 파일 업데이트
    oof_df.to_parquet(oof_path, index=False)
    print(f"OOF 업데이트: {oof_path}  shape={oof_df.shape}")

    # 상관계수 요약
    print()
    print(f"{'='*60}")
    print("  OOF 상관계수 요약 (lr_base 다양성)")
    print(f"{'='*60}")
    for r in corr_summary:
        xgb_r = r.get("corr_lr_base_xgb", float("nan"))
        lgbm_r = r.get("corr_lr_base_lgbm", float("nan"))
        et_r = r.get("corr_lr_base_et", float("nan"))
        print(f"  {r['label']:<28s}  lr↔xgb:{xgb_r:.3f}  lr↔lgbm:{lgbm_r:.3f}  lr↔et:{et_r:.3f}  AUC:{r['lr_base_auc']:.4f}")

    result_path = Path("data/lr_base_results.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(corr_summary, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {result_path}")


if __name__ == "__main__":
    main()
