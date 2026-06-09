import json
import duckdb
from datetime import date
from pathlib import Path

DB_PATH = Path("data/stock.duckdb")

_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv_min (
    ticker  VARCHAR NOT NULL,
    dt      TIMESTAMP NOT NULL,
    open    DOUBLE,
    high    DOUBLE,
    low     DOUBLE,
    close   DOUBLE,
    volume  BIGINT,
    amount  DOUBLE,
    source  VARCHAR,
    PRIMARY KEY (ticker, dt)
);

CREATE TABLE IF NOT EXISTS ohlcv_daily (
    ticker          VARCHAR NOT NULL,
    date            DATE NOT NULL,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          BIGINT,
    amount          DOUBLE,
    market_cap      BIGINT,
    shares          BIGINT,
    foreign_net     BIGINT,
    inst_net        BIGINT,
    short_balance   BIGINT,
    per             DOUBLE,
    pbr             DOUBLE,
    eps             BIGINT,
    bps             BIGINT,
    div_yield       DOUBLE,
    foreign_exh_rate DOUBLE,
    short_volume    BIGINT,
    short_ratio     DOUBLE,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS market_index (
    ticker     VARCHAR NOT NULL,
    date       DATE NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    volume     BIGINT,
    amount     DOUBLE,
    market_cap BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS ticker_master (
    ticker      VARCHAR PRIMARY KEY,
    name        VARCHAR,
    market      VARCHAR,
    sector      VARCHAR,
    listed_date DATE
);

CREATE TABLE IF NOT EXISTS signal_history (
    signal_date   DATE,
    ticker        VARCHAR,
    vol_score     INT,
    grade         VARCHAR,
    features      JSON,
    entry_price   DOUBLE,
    ensemble_prob DOUBLE,
    PRIMARY KEY (signal_date, ticker)
);

CREATE TABLE IF NOT EXISTS backtest_labels (
    signal_date      DATE,
    ticker           VARCHAR,
    entry_price      DOUBLE,
    max_close_3d     DOUBLE,
    max_close_5d     DOUBLE,
    max_close_10d    DOUBLE,
    max_drawdown_3d  DOUBLE,
    max_drawdown_5d  DOUBLE,
    max_drawdown_10d DOUBLE,
    return_3d        DOUBLE,
    return_5d        DOUBLE,
    return_10d       DOUBLE,
    label_3d_3pct_clean   BOOLEAN,
    label_3d_5pct_clean   BOOLEAN,
    label_3d_10pct_clean  BOOLEAN,
    label_5d_3pct_clean   BOOLEAN,
    label_5d_5pct_clean   BOOLEAN,
    label_5d_10pct_clean  BOOLEAN,
    label_10d_3pct_clean  BOOLEAN,
    label_10d_5pct_clean  BOOLEAN,
    label_10d_10pct_clean BOOLEAN,
    label_first_3d_3pct   BOOLEAN,
    label_first_3d_5pct   BOOLEAN,
    label_first_3d_10pct  BOOLEAN,
    label_first_5d_3pct   BOOLEAN,
    label_first_5d_5pct   BOOLEAN,
    label_first_5d_10pct  BOOLEAN,
    label_first_10d_3pct  BOOLEAN,
    label_first_10d_5pct  BOOLEAN,
    label_first_10d_10pct BOOLEAN,
    PRIMARY KEY (signal_date, ticker)
);

CREATE TABLE IF NOT EXISTS signal_xgb_probs (
    signal_date   DATE,
    ticker        VARCHAR,
    label         VARCHAR,
    ensemble_prob DOUBLE,
    PRIMARY KEY (signal_date, ticker, label)
);

CREATE TABLE IF NOT EXISTS macro_daily (
    date    DATE PRIMARY KEY,
    sp500   DOUBLE,
    nasdaq  DOUBLE,
    usdkrw  DOUBLE,
    us10y   DOUBLE,
    wti     DOUBLE,
    sox     DOUBLE
);

CREATE TABLE IF NOT EXISTS universe_features_daily (
    date   DATE    NOT NULL,
    ticker VARCHAR NOT NULL,
    PRIMARY KEY (date, ticker),

    -- ── Section A: Layer3 학습용 v2 on-the-fly 피처 ──────────────────────
    ma_cross_5_20          SMALLINT,   -- MA5 >= MA20 이면 1
    obv_slope_5d           DOUBLE,
    high_low_ratio         DOUBLE,
    body_ratio             DOUBLE,
    short_balance_ratio    DOUBLE,
    short_volume_ratio_5d  DOUBLE,
    short_balance_change_5d DOUBLE,
    volume_surge_ratio     DOUBLE,
    amount_surge_ratio     DOUBLE,
    price_momentum_3d      DOUBLE,
    price_momentum_10d     DOUBLE,
    inst_net_20d           DOUBLE,
    foreign_exh_change_5d  DOUBLE,
    roe_proxy              DOUBLE,
    relative_strength_5d   DOUBLE,
    combined_net_5d        DOUBLE,
    kospi_above_ma60       SMALLINT,
    market_volatility_20d  DOUBLE,
    grade_S                SMALLINT,
    grade_A                SMALLINT,
    grade_B                SMALLINT,

    -- ── Section B: Tier 1 신규 피처 (Layer3 학습 포함) ───────────────────
    bb_width               DOUBLE,
    atr_14                 DOUBLE,
    atr_ratio_60d          DOUBLE,
    volume_zscore_20d      DOUBLE,
    amount_zscore_20d      DOUBLE,
    rs_20d                 DOUBLE,
    rs_rank_pct            DOUBLE,   -- cross-sectional percentile rank
    market_breadth         DOUBLE,   -- 당일 상승종목 비율 (전체 동일값)
    breakout_distance_20d  DOUBLE,
    box_tightness_20d      DOUBLE,

    -- ── Section C: Layer2 패턴용 raw 피처 (DB 저장만, 학습 미포함) ────────
    breakout_distance_60d  DOUBLE,
    breakout_distance_120d DOUBLE,
    range_80d_pct          DOUBLE,
    distance_from_ma224    DOUBLE,
    up_days_5d             SMALLINT, -- 최근 5일 중 상승일 수 (consecutive_up_days 근사)
    gap_percent            DOUBLE,
    opening_strength       DOUBLE,
    intraday_close_strength DOUBLE,
    recovery_from_low_80d  DOUBLE,
    volume_acceleration    DOUBLE,   -- avg_vol_5d / avg_vol_20d
    volume_dryup_ratio     DOUBLE,   -- avg_vol_5d / avg_vol_60d
    retracement_ratio      DOUBLE,
    pullback_depth         DOUBLE,
    bb_width_pct_252       DOUBLE,   -- bb_width / 252일 평균 (정규화 밴드폭)
    turnover_rank_pct      DOUBLE,   -- date별 거래회전율 percentile rank
    amount_rank_pct        DOUBLE,   -- date별 거래대금 percentile rank
    volatility_rank_pct    DOUBLE    -- date별 변동성(box_tightness) percentile rank
);

CREATE TABLE IF NOT EXISTS universe_daily (
    date              DATE,
    ticker            VARCHAR,
    PRIMARY KEY (date, ticker),

    close             DOUBLE,
    volume            BIGINT,
    market_cap        BIGINT,
    per               DOUBLE,
    pbr               DOUBLE,
    turnover_rate     DOUBLE,

    trend_score       SMALLINT,
    ma5_ratio         DOUBLE,
    ma20_ratio        DOUBLE,
    ma60_ratio        DOUBLE,
    ma120_ratio       DOUBLE,
    rsi_14            DOUBLE,
    bb_position       DOUBLE,
    hist_vol_20d      DOUBLE,
    close_to_52w_high DOUBLE,

    foreign_net_5d    DOUBLE,
    inst_net_5d       DOUBLE,
    foreign_net_20d   DOUBLE,
    volume_surge_5d   DOUBLE,

    kospi_ret_5d      DOUBLE,
    kospi_ret_20d     DOUBLE,

    vol_score_approx  SMALLINT,
    vol_score_live    SMALLINT,
    grade_approx      VARCHAR,
    grade_live        VARCHAR,

    pred_3d_3pct_clean    DOUBLE,
    pred_3d_5pct_clean    DOUBLE,
    pred_3d_10pct_clean   DOUBLE,
    pred_5d_3pct_clean    DOUBLE,
    pred_5d_5pct_clean    DOUBLE,
    pred_5d_10pct_clean   DOUBLE,
    pred_10d_3pct_clean   DOUBLE,
    pred_10d_5pct_clean   DOUBLE,
    pred_10d_10pct_clean  DOUBLE,
    pred_first_3d_3pct    DOUBLE,
    pred_first_3d_5pct    DOUBLE,
    pred_first_3d_10pct   DOUBLE,
    pred_first_5d_3pct    DOUBLE,
    pred_first_5d_5pct    DOUBLE,
    pred_first_5d_10pct   DOUBLE,
    pred_first_10d_3pct   DOUBLE,
    pred_first_10d_5pct   DOUBLE,
    pred_first_10d_10pct  DOUBLE,

    entry_price        DOUBLE,
    label_3d_3pct_clean   BOOLEAN,
    label_3d_5pct_clean   BOOLEAN,
    label_3d_10pct_clean  BOOLEAN,
    label_5d_3pct_clean   BOOLEAN,
    label_5d_5pct_clean   BOOLEAN,
    label_5d_10pct_clean  BOOLEAN,
    label_10d_3pct_clean  BOOLEAN,
    label_10d_5pct_clean  BOOLEAN,
    label_10d_10pct_clean BOOLEAN
);

CREATE TABLE IF NOT EXISTS feature_catalog (
    feature_name    VARCHAR PRIMARY KEY,
    section         VARCHAR,
    used_in_train   BOOLEAN,
    added_date      DATE
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_id        VARCHAR PRIMARY KEY,
    label_key       VARCHAR,
    model_type      VARCHAR,
    file_path       VARCHAR,
    train_date      DATE,
    status          VARCHAR DEFAULT 'production',
    oof_auc         DOUBLE,
    prec_at_20      DOUBLE,
    ret_at_20       DOUBLE
);

CREATE TABLE IF NOT EXISTS evaluation_history (
    eval_date       DATE,
    label_key       VARCHAR,
    model_type      VARCHAR,
    oof_auc         DOUBLE,
    prec_at_10      DOUBLE,
    prec_at_20      DOUBLE,
    brier           DOUBLE,
    PRIMARY KEY (eval_date, label_key, model_type)
);

CREATE TABLE IF NOT EXISTS universe_predictions (
    date       DATE    NOT NULL,
    ticker     VARCHAR NOT NULL,
    model_type VARCHAR NOT NULL,
    label      VARCHAR NOT NULL,
    prob       DOUBLE,
    PRIMARY KEY (date, ticker, model_type, label)
);

CREATE TABLE IF NOT EXISTS universe_outcomes (
    date         DATE    NOT NULL,
    ticker       VARCHAR NOT NULL,
    hold_days    INTEGER NOT NULL,
    entry_price  DOUBLE,
    max_close    DOUBLE,
    max_drawdown DOUBLE,
    return_close DOUBLE,
    PRIMARY KEY (date, ticker, hold_days)
);

CREATE TABLE IF NOT EXISTS live_eval_daily (
    eval_date       DATE    NOT NULL,
    prediction_date DATE    NOT NULL,
    label_key       VARCHAR NOT NULL,
    PRIMARY KEY (eval_date, prediction_date, label_key),
    n_pairs         INTEGER,
    brier           DOUBLE,
    base_rate       DOUBLE,
    prec_at_5       DOUBLE,
    prec_at_10      DOUBLE,
    prec_at_20      DOUBLE,
    prec_at_30      DOUBLE,
    lift_at_5       DOUBLE,
    lift_at_10      DOUBLE,
    lift_at_20      DOUBLE,
    lift_at_30      DOUBLE,
    ret_at_5        DOUBLE,
    ret_at_10       DOUBLE,
    ret_at_20       DOUBLE,
    ret_at_30       DOUBLE,
    ret_base        DOUBLE,
    spearman        DOUBLE
);
"""


def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH), read_only=read_only)


_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS universe_features_daily (
    date   DATE    NOT NULL,
    ticker VARCHAR NOT NULL,
    PRIMARY KEY (date, ticker),
    ma_cross_5_20          SMALLINT,
    obv_slope_5d           DOUBLE,
    high_low_ratio         DOUBLE,
    body_ratio             DOUBLE,
    short_balance_ratio    DOUBLE,
    short_volume_ratio_5d  DOUBLE,
    short_balance_change_5d DOUBLE,
    volume_surge_ratio     DOUBLE,
    amount_surge_ratio     DOUBLE,
    price_momentum_3d      DOUBLE,
    price_momentum_10d     DOUBLE,
    inst_net_20d           DOUBLE,
    foreign_exh_change_5d  DOUBLE,
    roe_proxy              DOUBLE,
    relative_strength_5d   DOUBLE,
    combined_net_5d        DOUBLE,
    kospi_above_ma60       SMALLINT,
    market_volatility_20d  DOUBLE,
    grade_S                SMALLINT,
    grade_A                SMALLINT,
    grade_B                SMALLINT,
    bb_width               DOUBLE,
    atr_14                 DOUBLE,
    atr_ratio_60d          DOUBLE,
    volume_zscore_20d      DOUBLE,
    amount_zscore_20d      DOUBLE,
    rs_20d                 DOUBLE,
    rs_rank_pct            DOUBLE,
    market_breadth         DOUBLE,
    breakout_distance_20d  DOUBLE,
    box_tightness_20d      DOUBLE,
    breakout_distance_60d  DOUBLE,
    breakout_distance_120d DOUBLE,
    range_80d_pct          DOUBLE,
    distance_from_ma224    DOUBLE,
    up_days_5d             SMALLINT,
    gap_percent            DOUBLE,
    opening_strength       DOUBLE,
    intraday_close_strength DOUBLE,
    recovery_from_low_80d  DOUBLE,
    volume_acceleration    DOUBLE,
    volume_dryup_ratio     DOUBLE,
    retracement_ratio      DOUBLE,
    pullback_depth         DOUBLE
);
ALTER TABLE universe_features_daily ADD COLUMN IF NOT EXISTS bb_width_pct_252    DOUBLE;
ALTER TABLE universe_features_daily ADD COLUMN IF NOT EXISTS turnover_rank_pct   DOUBLE;
ALTER TABLE universe_features_daily ADD COLUMN IF NOT EXISTS amount_rank_pct     DOUBLE;
ALTER TABLE universe_features_daily ADD COLUMN IF NOT EXISTS volatility_rank_pct DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS per             DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS pbr             DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS eps             BIGINT;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS bps             BIGINT;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS div_yield       DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS foreign_exh_rate DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS short_volume    BIGINT;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS short_ratio     DOUBLE;
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS scoring_version VARCHAR;
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS ensemble_prob DOUBLE;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_3pct_clean   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_5pct_clean   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_10pct_clean  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_3pct_clean   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_5pct_clean   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_10pct_clean  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_3pct_clean  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_5pct_clean  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_10pct_clean BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_3d_3pct   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_3d_5pct   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_3d_10pct  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_5d_3pct   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_5d_5pct   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_5d_10pct  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_10d_3pct  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_10d_5pct  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_first_10d_10pct BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_3d_3pct_clean   BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_3d_5pct_clean   BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_3d_10pct_clean  BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_5d_3pct_clean   BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_5d_5pct_clean   BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_5d_10pct_clean  BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_10d_3pct_clean  BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_10d_5pct_clean  BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS label_10d_10pct_clean BOOLEAN;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_3d_3pct_clean    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_3d_5pct_clean    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_3d_10pct_clean   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_5d_3pct_clean    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_5d_5pct_clean    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_5d_10pct_clean   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_10d_3pct_clean   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_10d_5pct_clean   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_10d_10pct_clean  DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_3d_3pct    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_3d_5pct    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_3d_10pct   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_5d_3pct    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_5d_5pct    DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_5d_10pct   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_10d_3pct   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_10d_5pct   DOUBLE;
ALTER TABLE universe_daily ADD COLUMN IF NOT EXISTS pred_first_10d_10pct  DOUBLE;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_3d_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_3d_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_5d_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_5d_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_5d_10pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_10d_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_10d_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS c2_10d_10pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_3d_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_3d_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_3d_10pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_5d_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_5d_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_5d_10pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_10d_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_10d_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_10d_10pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_3d_3pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_3d_5pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_5d_3pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_5d_5pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_5d_10pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_10d_3pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_10d_5pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_10d_10pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_3d_3pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_3d_5pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_3d_10pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_5d_3pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_5d_5pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_5d_10pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_10d_3pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_10d_5pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_10d_10pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_3d_3pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_3d_5pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_5d_3pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_5d_5pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_5d_10pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_10d_3pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_10d_5pct_c2;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_10d_10pct_c2;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_first_up_3pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_first_up_5pct;
ALTER TABLE backtest_labels DROP COLUMN IF EXISTS label_first_up_10pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_first_up_3pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_first_up_5pct;
ALTER TABLE universe_daily DROP COLUMN IF EXISTS label_first_up_10pct;

-- P5.5: 모델 버전관리 + 예측 추적
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS version          VARCHAR;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS feature_set_hash VARCHAR;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS n_features       INTEGER;
ALTER TABLE universe_predictions ADD COLUMN IF NOT EXISTS model_ver  VARCHAR;

-- SMA 백테스트 결과
CREATE TABLE IF NOT EXISTS sma_backtest_results (
    ticker          VARCHAR,
    run_date        DATE,
    sma_period      INTEGER,
    confirm_days    INTEGER,
    lookback_days   INTEGER,
    drawdown_pct    DOUBLE,
    is_walkforward  BOOLEAN,
    window_start    DATE,
    window_end      DATE,
    calmar          DOUBLE,
    cagr            DOUBLE,
    mdd             DOUBLE,
    win_rate        DOUBLE,
    profit_factor   DOUBLE,
    ev              DOUBLE,
    total_trades    INTEGER,
    vs_buyhold      DOUBLE,
    PRIMARY KEY (ticker, run_date, sma_period, confirm_days,
                 lookback_days, drawdown_pct, window_start)
);

-- SMA 백테스트 v2 (전략 확장형)
CREATE TABLE IF NOT EXISTS sma_backtest_v2 (
    run_id              VARCHAR,
    ticker              VARCHAR,
    run_date            DATE,
    strategy            VARCHAR,
    is_walkforward      BOOLEAN,
    window_start        DATE,
    window_end          DATE,
    sma_period          INTEGER,
    pullback_sma_delta  INTEGER,
    stop_loss_pct       DOUBLE,
    calmar              DOUBLE,
    cagr                DOUBLE,
    mdd                 DOUBLE,
    win_rate            DOUBLE,
    profit_factor       DOUBLE,
    ev                  DOUBLE,
    total_trades        INTEGER,
    vs_buyhold          DOUBLE,
    PRIMARY KEY (run_id, ticker, strategy, window_start)
);

-- 라이브 예측 일별 평가 결과
CREATE TABLE IF NOT EXISTS live_eval_daily (
    eval_date       DATE    NOT NULL,
    prediction_date DATE    NOT NULL,
    label_key       VARCHAR NOT NULL,
    PRIMARY KEY (eval_date, prediction_date, label_key),
    n_pairs         INTEGER,
    brier           DOUBLE,
    base_rate       DOUBLE,
    prec_at_3       DOUBLE,
    prec_at_5       DOUBLE,
    prec_at_10      DOUBLE,
    prec_at_20      DOUBLE,
    prec_at_30      DOUBLE,
    lift_at_3       DOUBLE,
    lift_at_5       DOUBLE,
    lift_at_10      DOUBLE,
    lift_at_20      DOUBLE,
    lift_at_30      DOUBLE,
    ret_at_3        DOUBLE,
    ret_at_5        DOUBLE,
    ret_at_10       DOUBLE,
    ret_at_20       DOUBLE,
    ret_at_30       DOUBLE,
    ret_base        DOUBLE,
    spearman        DOUBLE
);
ALTER TABLE live_eval_daily ADD COLUMN IF NOT EXISTS prec_at_3 DOUBLE;
ALTER TABLE live_eval_daily ADD COLUMN IF NOT EXISTS lift_at_3 DOUBLE;
ALTER TABLE live_eval_daily ADD COLUMN IF NOT EXISTS ret_at_3  DOUBLE;
"""


def _migrate_backtest_labels(conn) -> None:
    """max_high_* → max_close_* 컬럼 rename (기존 DB 1회 적용)."""
    try:
        conn.execute("ALTER TABLE backtest_labels RENAME COLUMN max_high_3d TO max_close_3d")
        conn.execute("ALTER TABLE backtest_labels RENAME COLUMN max_high_5d TO max_close_5d")
        conn.execute("ALTER TABLE backtest_labels RENAME COLUMN max_high_10d TO max_close_10d")
    except Exception:
        pass  # 이미 변경됐거나 컬럼 없음


def _migrate_xgb_to_ensemble(conn) -> None:
    """xgb_prob → ensemble_prob 컬럼 rename (기존 DB 1회 적용)."""
    try:
        conn.execute("ALTER TABLE signal_history RENAME COLUMN xgb_prob TO ensemble_prob")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE signal_xgb_probs RENAME COLUMN xgb_prob TO ensemble_prob")
    except Exception:
        pass


# (feature_name, section, used_in_train) — feature_engineering.py 기준
_FEATURE_CATALOG_ROWS = [
    # Section A
    ("ma_cross_5_20",          "A", True),
    ("obv_slope_5d",           "A", True),
    ("high_low_ratio",         "A", True),
    ("body_ratio",             "A", True),
    ("short_balance_ratio",    "A", True),
    ("short_volume_ratio_5d",  "A", True),
    ("short_balance_change_5d","A", True),
    ("volume_surge_ratio",     "A", True),
    ("amount_surge_ratio",     "A", True),
    ("price_momentum_3d",      "A", True),
    ("price_momentum_10d",     "A", True),
    ("inst_net_20d",           "A", True),
    ("foreign_exh_change_5d",  "A", True),
    ("roe_proxy",              "A", True),
    ("relative_strength_5d",   "A", True),
    ("combined_net_5d",        "A", True),
    ("kospi_above_ma60",       "A", True),
    ("market_volatility_20d",  "A", True),
    ("grade_S",                "A", True),
    ("grade_A",                "A", True),
    ("grade_B",                "A", True),
    # Section B
    ("bb_width",               "B", True),
    ("atr_14",                 "B", True),
    ("atr_ratio_60d",          "B", True),
    ("volume_zscore_20d",      "B", True),
    ("amount_zscore_20d",      "B", True),
    ("rs_20d",                 "B", True),
    ("rs_rank_pct",            "B", True),
    ("market_breadth",         "B", True),
    ("breakout_distance_20d",  "B", True),
    ("box_tightness_20d",      "B", True),
    # Section C (layer2, DB 저장만 — 학습 미사용)
    ("breakout_distance_60d",  "C", False),
    ("breakout_distance_120d", "C", False),
    ("range_80d_pct",          "C", False),
    ("distance_from_ma224",    "C", False),
    ("up_days_5d",             "C", False),
    ("gap_percent",            "C", False),
    ("opening_strength",       "C", False),
    ("intraday_close_strength","C", False),
    ("recovery_from_low_80d",  "C", False),
    ("volume_acceleration",    "C", False),
    ("volume_dryup_ratio",     "C", False),
    ("retracement_ratio",      "C", False),
    ("pullback_depth",         "C", False),
    ("bb_width_pct_252",       "C", False),
    ("turnover_rank_pct",      "C", False),
    ("amount_rank_pct",        "C", False),
    ("volatility_rank_pct",    "C", False),
]


def _migrate_model_registry_versioning(conn) -> None:
    """기존 model_registry 행: model_id에 @train_date 추가 + version 세팅 (1회 적용)."""
    try:
        conn.execute("""
            UPDATE model_registry
            SET version = CAST(train_date AS VARCHAR),
                model_id = model_id || '@' || CAST(train_date AS VARCHAR)
            WHERE version IS NULL AND train_date IS NOT NULL
        """)
    except Exception:
        pass


def _seed_feature_catalog(conn) -> None:
    today = date.today().isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO feature_catalog (feature_name, section, used_in_train, added_date)"
        " VALUES (?, ?, ?, ?)",
        [(name, sec, used, today) for name, sec, used in _FEATURE_CATALOG_ROWS],
    )


def _upsert_model_registry(
    conn,
    label_key: str,
    mtype: str,
    fpath: str,
    train_date: str,
    auc: float | None,
    prec10: float | None,
    prec20: float | None,
    ret20: float | None,
    brier: float | None = None,
    feat_hash: str | None = None,
    n_feat: int | None = None,
) -> None:
    """model_registry + evaluation_history upsert (버전 포함).

    같은 (label_key, model_type)의 기존 production → retired 전이 후 신규 production INSERT.
    같은 train_date 재실행 시 INSERT OR REPLACE로 멱등.
    """
    model_id = f"{mtype}_{label_key}@{train_date}"
    conn.execute(
        "UPDATE model_registry SET status='retired'"
        " WHERE label_key=? AND model_type=? AND status='production' AND model_id<>?",
        [label_key, mtype, model_id],
    )
    conn.execute(
        "INSERT OR REPLACE INTO model_registry"
        " (model_id, label_key, model_type, file_path, train_date, status,"
        "  oof_auc, prec_at_20, ret_at_20, version, feature_set_hash, n_features)"
        " VALUES (?,?,?,?,?, 'production', ?,?,?,?,?,?)",
        [model_id, label_key, mtype, fpath, train_date,
         auc, prec20, ret20, train_date, feat_hash, n_feat],
    )
    conn.execute(
        "INSERT OR REPLACE INTO evaluation_history"
        " (eval_date, label_key, model_type, oof_auc, prec_at_10, prec_at_20, brier)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [train_date, label_key, mtype, auc, prec10, prec20, brier],
    )


def register_models_from_json(
    results_path: str = "data/model_results.json",
    train_date: str | None = None,
) -> None:
    """model_results.json → model_registry + evaluation_history 일괄 등록."""
    rp = Path(results_path)
    if not rp.exists():
        print(f"파일 없음: {rp}")
        return
    with open(rp, encoding="utf-8") as f:
        summary = json.load(f)
    if train_date is None:
        train_date = date.today().isoformat()
    out_dir = Path("data/models")
    ext_map = {"xgb": ".json", "lgbm": ".txt", "et": ".pkl"}
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
                    feat_hash=None,
                    n_feat=None,
                )
        print(f"등록 완료: {len(summary)}라벨 × {len(ext_map)}모델")
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        # old long-format backtest_labels (hold_days 컬럼 존재) → DROP 후 재생성
        cols = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='backtest_labels'"
        ).fetchall()
        if any(c[0] == 'hold_days' for c in cols):
            conn.execute("DROP TABLE backtest_labels")
        conn.execute(_DDL)
        conn.execute(_MIGRATIONS)
        _migrate_backtest_labels(conn)
        _migrate_xgb_to_ensemble(conn)
        _migrate_model_registry_versioning(conn)
        _seed_feature_catalog(conn)
        # 기존 signal_history 레코드에 scoring_version 소급 설정
        # vol_score <= 6 은 일봉 근사 backfill, 초과는 60분봉 실시간 구버전
        conn.execute("""
            UPDATE signal_history
            SET scoring_version = CASE WHEN vol_score <= 6 THEN 'backfill' ELSE 'live_v1' END
            WHERE scoring_version IS NULL
        """)


if __name__ == "__main__":
    init_db()
    with get_conn(read_only=True) as conn:
        tables = conn.execute("SHOW TABLES").fetchall()
    print("Tables:", [t[0] for t in tables])
