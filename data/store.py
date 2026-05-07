import logging
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from pykrx import stock

from config import OHLCV_DAILY_DIR, OHLCV_HOURLY_DIR
from data.db import get_conn, init_db

logger = logging.getLogger(__name__)

# pykrx 컬럼명 → 소문자 영어 (Korean/English 모두 처리)
_PYKRX_RENAME = {
    "시가": "open", "고가": "high", "저가": "low",
    "종가": "close", "거래량": "volume", "거래대금": "amount",
    "Open": "open", "High": "high", "Low": "low",
    "Close": "close", "Volume": "volume",
}
_REQUIRED = ["open", "high", "low", "close", "volume"]

_DAILY_COLS = [
    "ticker", "date", "open", "high", "low", "close", "volume", "amount",
    "market_cap", "shares", "foreign_net", "inst_net", "short_balance",
]
_MIN_COLS = ["ticker", "dt", "open", "high", "low", "close", "volume", "amount", "source"]


# ── 정규화 ────────────────────────────────────────────────────────────────────

def _prep_daily(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """pykrx 일봉 DataFrame → ohlcv_daily 스키마."""
    df = df.rename(columns=_PYKRX_RENAME)

    # 이름 매핑 후에도 필수 컬럼이 없으면 위치 기반 fallback
    if not all(c in df.columns for c in _REQUIRED):
        pos_map = {col: req for col, req in zip(df.columns, _REQUIRED) if col not in _REQUIRED}
        df = df.rename(columns=pos_map)

    if not all(c in df.columns for c in _REQUIRED):
        logger.warning("%s 일봉 컬럼 매핑 실패: %s", ticker, list(df.columns))
        return pd.DataFrame()

    df["ticker"] = ticker
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    for c in _DAILY_COLS:
        if c not in df.columns:
            df[c] = None

    return df[_DAILY_COLS]


def _prep_min(df: pd.DataFrame, ticker: str, source: str) -> pd.DataFrame:
    """60분봉 DataFrame → ohlcv_min 스키마 (KIS/yfinance 모두 처리)."""
    df = df.rename(columns=_PYKRX_RENAME)

    if not all(c in df.columns for c in _REQUIRED):
        logger.warning("%s 60분봉 컬럼 매핑 실패: %s", ticker, list(df.columns))
        return pd.DataFrame()

    df["ticker"] = ticker
    df["source"] = source
    df["amount"] = df.get("amount", None)
    df.index.name = "dt"
    df = df.reset_index()
    df["dt"] = pd.to_datetime(df["dt"])

    for c in _MIN_COLS:
        if c not in df.columns:
            df[c] = None

    return df[_MIN_COLS]


# ── DuckDB upsert ─────────────────────────────────────────────────────────────

def _upsert_daily(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    conn.register("_d", df)
    conn.execute(f"""
        INSERT OR REPLACE INTO ohlcv_daily
        SELECT {', '.join(_DAILY_COLS)} FROM _d
    """)


def _upsert_min(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    conn.register("_m", df)
    conn.execute(f"""
        INSERT OR REPLACE INTO ohlcv_min
        SELECT {', '.join(_MIN_COLS)} FROM _m
    """)


# ── DuckDB read → agent 형식 ──────────────────────────────────────────────────

def _read_daily(conn, ticker: str) -> pd.DataFrame:
    df = conn.execute(
        "SELECT date, open, high, low, close, volume, amount "
        "FROM ohlcv_daily WHERE ticker = ? ORDER BY date",
        [ticker],
    ).df()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return (
        df.set_index("date")
        .rename(columns={"open": "Open", "high": "High", "low": "Low",
                         "close": "Close", "volume": "Volume", "amount": "Amount"})
    )


def _read_min(conn, ticker: str) -> pd.DataFrame:
    df = conn.execute(
        "SELECT dt, open, high, low, close, volume "
        "FROM ohlcv_min WHERE ticker = ? ORDER BY dt",
        [ticker],
    ).df()
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["dt"])
    return (
        df.set_index("dt")
        .rename(columns={"open": "Open", "high": "High", "low": "Low",
                         "close": "Close", "volume": "Volume"})
    )


# ── OhlcvStore ────────────────────────────────────────────────────────────────

class OhlcvStore:
    """일봉 OHLCV 영속화 (DuckDB)."""

    @staticmethod
    def fetch_and_update_daily(ticker: str, start_days: int = 900) -> pd.DataFrame | None:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(date) FROM ohlcv_daily WHERE ticker = ?", [ticker]
            ).fetchone()
            last_date = row[0] if row and row[0] is not None else None

            if last_date is not None:
                start_date = datetime.combine(last_date, datetime.min.time()) + timedelta(days=1)
            else:
                start_date = datetime.now() - timedelta(days=start_days)

            today = datetime.now()
            start_str = start_date.strftime("%Y%m%d")
            today_str = today.strftime("%Y%m%d")

            logger.debug("%s 일봉 조회: %s ~ %s", ticker, start_str, today_str)
            try:
                new_df = stock.get_market_ohlcv_by_date(start_str, today_str, ticker)
            except Exception as e:
                logger.warning("%s pykrx 조회 실패: %s", ticker, e)
                new_df = None

            if new_df is not None and not new_df.empty:
                _upsert_daily(conn, _prep_daily(new_df, ticker))

            result = _read_daily(conn, ticker)
        finally:
            conn.close()

        return result if not result.empty else None


# ── HourlyStore ───────────────────────────────────────────────────────────────

class HourlyStore:
    """60분봉 영속화 (DuckDB).

    수집 전략:
    - 데이터 없음       → yfinance period=60d (초기 히스토리)
    - 오늘 데이터 없음  → KIS 당일 1분봉 → 60분 리샘플 (primary)
                          실패 시 yfinance 증분 (fallback)
    - 오늘 데이터 있음  → 그대로 반환
    """

    @staticmethod
    def fetch_and_update_hourly(ticker: str, market: str = "KOSPI") -> pd.DataFrame | None:
        today = datetime.now().date()

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(dt) FROM ohlcv_min WHERE ticker = ?", [ticker]
            ).fetchone()
            last_dt = row[0] if row and row[0] is not None else None

            if last_dt is None:
                new_df = HourlyStore._init_yfinance(ticker, market)
                source = "yfinance"
            else:
                last_date = pd.to_datetime(last_dt).date()
                if last_date >= today:
                    logger.debug("%s 60분봉 최신 (%s), 스킵", ticker, last_date)
                    result = _read_min(conn, ticker)
                    return result if not result.empty else None
                new_df = HourlyStore._fetch_kis(ticker)
                source = "KIS"
                if new_df is None or new_df.empty:
                    logger.debug("%s KIS 없음, yfinance fallback", ticker)
                    new_df = HourlyStore._fetch_yfinance_incremental(ticker, market, last_dt)
                    source = "yfinance"

            if new_df is not None and not new_df.empty:
                _upsert_min(conn, _prep_min(new_df, ticker, source))

            result = _read_min(conn, ticker)
        finally:
            conn.close()

        return result if not result.empty else None

    @staticmethod
    def _fetch_kis(ticker: str) -> pd.DataFrame | None:
        try:
            from data.kis_client import KisClient
            return KisClient().fetch_today_60min(ticker)
        except Exception as e:
            logger.warning("%s KIS 60분봉 실패: %s", ticker, e)
            return None

    @staticmethod
    def _init_yfinance(ticker: str, market: str) -> pd.DataFrame | None:
        yf_ticker = ticker + (".KS" if market == "KOSPI" else ".KQ")
        try:
            df = yf.download(yf_ticker, period="60d", interval="1h",
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                return None
            return _normalize_yf(df)
        except Exception as e:
            logger.warning("%s yfinance 초기 수집 실패: %s", ticker, e)
            return None

    @staticmethod
    def _fetch_yfinance_incremental(
        ticker: str, market: str, last_dt
    ) -> pd.DataFrame | None:
        yf_ticker = ticker + (".KS" if market == "KOSPI" else ".KQ")
        try:
            start = pd.to_datetime(last_dt) + timedelta(hours=1)
            df = yf.download(yf_ticker, start=start, interval="1h",
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                return None
            return _normalize_yf(df)
        except Exception as e:
            logger.warning("%s yfinance 증분 실패: %s", ticker, e)
            return None


def _normalize_yf(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 응답 컬럼 정리 + UTC→KST 변환."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is not None:
        df = df.tz_convert("Asia/Seoul").tz_localize(None)
    else:
        df.index = pd.to_datetime(df.index)
        df = (
            df.set_index(pd.DatetimeIndex(df.index, tz="UTC"))
            .tz_convert("Asia/Seoul")
            .tz_localize(None)
        )
    return df


# ── 마이그레이션 ──────────────────────────────────────────────────────────────

def migrate_parquet_to_duckdb() -> None:
    """기존 Parquet 파일 → DuckDB 일괄 마이그레이션 (1회용)."""
    init_db()

    daily_files = list(Path(OHLCV_DAILY_DIR).glob("*.parquet"))
    hourly_files = list(Path(OHLCV_HOURLY_DIR).glob("*.parquet"))
    logger.info("마이그레이션 시작: 일봉 %d개, 60분봉 %d개", len(daily_files), len(hourly_files))

    conn = get_conn()
    try:
        daily_ok = daily_fail = 0
        for f in daily_files:
            ticker = f.stem
            try:
                df = pd.read_parquet(f)
                prepped = _prep_daily(df, ticker)
                if not prepped.empty:
                    _upsert_daily(conn, prepped)
                    daily_ok += 1
                else:
                    daily_fail += 1
            except Exception as e:
                logger.warning("%s 일봉 마이그레이션 실패: %s", ticker, e)
                daily_fail += 1

        hourly_ok = hourly_fail = 0
        for f in hourly_files:
            ticker = f.stem
            try:
                df = pd.read_parquet(f)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                prepped = _prep_min(df, ticker, "yfinance")
                if not prepped.empty:
                    _upsert_min(conn, prepped)
                    hourly_ok += 1
                else:
                    hourly_fail += 1
            except Exception as e:
                logger.warning("%s 60분봉 마이그레이션 실패: %s", ticker, e)
                hourly_fail += 1
    finally:
        conn.close()

    print(f"일봉:   {daily_ok}개 완료, {daily_fail}개 실패")
    print(f"60분봉: {hourly_ok}개 완료, {hourly_fail}개 실패")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    migrate_parquet_to_duckdb()
