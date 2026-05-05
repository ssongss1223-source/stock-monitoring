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
            "index_1m_uptrend": _uptrend(close, 21),
            "index_above_20ma": _above_ma(close, 20),
            "foreign_futures_net_buy": _foreign_net_buy(market, today),
            "sector_strong_ratio_70pct": _sector_ratio(market, today),
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
    Phase 1: 현물 시장 외국인 순매수로 근사.
    TODO Phase 2: KOSPI200 선물 실제 데이터로 교체.
    """
    try:
        df = stock.get_market_trading_value_by_date(_ago(10), today, market)
        if df is None or df.empty:
            return False
        for col in ("외국인합계", "외국인", "foreigners"):
            if col in df.columns:
                return float(df[col].iloc[-1]) > 0
        return False
    except Exception:
        logger.debug("외국인 순매수 조회 실패 → False")
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


def _default_context(market: str, engine: ScoringEngine) -> MarketContext:
    """데이터 없음 → 모든 플래그 False → score=0 → bear (보수적)."""
    return engine.score_market({"market": market})


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")
