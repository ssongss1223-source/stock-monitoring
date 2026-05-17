import asyncio
import logging
from datetime import date, timedelta

import pandas as pd
from pykrx import stock

from core.scoring_engine import ScoringEngine
from models.signals import MarketContext

logger = logging.getLogger(__name__)

_INDEX = {"KOSPI": "1001", "KOSDAQ": "2001"}

# pykrx에서 업종 지수 목록 조회 시 제외할 메인 지수 티커
_MAIN_IDX = {
    "KOSPI": {"1001", "1002", "1003", "1004", "1005"},
    "KOSDAQ": {"2001", "2002"},
}


class MarketFilterAgent:
    """
    KOSPI/KOSDAQ 장세를 독립적으로 판단하여 MarketContext를 반환한다.
    출력: {"KOSPI": MarketContext, "KOSDAQ": MarketContext}
    Phase 2에서 매크로 센티멘트(뉴스/금리/환율) 추가 예정.
    """

    def __init__(self, engine: ScoringEngine):
        self.engine = engine

    async def run(self) -> dict[str, MarketContext]:
        loop = asyncio.get_running_loop()
        today = _today()
        start = _ago(270)  # 9개월치 (6개월 추세 계산에 여유 확보)

        kospi, kosdaq = await asyncio.gather(
            loop.run_in_executor(None, self._analyze, "KOSPI", today, start),
            loop.run_in_executor(None, self._analyze, "KOSDAQ", today, start),
        )
        logger.info(
            "MarketFilter | KOSPI: %s score=%d bias=%d | KOSDAQ: %s score=%d bias=%d",
            kospi.market_status, kospi.score, kospi.market_bias,
            kosdaq.market_status, kosdaq.score, kosdaq.market_bias,
        )
        return {"KOSPI": kospi, "KOSDAQ": kosdaq}

    def _analyze(self, market: str, today: str, start: str) -> MarketContext:
        try:
            df = stock.get_index_ohlcv_by_date(start, today, _INDEX[market])
        except Exception as e:
            logger.warning("%s 인덱스 데이터 조회 실패: %s", market, e)
            return _default_context(market, self.engine)

        if df is None or df.empty:
            logger.warning("%s 인덱스 데이터 없음", market)
            return _default_context(market, self.engine)

        close = _close(df)
        flags = {
            "market": market,
            "index_6m_uptrend": _uptrend(close, 126),
            "index_3m_uptrend": _uptrend(close, 63),
            "index_above_60ma":  _above_ma(close, 60),
            "index_above_120ma": _above_ma(close, 120),
            "foreign_futures_net_buy": _foreign_net_buy(market, today),
            "sector_strong_ratio_70pct": _sector_ratio(market, today),
            **_macro_flags(),
            **_rv_flag(),
        }
        return self.engine.score_market(flags)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _close(df: pd.DataFrame) -> pd.Series:
    """pykrx 버전별 컬럼명 차이 처리."""
    for col in ("종가", "Close", "close"):
        if col in df.columns:
            return df[col].astype(float)
    return df.iloc[:, 3].astype(float)


def _uptrend(close: pd.Series, lookback: int) -> bool:
    """현재가 > lookback 거래일 전 가격."""
    if len(close) < lookback + 1:
        return False
    return float(close.iloc[-1]) > float(close.iloc[-(lookback + 1)])


def _above_ma(close: pd.Series, window: int) -> bool:
    if len(close) < window:
        return False
    return float(close.iloc[-1]) > float(close.rolling(window).mean().iloc[-1])


def _foreign_net_buy(market: str, today: str) -> bool:
    """
    외국인 순매수 여부.
    ohlcv_daily.foreign_net 전종목 합산 기반 (DB-only).
    ticker_master.market 미구축으로 KOSPI/KOSDAQ 구분 없이 합산.
    """
    try:
        from data.db import get_conn
        conn = get_conn(read_only=True)
        try:
            row = conn.execute("""
                SELECT SUM(foreign_net)
                FROM ohlcv_daily
                WHERE date = (SELECT MAX(date) FROM ohlcv_daily)
            """).fetchone()
        finally:
            conn.close()
        if row and row[0] is not None:
            logger.debug("%s 외국인 순매수(전종목): %.0f", market, row[0])
            return float(row[0]) > 0
        return False
    except Exception as e:
        logger.warning("외국인 순매수 DB 조회 실패: %s → False", e)
        return False


