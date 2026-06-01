import asyncio
import json
import logging
from datetime import date, timedelta

import pandas as pd
from pykrx import stock

import config
from agents.buy_signal import BuySignalAgent, _calc_stop_loss, _calc_target
from agents.ml_scorer import score_all_labels, score_universe_all
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
from models.signals import BuySignal, MarketContext, PatternLearningResult, TechnicalResult

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
                SELECT ticker, vol_score, grade, features, entry_price, ensemble_prob
                FROM signal_history WHERE signal_date = ? AND grade = 'S'
            """, [signal_date]).df()

            probs_df = conn.execute("""
                SELECT ticker, label, ensemble_prob FROM signal_xgb_probs WHERE signal_date = ?
            """, [signal_date]).df()
        finally:
            conn.close()

        # 종목별 label_probs (전체 9라벨) + best_label
        all_label_probs: dict[str, dict[str, float]] = {}
        if not probs_df.empty:
            for ticker, group in probs_df.groupby("ticker"):
                lp = {str(r["label"]): float(r["ensemble_prob"]) for _, r in group.iterrows()}
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
            # DB ensemble_prob 칼럼을 fallback으로 사용 (signal_xgb_probs가 비어있을 때)
            db_prob = float(row["ensemble_prob"]) if (row["ensemble_prob"] is not None and pd.notna(row["ensemble_prob"])) else None
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
                ensemble_prob=best_prob,
            )
            buy_signals.append(s)

        if not buy_signals:
            logger.warning("재발송할 S등급(RR≥2.0) 신호 없음")
            return

        # market/mktcap_rank/market_cap: pykrx last trading day 기준으로 보정 (오늘 universe와 무관)
        live_rank, live_market, live_cap = _get_rank_and_market()
        for s in buy_signals:
            if not s.market:
                s.market = live_market.get(s.ticker, "")
            if s.mktcap_rank is None:
                s.mktcap_rank = live_rank.get(s.ticker)
            if s.market_cap is None:
                s.market_cap = live_cap.get(s.ticker)

        # label_probs 없으면 fresh ML 추론 (format test 용)
        if not any(s.label_probs for s in buy_signals):
            try:
                fresh_probs = score_all_labels(buy_signals)
                for s in buy_signals:
                    lp = fresh_probs.get(s.ticker, {})
                    if lp:
                        s.label_probs = lp
                        s.best_label = max(lp, key=lp.get)
                        s.ensemble_prob = lp[s.best_label]
                logger.info("재발송 ML 추론 완료: %d종목", len(buy_signals))
            except Exception:
                logger.warning("재발송 ML 추론 실패 — ensemble_prob 없이 계속")

        logger.info("재발송: %d종목 (signal_date=%s)", len(buy_signals), signal_date)
        await self.report_agent.send({}, buy_signals, [], None, None)
        logger.info("=== 재발송 완료 ===")

    async def run_collect(self) -> None:
        """장 마감 후 데이터 수집 (16:00 KST = 07:00 UTC)."""
        if not _is_trading_day():
            logger.info("오늘(%s)은 거래일이 아님 — 수집 건너뜀", date.today())
            return
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
            try:
                await loop.run_in_executor(None, _insert_universe_daily)
            except Exception:
                logger.exception("universe_daily INSERT 오류")
            try:
                await loop.run_in_executor(None, _insert_universe_features_daily)
            except Exception:
                logger.exception("universe_features_daily INSERT 오류")
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
        universe_scores: list[tuple[str, int, int]] = []  # (ticker, trend_score, vol_score_live)
        buy_signals: list[BuySignal] = []
        pattern_results: list[PatternLearningResult] = []
        tech_map: dict[str, TechnicalResult] = {}
        errs = []
        for r in results:
            if isinstance(r, Exception):
                errs.append(r)
            elif isinstance(r, tuple):
                tk, nm, bs, pr, ts, vs, tech_obj = r
                all_analyzed.append((tk, nm))
                universe_scores.append((tk, ts, vs))
                if bs is not None:
                    buy_signals.append(bs)
                pattern_results.append(pr)
                if tech_obj is not None:
                    tech_map[tk] = tech_obj
        if errs:
            logger.warning("종목 분석 중 예외 %d건 발생 (개별 종목 건너뜀)", len(errs))
        logger.info("매수 신호: %d종목 (전체 %d종목 분석)", len(buy_signals), len(universe))

        # ── 4. market / 시총 순위 세팅 (저장 전에 먼저) ──────────────────────
        ticker_market = {t: m for t, m in universe}
        mktcap_rank, _mkt, mktcap_cap = _get_rank_and_market()
        for s in buy_signals:
            s.market = ticker_market.get(s.ticker, "")
            s.mktcap_rank = mktcap_rank.get(s.ticker)
            s.market_cap = mktcap_cap.get(s.ticker)

        # ── 4b. 앙상블 추론 + signal_history / signal_xgb_probs 저장 ──────────
        if buy_signals:
            ensemble_probs: dict[str, dict[str, float]] = {}
            try:
                ensemble_probs = score_all_labels(buy_signals)
                for s in buy_signals:
                    label_probs = ensemble_probs.get(s.ticker, {})
                    if label_probs:
                        s.label_probs = label_probs
                        s.best_label = max(label_probs, key=label_probs.get)
                        s.ensemble_prob = label_probs[s.best_label]
            except Exception:
                logger.exception("앙상블 추론 실패 — ensemble_prob 없이 계속")
            _save_signal_history(buy_signals)
            if ensemble_probs:
                _save_signal_xgb_probs(ensemble_probs)
            try:
                _auto_label_unlabeled()
            except Exception:
                logger.exception("_auto_label_unlabeled 실패 — 파이프라인 계속")

        # ── 4c. universe_daily 업데이트 + ML-only 신호 생성 (텔레그램 전) ──────
        conn_r = get_conn(read_only=True)
        try:
            row = conn_r.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
            trade_date = str(row[0]) if row[0] else date.today().isoformat()
        finally:
            conn_r.close()

        universe_ml_probs: dict[str, dict[str, float]] = {}
        try:
            universe_ml_probs = score_universe_all(trade_date)
        except Exception:
            logger.exception("universe ML 추론 실패 — ML-only 신호 생성 건너뜀")

        # 규칙 신호에 없는 종목 중 best_prob >= 0.60 && RR >= 2.0 인 종목만 ML-only 신호
        _ML_PROB_THRESHOLD = 0.60
        rule_tickers = {s.ticker for s in buy_signals}
        ml_only_signals: list[BuySignal] = []
        for _tk, _lp in universe_ml_probs.items():
            if _tk in rule_tickers or not _lp:
                continue
            _best_label = max(_lp, key=_lp.get)
            _best_prob = _lp[_best_label]
            if _best_prob < _ML_PROB_THRESHOLD:
                continue
            _tech = tech_map.get(_tk)
            if _tech is None or _tech.current_price <= 0:
                continue
            _price = _tech.current_price
            _stop = _calc_stop_loss(_price, _tech)
            _target, _target_is_res = _calc_target(_price, _tech)
            _rr = round((_target - _price) / max(_price - _stop, 1), 2)
            if _rr < 2.0:
                continue
            ml_only_signals.append(BuySignal(
                ticker=_tk,
                name=_get_name(_tk),
                grade="ML",
                total_score=0,
                trend_score=0,
                volume_score=0,
                pattern=None,
                current_price=_price,
                stop_loss=_stop,
                target_price=_target,
                risk_reward=_rr,
                ensemble_prob=_best_prob,
                best_label=_best_label,
                target_is_resistance=_target_is_res,
                market=ticker_market.get(_tk, ""),
                mktcap_rank=mktcap_rank.get(_tk),
                market_cap=mktcap_cap.get(_tk),
                label_probs=_lp,
            ))
        logger.info("ML-only 신호: %d종목 (threshold=%.2f, RR≥2.0)", len(ml_only_signals), _ML_PROB_THRESHOLD)

        _update_universe_vol_trend(trade_date, universe_scores)
        _update_universe_preds(trade_date, universe_ml_probs)

        # universe_outcomes: 10거래일 전 예측일에 대한 raw measurement 저장
        try:
            from backtest.labeler import compute_outcomes_universe
            conn_r3 = get_conn(read_only=True)
            try:
                row10 = conn_r3.execute(f"""
                    SELECT MIN(date) FROM (
                        SELECT DISTINCT date FROM ohlcv_daily
                        WHERE date <= CAST('{trade_date}' AS DATE)
                        ORDER BY date DESC LIMIT 11
                    )
                """).fetchone()
            finally:
                conn_r3.close()
            outcome_date = str(row10[0]) if row10 and row10[0] else None
            if outcome_date:
                n = compute_outcomes_universe(outcome_date)
                logger.info("universe_outcomes INSERT 완료: %d행 (예측일 %s)", n, outcome_date)
        except Exception:
            logger.exception("universe_outcomes INSERT 실패 — 파이프라인 계속")

        try:
            _auto_label_universe_unlabeled()
        except Exception:
            logger.exception("_auto_label_universe_unlabeled 실패 — 파이프라인 계속")

        # ── 5. 매도신호 수집 + 발송 ───────────────────────────────────────────
        sell_signals = await sell_task
        rule_signals = [s for s in buy_signals if s.risk_reward >= 2.0]
        telegram_signals = rule_signals + ml_only_signals
        logger.info(
            "텔레그램 발송: 규칙(RR≥2.0) %d종목 + ML-only %d종목 (전체 규칙 매수신호 %d종목)",
            len(rule_signals), len(ml_only_signals), len(buy_signals),
        )
        await self.report_agent.send(markets, telegram_signals, sell_signals, pattern_results, all_analyzed)

    async def _analyze_stock(
        self,
        ticker: str,
        market_name: str,
        markets: dict[str, MarketContext],
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str, BuySignal | None, PatternLearningResult, int, int, TechnicalResult | None]:
        async with semaphore:
            loop = asyncio.get_running_loop()
            market_ctx = markets.get(market_name, markets.get("KOSPI"))
            name = _get_name(ticker)

            # ── 1. 일봉 DB 읽기 (수집은 run_collect에서 완료됨) ──
            df_daily = await loop.run_in_executor(None, OhlcvStore.load_daily, ticker)
            if df_daily is None:
                logger.warning("%s 일봉 데이터 없음 — 건너뜀 (run_collect 먼저 실행 필요)", ticker)
                return ticker, name, None, StockPatternLearner._insufficient(ticker), 0, 0, None

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
                return ticker, name, None, StockPatternLearner._insufficient(ticker), 0, 0, None

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
            return ticker, name, buy_signal, pattern_result, tech.total_score, vol.volume_score, tech


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
    rank, _, _cap = _get_rank_and_market()
    return rank


def _get_rank_and_market() -> tuple[dict[str, int], dict[str, str], dict[str, float]]:
    """시총 순위 + 시장(KOSPI/KOSDAQ) + 시총 절대값 동시 반환. DB 최근 거래일 기준."""
    try:
        conn = get_conn(read_only=True)
        try:
            row = conn.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
            trade_date = str(row[0]).replace("-", "") if row[0] else date.today().strftime("%Y%m%d")
        finally:
            conn.close()
        rank_result: dict[str, int] = {}
        market_result: dict[str, str] = {}
        cap_result: dict[str, float] = {}
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
                cap_result[ticker] = float(df.at[ticker, cap_col])
        return rank_result, market_result, cap_result
    except Exception:
        logger.warning("시총 순위 조회 실패")
        return {}, {}, {}


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
                   (signal_date, ticker, vol_score, grade, features, entry_price, scoring_version, ensemble_prob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [signal_date, s.ticker, s.volume_score, s.grade, features, s.current_price, 'live_v2', s.ensemble_prob],
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
                       (signal_date, ticker, label, ensemble_prob)
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


def _insert_universe_daily() -> None:
    """전종목 일봉 피쳐를 universe_daily에 INSERT OR REPLACE. 수집 배치 완료 후 실행."""
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO universe_daily (
                date, ticker,
                close, volume, market_cap, per, pbr, turnover_rate,
                trend_score,
                ma5_ratio, ma20_ratio, ma60_ratio, ma120_ratio,
                rsi_14, bb_position, hist_vol_20d, close_to_52w_high,
                foreign_net_5d, inst_net_5d, foreign_net_20d, volume_surge_5d,
                kospi_ret_5d, kospi_ret_20d,
                vol_score_approx, vol_score_live,
                grade_approx, grade_live
            )
            WITH
            latest AS (
                SELECT MAX(date) AS td FROM ohlcv_daily
            ),
            hist AS (
                SELECT
                    ticker, date,
                    CAST(close   AS DOUBLE) AS close,
                    CAST(volume  AS DOUBLE) AS volume,
                    market_cap, per, pbr,
                    COALESCE(CAST(shares      AS BIGINT), 0)  AS shares,
                    COALESCE(CAST(foreign_net AS DOUBLE), 0.0) AS foreign_net,
                    COALESCE(CAST(inst_net    AS DOUBLE), 0.0) AS inst_net
                FROM ohlcv_daily
                WHERE date >= (SELECT td FROM latest) - INTERVAL '265 days'
                  AND ticker IN (
                      SELECT DISTINCT ticker FROM ohlcv_daily
                      WHERE date = (SELECT td FROM latest)
                  )
            ),
            roll AS (
                SELECT
                    ticker, date, close, volume, market_cap, per, pbr, shares,
                    foreign_net, inst_net,
                    CASE WHEN shares > 0 THEN volume / shares ELSE NULL END AS turnover_rate,
                    close / NULLIF(AVG(close) OVER w5,   0) AS ma5_ratio,
                    close / NULLIF(AVG(close) OVER w20,  0) AS ma20_ratio,
                    close / NULLIF(AVG(close) OVER w60,  0) AS ma60_ratio,
                    close / NULLIF(AVG(close) OVER w120, 0) AS ma120_ratio,
                    close / NULLIF(MAX(close) OVER w252, 0) AS close_to_52w_high,
                    AVG(close)    OVER w20 AS sma20,
                    STDDEV(close) OVER w20 AS std20,
                    volume / NULLIF(
                        AVG(volume) OVER (PARTITION BY ticker ORDER BY date
                                         ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), 0
                    ) AS volume_surge_5d,
                    SUM(foreign_net) OVER w5  AS foreign_net_5d,
                    SUM(inst_net)    OVER w5  AS inst_net_5d,
                    SUM(foreign_net) OVER w20 AS foreign_net_20d,
                    QUANTILE_CONT(
                        CASE WHEN shares > 0 THEN volume / shares ELSE NULL END, 0.8
                    ) OVER w60 AS turnover_p80
                FROM hist
                WINDOW
                    w5   AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4   PRECEDING AND CURRENT ROW),
                    w20  AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19  PRECEDING AND CURRENT ROW),
                    w60  AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 59  PRECEDING AND CURRENT ROW),
                    w120 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW),
                    w252 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
            ),
            pdiff AS (
                SELECT ticker, date,
                       close - LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS d
                FROM hist
            ),
            rsi AS (
                SELECT ticker, date,
                       100.0 - 100.0 / (
                           1 + AVG(GREATEST(d, 0))  OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
                             / NULLIF(AVG(GREATEST(-d, 0)) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW), 0)
                       ) AS rsi_14
                FROM pdiff
            ),
            lret AS (
                SELECT ticker, date,
                       ln(close / NULLIF(LAG(close) OVER (PARTITION BY ticker ORDER BY date), 0)) AS lr
                FROM hist
            ),
            hvol AS (
                SELECT ticker, date,
                       STDDEV(lr) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS hist_vol_20d
                FROM lret
            ),
            kospi AS (
                SELECT date,
                       close / NULLIF(LAG(close, 5)  OVER (ORDER BY date), 0) - 1 AS kospi_ret_5d,
                       close / NULLIF(LAG(close, 20) OVER (ORDER BY date), 0) - 1 AS kospi_ret_20d
                FROM market_index WHERE ticker = '1001'
            ),
            combined AS (
                SELECT
                    r.ticker, r.date,
                    r.close, CAST(r.volume AS BIGINT) AS volume,
                    r.market_cap, r.per, r.pbr, r.turnover_rate,
                    r.ma5_ratio, r.ma20_ratio, r.ma60_ratio, r.ma120_ratio,
                    r.close_to_52w_high,
                    CASE WHEN r.std20 > 0
                         THEN (r.close - (r.sma20 - 2 * r.std20)) / (4 * r.std20)
                         ELSE 0.5 END AS bb_position,
                    hv.hist_vol_20d,
                    rs.rsi_14,
                    r.foreign_net_5d, r.inst_net_5d, r.foreign_net_20d,
                    r.volume_surge_5d,
                    k.kospi_ret_5d, k.kospi_ret_20d,
                    CAST(
                        CASE WHEN r.volume_surge_5d >= 2.0 THEN 5 ELSE 0 END
                        + CASE WHEN r.turnover_rate IS NOT NULL
                                    AND r.turnover_p80 IS NOT NULL
                                    AND r.turnover_rate >= r.turnover_p80 THEN 3 ELSE 0 END
                        + CASE WHEN r.foreign_net_5d > 0 THEN 2 ELSE 0 END
                    AS SMALLINT) AS vol_score_approx
                FROM roll r
                LEFT JOIN rsi  rs USING (ticker, date)
                LEFT JOIN hvol hv USING (ticker, date)
                LEFT JOIN kospi k USING (date)
            )
            SELECT
                c.date, c.ticker,
                c.close, c.volume, c.market_cap, c.per, c.pbr, c.turnover_rate,
                NULL::SMALLINT AS trend_score,
                c.ma5_ratio, c.ma20_ratio, c.ma60_ratio, c.ma120_ratio,
                c.rsi_14, c.bb_position, c.hist_vol_20d, c.close_to_52w_high,
                c.foreign_net_5d, c.inst_net_5d, c.foreign_net_20d, c.volume_surge_5d,
                c.kospi_ret_5d, c.kospi_ret_20d,
                c.vol_score_approx, NULL::SMALLINT AS vol_score_live,
                CASE WHEN c.vol_score_approx >= 8 THEN 'S'
                     WHEN c.vol_score_approx >= 5 THEN 'A'
                     WHEN c.vol_score_approx >= 2 THEN 'B'
                     ELSE NULL END AS grade_approx,
                NULL::VARCHAR AS grade_live
            FROM combined c
            CROSS JOIN latest l
            WHERE c.date = l.td
        """)
        logger.info("universe_daily INSERT 완료")
    except Exception:
        logger.exception("universe_daily INSERT 실패")
    finally:
        conn.close()


