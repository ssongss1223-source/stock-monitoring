import asyncio
import json
import logging
from datetime import date

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
from data.db import get_conn
from data.store import HourlyStore, MarketIndexStore, OhlcvStore
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

    async def run_collect(self) -> None:
        """장 마감 후 데이터 수집 (16:00 KST = 07:00 UTC)."""
        logger.info("=== 데이터 수집 시작 ===")
        try:
            universe = UniverseManager().get_universe()
            semaphore = asyncio.Semaphore(_CONCURRENCY)
            loop = asyncio.get_running_loop()

            async def _collect_one(ticker: str, market: str) -> None:
                async with semaphore:
                    await loop.run_in_executor(None, OhlcvStore.fetch_and_update_daily, ticker)
                    try:
                        await loop.run_in_executor(
                            None, HourlyStore.fetch_and_update_hourly, ticker, market
                        )
                    except Exception:
                        pass

            await asyncio.gather(*[_collect_one(t, m) for t, m in universe])
            await loop.run_in_executor(None, MarketIndexStore.fetch_and_update)
        except Exception:
            logger.exception("데이터 수집 오류")
        logger.info("=== 데이터 수집 완료 ===")

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

        all_analyzed: list[tuple[str, str]] = []  # (ticker, name) 전체 분석 종목
        buy_signals: list[BuySignal] = []
        pattern_results: list[PatternLearningResult] = []
        errs = []
        for r in results:
            if isinstance(r, Exception):
                errs.append(r)
            elif isinstance(r, tuple):
                tk, nm, bs, pr = r
                all_analyzed.append((tk, nm))
                if bs is not None:
                    buy_signals.append(bs)
                pattern_results.append(pr)
        if errs:
            logger.warning("종목 분석 중 예외 %d건 발생 (개별 종목 건너뜀)", len(errs))
        logger.info("매수 신호: %d종목 (전체 %d종목 분석)", len(buy_signals), len(universe))

        # ── 4. signal_history 저장 ────────────────────────────────────────────
        if buy_signals:
            _save_signal_history(buy_signals)

        # ── 5. 매도신호 수집 + 발송 ───────────────────────────────────────────
        sell_signals = await sell_task
        s_grade_signals = [s for s in buy_signals if s.grade == "S"]
        logger.info("텔레그램 발송: S등급 %d종목 (전체 매수신호 %d종목)", len(s_grade_signals), len(buy_signals))
        await self.report_agent.send(markets, s_grade_signals, sell_signals, pattern_results, all_analyzed)

    async def _analyze_stock(
        self,
        ticker: str,
        market_name: str,
        markets: dict[str, MarketContext],
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str, BuySignal | None, PatternLearningResult]:
        async with semaphore:
            loop = asyncio.get_running_loop()
            market_ctx = markets.get(market_name, markets.get("KOSPI"))
            name = _get_name(ticker)

            # ── 1. 일봉 영속 로드 (증분 업데이트) ──
            df_daily = await loop.run_in_executor(
                None, OhlcvStore.fetch_and_update_daily, ticker
            )

            # ── 2. 60분봉 영속 로드 (실패해도 계속) ──
            try:
                df_60m = await loop.run_in_executor(
                    None, HourlyStore.fetch_and_update_hourly, ticker, market_name
                )
            except Exception:
                df_60m = None

            # ── 3. 기술/거래량 분석 (영속 데이터 재사용) ──
            tech_agent = TechnicalAnalysisAgent(self.engine)
            vol_agent = VolumeAnalysisAgent(self.engine)

            try:
                tech, vol = await asyncio.wait_for(
                    asyncio.gather(
                        tech_agent.run(ticker, df=df_daily),
                        vol_agent.run(ticker, df=df_daily, df_60m=df_60m),
                    ),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                logger.warning("%s 분석 타임아웃(20s) — 건너뜀", ticker)
                return ticker, name, None, StockPatternLearner._insufficient(ticker)

            # ── 4. 패턴학습 (60분봉 채널 포함) ──
            pattern_learner = StockPatternLearner()
            pattern_result = await pattern_learner.run(
                ticker, df=df_daily if df_daily is not None else tech_agent.last_df, df_60m=df_60m
            )

            # ── 5. 매수신호 (패턴 보너스 반영) ──
            buy_signal = self.buy_agent.evaluate(
                ticker, name, tech, vol, market_ctx,
                pattern_result=pattern_result,
            )
            return ticker, name, buy_signal, pattern_result


def _save_signal_history(signals: list[BuySignal]) -> None:
    today = date.today()
    conn = get_conn()
    try:
        for s in signals:
            features = json.dumps({
                "total_score": s.total_score,
                "trend_score": s.trend_score,
                "pattern": s.pattern,
                "pattern_score": s.pattern_score,
                "stop_loss": s.stop_loss,
                "target_price": s.target_price,
                "risk_reward": s.risk_reward,
            })
            conn.execute(
                """INSERT OR REPLACE INTO signal_history
                   (signal_date, ticker, vol_score, grade, features, entry_price)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [today, s.ticker, s.volume_score, s.grade, features, s.current_price],
            )
        logger.info("signal_history 저장: %d건", len(signals))
    except Exception:
        logger.exception("signal_history 저장 실패")
    finally:
        conn.close()


def _get_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        return ticker