def _sector_ratio(market: str, today: str) -> bool:
    """
    업종 지수의 1개월 상승 비율 >= 70%.
    Phase 1: pykrx 업종 인덱스 티커 목록에서 최대 20개 섹터 체크.
    """
    try:
        all_idx = stock.get_index_ticker_list(today, market=market)
        sectors = [t for t in (all_idx or []) if t not in _MAIN_IDX[market]][:20]
        if not sectors:
            return False

        strong = total = 0
        for ticker in sectors:
            try:
                df = stock.get_index_ohlcv_by_date(_ago(40), today, ticker)
                if df is None or df.empty or len(df) < 22:
                    continue
                if _uptrend(_close(df), 21):
                    strong += 1
                total += 1
            except Exception:
                continue

        if total == 0:
            return False
        ratio = strong / total
        logger.debug("%s 섹터 강세 비율: %.0f%% (%d/%d)", market, ratio * 100, strong, total)
        return ratio >= 0.70

    except Exception:
        logger.debug("섹터 강세 비율 조회 실패 → False")
        return False


def _rv_flag() -> dict:
    """KOSPI 20일 실현변동성 < 20% 이면 True (저변동성 = 평온한 시장)."""
    try:
        import numpy as np
        from data.db import get_conn
        conn = get_conn(read_only=True)
        try:
            df = conn.execute(
                "SELECT close FROM market_index WHERE ticker='1001' ORDER BY date DESC LIMIT 25"
            ).df()
        finally:
            conn.close()

        if len(df) < 22:
            logger.warning("market_index 데이터 부족(%d행) → rv_flag False", len(df))
            return {"kospi_rv20_low": False}

        close = df["close"].iloc[::-1].astype(float).values
        log_ret = np.log(close[1:] / close[:-1])
        rv = float(log_ret[-20:].std() * np.sqrt(252) * 100)
        flag = rv <= 20.0
        logger.debug("KOSPI 실현변동성(20일): %.1f%% → kospi_rv20_low=%s", rv, flag)
        return {"kospi_rv20_low": flag}
    except Exception as e:
        logger.warning("실현변동성 계산 실패: %s → False", e)
        return {"kospi_rv20_low": False}


def _macro_flags() -> dict:
    """macro_daily DB에서 글로벌 매크로 플래그 계산."""
    try:
        from data.store import MacroStore
        df = MacroStore.load(lookback_days=60)
        if df.empty or len(df) < 22:
            logger.warning("macro_daily 데이터 부족(%d행) → 매크로 플래그 False", len(df))
            return {"sp500_above_20ma": False, "usdkrw_below_ma20": False, "sox_uptrend": False}

        sp500  = df["sp500"].dropna()
        usdkrw = df["usdkrw"].dropna()
        sox    = df["sox"].dropna()

        sp500_flag  = len(sp500)  >= 20 and float(sp500.iloc[-1])  > float(sp500.rolling(20).mean().iloc[-1])
        usdkrw_flag = len(usdkrw) >= 20 and float(usdkrw.iloc[-1]) < float(usdkrw.rolling(20).mean().iloc[-1])
        sox_flag    = len(sox)    >= 22 and float(sox.iloc[-1])    > float(sox.iloc[-22])

        logger.debug("매크로 플래그 | sp500_above_20ma=%s usdkrw_below_ma20=%s sox_uptrend=%s",
                     sp500_flag, usdkrw_flag, sox_flag)
        return {
            "sp500_above_20ma":  sp500_flag,
            "usdkrw_below_ma20": usdkrw_flag,
            "sox_uptrend":       sox_flag,
        }
    except Exception as e:
        logger.warning("매크로 플래그 계산 실패: %s → False", e)
        return {"sp500_above_20ma": False, "usdkrw_below_ma20": False, "sox_uptrend": False}


def _default_context(market: str, engine: ScoringEngine) -> MarketContext:
    """데이터 없음 → 모든 플래그 False → score=0 → bear (보수적)."""
    return engine.score_market({"market": market})


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")
