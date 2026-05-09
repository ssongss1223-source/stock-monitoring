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
    PRIMARY KEY (signal_date, ticker)
);

CREATE TABLE IF NOT EXISTS backtest_labels (
    signal_date   DATE,
    ticker        VARCHAR,
    hold_days     INT,
    target_return DOUBLE,
    entry_price   DOUBLE,
    max_high      DOUBLE,
    min_low       DOUBLE,
    close_n       DOUBLE,
    label         TINYINT,
    max_drawdown  DOUBLE,
    return_n      DOUBLE,
    PRIMARY KEY (signal_date, ticker, hold_days, target_return)
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
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(_DDL)
        conn.execute(_MIGRATIONS)


if __name__ == "__main__":
    init_db()
    with get_conn(read_only=True) as conn:
        tables = conn.execute("SHOW TABLES").fetchall()
    print("Tables:", [t[0] for t in tables])
