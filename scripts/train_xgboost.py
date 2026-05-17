"""
XGBoost walk-forward 학습 — feature_matrix.parquet → 3개 타겟

Walk-forward: signal_date 시간 순 정렬 후 TimeSeriesSplit 5-fold
최종 모델: 전체 데이터 80% train / 20% early-stopping val
저장: data/models/xgb_{target}.json, data/xgb_results.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

_TARGETS = [
    "label_3d_3pct", "label_3d_5pct", "label_3d_10pct",
    "label_5d_3pct", "label_5d_5pct", "label_5d_10pct",
    "label_10d_3pct", "label_10d_5pct", "label_10d_10pct",
    "label_3d_3pct_c2", "label_3d_5pct_c2",
    "label_5d_3pct_c2", "label_5d_5pct_c2", "label_5d_10pct_c2",
    "label_10d_3pct_c2", "label_10d_5pct_c2", "label_10d_10pct_c2",
]

# 피처에서 제외 (미래 데이터 / 식별자 / 절대 가격)
_DROP = {
    "signal_date", "ticker", "entry_price",
    "max_close_3d", "max_close_5d", "max_close_10d",
    "max_drawdown_3d", "max_drawdown_5d", "max_drawdown_10d",
    "return_3d", "return_5d", "return_10d",
    "c2_3d_3pct", "c2_3d_5pct",
    "c2_5d_3pct", "c2_5d_5pct", "c2_5d_10pct",
    "c2_10d_3pct", "c2_10d_5pct", "c2_10d_10pct",
}

_XGB_PARAMS = dict(
    n_estimators=500,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=10,   # 소규모 리프 방지 (과적합 억제)
    eval_metric="auc",
    early_stopping_rounds=30,
    random_state=42,
    n_jobs=-1,
)


def _feature_cols(df: pd.DataFrame) -> list[str]:
    label_cols = {c for c in df.columns if c.startswith("label_")}
    return [c for c in df.columns if c not in _DROP and c not in label_cols]


def _walk_forward(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> dict:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    aucs, aps, importances = [], [], []

    for fold, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]

        spw = (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)
        model = xgb.XGBClassifier(scale_pos_weight=spw, **_XGB_PARAMS)
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)

        prob = model.predict_proba(X_va)[:, 1]
        auc = roc_auc_score(y_va, prob)
        ap  = average_precision_score(y_va, prob)
        aucs.append(auc)
        aps.append(ap)
        importances.append(pd.Series(model.feature_importances_, index=X.columns))

        print(f"    fold {fold+1}  AUC={auc:.4f}  AP={ap:.4f}"
              f"  train={len(tr_idx):,}  val={len(va_idx):,}"
              f"  best_iter={model.best_iteration}")

    mean_fi = pd.concat(importances, axis=1).mean(axis=1).sort_values(ascending=False)
    return {
        "mean_auc": float(np.mean(aucs)),
        "std_auc":  float(np.std(aucs)),
        "mean_ap":  float(np.mean(aps)),
        "top_features": mean_fi.head(15).to_dict(),
    }


def _train_final(X: pd.DataFrame, y: pd.Series) -> xgb.XGBClassifier:
    n_val = max(int(len(X) * 0.2), 1)
    X_tr, X_va = X.iloc[:-n_val], X.iloc[-n_val:]
    y_tr, y_va = y.iloc[:-n_val], y.iloc[-n_val:]
    spw = (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)
    model = xgb.XGBClassifier(scale_pos_weight=spw, **_XGB_PARAMS)
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
    return model


def main() -> None:
    df = pd.read_parquet("data/feature_matrix.parquet")
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df.sort_values("signal_date").reset_index(drop=True)

    fcols = _feature_cols(df)
    X = df[fcols]

    print(f"샘플: {len(df):,}건  피처: {len(fcols)}개")
    print(f"기간: {df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")
    print(f"피처: {fcols}\n")

    out_dir = Path("data/models")
    out_dir.mkdir(exist_ok=True)
    all_results = []

    for target in _TARGETS:
        y = df[target]
        print(f"{'='*55}")
        print(f"  타겟: {target}  (positive={y.mean():.1%})")
        print(f"{'='*55}")

        res = _walk_forward(X, y)

        print(f"\n  AUC {res['mean_auc']:.4f} ± {res['std_auc']:.4f}   AP {res['mean_ap']:.4f}")
        print(f"  상위 피처 (fold 평균 importance):")
        for feat, imp in list(res["top_features"].items())[:10]:
            print(f"    {feat:<35s} {imp:.4f}")

        final = _train_final(X, y)
        model_path = out_dir / f"xgb_{target}.json"
        final.save_model(str(model_path))
        print(f"  최종 모델 저장: {model_path}  (best_iter={final.best_iteration})")

        res["target"] = target
        all_results.append(res)
        print()

    result_path = Path("data/xgb_results.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"{'='*55}")
    print("  요약")
    print(f"{'='*55}")
    print(f"  {'타겟':<22s} {'AUC':>8s} {'±':>6s} {'AP':>8s}")
    for r in all_results:
        print(f"  {r['target']:<22s} {r['mean_auc']:>8.4f} {r['std_auc']:>6.4f} {r['mean_ap']:>8.4f}")
    print(f"\n결과 저장: {result_path}")


if __name__ == "__main__":
    main()
