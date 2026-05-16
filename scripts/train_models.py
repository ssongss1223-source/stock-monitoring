"""
멀티모델 학습 — feature_matrix.parquet → XGBoost + LightGBM + (CatBoost)

Walk-forward: TimeSeriesSplit 5-fold, OOF 예측값 수집
평가: AUC, Precision@K, Return@K (soft voting / rank ensemble 포함)
저장:
  data/models/xgb_label_{label}.json
  data/models/lgbm_label_{label}.txt
  data/models/catboost_label_{label}.cbm  (catboost 설치 시)
  data/oof_predictions.parquet            (OOF 예측값 전체)
  data/model_meta.json                    (라벨별 최고 모델)

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
    print("lightgbm 미설치 - XGBoost만 학습합니다.")

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

_EVAL_K = [10, 20]  # Precision@K, Return@K 계산 기준

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


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _feature_cols(df: pd.DataFrame) -> list[str]:
    label_cols = {c for c in df.columns if c.startswith("label_")}
    return [c for c in df.columns if c not in _DROP and c not in label_cols]


def _spw(y_tr: pd.Series) -> float:
    return float((y_tr == 0).sum() / max((y_tr == 1).sum(), 1))


def _rank_normalize(oof: np.ndarray) -> np.ndarray:
    """OOF 확률값 → 0~1 rank 정규화 (NaN 보존)."""
    valid = ~np.isnan(oof)
    result = np.full_like(oof, np.nan)
    if valid.any():
        vals = oof[valid]
        ranks = np.argsort(np.argsort(vals)) + 1
        result[valid] = ranks / valid.sum()
    return result


def _precision_at_k(y_true: pd.Series, oof: np.ndarray, k: int) -> float:
    """OOF 유효 구간에서 상위 K 예측의 실제 positive 비율."""
    valid = ~np.isnan(oof)
    y_v = np.asarray(y_true)[valid]
    p_v = oof[valid]
    top_k = np.argsort(p_v)[-k:]
    return float(y_v[top_k].mean())


def _return_at_k(max_return: pd.Series, oof: np.ndarray, k: int) -> float:
    """OOF 유효 구간에서 상위 K 예측의 평균 수익률."""
    valid = ~np.isnan(oof)
    r_v = np.asarray(max_return)[valid]
    p_v = oof[valid]
    top_k = np.argsort(p_v)[-k:]
    return float(r_v[top_k].mean())


def _auc_from_oof(y_true: pd.Series, oof: np.ndarray) -> float:
    valid = ~np.isnan(oof)
    return float(roc_auc_score(np.asarray(y_true)[valid], oof[valid]))


# ── CV + OOF 수집 ─────────────────────────────────────────────────────────────

def _xgb_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> tuple[list[float], np.ndarray]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_aucs: list[float] = []
    oof = np.full(len(X), np.nan)
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = xgb.XGBClassifier(scale_pos_weight=_spw(y_tr), **_XGB_PARAMS)
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        prob = m.predict_proba(X_va)[:, 1]
        oof[va_idx] = prob
        fold_aucs.append(roc_auc_score(y_va, prob))
    return fold_aucs, oof


def _lgbm_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> tuple[list[float], np.ndarray]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_aucs: list[float] = []
    oof = np.full(len(X), np.nan)
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = lgb.LGBMClassifier(class_weight={0: 1, 1: _spw(y_tr)}, **_LGBM_PARAMS)
        m.fit(X_tr, y_tr, eval_set=[(X_va, y_va)])
        prob = m.predict_proba(X_va)[:, 1]
        oof[va_idx] = prob
        fold_aucs.append(roc_auc_score(y_va, prob))
    return fold_aucs, oof


def _catboost_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> tuple[list[float], np.ndarray]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_aucs: list[float] = []
    oof = np.full(len(X), np.nan)
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = CatBoostClassifier(scale_pos_weight=_spw(y_tr), **_CATBOOST_PARAMS)
        m.fit(X_tr, y_tr, eval_set=(X_va, y_va))
        prob = m.predict_proba(X_va)[:, 1]
        oof[va_idx] = prob
        fold_aucs.append(roc_auc_score(y_va, prob))
    return fold_aucs, oof


# ── 최종 모델 학습 ────────────────────────────────────────────────────────────

def _xgb_final(X: pd.DataFrame, y: pd.Series) -> xgb.XGBClassifier:
    n_val = max(int(len(X) * 0.2), 1)
    m = xgb.XGBClassifier(scale_pos_weight=_spw(y.iloc[:-n_val]), **_XGB_PARAMS)
    m.fit(X.iloc[:-n_val], y.iloc[:-n_val], eval_set=[(X.iloc[-n_val:], y.iloc[-n_val:])], verbose=False)
    return m


def _lgbm_final(X: pd.DataFrame, y: pd.Series) -> "lgb.LGBMClassifier":
    n_val = max(int(len(X) * 0.2), 1)
    m = lgb.LGBMClassifier(class_weight={0: 1, 1: _spw(y.iloc[:-n_val])}, **_LGBM_PARAMS)
    m.fit(X.iloc[:-n_val], y.iloc[:-n_val], eval_set=[(X.iloc[-n_val:], y.iloc[-n_val:])])
    return m


def _catboost_final(X: pd.DataFrame, y: pd.Series) -> "CatBoostClassifier":
    n_val = max(int(len(X) * 0.2), 1)
    m = CatBoostClassifier(scale_pos_weight=_spw(y.iloc[:-n_val]), **_CATBOOST_PARAMS)
    m.fit(X.iloc[:-n_val], y.iloc[:-n_val], eval_set=(X.iloc[-n_val:], y.iloc[-n_val:]))
    return m


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

    # Step 5: OOF 저장용 DataFrame 기반 컬럼 준비
    oof_df = df[["signal_date", "ticker", "entry_price",
                 "max_high_3d", "max_high_5d", "max_high_10d"]].copy()
    for t in _TARGETS:
        oof_df[t] = df[t]

    summary: list[dict] = []
    model_meta: dict[str, dict] = {}  # {label_key: {best, precision@K, ...}}

    for target in _TARGETS:
        label_key = target.replace("label_", "")          # "3d_5pct"
        hold_period = label_key.split("_")[0]              # "3d"
        max_high_col = f"max_high_{hold_period}"           # "max_high_3d"

        y = df[target]
        # 수익률: max_high / entry_price - 1 (entry_price=0 방어)
        max_return = (df[max_high_col] / df["entry_price"].replace(0, np.nan) - 1)

        print(f"{'='*62}")
        print(f"  {target}  (positive={y.mean():.1%})")
        print(f"{'='*62}")

        row: dict = {"target": target}
        model_oofs: dict[str, np.ndarray] = {}  # CV 완료 후 앙상블용

        # ── XGB CV + OOF ─────────────────────────────────────────────────
        xgb_fold_aucs, xgb_oof = _xgb_cv(X, y)
        model_oofs["xgb"] = xgb_oof
        oof_df[f"xgb_oof_{label_key}"] = xgb_oof
        row["xgb_auc"] = float(np.mean(xgb_fold_aucs))
        print(f"  XGB   fold AUC: {np.mean(xgb_fold_aucs):.4f} ± {np.std(xgb_fold_aucs):.4f}")

        # ── LGBM CV + OOF ────────────────────────────────────────────────
        if HAS_LGBM:
            lgbm_fold_aucs, lgbm_oof = _lgbm_cv(X, y)
            model_oofs["lgbm"] = lgbm_oof
            oof_df[f"lgbm_oof_{label_key}"] = lgbm_oof
            row["lgbm_auc"] = float(np.mean(lgbm_fold_aucs))
            print(f"  LGBM  fold AUC: {np.mean(lgbm_fold_aucs):.4f} ± {np.std(lgbm_fold_aucs):.4f}")

        # ── CatBoost CV + OOF ────────────────────────────────────────────
        if HAS_CATBOOST:
            cat_fold_aucs, cat_oof = _catboost_cv(X, y)
            model_oofs["catboost"] = cat_oof
            oof_df[f"catboost_oof_{label_key}"] = cat_oof
            row["catboost_auc"] = float(np.mean(cat_fold_aucs))
            print(f"  CatB  fold AUC: {np.mean(cat_fold_aucs):.4f} ± {np.std(cat_fold_aucs):.4f}")

        # ── Ensemble OOF (OOF 기반 — 재학습 없음) ────────────────────────
        all_oofs = list(model_oofs.values())
        if len(all_oofs) >= 2:
            soft_oof = np.nanmean(all_oofs, axis=0)
            rank_oof = np.nanmean([_rank_normalize(o) for o in all_oofs], axis=0)
            model_oofs["soft"] = soft_oof
            model_oofs["rank"] = rank_oof
            oof_df[f"soft_oof_{label_key}"] = soft_oof
            oof_df[f"rank_oof_{label_key}"] = rank_oof
            row["soft_auc"] = _auc_from_oof(y, soft_oof)
            row["rank_auc"] = _auc_from_oof(y, rank_oof)
            print(f"  Soft  OOF AUC:  {row['soft_auc']:.4f}  ← 앙상블")
            print(f"  Rank  OOF AUC:  {row['rank_auc']:.4f}  ← 앙상블")

        # ── Step 9: Precision@K, Return@K ────────────────────────────────
        print()
        for k in _EVAL_K:
            print(f"  {'모델':<12s} Prec@{k:<3d} Return@{k}")
            for name, oof in model_oofs.items():
                prec = _precision_at_k(y, oof, k)
                ret  = _return_at_k(max_return, oof, k)
                row[f"{name}_prec@{k}"] = prec
                row[f"{name}_ret@{k}"]  = ret
                print(f"  {name:<12s} {prec:>6.1%}   {ret:>+7.1%}")
            print()

        # ── Step 10: 라벨별 최고 모델 선택 (Precision@20 기준) ──────────
        base_models = [n for n in model_oofs if n not in ("soft", "rank")]
        if base_models:
            best_name = max(base_models, key=lambda n: row.get(f"{n}_prec@20", 0.0))
            best_prec = row.get(f"{best_name}_prec@20", 0.0)
            best_ret  = row.get(f"{best_name}_ret@20", 0.0)
            model_meta[label_key] = {
                "best": best_name,
                "precision@20": round(best_prec, 4),
                "return@20": round(best_ret, 4),
            }
            print(f"  → 최고 모델: {best_name.upper()}  (Prec@20={best_prec:.1%}, Ret@20={best_ret:+.1%})")

        # ── 최종 모델 학습 + 저장 ────────────────────────────────────────
        xgb_model = _xgb_final(X, y)
        xgb_model.save_model(str(out_dir / f"xgb_label_{label_key}.json"))

        if HAS_LGBM:
            lgbm_model = _lgbm_final(X, y)
            lgbm_model.booster_.save_model(str(out_dir / f"lgbm_label_{label_key}.txt"))

        if HAS_CATBOOST:
            cat_model = _catboost_final(X, y)
            cat_model.save_model(str(out_dir / f"catboost_label_{label_key}.cbm"))

        summary.append(row)
        print()

    # ── 전체 요약 테이블 ─────────────────────────────────────────────────────
    print(f"{'='*62}")
    print("  요약 (walk-forward AUC / Precision@20 / Return@20)")
    print(f"{'='*62}")
    model_names = ["xgb"] + (["lgbm"] if HAS_LGBM else []) + (["catboost"] if HAS_CATBOOST else [])
    if len(model_names) >= 2:
        model_names += ["soft", "rank"]

    header = f"  {'타겟':<22s}"
    for n in model_names:
        auc_key = f"{n}_auc" if n not in ("soft", "rank") else f"{n}_auc"
        header += f"  {n.upper():>5s}_AUC  P@20  R@20"
    print(header)
    for r in summary:
        line = f"  {r['target']:<22s}"
        for n in model_names:
            auc_key = f"{n}_auc" if n not in ("soft", "rank") else f"{n}_auc"
            auc   = r.get(auc_key, float("nan"))
            prec  = r.get(f"{n}_prec@20", float("nan"))
            ret   = r.get(f"{n}_ret@20", float("nan"))
            line += f"  {auc:>8.4f} {prec:>5.1%} {ret:>+6.1%}"
        print(line)

    # ── 파일 저장 ────────────────────────────────────────────────────────────
    # Step 5: OOF 예측값
    oof_path = Path("data/oof_predictions.parquet")
    oof_df.to_parquet(oof_path, index=False)
    print(f"\nOOF 저장: {oof_path}  shape={oof_df.shape}")

    # Step 10: 모델 메타 (라벨별 최고 모델)
    meta_path = Path("data/model_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(model_meta, f, ensure_ascii=False, indent=2)
    print(f"모델 메타 저장: {meta_path}")

    # 상세 결과
    result_path = Path("data/model_results.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"상세 결과 저장: {result_path}")


if __name__ == "__main__":
    main()
