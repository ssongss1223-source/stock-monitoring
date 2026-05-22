import duckdb
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
    signal_date DATE,
    ticker      VARCHAR,
    vol_score   INT,
    grade       VARCHAR,
    features    JSON,
    entry_price DOUBLE,
    xgb_prob    DOUBLE,
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
    c2_3d_3pct       INT,
    c2_3d_5pct       INT,
    c2_5d_3pct       INT,
    c2_5d_5pct       INT,
    c2_5d_10pct      INT,
    c2_10d_3pct      INT,
    c2_10d_5pct      INT,
    c2_10d_10pct     INT,
    label_3d_3pct    BOOLEAN,
    label_3d_5pct    BOOLEAN,
    label_3d_10pct   BOOLEAN,
    label_5d_3pct    BOOLEAN,
    label_5d_5pct    BOOLEAN,
    label_5d_10pct   BOOLEAN,
    label_10d_3pct   BOOLEAN,
    label_10d_5pct   BOOLEAN,
    label_10d_10pct  BOOLEAN,
    label_3d_3pct_c2  BOOLEAN,
    label_3d_5pct_c2  BOOLEAN,
    label_5d_3pct_c2  BOOLEAN,
    label_5d_5pct_c2  BOOLEAN,
    label_5d_10pct_c2 BOOLEAN,
    label_10d_3pct_c2 BOOLEAN,
    label_10d_5pct_c2 BOOLEAN,
    label_10d_10pct_c2 BOOLEAN,
    PRIMARY KEY (signal_date, ticker)
);

CREATE TABLE IF NOT EXISTS signal_xgb_probs (
    signal_date DATE,
    ticker      VARCHAR,
    label       VARCHAR,
    xgb_prob    DOUBLE,
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

    pred_3d_3pct      DOUBLE,
    pred_3d_5pct      DOUBLE,
    pred_3d_10pct     DOUBLE,
    pred_5d_3pct      DOUBLE,
    pred_5d_5pct      DOUBLE,
    pred_5d_10pct     DOUBLE,
    pred_10d_3pct     DOUBLE,
    pred_10d_5pct     DOUBLE,
    pred_10d_10pct    DOUBLE,
    pred_3d_3pct_c2   DOUBLE,
    pred_3d_5pct_c2   DOUBLE,
    pred_5d_3pct_c2   DOUBLE,
    pred_5d_5pct_c2   DOUBLE,
    pred_5d_10pct_c2  DOUBLE,
    pred_10d_3pct_c2  DOUBLE,
    pred_10d_5pct_c2  DOUBLE,
    pred_10d_10pct_c2 DOUBLE,

    entry_price        DOUBLE,
    label_3d_3pct      BOOLEAN,
    label_3d_5pct      BOOLEAN,
    label_3d_10pct     BOOLEAN,
    label_5d_3pct      BOOLEAN,
    label_5d_5pct      BOOLEAN,
    label_5d_10pct     BOOLEAN,
    label_10d_3pct     BOOLEAN,
    label_10d_5pct     BOOLEAN,
    label_10d_10pct    BOOLEAN,
    label_3d_3pct_c2   BOOLEAN,
    label_3d_5pct_c2   BOOLEAN,
    label_5d_3pct_c2   BOOLEAN,
    label_5d_5pct_c2   BOOLEAN,
    label_5d_10pct_c2  BOOLEAN,
    label_10d_3pct_c2  BOOLEAN,
    label_10d_5pct_c2  BOOLEAN,
    label_10d_10pct_c2 BOOLEAN
);
"""


def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH), read_only=read_only)


_MIGRATIONS = """
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS per             DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS pbr             DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS eps             BIGINT;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS bps             BIGINT;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS div_yield       DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS foreign_exh_rate DOUBLE;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS short_volume    BIGINT;
ALTER TABLE ohlcv_daily ADD COLUMN IF NOT EXISTS short_ratio     DOUBLE;
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS scoring_version VARCHAR;
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS xgb_prob DOUBLE;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_3d_3pct   INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_3d_5pct   INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_5d_3pct   INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_5d_5pct   INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_5d_10pct  INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_10d_3pct  INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_10d_5pct  INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS c2_10d_10pct INT;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_3pct     BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_5pct     BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_10pct    BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_3pct     BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_5pct     BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_10pct    BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_3pct    BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_5pct    BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_10pct   BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_3pct_c2  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_3d_5pct_c2  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_3pct_c2  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_5pct_c2  BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_5d_10pct_c2 BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_3pct_c2 BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_5pct_c2 BOOLEAN;
ALTER TABLE backtest_labels ADD COLUMN IF NOT EXISTS label_10d_10pct_c2 BOOLEAN;
"""


def _migrate_backtest_labels(conn) -> None:
    """max_high_* → max_close_* 컬럼 rename (기존 DB 1회 적용)."""
    try:
        conn.execute("ALTER TABLE backtest_labels RENAME COLUMN max_high_3d TO max_close_3d")
        conn.execute("ALTER TABLE backtest_labels RENAME COLUMN max_high_5d TO max_close_5d")
        conn.execute("ALTER TABLE backtest_labels RENAME COLUMN max_high_10d TO max_close_10d")
    except Exception:
        pass  # 이미 변경됐거나 컬럼 없음


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
