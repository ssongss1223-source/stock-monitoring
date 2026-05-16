"""
멀티모델 학습 — feature_matrix.parquet → XGBoost + LightGBM + (CatBoost) + 앙상블 비교

Walk-forward: TimeSeriesSplit 5-fold
최종 모델: 전체 데이터 80% train / 20% early-stopping val
저장:
  data/models/xgb_label_{target}.json
  data/models/lgbm_label_{target}.txt
  data/models/catboost_label_{target}.cbm  (catboost 설치 시)

Usage:
    python scripts/train_models.py
    python scripts/train_models.py --feature-matrix data/fm_v2.parquet
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False
    print("lightgbm 미설치 — XGBoost만 학습합니다.")

try:
    from catboost import CatBoostClassifier
    HAS_CATBOOST = True
except ImportError:
    HAS_CATBOOST = False

_TARGETS = [
    "label_3d_3pct", "label_3d_5pct", "label_3d_10pct",
    "label_5d_3pct", "label_5d_5pct", "label_5d_10pct",
    "label_10d_3pct", "label_10d_5pct", "label_10d_10pct",
]

_DROP = {
    "signal_date", "ticker", "entry_price",
    "max_high_3d", "max_high_5d", "max_high_10d",
    "max_drawdown_3d", "max_drawdown_5d", "max_drawdown_10d",
    "return_3d", "return_5d", "return_10d",
}

_XGB_PARAMS = dict(
    n_estimators=500, max_depth=4, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8, min_child_weight=10,
    eval_metric="auc", early_stopping_rounds=30,
    random_state=42, n_jobs=-1,
)

_LGBM_PARAMS = dict(
    n_estimators=500, max_depth=4, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8, min_child_samples=20,
    metric="auc", early_stopping_rounds=30,
    random_state=42, n_jobs=-1, verbose=-1,
)

_CATBOOST_PARAMS = dict(
    iterations=500, depth=4, learning_rate=0.05,
    eval_metric="AUC", early_stopping_rounds=30,
    random_seed=42, verbose=0,
)


def _feature_cols(df: pd.DataFrame) -> list[str]:
    label_cols = {c for c in df.columns if c.startswith("label_")}
    return [c for c in df.columns if c not in _DROP and c not in label_cols]


def _spw(y_tr: pd.Series) -> float:
    return float((y_tr == 0).sum() / max((y_tr == 1).sum(), 1))


# ── XGBoost ──────────────────────────────────────────────────────────────────

def _xgb_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> list[float]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    aucs = []
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = xgb.XGBClassifier(scale_pos_weight=_spw(y_tr), **_XGB_PARAMS)
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        aucs.append(roc_auc_score(y_va, m.predict_proba(X_va)[:, 1]))
    return aucs


def _xgb_final(X: pd.DataFrame, y: pd.Series) -> xgb.XGBClassifier:
    n_val = max(int(len(X) * 0.2), 1)
    X_tr, X_va = X.iloc[:-n_val], X.iloc[-n_val:]
    y_tr, y_va = y.iloc[:-n_val], y.iloc[-n_val:]
    m = xgb.XGBClassifier(scale_pos_weight=_spw(y_tr), **_XGB_PARAMS)
    m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
    return m


# ── LightGBM ─────────────────────────────────────────────────────────────────

def _lgbm_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> list[float]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    aucs = []
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = lgb.LGBMClassifier(class_weight={0: 1, 1: _spw(y_tr)}, **_LGBM_PARAMS)
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)])
        aucs.append(roc_auc_score(y_va, m.predict_proba(X_va)[:, 1]))
    return aucs


def _lgbm_final(X: pd.DataFrame, y: pd.Series) -> "lgb.LGBMClassifier":
    n_val = max(int(len(X) * 0.2), 1)
    X_tr, X_va = X.iloc[:-n_val], X.iloc[-n_val:]
    y_tr, y_va = y.iloc[:-n_val], y.iloc[-n_val:]
    m = lgb.LGBMClassifier(class_weight={0: 1, 1: _spw(y_tr)}, **_LGBM_PARAMS)
    m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)])
    return m


# ── CatBoost ─────────────────────────────────────────────────────────────────

def _catboost_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> list[float]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    aucs = []
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = CatBoostClassifier(scale_pos_weight=_spw(y_tr), **_CATBOOST_PARAMS)
        m.fit(X_tr, y_tr, eval_set=(X_va, y_va))
        aucs.append(roc_auc_score(y_va, m.predict_proba(X_va)[:, 1]))
    return aucs


def _catboost_final(X: pd.DataFrame, y: pd.Series) -> "CatBoostClassifier":
    n_val = max(int(len(X) * 0.2), 1)
    X_tr, X_va = X.iloc[:-n_val], X.iloc[-n_val:]
    y_tr, y_va = y.iloc[:-n_val], y.iloc[-n_val:]
    m = CatBoostClassifier(scale_pos_weight=_spw(y_tr), **_CATBOOST_PARAMS)
    m.fit(X_tr, y_tr, eval_set=(X_va, y_va))
    return m


# ── 앙상블 AUC (CV 결과 기반 추정) ──────────────────────────────────────────

def _ensemble_cv_auc(
    X: pd.DataFrame, y: pd.Series,
    models: list[str], n_splits: int = 5,
) -> list[float]:
    """Walk-forward CV에서 앙상블 AUC 계산 (학습된 final model 재사용 아님, 별도 CV)."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    aucs = []
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]

        fold_probs = []
        if "xgb" in models:
            m = xgb.XGBClassifier(scale_pos_weight=_spw(y_tr), **_XGB_PARAMS)
            m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
            fold_probs.append(m.predict_proba(X_va)[:, 1])
        if "lgbm" in models and HAS_LGBM:
            m = lgb.LGBMClassifier(class_weight={0: 1, 1: _spw(y_tr)}, **_LGBM_PARAMS)
            m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)])
            fold_probs.append(m.predict_proba(X_va)[:, 1])
        if "catboost" in models and HAS_CATBOOST:
            m = CatBoostClassifier(scale_pos_weight=_spw(y_tr), **_CATBOOST_PARAMS)
            m.fit(X_tr, y_tr, eval_set=(X_va, y_va))
            fold_probs.append(m.predict_proba(X_va)[:, 1])

        if fold_probs:
            ens = np.mean(fold_probs, axis=0)
            aucs.append(roc_auc_score(y_va, ens))
    return aucs


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--feature-matrix", default="data/feature_matrix.parquet")
    args = p.parse_args()

    df = pd.read_parquet(args.feature_matrix)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df.sort_values("signal_date").reset_index(drop=True)

    fcols = _feature_cols(df)
    X = df[fcols]

    print(f"샘플: {len(df):,}건  피처: {len(fcols)}개")
    print(f"기간: {df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")
    print(f"모델: XGBoost" + (" + LightGBM" if HAS_LGBM else "") + (" + CatBoost" if HAS_CATBOOST else ""))
    print()

    out_dir = Path("data/models")
    out_dir.mkdir(exist_ok=True)
    summary = []

    active_models = ["xgb"] + (["lgbm"] if HAS_LGBM else []) + (["catboost"] if HAS_CATBOOST else [])

    for target in _TARGETS:
        y = df[target]
        pos_rate = y.mean()
        print(f"{'='*60}")
        print(f"  {target}  (positive={pos_rate:.1%})")
        print(f"{'='*60}")

        row: dict = {"target": target}

        # XGB
        xgb_aucs = _xgb_cv(X, y)
        row["xgb_auc"] = float(np.mean(xgb_aucs))
        print(f"  XGB   AUC: {np.mean(xgb_aucs):.4f} ± {np.std(xgb_aucs):.4f}")
        xgb_model = _xgb_final(X, y)
        xgb_path = out_dir / f"xgb_label_{target.replace('label_', '')}.json"
        xgb_model.save_model(str(xgb_path))

        # LightGBM
        if HAS_LGBM:
            lgbm_aucs = _lgbm_cv(X, y)
            row["lgbm_auc"] = float(np.mean(lgbm_aucs))
            print(f"  LGBM  AUC: {np.mean(lgbm_aucs):.4f} ± {np.std(lgbm_aucs):.4f}")
            lgbm_model = _lgbm_final(X, y)
            lgbm_path = out_dir / f"lgbm_label_{target.replace('label_', '')}.txt"
            lgbm_model.booster_.save_model(str(lgbm_path))

        # CatBoost
        if HAS_CATBOOST:
            cat_aucs = _catboost_cv(X, y)
            row["catboost_auc"] = float(np.mean(cat_aucs))
            print(f"  CatB  AUC: {np.mean(cat_aucs):.4f} ± {np.std(cat_aucs):.4f}")
            cat_model = _catboost_final(X, y)
            cat_path = out_dir / f"catboost_label_{target.replace('label_', '')}.cbm"
            cat_model.save_model(str(cat_path))

        # 앙상블 (2개 이상 모델 있을 때)
        if len(active_models) >= 2:
            ens_aucs = _ensemble_cv_auc(X, y, active_models)
            row["ensemble_auc"] = float(np.mean(ens_aucs))
            print(f"  ENS   AUC: {np.mean(ens_aucs):.4f} ± {np.std(ens_aucs):.4f}  ← 앙상블")

        summary.append(row)
        print()

    # 요약 테이블
    print(f"{'='*60}")
    print("  요약 (walk-forward mean AUC)")
    print(f"{'='*60}")
    header = f"  {'타겟':<22s}"
    if "xgb_auc" in summary[0]:     header += f" {'XGB':>7s}"
    if "lgbm_auc" in summary[0]:    header += f" {'LGBM':>7s}"
    if "catboost_auc" in summary[0]: header += f" {'CatB':>7s}"
    if "ensemble_auc" in summary[0]: header += f" {'ENS':>7s}"
    print(header)
    for r in summary:
        line = f"  {r['target']:<22s}"
        if "xgb_auc" in r:      line += f" {r['xgb_auc']:>7.4f}"
        if "lgbm_auc" in r:     line += f" {r['lgbm_auc']:>7.4f}"
        if "catboost_auc" in r: line += f" {r['catboost_auc']:>7.4f}"
        if "ensemble_auc" in r: line += f" {r['ensemble_auc']:>7.4f}"
        print(line)

    result_path = Path("data/model_results.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {result_path}")


if __name__ == "__main__":
    main()