def _update_universe_vol_trend(
    date_str: str,
    scores: list[tuple[str, int, int]],  # (ticker, trend_score, vol_score_live)
) -> None:
    """universe_daily에 trend_score, vol_score_live, grade_live 일괄 UPDATE."""
    if not scores:
        return
    df = pd.DataFrame(scores, columns=["ticker", "trend_score", "vol_score_live"])
    conn = get_conn()
    try:
        conn.register("_scores", df)
        conn.execute(f"""
            UPDATE universe_daily ud
            SET trend_score    = s.trend_score,
                vol_score_live = s.vol_score_live,
                grade_live = CASE WHEN s.vol_score_live >= 13 THEN 'S'
                                  WHEN s.vol_score_live >= 10 THEN 'A'
                                  WHEN s.vol_score_live >= 7  THEN 'B'
                                  ELSE NULL END
            FROM _scores s
            WHERE ud.date = CAST('{date_str}' AS DATE)
              AND ud.ticker = s.ticker
        """)
        logger.info("universe_daily vol/trend UPDATE 완료: %d건", len(scores))
    except Exception:
        logger.exception("universe_daily vol/trend UPDATE 실패")
    finally:
        conn.close()


def _update_universe_preds(date_str: str, probs_by_ticker: dict | None = None) -> None:
    """universe_daily에 ML 예측 확률 일괄 UPDATE.

    probs_by_ticker가 주어지면 재사용, None이면 score_universe_all() 호출.
    """
    _PRED_LABELS = [
        "3d_3pct_clean", "3d_5pct_clean", "3d_10pct_clean",
        "5d_3pct_clean", "5d_5pct_clean", "5d_10pct_clean",
        "10d_3pct_clean", "10d_5pct_clean", "10d_10pct_clean",
        "first_3d_3pct", "first_3d_5pct", "first_3d_10pct",
        "first_5d_3pct", "first_5d_5pct", "first_5d_10pct",
        "first_10d_3pct", "first_10d_5pct", "first_10d_10pct",
    ]
    if probs_by_ticker is None:
        try:
            probs_by_ticker = score_universe_all(date_str)
        except Exception:
            logger.exception("universe ML 추론 실패 — pred_* UPDATE 건너뜀")
            return
    if not probs_by_ticker:
        return

    rows = []
    for ticker, lp in probs_by_ticker.items():
        row: dict = {"ticker": ticker}
        for lbl in _PRED_LABELS:
            row[f"pred_{lbl}"] = lp.get(lbl)
        rows.append(row)

    df = pd.DataFrame(rows)
    pred_cols = [f"pred_{l}" for l in _PRED_LABELS]
    set_clause = ", ".join(f"{col} = s.{col}" for col in pred_cols)

    conn = get_conn()
    try:
        conn.register("_preds", df)
        conn.execute(f"""
            UPDATE universe_daily ud
            SET {set_clause}
            FROM _preds s
            WHERE ud.date = CAST('{date_str}' AS DATE)
              AND ud.ticker = s.ticker
        """)
        logger.info("universe_daily ML 예측 UPDATE 완료: %d종목", len(rows))
    except Exception:
        logger.exception("universe_daily ML 예측 UPDATE 실패")
    finally:
        conn.close()

    # universe_predictions (long format) INSERT
    conn_ver = get_conn(read_only=True)
    try:
        ver_row = conn_ver.execute(
            "SELECT MAX(version) FROM model_registry WHERE status='production'"
        ).fetchone()
        cur_ver = ver_row[0] if ver_row else None
    except Exception:
        cur_ver = None
    finally:
        conn_ver.close()

    pred_rows = []
    for ticker, lp in probs_by_ticker.items():
        for lbl in _PRED_LABELS:
            prob = lp.get(lbl)
            if prob is not None:
                pred_rows.append({
                    "date": date_str,
                    "ticker": ticker,
                    "model_type": "ensemble",
                    "label": lbl,
                    "prob": prob,
                    "model_ver": cur_ver,
                })
    if pred_rows:
        df_pred = pd.DataFrame(pred_rows)
        conn2 = get_conn()
        try:
            conn2.register("_pred_long", df_pred)
            conn2.execute("""
                INSERT OR REPLACE INTO universe_predictions
                    (date, ticker, model_type, label, prob, model_ver)
                SELECT date, ticker, model_type, label, prob, model_ver
                FROM _pred_long
            """)
            logger.info("universe_predictions INSERT 완료: %d행", len(pred_rows))
        except Exception:
            logger.exception("universe_predictions INSERT 실패")
        finally:
            conn2.close()


