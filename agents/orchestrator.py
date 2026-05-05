import asyncio
import logging

from pykrx import stock

import config
from agents.buy_signal import BuySignalAgent
from agents.market_filter import MarketFilterAgent
from agents.pattern_learning import StockPatternLearner
from agents.report import ReportAgent
from agents.sell_signal import SellSignalAgent
from agents.technical_analysis import TechnicalAnalysisAgent
from agents.universe_manager import UniverseManager
from agents.volume_analysis import VolumeAnalysisAgent
from core.scoring_engine import ScoringEngine
from models.signals import BuySignal, MarketContext, PatternLearningResult

logger = logging.getLogger(__name__)

# pykrx/KRX 서버 rate-limit 방지 — 동시 2종목 유지
_CONCURRENCY = 2


class Orchestrator:
    """
    전체 파이프라인 제어.
    1. 장세 판단 + 매도신호 병렬 시작
    2. 유니버스 결정 → 종목별 병렬 분석
    3. 매수 추천 등급 필터링
    4. 텔레그램 발송
    """

    def __init__(self, scoring_config: str | None = None):
        cfg_dir = scoring_config or config.SCORING_CONFIG_DIR
        self.engine = ScoringEngine(cfg_dir)
        self.market_agent = MarketFilterAgent(self.engine)
        self.sell_agent = SellSignalAgent(self.engine)
        self.buy_agent = BuySignalAgent(self.engine)
        self.report_agent = ReportAgent()

    async def run_daily(self) -> None:
        logger.info("=== 일일 분석 시작 ===")
        try:
            await self._pipeline()
        except Exception:
            logger.exception("파이프라인 오류")
        logger.info("=== 일일 분석 완료 ===")

    async def _pipeline(self) -> None:
        # ── 1. 장세 판단 + 매도신호 병렬 시작 ───────────────────────────────
        market_task = asyncio.create_task(self.market_agent.run())
        sell_task = asyncio.create_task(self.sell_agent.run())

        markets = await market_task
        logger.info("장세: %s", {k: v.market_status for k, v in markets.items()})

        # ── 2. 유니버스 결정 ──────────────────────────────────────────────────
        universe = UniverseManager().get_universe()
        if not universe:
            logger.warning("분석 대상 종목 없음")
            sell_signals = await sell_task
            await self.report_agent.send(markets, [], sell_signals, [])
            return

        # ── 3. 종목별 병렬 분석 (세마포어로 동시성 제한) ─────────────────────
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        tasks = [
            asyncio.create_task(self._analyze_stock(ticker, market, markets, semaphore))
            for ticker, market in universe
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        buy_signals: list[BuySignal] = []
        pattern_results: list[PatternLearningResult] = []
        errs = []
        for r in results:
            if isinstance(r, Exception):
                errs.append(r)
            elif isinstance(r, tuple):
                bs, pr = r
                if bs is not None:
                    buy_signals.append(bs)
                pattern_results.append(pr)
        if errs:
            logger.warning("종목 분석 중 예외 %d건 발생 (개별 종목 건너뜀)", len(errs))
        logger.info("매수 신호: %d종목 (전체 %d종목 분석)", len(buy_signals), len(universe))

        # ── 4. 매도신호 수집 + 발송 ───────────────────────────────────────────
        sell_signals = await sell_task
        await self.report_agent.send(markets, buy_signals, sell_signals, pattern_results)

    async def _analyze_stock(
        self,
        ticker: str,
        market_name: str,
        markets: dict[str, MarketContext],
        semaphore: asyncio.Semaphore,
    ) -> tuple[BuySignal | None, PatternLearningResult]:
        async with semaphore:
            market_ctx = markets.get(market_name, markets.get("KOSPI"))
            tech_agent = TechnicalAnalysisAgent(self.engine)
            vol_agent = VolumeAnalysisAgent(self.engine)

            try:
                tech, vol = await asyncio.wait_for(
                    asyncio.gather(tech_agent.run(ticker), vol_agent.run(ticker)),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                logger.warning("%s 분석 타임아웃(20s) — 건너뜀", ticker)
                return None, StockPatternLearner._insufficient(ticker)

            pattern_learner = StockPatternLearner()
            pattern_result = await pattern_learner.run(ticker, df=tech_agent.last_df)

            name = _get_name(ticker)
            buy_signal = self.buy_agent.evaluate(ticker, name, tech, vol, market_ctx)
            return buy_signal, pattern_result


def _get_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        return ticker
