"""XGBoost 추론 — BuySignal 목록에 xgb_prob 인플레이스 업데이트 + 9개 라벨 동시 추론.

앙상블: 라벨별로 xgb_*.json / lgbm_*.txt / catboost_*.cbm 중 존재하는 모든 모델의
확률 평균을 사용한다. 모델 파일이 없으면 조용히 스킵한다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from data.db import get_conn
from models.signals import BuySignal

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("data/models")
_META_PATH = Path("data/model_meta.json")
_LABELS = [
    "3d_3pct", "3d_5pct", "3d_10pct",
    "5d_3pct", "5d_5pct", "5d_10pct",
    "10d_3pct", "10d_5pct", "10d_10pct",
]
_PATTERNS = ["cup_handle", "falling_box_breakout", "triangle_convergence", "bb_squeeze"]

# (모델 접두사, 파일 확장자)
_MODEL_TYPES = [("xgb", ".json"), ("lgbm", ".txt"), ("catboost", ".cbm")]


def _load_model_meta() -> dict[str, str]:
    """model_meta.json에서 {label: best_model_prefix} 로드. 없으면 빈 dict."""
    if not _META_PATH.exists():
        return {}
    try:
        with open(_META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
        return {label: info["best"] for label, info in meta.items()}
    except Exception as e:
        logger.warning("model_meta.json 로드 실패: %s", e)
        return {}


def _build_feature_df(signals: list[BuySignal]) -> pd.DataFrame:
    """signals → feature DataFrame (DB 조회 + signal 피처 병합)."""
    in_clause = ", ".join(f"'{s.ticker}'" for s in signals)

    conn = get_conn(read_only=True)
    try:
        df_snap = conn.execute(f"""
            WITH latest AS (
                SELECT ticker, MAX(date) AS date
                FROM ohlcv_daily WHERE ticker IN ({in_clause})
                GROUP BY ticker
            )
            SELECT o.ticker, o.volume, o.amount, o.market_cap,
                   o.per, o.pbr, o.div_yield, o.foreign_exh_rate, o.short_ratio,
                   CASE WHEN o.market_cap > 0 THEN o.amount / o.market_cap ELSE NULL END AS turnover_rate
            FROM ohlcv_daily o
            JOIN latest l ON o.ticker = l.ticker AND o.date = l.date
        """).df()

        df_roll = conn.execute(f"""
            WITH base AS (
                SELECT ticker, date, volume, foreign_net, inst_net, foreign_exh_rate,
                       close / NULLIF(LAG(close, 1) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS daily_ret
                FROM ohlcv_daily WHERE ticker IN ({in_clause})
            ),
            latest AS (SELECT ticker, MAX(date) AS date FROM base GROUP BY ticker),
            w AS (
                SELECT ticker, date,
                       SUM(foreign_net) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS foreign_net_5d,
                       SUM(inst_net)    OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS inst_net_5d,
                       LN(NULLIF(AVG(volume)           OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING), 0)) AS log_avg_volume_20d,
                       STDDEV_POP(daily_ret)            OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING) * SQRT(252) AS hist_volatility_20d,
                       AVG(foreign_exh_rate)            OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING) AS avg_foreign_exh_rate_20d
                FROM base
            )
            SELECT w.ticker, w.foreign_net_5d, w.inst_net_5d,
                   w.log_avg_volume_20d, w.hist_volatility_20d, w.avg_foreign_exh_rate_20d
            FROM w JOIN latest l ON w.ticker = l.ticker AND w.date = l.date
        """).df()
    finally:
        conn.close()

    rows = []
    for s in signals:
        row = {
            "ticker": s.ticker,
            "vol_score": s.volume_score,
            "trend_score": s.trend_score,
            "pattern_score": s.pattern_score,
            "risk_reward": s.risk_reward,
            "grade_S": int(s.grade == "S"),
            "grade_A": int(s.grade == "A"),
            "grade_B": int(s.grade == "B"),
            "sv_live_v1": 0,
            "sv_live_v2": 1,
        }
        for pat in _PATTERNS:
            row[f"pattern_{pat}"] = int(s.pattern == pat)
        rows.append(row)

    df_sig = pd.DataFrame(rows)
    return df_sig.merge(df_snap, on="ticker", how="left").merge(df_roll, on="ticker", how="left")


def _predict_one(prefix: str, ext: str, label: str, df: pd.DataFrame) -> np.ndarray | None:
    """모델 파일 로드 + 예측. 파일 없거나 패키지 없으면 None 반환."""
    path = _MODEL_DIR / f"{prefix}_label_{label}{ext}"
    if not path.exists():
        return None
    try:
        if ext == ".json":
            model = xgb.XGBClassifier()
            model.load_model(str(path))
            feature_names = model.get_booster().feature_names
            X = df[feature_names].fillna(0)
            return model.predict_proba(X)[:, 1]
        elif ext == ".txt":
            import lightgbm as lgb
            model = lgb.Booster(model_file=str(path))
            X = df[model.feature_name()].fillna(0)
            return model.predict(X)
        elif ext == ".cbm":
            from catboost import CatBoostClassifier
            model = CatBoostClassifier()
            model.load_model(str(path))
            X = df[model.feature_names_].fillna(0)
            return model.predict_proba(X)[:, 1]
    except Exception as e:
        logger.warning("모델 로드/추론 실패 %s: %s", path.name, e)
        return None


def score_all_labels(signals: list[BuySignal]) -> dict[str, dict[str, float]]:
    """9개 라벨 추론. {ticker: {label: prob}} 반환.

    model_meta.json 있으면 라벨별 최고 모델(단일) 사용.
    없으면 모든 사용 가능한 모델의 soft voting 앙상블.
    """
    if not signals:
        return {}

    df = _build_feature_df(signals)
    result: dict[str, dict[str, float]] = {s.ticker: {} for s in signals}
    meta = _load_model_meta()  # {label: best_prefix} or {}

    ext_map = {"xgb": ".json", "lgbm": ".txt", "catboost": ".cbm"}

    for label in _LABELS:
        best_prefix = meta.get(label)

        if best_prefix:
            # Step 10: 라벨별 최고 모델만 사용
            ext = ext_map.get(best_prefix, ".json")
            probs = _predict_one(best_prefix, ext, label, df)
            if probs is None:
                logger.warning("최고 모델 파일 없음(%s %s) — soft voting으로 fallback", best_prefix, label)
                best_prefix = None  # fallback 트리거

        if not best_prefix:
            # Soft voting: 존재하는 모든 모델 평균
            probs_list = [_predict_one(p, e, label, df) for p, e in _MODEL_TYPES]
            available = [p for p in probs_list if p is not None]
            if not available:
                logger.warning("사용 가능한 모델 없음: label=%s", label)
                continue
            probs = np.mean(available, axis=0)

        for ticker, prob in zip(df["ticker"], probs.tolist()):
            if ticker in result:
                result[ticker][label] = float(prob)

    mode = "meta" if meta else "soft-voting"
    logger.info("ML 추론 완료: %d종목 9라벨 (%s)", len(signals), mode)
    return result


def score_signals(signals: list[BuySignal]) -> None:
    """signals의 각 BuySignal.xgb_prob(3d_5pct)를 인플레이스 업데이트."""
    probs = score_all_labels(signals)
    for s in signals:
        s.xgb_prob = probs.get(s.ticker, {}).get("3d_5pct")
