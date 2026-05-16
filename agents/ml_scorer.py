"""XGBoost 추론 — BuySignal 목록에 xgb_prob 인플레이스 업데이트 + 9개 라벨 동시 추론."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import xgboost as xgb

from data.db import get_conn
from models.signals import BuySignal

logger = logging.getLogger(__name__)

_MODEL_DIR = Path("data/models")
_LABELS = [
    "3d_3pct", "3d_5pct", "3d_10pct",
    "5d_3pct", "5d_5pct", "5d_10pct",
    "10d_3pct", "10d_5pct", "10d_10pct",
]
_PATTERNS = ["cup_handle", "falling_box_breakout", "triangle_convergence", "bb_squeeze"]


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


def score_all_labels(signals: list[BuySignal]) -> dict[str, dict[str, float]]:
    """9개 라벨 동시 추론. {ticker: {label: prob}} 반환."""
    if not signals:
        return {}

    df = _build_feature_df(signals)
    result: dict[str, dict[str, float]] = {s.ticker: {} for s in signals}

    for label in _LABELS:
        path = _MODEL_DIR / f"xgb_label_{label}.json"
        if not path.exists():
            logger.warning("모델 없음: %s", path)
            continue
        model = xgb.XGBClassifier()
        model.load_model(str(path))
        feature_names = model.get_booster().feature_names
        X = df[feature_names].fillna(0)
        probs = model.predict_proba(X)[:, 1]
        for ticker, prob in zip(df["ticker"], probs.tolist()):
            if ticker in result:
                result[ticker][label] = prob

    logger.info("XGBoost 9개 라벨 추론 완료: %d종목", len(signals))
    return result


def score_signals(signals: list[BuySignal]) -> None:
    """signals의 각 BuySignal.xgb_prob(3d_5pct)를 인플레이스 업데이트."""
    probs = score_all_labels(signals)
    for s in signals:
        s.xgb_prob = probs.get(s.ticker, {}).get("3d_5pct")