def _insert_universe_features_daily() -> None:
    """오늘 날짜의 파생 피처를 계산해 universe_features_daily에 INSERT OR REPLACE.
    feature_engineering.py --mode build 와 동일 로직을 일별로 실행.
    """
    try:
        from scripts.feature_engineering import (
            _compute_all_features,
            _build_market_features,
            _add_derived_and_cross_sectional,
            _save_features_to_db,
        )
    except ImportError:
        logger.error("feature_engineering import 실패 — universe_features_daily 건너뜀")
        return

    conn_r = get_conn(read_only=True)
    try:
        today = conn_r.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()[0]
        if today is None:
            return
        today_str = str(today)

        df_all = _compute_all_features(conn_r)
        df_mkt = _build_market_features(conn_r)
        df_grade = conn_r.execute(
            "SELECT date, ticker, grade_approx FROM universe_daily WHERE date = ?",
            [today_str],
        ).df()
    finally:
        conn_r.close()

    import pandas as pd
    df_all["date"] = pd.to_datetime(df_all["date"])
    today_ts = pd.Timestamp(today_str)
    df_today = df_all[df_all["date"] == today_ts].copy()

    if df_today.empty:
        logger.warning("universe_features_daily: 오늘 데이터 없음 (%s)", today_str)
        return

    df_today = _add_derived_and_cross_sectional(df_today, df_mkt)

    df_grade["date"] = pd.to_datetime(df_grade["date"])
    df_today = df_today.merge(df_grade, on=["date", "ticker"], how="left")
    for g in ["S", "A", "B"]:
        df_today[f"grade_{g}"] = (df_today.get("grade_approx") == g).astype("Int8")
    df_today = df_today.drop(columns=["grade_approx"], errors="ignore")

    _save_features_to_db(df_today)
    logger.info("universe_features_daily INSERT 완료: %s %d건", today_str, len(df_today))


