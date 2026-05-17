import asyncio
import json
import logging
from datetime import date, timedelta

from pykrx import stock

import config
from agents.buy_signal import BuySignalAgent
from agents.ml_scorer import score_all_labels
from agents.market_filter import MarketFilterAgent
from agents.pattern_learning import StockPatternLearner
from agents.report import ReportAgent
from agents.sell_signal import SellSignalAgent
from agents.technical_analysis import TechnicalAnalysisAgent
from agents.universe_manager import UniverseManager
from agents.volume_analysis import VolumeAnalysisAgent
from core.scoring_engine import ScoringEngine
from data.db import get_conn
from data.store import HourlyStore, MacroStore, MarketIndexStore, OhlcvStore
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

    async def run_resend_last(self) -> None:
        """signal_history DB에서 마지막 결과를 읽어 텔레그램 재발송 (형식 테스트용)."""
        logger.info("=== 마지막 리포트 재발송 ===")
        conn = get_conn(read_only=True)
        try:
            row = conn.execute("SELECT MAX(signal_date) FROM signal_history WHERE grade = 'S'").fetchone()
            if not row or not row[0]:
                logger.warning("재발송할 signal_history 없음")
                return
            signal_date = row[0]

            df = conn.execute("""
                SELECT ticker, vol_score, grade, features, entry_price, xgb_prob
                FROM signal_history WHERE signal_date = ? AND grade = 'S'
            """, [signal_date]).df()

            probs_df = conn.execute("""
                SELECT ticker, label, xgb_prob FROM signal_xgb_probs WHERE signal_date = ?
            """, [signal_date]).df()
        finally:
            conn.close()

        # 종목별 label_probs (전체 9라벨) + best_label
        all_label_probs: dict[str, dict[str, float]] = {}
        if not probs_df.empty:
            for ticker, group in probs_df.groupby("ticker"):
                lp = {str(r["label"]): float(r["xgb_prob"]) for _, r in group.iterrows()}
                all_label_probs[str(ticker)] = lp

        buy_signals: list[BuySignal] = []
        for _, row in df.iterrows():
            try:
                feat = json.loads(row["features"])
            except Exception:
                feat = {}
            rr = float(feat.get("risk_reward", 0))
            if rr < 2.0:
                continue
            ticker = str(row["ticker"])
            lp = all_label_probs.get(ticker, {})
            best_lbl = max(lp, key=lp.get) if lp else feat.get("best_label")
            # DB xgb_prob 칼럼을 fallback으로 사용 (signal_xgb_probs가 비어있을 때)
            import pandas as pd
            db_prob = float(row["xgb_prob"]) if (row["xgb_prob"] is not None and pd.notna(row["xgb_prob"])) else None
            best_prob = lp[best_lbl] if (lp and best_lbl) else db_prob
            s = BuySignal(
                ticker=ticker,
                name=_get_name(ticker),
                grade=str(row["grade"]),
                total_score=int(feat.get("total_score", 0)),
                trend_score=int(feat.get("trend_score", 0)),
                volume_score=int(row["vol_score"]),
                pattern=feat.get("pattern"),
                current_price=float(row["entry_price"]),
                stop_loss=float(feat.get("stop_loss", 0)),
                target_price=float(feat.get("target_price", 0)),
                target_is_resistance=bool(feat.get("target_is_resistance", False)),
                risk_reward=rr,
                pattern_score=int(feat.get("pattern_score", 0)),
                market=str(feat.get("market", "")),
                mktcap_rank=feat.get("mktcap_rank"),
                label_probs=lp,
                best_label=best_lbl,
                xgb_prob=best_prob,
            )
            buy_signals.append(s)

        if not buy_signals:
            logger.warning("재발송할 S등급(RR≥2.0) 신호 없음")
            return

        # market/mktcap_rank: pykrx last trading day 기준으로 보정 (오늘 universe와 무관)
        live_rank, live_market = _get_rank_and_market()
        for s in buy_signals:
            if not s.market:
                s.market = live_market.get(s.ticker, "")
            if s.mktcap_rank is None:
                s.mktcap_rank = live_rank.get(s.ticker)

        # label_probs 없으면 fresh ML 추론 (format test 용)
        if not any(s.label_probs for s in buy_signals):
            try:
                fresh_probs = score_all_labels(buy_signals)
                for s in buy_signals:
                    lp = fresh_probs.get(s.ticker, {})
                    if lp:
                        s.label_probs = lp
                        s.best_label = max(lp, key=lp.get)
                        s.xgb_prob = lp[s.best_label]
                logger.info("재발송 ML 추론 완료: %d종목", len(buy_signals))
            except Exception:
                logger.warning("재발송 ML 추론 실패 — xgb_prob 없이 계속")

        logger.info("재발송: %d종목 (signal_date=%s)", len(buy_signals), signal_date)
        await self.report_agent.send({}, buy_signals, [], None, None)
        logger.info("=== 재발송 완료 ===")

    async def run_collect(self) -> None:
        """장 마감 후 데이터 수집 (16:00 KST = 07:00 UTC)."""
        import time
        logger.info("=== 데이터 수집 시작 ===")
        start = time.monotonic()
        counts = [0, 0, 0, 0]  # [ohlcv_ok, ohlcv_fail, hourly_ok, hourly_fail]
        index_ok = False
        universe: list = []
        try:
            universe = UniverseManager().get_universe()
            semaphore = asyncio.Semaphore(_CONCURRENCY)
            loop = asyncio.get_running_loop()

            async def _collect_one(ticker: str, market: str) -> None:
                async with semaphore:
                    try:
                        df = await loop.run_in_executor(None, OhlcvStore.fetch_and_update_daily, ticker)
                        counts[0 if (df is not None and not df.empty) else 1] += 1
                    except Exception:
                        counts[1] += 1
                    try:
                        df_h = await loop.run_in_executor(
                            None, HourlyStore.fetch_and_update_hourly, ticker, market
                        )
                        counts[2 if (df_h is not None and not df_h.empty) else 3] += 1
                    except Exception:
                        counts[3] += 1

            await asyncio.gather(*[_collect_one(t, m) for t, m in universe])
            try:
                await loop.run_in_executor(None, MarketIndexStore.fetch_and_update)
                index_ok = True
            except Exception:
                logger.exception("지수 데이터 수집 오류")
            try:
                await loop.run_in_executor(None, MacroStore.fetch_and_update)
            except Exception:
                logger.exception("매크로 데이터 수집 오류")
        except Exception:
            logger.exception("데이터 수집 오류")
        elapsed = int(time.monotonic() - start)
        logger.info("=== 데이터 수집 완료 ===")
        await self.report_agent.send_collect_report(
            total=len(universe),
            ohlcv_ok=counts[0],
            ohlcv_fail=counts[1],
            hourly_ok=counts[2],
            hourly_fail=counts[3],
            index_ok=index_ok,
            elapsed_sec=elapsed,
        )

    async def run_daily(self, force: bool = False) -> None:
        logger.info("=== 일일 분석 시작 ===")
        if not force and not _is_trading_day():
            logger.info("오늘(%s)은 거래일이 아님 — 분석 건너뜀", date.today())
            return
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

        # ── 4. market / 시총 순위 세팅 (저장 전에 먼저) ──────────────────────
        ticker_market = {t: m for t, m in universe}
        mktcap_rank = _get_mktcap_rank()
        for s in buy_signals:
            s.market = ticker_market.get(s.ticker, "")
            s.mktcap_rank = mktcap_rank.get(s.ticker)

        # ── 4b. XGBoost 추론 + signal_history / signal_xgb_probs 저장 ─────────
        if buy_signals:
            xgb_probs: dict[str, dict[str, float]] = {}
            try:
                xgb_probs = score_all_labels(buy_signals)
                for s in buy_signals:
                    label_probs = xgb_probs.get(s.ticker, {})
                    if label_probs:
                        s.label_probs = label_probs
                        s.best_label = max(label_probs, key=label_probs.get)
                        s.xgb_prob = label_probs[s.best_label]
            except Exception:
                logger.exception("XGBoost 추론 실패 — xgb_prob 없이 계속")
            _save_signal_history(buy_signals)
            if xgb_probs:
                _save_signal_xgb_probs(xgb_probs)
            _auto_label_unlabeled()

        # ── 5. 매도신호 수집 + 발송 ───────────────────────────────────────────
        sell_signals = await sell_task
        s_grade_signals = [s for s in buy_signals if s.grade == "S" and s.risk_reward >= 2.0]
        logger.info("텔레그램 발송: S등급(RR≥2.0) %d종목 → top10 cap (전체 매수신호 %d종목)", len(s_grade_signals), len(buy_signals))
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

            # ── 1. 일봉 DB 읽기 (수집은 run_collect에서 완료됨) ──
            df_daily = await loop.run_in_executor(None, OhlcvStore.load_daily, ticker)
            if df_daily is None:
                logger.warning("%s 일봉 데이터 없음 — 건너뜀 (run_collect 먼저 실행 필요)", ticker)
                return ticker, name, None, StockPatternLearner._insufficient(ticker)

            # ── 2. 60분봉 DB 읽기 (실패해도 계속) ──
            try:
                df_60m = await loop.run_in_executor(None, HourlyStore.load_hourly, ticker)
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


