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
