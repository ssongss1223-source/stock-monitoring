"""
Track C 일일 추론 파이프라인.

실행 흐름:
  1. universe_features_daily + universe_daily → 55 일봉 피처
  2. ohlcv_daily rolling 350d → 16 extra daily 피처 (rs_60d, rs_acceleration 포함)
  3. ohlcv_min → 15 intraday 피처
  4. XGB + LGBM + ET soft voting → signal_history_c 저장

Usage:
    python scripts/orchestrator_c.py [--date 2026-06-27]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date as dt
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn
from scripts.feature_engineering_c import (
    _DAILY_FEAT_COLS,
    _UD_COLS,
    add_rank_features,
    compute_extra_daily_features,
    compute_intraday_features,
)

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("data/models_c")
_FEAT_COLS_FILE = _MODEL_DIR / "feature_cols.json"
_MODEL_RESULTS_FILE = Path("data/model_results_c.json")
_MODEL_TYPES = [("xgb", ".json"), ("lgbm", ".txt"), ("et", ".pkl")]


def _load_labels() -> list[str]:
    with open(_MODEL_RESULTS_FILE) as f:
        return [r["target"] for r in json.load(f)]


def _load_feature_cols() -> list[str]:
    with open(_FEAT_COLS_FILE) as f:
        return json.load(f)


def _build_feature_df(date_str: str) -> pd.DataFrame:
    """date_str 기준 전 universe 종목 86-피처 DataFrame 반환."""
    ufd_cols = [c for c in _DAILY_FEAT_COLS if c not in _UD_COLS]
    ud_sql = ", ".join(f"ud.{c}" for c in sorted(_UD_COLS))
    ufd_sql = ", ".join(f"ufd.{c}" for c in ufd_cols)

    conn = get_conn(read_only=True)
    try:
        # 1. 기존 55 일봉 피처
        df_feats = conn.execute(f"""
            SELECT ufd.ticker, {ufd_sql}, {ud_sql}
            FROM universe_features_daily ufd
            LEFT JOIN universe_daily ud
              ON ud.date = ufd.date AND ud.ticker = ufd.ticker
            WHERE ufd.date = CAST(? AS DATE)
        """, [date_str]).df()

        if df_feats.empty:
            logger.warning("universe_features_daily: %s 날짜 데이터 없음", date_str)
            return pd.DataFrame()

        # 2. 16 extra daily 피처 (rolling 350d 필요 → start_date=date_str 전달)
        df_extra_all = compute_extra_daily_features(conn, date_str)
        mask_extra = df_extra_all["date"].astype(str).str[:10] == date_str
        df_extra = df_extra_all[mask_extra].drop(
            columns=["date"]
        ).reset_index(drop=True)

        # 3. 15 intraday 피처
        df_min = conn.execute("""
            SELECT ticker, dt, open, high, low, close, volume, amount
            FROM ohlcv_min
            WHERE CAST(dt AS DATE) = CAST(? AS DATE)
        """, [date_str]).df()
    finally:
        conn.close()

    df_intraday_all = compute_intraday_features(df_min)
    if not df_intraday_all.empty:
        # date 컬럼 타입(datetime64 or object)에 무관하게 문자열로 비교
        mask = df_intraday_all["date"].astype(str).str[:10] == date_str
        df_intraday = df_intraday_all[mask].drop(
            columns=["date"], errors="ignore"
        ).reset_index(drop=True)
    else:
        df_intraday = pd.DataFrame()

    # Merge (left join — extra/intraday 없으면 NaN → fillna(0) at predict time)
    df = df_feats.merge(df_extra, on="ticker", how="left")
    if not df_intraday.empty:
        df = df.merge(df_intraday, on="ticker", how="left")

    # rs_acceleration = rs_20d - rs_60d
    if "rs_20d" in df.columns and "rs_60d" in df.columns:
        df["rs_acceleration"] = df["rs_20d"] - df["rs_60d"]

    # 크로스섹셔널 순위 피처 (단일 날짜 — 전체 df가 하나의 날짜)
    df = add_rank_features(df, date_col=None)

    return df


def _predict_one(
    prefix: str,
    ext: str,
    label: str,
    df: pd.DataFrame,
    feat_cols: list[str],
) -> np.ndarray | None:
    path = _MODEL_DIR / f"{prefix}_{label}{ext}"
    if not path.exists():
        return None
    try:
        if ext == ".json":
            import xgboost as xgb
            model = xgb.Booster()
            model.load_model(str(path))
            X = df[feat_cols].fillna(0)
            return model.predict(xgb.DMatrix(X))
        elif ext == ".txt":
            import lightgbm as lgb
            model = lgb.Booster(model_file=str(path))
            X = df[feat_cols].fillna(0).values.astype("float32")
            return model.predict(X)
        elif ext == ".pkl":
            import joblib
            model, model_feat_cols = joblib.load(path)
            X = df[model_feat_cols].fillna(0)
            return model.predict_proba(X)[:, 1]
    except Exception as e:
        logger.warning("모델 추론 실패 %s: %s", path.name, e)
        return None


def _save_to_db(
    date_str: str,
    result: dict[str, dict[str, float]],
    model_version: str = "track_c_v1",
) -> None:
    """signal_history_c에 저장. 스키마: (signal_date, ticker, model_version, label_probs JSON, top_labels JSON)."""
    rows = []
    for ticker, label_probs in result.items():
        if not label_probs:
            continue
        top_labels = sorted(label_probs, key=lambda l: label_probs[l], reverse=True)[:5]
        rows.append({
            "signal_date": date_str,
            "ticker": ticker,
            "model_version": model_version,
            "label_probs": json.dumps(label_probs),
            "top_labels": json.dumps(top_labels),
        })
    if not rows:
        return

    df_out = pd.DataFrame(rows)
    conn = get_conn(read_only=False)
    try:
        conn.register("_df_c", df_out)
        conn.execute("""
            INSERT OR REPLACE INTO signal_history_c
                (signal_date, ticker, model_version, label_probs, top_labels)
            SELECT signal_date, ticker, model_version, label_probs, top_labels
            FROM _df_c
        """)
        conn.commit()
    finally:
        conn.close()

    logger.info("signal_history_c: %d종목 저장 (%s)", len(rows), date_str)


def run_daily_c(date_str: str | None = None) -> dict[str, dict[str, float]]:
    """Track C 일일 추론 실행. date_str 없으면 오늘 날짜 사용."""
    if date_str is None:
        date_str = dt.today().isoformat()

    logger.info("[Track C] 추론 시작 — %s", date_str)

    feat_cols = _load_feature_cols()
    labels = _load_labels()

    logger.info("[Track C] 피처 빌드 중 (%s)...", date_str)
    df = _build_feature_df(date_str)
    if df.empty:
        logger.warning("[Track C] %s 피처 없음 — 추론 건너뜀", date_str)
        return {}

    logger.info("[Track C] %d종목 × %d피처", len(df), len(feat_cols))
    result: dict[str, dict[str, float]] = {t: {} for t in df["ticker"]}

    for label in labels:
        probs_list = [_predict_one(p, e, label, df, feat_cols) for p, e in _MODEL_TYPES]
        available = [p for p in probs_list if p is not None]
        if not available:
            logger.warning("[Track C] 모델 없음: %s", label)
            continue
        probs = np.mean(available, axis=0)
        for ticker, prob in zip(df["ticker"], probs.tolist()):
            result[ticker][label] = float(prob)

    # BB 상단 위 종목의 bb_upper_break 확률 마스킹
    # 라벨러가 today_close >= bb_upper → NULL로 처리한 것과 동일한 조건 적용
    _BB_LABEL = "label_3d_bb_upper_break"
    if _BB_LABEL in labels:
        conn_bb = get_conn(read_only=True)
        try:
            bb_rows = conn_bb.execute(
                "SELECT ticker, bb_position FROM universe_daily WHERE date = CAST(? AS DATE)",
                [date_str],
            ).fetchall()
        finally:
            conn_bb.close()
        above_bb = {t for t, bp in bb_rows if bp is not None and bp >= 1.0}
        for ticker in above_bb:
            if ticker in result:
                result[ticker][_BB_LABEL] = 0.0
        if above_bb:
            logger.info("[Track C] BB 상단 위 %d종목 → %s 확률 0으로 마스킹", len(above_bb), _BB_LABEL)

    _save_to_db(date_str, result)
    logger.info("[Track C] 완료 — %d종목 %d라벨", len(result), len(labels))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘)")
    args = p.parse_args()
    run_daily_c(args.date)