def _is_trading_day() -> bool:
    """오늘이 한국 주식시장 거래일인지 확인 (주말 + 공휴일 모두 처리)."""
    today = date.today()
    if today.weekday() >= 5:   # 토=5, 일=6
        return False
    # 평일 공휴일: pykrx KRX 공식 거래일 캘린더로 확인
    today_str = today.strftime("%Y%m%d")
    try:
        biz = stock.get_exchange_business_day_list(today_str, today_str)
        return len(biz) > 0
    except Exception:
        # 함수 미지원 또는 API 오류 → 평일이면 거래일로 간주
        logger.debug("거래일 캘린더 조회 실패 → 평일 기준 실행")
        return True


def _get_mktcap_rank() -> dict[str, int]:
    rank, _ = _get_rank_and_market()
    return rank


def _get_rank_and_market() -> tuple[dict[str, int], dict[str, str]]:
    """시총 순위 + 시장(KOSPI/KOSDAQ) 동시 반환. DB 최근 거래일 기준."""
    try:
        conn = get_conn(read_only=True)
        try:
            row = conn.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
            trade_date = str(row[0]).replace("-", "") if row[0] else date.today().strftime("%Y%m%d")
        finally:
            conn.close()
        rank_result: dict[str, int] = {}
        market_result: dict[str, str] = {}
        for mkt in ("KOSPI", "KOSDAQ"):
            df = stock.get_market_cap_by_ticker(trade_date, market=mkt)
            if df is None or df.empty:
                continue
            cap_col = next((c for c in ("시가총액", "Mktcap") if c in df.columns), None)
            if cap_col is None:
                continue
            df = df.sort_values(cap_col, ascending=False)
            for rank, ticker in enumerate(df.index, 1):
                rank_result[ticker] = rank
                market_result[ticker] = mkt
        return rank_result, market_result
    except Exception:
        logger.warning("시총 순위 조회 실패")
        return {}, {}


