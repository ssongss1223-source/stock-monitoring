"""멀티모델 추론 — BuySignal 목록에 ensemble_prob 인플레이스 업데이트 + 18개 라벨 동시 추론.

XGB + LGBM + ET soft voting 앙상블 사용. 모델 파일이 없으면 조용히 스킵.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from data.db import get_conn
from models.signals import BuySignal

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("data/models")
_LABELS = [
    "3d_3pct_clean", "3d_5pct_clean", "3d_10pct_clean",
    "5d_3pct_clean", "5d_5pct_clean", "5d_10pct_clean",
    "10d_3pct_clean", "10d_5pct_clean", "10d_10pct_clean",
    "first_3d_3pct", "first_3d_5pct", "first_3d_10pct",
    "first_5d_3pct", "first_5d_5pct", "first_5d_10pct",
    "first_10d_3pct", "first_10d_5pct", "first_10d_10pct",
]

# (모델 접두사, 파일 확장자) — ET 복귀: VM 4GB 증설 후 실측 peak RSS 1.1GB (라벨당 순차 로드)
_MODEL_TYPES = [("xgb", ".json"), ("lgbm", ".txt"), ("et", ".pkl")]

# feature_engineering._FEAT_TRAIN_COLS 와 동일한 순서 — universe_features_daily 컬럼
_FEAT_COLS = [
    "ma_cross_5_20", "obv_slope_5d", "high_low_ratio", "body_ratio",
    "short_balance_ratio", "short_volume_ratio_5d", "short_balance_change_5d",
    "volume_surge_ratio", "amount_surge_ratio",
    "price_momentum_3d", "price_momentum_10d",
    "inst_net_20d", "foreign_exh_change_5d", "roe_proxy",
    "relative_strength_5d", "combined_net_5d",
    "kospi_above_ma60", "market_volatility_20d",
    "grade_S", "grade_A", "grade_B",
    "bb_width", "atr_14", "atr_ratio_60d",
    "volume_zscore_20d", "amount_zscore_20d",
    "rs_20d", "rs_rank_pct", "market_breadth",
    "breakout_distance_20d", "box_tightness_20d",
]


def _build_feature_df(signals: list[BuySignal]) -> pd.DataFrame:
    """signals → feature DataFrame. universe_daily + universe_features_daily 직접 읽기.

    학습 파이프라인(feature_engineering.run_train)과 동일한 컬럼명/계산 방식 사용.
    """
    in_clause = ", ".join(f"'{s.ticker}'" for s in signals)
    feat_cols = ", ".join(f"uf.{c}" for c in _FEAT_COLS)

    conn = get_conn(read_only=True)
    try:
        df = conn.execute(f"""
            WITH latest AS (
                SELECT ticker, MAX(date) AS date
                FROM universe_daily WHERE ticker IN ({in_clause})
                GROUP BY ticker
            )
            SELECT
                ud.ticker,
                ud.volume, ud.market_cap, ud.per, ud.pbr, ud.turnover_rate,
                ud.ma5_ratio, ud.ma20_ratio, ud.ma60_ratio, ud.ma120_ratio,
                ud.rsi_14, ud.bb_position, ud.hist_vol_20d, ud.close_to_52w_high,
                ud.foreign_net_5d, ud.inst_net_5d, ud.foreign_net_20d, ud.volume_surge_5d,
                ud.kospi_ret_5d  AS kospi_return_5d,
                ud.kospi_ret_20d AS kospi_return_20d,
                ud.vol_score_approx,
                {feat_cols}
            FROM universe_daily ud
            JOIN universe_features_daily uf ON ud.date = uf.date AND ud.ticker = uf.ticker
            JOIN latest l ON ud.ticker = l.ticker AND ud.date = l.date
        """).df()
    finally:
        conn.close()
    return df


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
        elif ext == ".pkl":
            import joblib
            model, feat_cols = joblib.load(path)
            X = df[feat_cols].fillna(0)
            return model.predict_proba(X)[:, 1]
    except Exception as e:
        logger.warning("모델 로드/추론 실패 %s: %s", path.name, e)
        return None


def score_all_labels(signals: list[BuySignal]) -> dict[str, dict[str, float]]:
    """18개 라벨 추론. {ticker: {label: prob}} 반환. XGB + LGBM + ET soft voting."""
    if not signals:
        return {}

    df = _build_feature_df(signals)
    result: dict[str, dict[str, float]] = {s.ticker: {} for s in signals}

    for label in _LABELS:
        probs_list = [_predict_one(p, e, label, df) for p, e in _MODEL_TYPES]
        available = [p for p in probs_list if p is not None]
        if not available:
            logger.warning("사용 가능한 모델 없음: label=%s", label)
            continue
        probs = np.mean(available, axis=0)
        for ticker, prob in zip(df["ticker"], probs.tolist()):
            if ticker in result:
                result[ticker][label] = float(prob)

    logger.info("ML 추론 완료: %d종목 18라벨 (soft-voting)", len(signals))
    return result


def score_signals(signals: list[BuySignal]) -> None:
    """signals의 각 BuySignal.ensemble_prob(3d_5pct_clean)를 인플레이스 업데이트."""
    probs = score_all_labels(signals)
    for s in signals:
        s.ensemble_prob = probs.get(s.ticker, {}).get("3d_5pct_clean")


def _build_feature_df_universe(tickers: list[str], date_str: str) -> pd.DataFrame:
    """전종목 추론용 피처 DataFrame. universe_daily + universe_features_daily 직접 읽기."""
    in_clause = ", ".join(f"'{t}'" for t in tickers)
    feat_cols = ", ".join(f"uf.{c}" for c in _FEAT_COLS)

    conn = get_conn(read_only=True)
    try:
        df = conn.execute(f"""
            SELECT
                ud.ticker,
                ud.volume, ud.market_cap, ud.per, ud.pbr, ud.turnover_rate,
                ud.ma5_ratio, ud.ma20_ratio, ud.ma60_ratio, ud.ma120_ratio,
                ud.rsi_14, ud.bb_position, ud.hist_vol_20d, ud.close_to_52w_high,
                ud.foreign_net_5d, ud.inst_net_5d, ud.foreign_net_20d, ud.volume_surge_5d,
                ud.kospi_ret_5d  AS kospi_return_5d,
                ud.kospi_ret_20d AS kospi_return_20d,
                ud.vol_score_approx,
                {feat_cols}
            FROM universe_daily ud
            JOIN universe_features_daily uf ON ud.date = uf.date AND ud.ticker = uf.ticker
            WHERE ud.ticker IN ({in_clause}) AND ud.date = CAST(? AS DATE)
        """, [date_str]).df()
    finally:
        conn.close()
    return df


def score_universe_all(date_str: str) -> dict[str, dict[str, float]]:
    """전종목(universe) 기준일 ML 추론. {ticker: {label: prob}} 반환.

    XGB + LGBM + ET soft voting. 신호 피처는 0으로 채워 모든 종목에 동일 모델 적용.
    """
    conn = get_conn(read_only=True)
    try:
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM universe_daily WHERE date = CAST(? AS DATE)", [date_str]
        ).fetchall()]
    finally:
        conn.close()

    if not tickers:
        logger.warning("score_universe_all: %s 날짜 데이터 없음", date_str)
        return {}

    df = _build_feature_df_universe(tickers, date_str)
    result: dict[str, dict[str, float]] = {t: {} for t in tickers}

    for label in _LABELS:
        probs_list = [_predict_one(p, e, label, df) for p, e in _MODEL_TYPES]
        available = [p for p in probs_list if p is not None]
        if not available:
            continue
        probs = np.mean(available, axis=0)
        for ticker, prob in zip(df["ticker"], probs.tolist()):
            if ticker in result:
                result[ticker][label] = float(prob)

    logger.info("universe ML 추론 완료: %d종목 %s", len(tickers), date_str)
    return result