def _auto_label_universe_unlabeled() -> None:
    """universe_daily 중 10 거래일 이상 경과한 미라벨 행의 라벨을 자동 계산해 UPDATE.

    달력일 기준 대신 거래일 기준 사용: ohlcv_daily 최근 11번째 거래일 = 10 거래일 전.
    label_one()이 10 거래일 미래 데이터를 요구하므로 이 기준이 실제 최솟값.
    """
    from backtest.labeler import label_one

    # 10 거래일 전 날짜를 거래일 캘린더 기준으로 산출
    conn_cut = get_conn(read_only=True)
    try:
        row_cut = conn_cut.execute("""
            SELECT MIN(date) FROM (
                SELECT DISTINCT date FROM ohlcv_daily
                ORDER BY date DESC LIMIT 11
            )
        """).fetchone()
    finally:
        conn_cut.close()

    if not row_cut or not row_cut[0]:
        return
    cutoff = str(row_cut[0])

    conn = get_conn(read_only=True)
    try:
        rows = conn.execute("""
            SELECT ticker, date::VARCHAR FROM universe_daily
            WHERE (label_3d_3pct_clean IS NULL OR label_first_3d_3pct IS NULL)
              AND date <= CAST(? AS DATE)
            ORDER BY date, ticker
        """, [cutoff]).fetchall()
    finally:
        conn.close()

    if not rows:
        return

    tickers = list({r[0] for r in rows})
    conn_r = get_conn(read_only=True)
    try:
        placeholders = ", ".join("?" * len(tickers))
        df_all = conn_r.execute(
            f"SELECT ticker, date, open, high, low, close FROM ohlcv_daily "
            f"WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
            tickers,
        ).df()
    finally:
        conn_r.close()

    df_all["date"] = pd.to_datetime(df_all["date"])

    _LABEL_COLS = [
        "entry_price",
        "label_3d_3pct_clean", "label_3d_5pct_clean", "label_3d_10pct_clean",
        "label_5d_3pct_clean", "label_5d_5pct_clean", "label_5d_10pct_clean",
        "label_10d_3pct_clean", "label_10d_5pct_clean", "label_10d_10pct_clean",
        "label_first_3d_3pct", "label_first_3d_5pct", "label_first_3d_10pct",
        "label_first_5d_3pct", "label_first_5d_5pct", "label_first_5d_10pct",
        "label_first_10d_3pct", "label_first_10d_5pct", "label_first_10d_10pct",
    ]
    labeled_rows = []
    for ticker, date_str in rows:
        df = df_all[df_all["ticker"] == ticker].set_index("date")
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        result = label_one(df, date_str)
        if result is not None:
            labeled_rows.append({"ticker": ticker, "date": date_str, **result})

    if not labeled_rows:
        return

    df_labels = pd.DataFrame(labeled_rows)
    set_clause = ", ".join(f"{col} = s.{col}" for col in _LABEL_COLS)
    conn = get_conn()
    try:
        conn.register("_lbls", df_labels)
        conn.execute(f"""
            UPDATE universe_daily
            SET {set_clause}
            FROM _lbls s
            WHERE universe_daily.date = CAST(s.date AS DATE)
              AND universe_daily.ticker = s.ticker
        """)
        logger.info("universe_daily 라벨 UPDATE 완료: %d건", len(labeled_rows))
    except Exception:
        logger.exception("universe_daily 라벨 UPDATE 실패")
    finally:
        conn.close()


def _get_name(ticker: str) -> str:
    try:
        return stock.get_market_ticker_name(ticker) or ticker
    except Exception:
        return ticker