def _save_signal_history(signals: list[BuySignal]) -> None:
    # 분석 기준일 = 마지막 거래일 (T-1)
    # VM은 08:00 KST에 전날 데이터를 분석하므로 signal_date = T-1
    conn_r = get_conn(read_only=True)
    try:
        row = conn_r.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
        signal_date = row[0] if row[0] else date.today()
    finally:
        conn_r.close()

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
                "target_is_resistance": s.target_is_resistance,
                "risk_reward": s.risk_reward,
                "market": s.market,
                "mktcap_rank": s.mktcap_rank,
                "best_label": s.best_label,
            })
            conn.execute(
                """INSERT OR REPLACE INTO signal_history
                   (signal_date, ticker, vol_score, grade, features, entry_price, scoring_version, xgb_prob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [signal_date, s.ticker, s.volume_score, s.grade, features, s.current_price, 'live_v2', s.xgb_prob],
            )
        logger.info("signal_history 저장: %d건", len(signals))
    except Exception:
        logger.exception("signal_history 저장 실패")
    finally:
        conn.close()


def _save_signal_xgb_probs(probs_by_ticker: dict[str, dict[str, float]]) -> None:
    conn_r = get_conn(read_only=True)
    try:
        row = conn_r.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
        signal_date = row[0] if row[0] else date.today()
    finally:
        conn_r.close()

    conn = get_conn()
    try:
        for ticker, label_probs in probs_by_ticker.items():
            for label, prob in label_probs.items():
                conn.execute(
                    """INSERT OR REPLACE INTO signal_xgb_probs
                       (signal_date, ticker, label, xgb_prob)
                       VALUES (?, ?, ?, ?)""",
                    [signal_date, ticker, label, prob],
                )
        logger.info("signal_xgb_probs 저장: %d건", sum(len(v) for v in probs_by_ticker.values()))
    except Exception:
        logger.exception("signal_xgb_probs 저장 실패")
    finally:
        conn.close()


def _auto_label_unlabeled(cutoff_days: int = 15) -> None:
    """signal_history 중 T+10 이상 경과한 미라벨 신호를 자동으로 라벨링."""
    from backtest.labeler import label_batch, save_labels
    cutoff = (date.today() - timedelta(days=cutoff_days)).isoformat()
    conn = get_conn(read_only=True)
    try:
        rows = conn.execute("""
            SELECT sh.ticker, sh.signal_date
            FROM signal_history sh
            LEFT JOIN backtest_labels bl
                ON sh.ticker = bl.ticker AND sh.signal_date = bl.signal_date
            WHERE bl.signal_date IS NULL AND sh.signal_date <= ?
        """, [cutoff]).fetchall()
    finally:
        conn.close()
    if not rows:
        return
    pairs = [(r[0], r[1]) for r in rows]
    labels = label_batch(pairs)
    save_labels(labels)
    logger.info("자동 라벨링 완료: %d건", len(labels))


def _get_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        return ticker
