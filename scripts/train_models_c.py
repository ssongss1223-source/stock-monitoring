"""
Track C 멀티모델 학습 — fm_c.parquet → XGBoost + LightGBM + ExtraTrees + LR Stacking

Walk-forward: TimeSeriesSplit 5-fold, OOF 예측값 수집
저장:
  data/models_c/xgb_label_{label}.json
  data/models_c/lgbm_label_{label}.txt
  data/models_c/et_label_{label}.pkl
  data/models_c/lr_base_label_{label}.pkl
  data/models_c/lr_stacker_{label}.pkl
  data/models_c/feature_cols.json          (피처 컬럼 리스트)
  data/oof_predictions_c.parquet
  data/model_meta_c.json

Usage:
    python scripts/train_models_c.py
    python scripts/train_models_c.py --feature-matrix data/fm_c.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False
    print("lightgbm 미설치 - XGBoost만 학습합니다.")

from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import joblib

_TARGETS = [
    "label_3d_5pct_first", "label_3d_10pct_first_c", "label_3d_trend_start_atr",
    "label_5d_7pct_first", "label_5d_10pct_first_c",
    "label_2d_5pct_first", "label_1d_5pct_first",
    "label_3d_bb_upper_break", "label_3d_range_breakout_20d",
    "label_3d_bb_squeeze_breakout", "label_5d_bb_squeeze_breakout",
    "label_5d_range_breakout_20d",
    "label_3d_recover_pullback", "label_2d_volume_surge_5pct",
]

_DROP = {
    "signal_date", "ticker", "entry_price",
    "return_2d", "return_3d", "return_5d",
    "max_drawdown_2d", "max_drawdown_3d", "max_drawdown_5d",
}

_EVAL_K = [10, 20]

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
_ET_PARAMS = dict(
    n_estimators=200, max_depth=15, min_samples_leaf=10,
    max_features="sqrt", class_weight="balanced", random_state=42, n_jobs=-1,
)


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _feature_cols(df: pd.DataFrame) -> list[str]:
    label_cols = {c for c in df.columns if c.startswith("label_")}
    return [c for c in df.columns if c not in _DROP and c not in label_cols]


def _feat_hash(cols: list[str]) -> str:
    return hashlib.sha1(",".join(sorted(cols)).encode()).hexdigest()[:12]


def _spw(y_tr: pd.Series) -> float:
    return float((y_tr == 0).sum() / max((y_tr == 1).sum(), 1))


def _rank_normalize(oof: np.ndarray) -> np.ndarray:
    valid = ~np.isnan(oof)
    result = np.full_like(oof, np.nan)
    if valid.any():
        vals = oof[valid]
        ranks = np.argsort(np.argsort(vals)) + 1
        result[valid] = ranks / valid.sum()
    return result


def _precision_at_k(y_true: pd.Series, oof: np.ndarray, k: int) -> float:
    valid = ~np.isnan(oof)
    y_v = np.asarray(y_true)[valid]
    p_v = oof[valid]
    top_k = np.argsort(p_v)[-k:]
    return float(y_v[top_k].mean())


def _return_at_k(ret_col: pd.Series, oof: np.ndarray, k: int) -> float:
    valid = ~np.isnan(oof)
    r_v = np.asarray(ret_col)[valid]
    p_v = oof[valid]
    top_k = np.argsort(p_v)[-k:]
    return float(r_v[top_k].mean())


def _auc_from_oof(y_true: pd.Series, oof: np.ndarray) -> float:
    valid = ~np.isnan(oof)
    return float(roc_auc_score(np.asarray(y_true)[valid], oof[valid]))


def _brier_from_oof(y_true: pd.Series, oof: np.ndarray) -> float:
    valid = ~np.isnan(oof)
    return float(brier_score_loss(np.asarray(y_true)[valid], oof[valid]))


def _return_col_for_label(label_key: str, df_cols: set) -> str | None:
    """라벨명에서 보유 기간을 추출해 return_{period} 컬럼을 반환."""
    for period in ("5d", "3d", "2d"):
        if label_key.startswith(period):
            col = f"return_{period}"
            return col if col in df_cols else None
    return None


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


def _et_cv(X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> tuple[list[float], np.ndarray]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_aucs: list[float] = []
    oof = np.full(len(X), np.nan)
    for tr_idx, va_idx in tscv.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        m = ExtraTreesClassifier(**_ET_PARAMS)
        m.fit(X_tr.fillna(0), y_tr)
        prob = m.predict_proba(X_va.fillna(0))[:, 1]
        oof[va_idx] = prob
        fold_aucs.append(roc_auc_score(y_va, prob))
    return fold_aucs, oof


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


def _et_final(X: pd.DataFrame, y: pd.Series) -> ExtraTreesClassifier:
    m = ExtraTreesClassifier(**_ET_PARAMS)
    m.fit(X.fillna(0), y)
    return m


def _lr_base_final(X: pd.DataFrame, y: pd.Series) -> tuple:
    X_f = X.fillna(0)
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_f)
    m = LogisticRegression(C=0.1, max_iter=1000, class_weight="balanced", random_state=42)
    m.fit(X_s, y)
    return m, scaler, list(X.columns)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--feature-matrix", default="data/fm_c.parquet")
    args = p.parse_args()

    df = pd.read_parquet(args.feature_matrix)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df.sort_values("signal_date").reset_index(drop=True)

    # pd.NA(nullable boolean/integer) → int (XGBoost/sklearn 호환)
    for t in _TARGETS:
        if t in df.columns:
            df[t] = df[t].astype("float64").fillna(0).astype("int8")

    fcols = _feature_cols(df)
    X = df[fcols]
    df_cols = set(df.columns)

    print(f"샘플: {len(df):,}건  피처: {len(fcols)}개")
    print(f"기간: {df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")
    print(f"모델: XGBoost" + (" + LightGBM" if HAS_LGBM else "") + " + ExtraTrees + LR Stacking")
    print()

    out_dir = Path("data/models_c")
    out_dir.mkdir(exist_ok=True)

    # 피처 컬럼 리스트 저장 (orchestrator_c에서 사용)
    feat_cols_path = out_dir / "feature_cols.json"
    with open(feat_cols_path, "w", encoding="utf-8") as f:
        json.dump(fcols, f, ensure_ascii=False)
    print(f"피처 컬럼 저장: {feat_cols_path}  ({len(fcols)}개)")

    oof_cols = ["signal_date", "ticker"]
    for col in ["entry_price", "return_3d", "return_5d", "return_2d"]:
        if col in df.columns:
            oof_cols.append(col)
    oof_df = df[oof_cols].copy()
    for t in _TARGETS:
        if t in df.columns:
            oof_df[t] = df[t]

    summary: list[dict] = []
    model_meta: dict[str, dict] = {}

    for target in _TARGETS:
        if target not in df.columns:
            print(f"  건너뜀 (컬럼 없음): {target}")
            continue

        label_key = target.replace("label_", "")

        # ── 체크포인트 복구 ────────────────────────────────────────────────
        ckpt_oof = out_dir / f"oof_ckpt_{label_key}.parquet"
        ckpt_sum = out_dir / f"summary_ckpt_{label_key}.json"
        if (out_dir / f"xgb_label_{label_key}.json").exists() and ckpt_oof.exists() and ckpt_sum.exists():
            print(f"  → 체크포인트 복구: {target}")
            df_ckpt = pd.read_parquet(ckpt_oof)
            for col in df_ckpt.columns:
                oof_df[col] = df_ckpt[col].values
            with open(ckpt_sum, encoding="utf-8") as _f:
                _ckpt = json.load(_f)
            summary.append(_ckpt["row"])
            if _ckpt.get("model_meta"):
                model_meta[label_key] = _ckpt["model_meta"]
            if f"lr_base_oof_{label_key}" not in df_ckpt.columns:
                print(f"     lr_base OOF 없음 → lr_base CV 보완")
                y_ckpt = df[target]
                lr_base_fold_aucs, lr_base_oof = _lr_base_cv(X, y_ckpt)
                oof_df[f"lr_base_oof_{label_key}"] = lr_base_oof
                _ckpt["row"]["lr_base_auc"] = float(np.mean(lr_base_fold_aucs))
                print(f"     LR_B  fold AUC: {np.mean(lr_base_fold_aucs):.4f}")
                lr_base_m, lr_base_scaler, lr_base_cols = _lr_base_final(X, y_ckpt)
                joblib.dump({"model": lr_base_m, "scaler": lr_base_scaler, "feat_cols": lr_base_cols},
                            str(out_dir / f"lr_base_label_{label_key}.pkl"))
            continue

        ret_col_name = _return_col_for_label(label_key, df_cols)
        ret_series = df[ret_col_name] if ret_col_name else None

        y = df[target]

        pos_rate = float(y.mean())
        print(f"{'='*62}")
        print(f"  {target}  (positive={pos_rate:.1%})")
        print(f"{'='*62}")

        if pos_rate == 0.0 or pos_rate == 1.0:
            print(f"  ⚠ 단일 클래스 라벨 — 스킵 (학습 불가)")
            continue

        row: dict = {"target": target}
        model_oofs: dict[str, np.ndarray] = {}

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

        # ── ExtraTrees CV + OOF ───────────────────────────────────────────
        et_fold_aucs, et_oof = _et_cv(X, y)
        model_oofs["et"] = et_oof
        oof_df[f"et_oof_{label_key}"] = et_oof
        row["et_auc"] = float(np.mean(et_fold_aucs))
        print(f"  ET    fold AUC: {np.mean(et_fold_aucs):.4f} ± {np.std(et_fold_aucs):.4f}")

        # ── LR Base CV + OOF ─────────────────────────────────────────────
        lr_base_fold_aucs, lr_base_oof = _lr_base_cv(X, y)
        model_oofs["lr_base"] = lr_base_oof
        oof_df[f"lr_base_oof_{label_key}"] = lr_base_oof
        row["lr_base_auc"] = float(np.mean(lr_base_fold_aucs))
        print(f"  LR_B  fold AUC: {np.mean(lr_base_fold_aucs):.4f} ± {np.std(lr_base_fold_aucs):.4f}")

        # ── OOF 상관계수 (다양성 측정) ───────────────────────────────────
        base_names_corr = ["xgb"] + (["lgbm"] if HAS_LGBM else []) + ["et", "lr_base"]
        avail_corr = [n for n in base_names_corr if n in model_oofs]
        if len(avail_corr) >= 2:
            pairs = [(a, b) for i, a in enumerate(avail_corr) for b in avail_corr[i+1:]]
            corr_parts = []
            for a, b in pairs:
                oa, ob = model_oofs[a], model_oofs[b]
                valid = ~(np.isnan(oa) | np.isnan(ob))
                r = np.corrcoef(oa[valid], ob[valid])[0, 1]
                corr_parts.append(f"{a}↔{b}:{r:.3f}")
            print(f"  OOF 상관: {' | '.join(corr_parts)}")

        # ── Ensemble OOF ─────────────────────────────────────────────────
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

        # ── LR Stacking ──────────────────────────────────────────────────
        lr_model = None
        base_oof_keys = [k for k in model_oofs if k not in ("soft", "rank")]
        if len(base_oof_keys) >= 2:
            base_oofs = [model_oofs[k] for k in base_oof_keys]
            valid_mask = np.ones(len(y), dtype=bool)
            for o in base_oofs:
                valid_mask &= ~np.isnan(o)
            meta_X = np.column_stack([o[valid_mask] for o in base_oofs])
            meta_y = np.asarray(y)[valid_mask]

            tscv_lr = TimeSeriesSplit(n_splits=5)
            lr_oof_valid = np.full(len(meta_y), np.nan)
            for tr_idx, va_idx in tscv_lr.split(meta_X):
                _lr = LogisticRegression(C=0.1, max_iter=1000, random_state=42)
                _lr.fit(meta_X[tr_idx], meta_y[tr_idx])
                lr_oof_valid[va_idx] = _lr.predict_proba(meta_X[va_idx])[:, 1]

            lr_oof = np.full(len(y), np.nan)
            lr_oof[valid_mask] = lr_oof_valid

            lr_model = LogisticRegression(C=0.1, max_iter=1000, random_state=42)
            lr_model.fit(meta_X, meta_y)

            model_oofs["lr"] = lr_oof
            oof_df[f"lr_oof_{label_key}"] = lr_oof
            row["lr_auc"] = _auc_from_oof(y, lr_oof)
            print(f"  LR    OOF AUC:  {row['lr_auc']:.4f}  ← stacking")

        # ── Precision@K, Return@K, Brier ─────────────────────────────────
        print()
        for k in _EVAL_K:
            print(f"  {'모델':<12s} Prec@{k:<3d} Return@{k}")
            for name, oof in model_oofs.items():
                prec = _precision_at_k(y, oof, k)
                row[f"{name}_prec@{k}"] = prec
                if ret_series is not None:
                    ret = _return_at_k(ret_series, oof, k)
                    row[f"{name}_ret@{k}"] = ret
                    print(f"  {name:<12s} {prec:>6.1%}   {ret:>+7.1%}")
                else:
                    print(f"  {name:<12s} {prec:>6.1%}   N/A")
            print()
        for name, oof in model_oofs.items():
            row[f"{name}_brier"] = _brier_from_oof(y, oof)

        # ── 라벨별 최고 모델 선택 (Precision@20 기준) ────────────────────
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

        et_model = _et_final(X, y)
        joblib.dump((et_model, list(X.columns)), str(out_dir / f"et_label_{label_key}.pkl"))

        lr_base_m, lr_base_scaler, lr_base_cols = _lr_base_final(X, y)
        joblib.dump({"model": lr_base_m, "scaler": lr_base_scaler, "feat_cols": lr_base_cols},
                    str(out_dir / f"lr_base_label_{label_key}.pkl"))

        if lr_model is not None:
            joblib.dump({"model": lr_model, "base_keys": base_oof_keys},
                        str(out_dir / f"lr_stacker_{label_key}.pkl"))

        summary.append(row)

        # ── 체크포인트 저장 ────────────────────────────────────────────────
        label_oof_cols = [c for c in oof_df.columns if f"_{label_key}" in c]
        oof_df[label_oof_cols].to_parquet(ckpt_oof, index=False)
        with open(ckpt_sum, "w", encoding="utf-8") as _f:
            json.dump({"row": row, "model_meta": model_meta.get(label_key)}, _f, ensure_ascii=False, indent=2)

        best_info = model_meta.get(label_key, {})
        print(f"체크포인트: {label_key} — 최고:{best_info.get('best','?').upper()} AUC={row.get('xgb_auc', float('nan')):.4f}")
        print()

    # ── 전체 요약 테이블 ─────────────────────────────────────────────────────
    print(f"{'='*62}")
    print("  요약 (walk-forward AUC / Precision@20 / Return@20)")
    print(f"{'='*62}")
    model_names = ["xgb"] + (["lgbm"] if HAS_LGBM else []) + ["et", "lr_base"]
    if len(model_names) >= 2:
        model_names += ["soft", "rank", "lr"]

    header = f"  {'타겟':<34s}"
    for n in model_names:
        header += f"  {n.upper():>7s}_AUC  P@20  R@20"
    print(header)
    for r in summary:
        line = f"  {r['target']:<34s}"
        for n in model_names:
            auc_key = f"{n}_auc"
            auc  = r.get(auc_key, float("nan"))
            prec = r.get(f"{n}_prec@20", float("nan"))
            ret  = r.get(f"{n}_ret@20", float("nan"))
            line += f"  {auc:>10.4f} {prec:>5.1%} {ret:>+6.1%}"
        print(line)

    # ── 파일 저장 ────────────────────────────────────────────────────────────
    oof_path = Path("data/oof_predictions_c.parquet")
    oof_df.to_parquet(oof_path, index=False)
    print(f"\nOOF 저장: {oof_path}  shape={oof_df.shape}")

    meta_path = Path("data/model_meta_c.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(model_meta, f, ensure_ascii=False, indent=2)
    print(f"모델 메타 저장: {meta_path}")

    result_path = Path("data/model_results_c.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"상세 결과 저장: {result_path}")

    _write_to_db(summary, _date.today().isoformat(), fcols)


def _write_to_db(summary: list[dict], train_date: str, feat_cols: list[str]) -> None:
    from data.db import get_conn, _upsert_model_registry
    out_dir = Path("data/models_c")
    ext_map = {"xgb": ".json", "lgbm": ".txt", "et": ".pkl", "lr_base": ".pkl"}
    fhash = _feat_hash(feat_cols)
    n_feat = len(feat_cols)
    conn = get_conn()
    try:
        for row in summary:
            label_key = row["target"].replace("label_", "")
            for mtype, ext in ext_map.items():
                if f"{mtype}_auc" not in row:
                    continue
                fpath = str(out_dir / f"{mtype}_label_{label_key}{ext}")
                _upsert_model_registry(
                    conn, label_key, mtype, fpath, train_date,
                    auc=row.get(f"{mtype}_auc"),
                    prec10=row.get(f"{mtype}_prec@10"),
                    prec20=row.get(f"{mtype}_prec@20"),
                    ret20=row.get(f"{mtype}_ret@20"),
                    brier=row.get(f"{mtype}_brier"),
                    feat_hash=fhash,
                    n_feat=n_feat,
                )
        print(f"DB 등록: model_registry + evaluation_history ({len(summary)}라벨)")
    except Exception as e:
        print(f"DB 등록 실패 (무시): {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
