import json
import logging
from datetime import date

import pandas as pd
from pykrx import stock

import config

logger = logging.getLogger(__name__)

# pykrx 인덱스 목록에서 제외할 메인 지수 티커 (섹터 인덱스만 남기기 위해)
_MAIN_IDX = {
    "KOSPI": {"1001", "1002", "1003", "1004", "1005"},
    "KOSDAQ": {"2001", "2002"},
}


class UniverseManager:
    """
    분석 대상 종목 목록을 결정한다.
    반환 형식: list[tuple[str, str]] — (ticker, "KOSPI"|"KOSDAQ")
    """

    def __init__(self):
        self.mode = config.UNIVERSE_MODE
        self.include_watchlist = config.INCLUDE_WATCHLIST

    def get_universe(self) -> list[tuple[str, str]]:
        today = date.today().strftime("%Y%m%d")

        if self.mode == "watchlist":
            result = _load_watchlist(today)
        elif self.mode == "top50_mktcap":
            result = _top_by_mktcap(today, 50)
        elif self.mode == "top100_mktcap":
            result = _top_by_mktcap(today, 100)
        elif self.mode == "top200_mktcap":
            result = _top_by_mktcap(today, 200)
        elif self.mode == "kospi200_daq150":
            result = _kospi200_daq150(today)
        elif self.mode == "sector_top5":
            result = _sector_top5(today)
        else:
            logger.warning("알 수 없는 UNIVERSE_MODE: %s → top100_mktcap으로 대체", self.mode)
            result = _top_by_mktcap(today, 100)

        if self.include_watchlist and self.mode != "watchlist":
            existing = {t for t, _ in result}
            for item in _load_watchlist(today):
                if item[0] not in existing:
                    result.append(item)
                    existing.add(item[0])

        logger.info("UniverseManager: %d종목 (mode=%s)", len(result), self.mode)
        return result


# ── 모드별 구현 ───────────────────────────────────────────────────────────────

def _top_by_mktcap(today: str, n: int) -> list[tuple[str, str]]:
    """KOSPI + KOSDAQ 합산 시총 상위 n개."""
    records: list[dict] = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_cap_by_ticker(today, market=market)
            if df is None or df.empty:
                continue
            cap_col = next((c for c in ("시가총액", "Mktcap") if c in df.columns), None)
            if cap_col is None:
                continue
            for ticker, row in df.iterrows():
                records.append({"ticker": ticker, "market": market, "cap": int(row[cap_col])})
        except Exception as e:
            logger.warning("%s 시총 데이터 조회 실패: %s", market, e)

    if not records:
        return []
    records.sort(key=lambda x: x["cap"], reverse=True)
    return [(r["ticker"], r["market"]) for r in records[:n]]


def _kospi200_daq150(today: str) -> list[tuple[str, str]]:
    """KOSPI200 + KOSDAQ150 구성종목."""
    result: list[tuple[str, str]] = []
    for index_code, market in (("1028", "KOSPI"), ("2203", "KOSDAQ")):
        try:
            tickers = stock.get_index_portfolio_deposit_file(index_code)
            if tickers:
                result.extend((t, market) for t in tickers)
        except Exception as e:
            logger.warning("%s(%s) 구성종목 조회 실패: %s", market, index_code, e)
    if not result:
        logger.warning("kospi200_daq150 조회 실패 → top200_mktcap으로 대체")
        result = _top_by_mktcap(today, 200)
    return result


def _sector_top5(today: str) -> list[tuple[str, str]]:
    """
    KOSPI/KOSDAQ 섹터 인덱스별 시총 상위 5종목.
    pykrx의 업종 인덱스 포트폴리오를 활용. 실패 시 top50으로 대체.
    """
    result: list[tuple[str, str]] = []
    existing: set[str] = set()

    for market in ("KOSPI", "KOSDAQ"):
        try:
            all_idx = stock.get_index_ticker_list(today, market=market)
            sectors = [t for t in (all_idx or []) if t not in _MAIN_IDX[market]]
            if not sectors:
                continue

            # 시총 캐시 (시장별 1번만 조회)
            try:
                cap_df = stock.get_market_cap_by_ticker(today, market=market)
                cap_col = next((c for c in ("시가총액", "Mktcap") if c in cap_df.columns), None)
            except Exception:
                cap_df, cap_col = None, None

            for sector_ticker in sectors:
                try:
                    constituents = stock.get_index_portfolio_deposit_file(sector_ticker)
                    if not constituents:
                        continue

                    top5 = _pick_top_n(constituents, cap_df, cap_col, n=5)
                    for t in top5:
                        if t not in existing:
                            result.append((t, market))
                            existing.add(t)

                except Exception:
                    continue

        except Exception as e:
            logger.warning("%s 섹터 유니버스 조회 실패: %s", market, e)

    if not result:
        logger.warning("sector_top5 실패 → top50_mktcap으로 대체")
        return _top_by_mktcap(today, 50)

    logger.debug("sector_top5: %d종목", len(result))
    return result


def _pick_top_n(
    tickers: list[str],
    cap_df: pd.DataFrame | None,
    cap_col: str | None,
    n: int,
) -> list[str]:
    """주어진 티커 중 시총 상위 n개. cap_df 없으면 앞 n개 반환."""
    if cap_df is None or cap_col is None:
        return tickers[:n]
    try:
        sub = cap_df[cap_df.index.isin(tickers)].sort_values(cap_col, ascending=False)
        return sub.index[:n].tolist()
    except Exception:
        return tickers[:n]


# ── Watchlist ─────────────────────────────────────────────────────────────────

def _load_watchlist(today: str) -> list[tuple[str, str]]:
    try:
        with open(config.WATCHLIST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        tickers = [t for t in data.get("stocks", []) if isinstance(t, str)]
        if not tickers:
            return []
        return _assign_markets(tickers, today)
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning("watchlist 로드 실패: %s", e)
        return []


def _assign_markets(tickers: list[str], today: str) -> list[tuple[str, str]]:
    """각 티커의 소속 시장(KOSPI/KOSDAQ) 판별."""
    try:
        kospi_set = set(stock.get_market_ticker_list(today, market="KOSPI"))
        return [(t, "KOSPI" if t in kospi_set else "KOSDAQ") for t in tickers]
    except Exception:
        logger.debug("시장 판별 실패 → KOSPI 기본값 사용")
        return [(t, "KOSPI") for t in tickers]
