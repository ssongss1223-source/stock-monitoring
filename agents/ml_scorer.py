"""멀티모델 추론 — BuySignal 목록에 xgb_prob 인플레이스 업데이트 + 9개 라벨 동시 추론.

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
    "3d_3pct", "3d_5pct", "3d_10pct",
    "5d_3pct", "5d_5pct", "5d_10pct",
    "10d_3pct", "10d_5pct", "10d_10pct",
]
_PATTERNS = ["cup_handle", "falling_box_breakout", "triangle_convergence", "bb_squeeze"]

# (모델 접두사, 파일 확장자)
_MODEL_TYPES = [("xgb", ".json"), ("lgbm", ".txt"), ("et", ".pkl")]


def _build_feature_df(signals: list[BuySignal]) -> pd.DataFrame:
    """signals → feature DataFrame (v1 + v2 피처 전체)."""
    in_clause = ", ".join(f"'{s.ticker}'" for s in signals)

    conn = get_conn(read_only=True)
    try:
        # ── v1: 스냅샷 ───────────────────────────────────────────────────────
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

        # ── v1: 롤링 ─────────────────────────────────────────────────────────
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
                       LN(NULLIF(AVG(volume)      OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING), 0)) AS log_avg_volume_20d,
                       STDDEV_POP(daily_ret)       OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING) * SQRT(252) AS hist_volatility_20d,
                       AVG(foreign_exh_rate)       OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING) AS avg_foreign_exh_rate_20d
                FROM base
            )
            SELECT w.ticker, w.foreign_net_5d, w.inst_net_5d,
                   w.log_avg_volume_20d, w.hist_volatility_20d, w.avg_foreign_exh_rate_20d
            FROM w JOIN latest l ON w.ticker = l.ticker AND w.date = l.date
        """).df()

        # ── v2: 기술적 피처 (최신일 기준) ────────────────────────────────────
        df_v2 = conn.execute(f"""
            WITH
            base AS (SELECT * FROM ohlcv_daily WHERE ticker IN ({in_clause})),
            latest AS (SELECT ticker, MAX(date) AS max_date FROM base GROUP BY ticker),
            ma AS (
                SELECT ticker, date, close, open, high, low, volume, amount,
                       AVG(close)  OVER w5     AS ma5,
                       AVG(close)  OVER w20    AS ma20,
                       AVG(close)  OVER w60    AS ma60,
                       MAX(high)   OVER w252   AS high_52w,
                       STDDEV_POP(close) OVER w20 AS std20,
                       AVG(volume) OVER w20_lag AS avg_vol_20d,
                       AVG(amount) OVER w20_lag AS avg_amt_20d
                FROM base
                WINDOW
                    w5      AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
                    w20     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                    w60     AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),
                    w252    AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW),
                    w20_lag AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
            ),
            rsi_raw AS (
                SELECT ticker, date,
                       GREATEST(close - LAG(close,1) OVER (PARTITION BY ticker ORDER BY date), 0) AS gain,
                       GREATEST(LAG(close,1) OVER (PARTITION BY ticker ORDER BY date) - close, 0) AS loss
                FROM base
            ),
            rsi_avg AS (
                SELECT ticker, date,
                       AVG(gain) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain,
                       AVG(loss) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss
                FROM rsi_raw
            ),
            obv_dir AS (
                SELECT ticker, date, volume,
                       SIGN(close - LAG(close,1) OVER (PARTITION BY ticker ORDER BY date)) AS dir
                FROM base
            ),
            obv_val AS (
                SELECT ticker, date,
                       SUM(volume * dir) OVER (PARTITION BY ticker ORDER BY date) AS obv
                FROM obv_dir
            ),
            obv_slope AS (
                SELECT ticker, date,
                       (obv - LAG(obv,5) OVER (PARTITION BY ticker ORDER BY date))
                           / NULLIF(ABS(LAG(obv,5) OVER (PARTITION BY ticker ORDER BY date)), 0) AS obv_slope_5d
                FROM obv_val
            ),
            short_roll AS (
                SELECT ticker, date, short_balance, shares,
                       AVG(short_ratio) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS short_volume_ratio_5d
                FROM base
            ),
            ret AS (
                SELECT ticker, date,
                       close / NULLIF(LAG(close,3)  OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS price_momentum_3d,
                       close / NULLIF(LAG(close,5)  OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS stock_ret_5d,
                       close / NULLIF(LAG(close,10) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS price_momentum_10d
                FROM base
            ),
            flows AS (
                SELECT ticker, date,
                       SUM(foreign_net) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS foreign_net_20d,
                       SUM(inst_net)    OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS inst_net_20d,
                       foreign_exh_rate - LAG(foreign_exh_rate, 5) OVER (PARTITION BY ticker ORDER BY date) AS foreign_exh_change_5d
                FROM base
            ),
            short_chg AS (
                SELECT ticker, date,
                       (short_balance - LAG(short_balance, 5) OVER (PARTITION BY ticker ORDER BY date))
                           / NULLIF(ABS(LAG(short_balance, 5) OVER (PARTITION BY ticker ORDER BY date)), 0) AS short_balance_change_5d
                FROM base
            ),
            valuation AS (
                SELECT ticker, date,
                       CASE WHEN bps > 0 THEN CAST(eps AS DOUBLE) / NULLIF(bps, 0) ELSE NULL END AS roe_proxy
                FROM base
            ),
            combined AS (
                SELECT
                    ma.ticker,
                    ma.close / NULLIF(ma.ma20, 0) - 1                             AS close_to_20ma_ratio,
                    ma.close / NULLIF(ma.ma60, 0) - 1                             AS close_to_60ma_ratio,
                    ma.close / NULLIF(ma.high_52w, 0) - 1                         AS close_to_52w_high,
                    ma.close / NULLIF(ma.ma5, 0) - 1                              AS close_to_5ma_ratio,
                    CASE WHEN ma.ma5 >= ma.ma20 THEN 1 ELSE 0 END                 AS ma_cross_5_20,
                    (ma.close - (ma.ma20 - 2*ma.std20)) / NULLIF(4*ma.std20, 0)  AS bb_position,
                    CASE WHEN rsi_avg.avg_loss = 0 THEN 100.0
                         ELSE 100 - 100 / (1 + rsi_avg.avg_gain / NULLIF(rsi_avg.avg_loss, 0))
                    END                                                            AS rsi_14,
                    obv_slope.obv_slope_5d,
                    (ma.high - ma.low) / NULLIF(ma.close, 0)                     AS high_low_ratio,
                    (ma.close - ma.open) / NULLIF(ma.high - ma.low, 0)           AS body_ratio,
                    sr.short_balance / NULLIF(sr.shares, 0)                       AS short_balance_ratio,
                    sr.short_volume_ratio_5d,
                    short_chg.short_balance_change_5d,
                    ma.volume / NULLIF(ma.avg_vol_20d, 0)                         AS volume_surge_ratio,
                    ma.amount / NULLIF(ma.avg_amt_20d, 0)                         AS amount_surge_ratio,
                    ret.price_momentum_3d,
                    ret.price_momentum_10d,
                    flows.foreign_net_20d,
                    flows.inst_net_20d,
                    flows.foreign_exh_change_5d,
                    valuation.roe_proxy,
                    ret.stock_ret_5d
                FROM ma
                JOIN rsi_avg   ON ma.ticker = rsi_avg.ticker   AND ma.date = rsi_avg.date
                JOIN obv_slope ON ma.ticker = obv_slope.ticker AND ma.date = obv_slope.date
                JOIN short_roll sr ON ma.ticker = sr.ticker    AND ma.date = sr.date
                JOIN ret       ON ma.ticker = ret.ticker       AND ma.date = ret.date
                JOIN flows     ON ma.ticker = flows.ticker     AND ma.date = flows.date
                JOIN short_chg ON ma.ticker = short_chg.ticker AND ma.date = short_chg.date
                JOIN valuation ON ma.ticker = valuation.ticker AND ma.date = valuation.date
                JOIN latest    ON ma.ticker = latest.ticker    AND ma.date = latest.max_date
            )
            SELECT * FROM combined
        """).df()

        # ── v2: 시장 피처 (최신일) ───────────────────────────────────────────
        df_mkt = conn.execute("""
            WITH kospi_ret AS (
                SELECT date, close,
                       close / NULLIF(LAG(close,1)  OVER (ORDER BY date), 0) - 1 AS daily_ret,
                       close / NULLIF(LAG(close,5)  OVER (ORDER BY date), 0) - 1 AS kospi_return_5d,
                       close / NULLIF(LAG(close,20) OVER (ORDER BY date), 0) - 1 AS kospi_return_20d
                FROM market_index WHERE ticker = '1001'
            )
            SELECT
                kospi_return_20d,
                kospi_return_5d,
                CASE WHEN close >= AVG(close) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
                     THEN 1 ELSE 0 END AS kospi_above_ma60,
                STDDEV_POP(daily_ret) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                    * SQRT(252) AS market_volatility_20d
            FROM kospi_ret
            ORDER BY date DESC
            LIMIT 1
        """).df()

    finally:
        conn.close()

    rows = []
    for s in signals:
        row = {
            "ticker": s.ticker,
            "vol_score": s.volume_score,
            "total_score": s.total_score,
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

    # v1 병합
    df = df_sig.merge(df_snap, on="ticker", how="left").merge(df_roll, on="ticker", how="left")

    # v2 OHLCV 병합
    df = df.merge(df_v2, on="ticker", how="left")

    # v2 파생: 상대강도, 합산수급
    if "stock_ret_5d" in df.columns and "kospi_return_5d" in df_mkt.columns:
        kospi_ret_5d = df_mkt["kospi_return_5d"].iloc[0] if not df_mkt.empty else 0.0
        df["relative_strength_5d"] = df["stock_ret_5d"] - kospi_ret_5d
        df = df.drop(columns=["stock_ret_5d"])
    if "foreign_net_5d" in df.columns and "inst_net_5d" in df.columns:
        df["combined_net_5d"] = df["foreign_net_5d"] + df["inst_net_5d"]

    # v2 시장 피처: 모든 종목에 동일하게 적용
    if not df_mkt.empty:
        for col in df_mkt.columns:
            df[col] = df_mkt[col].iloc[0]

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
    """9개 라벨 추론. {ticker: {label: prob}} 반환. XGB + LGBM + ET soft voting."""
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

    logger.info("ML 추론 완료: %d종목 9라벨 (soft-voting)", len(signals))
    return result


def score_signals(signals: list[BuySignal]) -> None:
    """signals의 각 BuySignal.xgb_prob(3d_5pct)를 인플레이스 업데이트."""
    probs = score_all_labels(signals)
    for s in signals:
        s.xgb_prob = probs.get(s.ticker, {}).get("3d_5pct")
