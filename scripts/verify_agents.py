"""
에이전트별 단독 실행 검증 스크립트.
프로젝트 루트에서 실행: python scripts/verify_agents.py [--agent <name>]

에이전트 이름: market, universe, volume, technical, buy, sell, full
기본값: 순서대로 전체 실행
"""
import asyncio
import io
import logging
import sys
import os

# Windows CP949 콘솔 → UTF-8 강제
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# load_dotenv()를 pykrx 임포트 전에 실행해서 KRX_ID/KRX_PW 주입
import config  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,  # 에이전트 내부 로그는 줄이고 결과만 출력
    format="%(levelname)s %(name)s: %(message)s",
)

TICKER = "005930"   # 삼성전자 (가장 유동성 풍부, 항상 데이터 존재)
TICKER_NAME = "삼성전자"

SEP = "─" * 60


def section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")


def info(label: str, value) -> None:
    print(f"  {label:<22} {value}")


# ── 1. MarketFilterAgent ──────────────────────────────────────────────────────

async def verify_market():
    section("1. MarketFilterAgent — KOSPI/KOSDAQ 장세 판단")
    from core.scoring_engine import ScoringEngine
    from agents.market_filter import MarketFilterAgent
    import config

    engine = ScoringEngine(config.SCORING_CONFIG_DIR)
    agent = MarketFilterAgent(engine)
    markets = await agent.run()

    for name, ctx in markets.items():
        print(f"\n  [{name}]")
        info("장세 상태", ctx.market_status.upper())
        info("점수", ctx.score)
        info("market_bias", f"+{ctx.market_bias}")
        if ctx.market_status == "bear":
            ok("하락장 감지 → market_bias=+5 적용됨")
        elif ctx.market_status == "sideways":
            warn("횡보장 → market_bias=+2 적용됨")
        else:
            ok("상승장 → market_bias=0")

    return markets


# ── 2. UniverseManager ───────────────────────────────────────────────────────

def verify_universe():
    section("2. UniverseManager — 분석 종목 유니버스")
    from agents.universe_manager import UniverseManager
    import config

    print(f"\n  UNIVERSE_MODE = {config.UNIVERSE_MODE}")
    universe = UniverseManager().get_universe()

    if not universe:
        warn("유니버스 비어있음 (sector_top5 실패 시 top50으로 폴백됨)")
        return []

    ok(f"종목 {len(universe)}개 확보")
    print()
    for i, (ticker, market) in enumerate(universe[:10]):  # 최대 10개만 출력
        print(f"    {i+1:2d}. {ticker}  ({market})")
    if len(universe) > 10:
        print(f"    ... 외 {len(universe)-10}개")

    return universe


# ── 3. VolumeAnalysisAgent ───────────────────────────────────────────────────

async def verify_volume():
    section(f"3. VolumeAnalysisAgent — {TICKER_NAME}({TICKER}) 거래량 분석")
    from core.scoring_engine import ScoringEngine
    from agents.volume_analysis import VolumeAnalysisAgent
    import config

    engine = ScoringEngine(config.SCORING_CONFIG_DIR)
    agent = VolumeAnalysisAgent(engine)
    result = await agent.run(TICKER)

    print()
    info("거래량 점수", f"{result.volume_score}점")
    info("폭발 임박", "YES" if result.explosion_imminent else "NO")
    info("수급 흐름", result.smart_money_flow)

    if result.volume_score > 0:
        ok("거래량 분석 정상 동작")
    else:
        warn("거래량 점수 0 — 조건 미충족 또는 데이터 부족")

    return result


# ── 4. TechnicalAnalysisAgent ─────────────────────────────────────────────────

async def verify_technical():
    section(f"4. TechnicalAnalysisAgent — {TICKER_NAME}({TICKER}) 기술 분석")
    from core.scoring_engine import ScoringEngine
    from agents.technical_analysis import TechnicalAnalysisAgent
    import config

    engine = ScoringEngine(config.SCORING_CONFIG_DIR)
    agent = TechnicalAnalysisAgent(engine)
    result = await agent.run(TICKER)

    print()
    info("현재가", f"{result.current_price:,.0f}원" if result.current_price > 0 else "조회 실패")
    info("추세 점수 (MA)", f"{result.trend_score}점")
    info("일목 점수", f"{result.ichimoku_score}점")
    info("합산 점수", f"{result.total_score}점")
    info("패턴", result.pattern or "없음")
    info("지지선", f"{result.support:,.0f}원" if result.support else "없음")
    info("저항선", f"{result.resistance:,.0f}원" if result.resistance else "없음")

    if result.current_price > 0:
        ok("OHLCV 데이터 조회 및 분석 정상")
    else:
        warn("현재가 조회 실패 — 데이터 확인 필요")

    return result


# ── 5. BuySignalAgent (앙상블) ────────────────────────────────────────────────

async def verify_buy(markets=None, tech=None, vol=None):
    section(f"5. BuySignalAgent — {TICKER_NAME}({TICKER}) 매수 등급 판정")
    from core.scoring_engine import ScoringEngine
    from agents.buy_signal import BuySignalAgent
    from agents.market_filter import MarketFilterAgent
    from agents.technical_analysis import TechnicalAnalysisAgent
    from agents.volume_analysis import VolumeAnalysisAgent
    import config

    engine = ScoringEngine(config.SCORING_CONFIG_DIR)

    if markets is None:
        print("  (장세 데이터 조회 중...)")
        markets = await MarketFilterAgent(engine).run()
    if tech is None:
        print("  (기술 분석 중...)")
        tech = await TechnicalAnalysisAgent(engine).run(TICKER)
    if vol is None:
        print("  (거래량 분석 중...)")
        vol = await VolumeAnalysisAgent(engine).run(TICKER)

    market_ctx = markets.get("KOSPI")
    signal = BuySignalAgent(engine).evaluate(TICKER, TICKER_NAME, tech, vol, market_ctx)

    print()
    if signal:
        ok(f"매수 신호 발생: {signal.grade}급")
        info("총점", signal.total_score)
        info("손절가 (참고)", f"{signal.stop_loss:,.0f}원")
        info("목표가 (참고)", f"{signal.target_price:,.0f}원")
        info("손익비 (참고)", f"1:{signal.risk_reward}")
    else:
        info("결과", "매수 신호 없음 (NONE — 임계값 미달)")
        info("추세 점수", tech.total_score)
        info("거래량 점수", vol.volume_score)
        info("market_bias", market_ctx.market_bias)
        warn("신호 없음 자체는 정상 — 점수 참고")


# ── 6. SellSignalAgent ───────────────────────────────────────────────────────

async def verify_sell():
    section("6. SellSignalAgent — 보유 종목 매도 신호")
    from core.scoring_engine import ScoringEngine
    from agents.sell_signal import SellSignalAgent
    import config
    import json

    engine = ScoringEngine(config.SCORING_CONFIG_DIR)
    agent = SellSignalAgent(engine)

    # portfolio.json 내용 미리 출력
    try:
        with open(config.PORTFOLIO_FILE, encoding="utf-8") as f:
            portfolio_raw = json.load(f)
        holdings = {k: v for k, v in portfolio_raw.items() if not k.startswith("_")}
    except Exception:
        holdings = {}

    if not holdings:
        warn(f"portfolio.json 비어있음 → 테스트용 임시 종목 주입 ({TICKER_NAME} 매수가 60,000)")
        # 임시로 파일에 테스트 데이터 쓰기
        test_data = {TICKER: 60000}
        with open(config.PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(test_data, f)
        injected = True
    else:
        injected = False
        print(f"\n  보유 종목: {list(holdings.keys())}")

    signals = await agent.run()

    print()
    if signals:
        for s in signals:
            ok(f"{s.name}({s.ticker}) → {s.action} (score={s.score})")
            info("수익률", f"{s.profit_pct:+.1f}%")
            info("사유", s.reason)
    else:
        info("결과", "매도 신호 없음 — 보유 유지")

    # 임시 주입한 경우 원래대로 복원
    if injected:
        with open(config.PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print("\n  (테스트용 임시 데이터 복원 완료)")

    if signals is not None:
        ok("SellSignalAgent 정상 동작")


# ── 7. 2025-04 폭락기 market_bias 검증 ───────────────────────────────────────

async def verify_crash_period():
    section("7. 2025-04 폭락기 market_bias=+5 검증")
    from core.scoring_engine import ScoringEngine
    from agents.market_filter import MarketFilterAgent
    import config
    import agents.market_filter as mf_module

    engine = ScoringEngine(config.SCORING_CONFIG_DIR)
    agent = MarketFilterAgent(engine)

    # _today()를 패치해서 2025-04-15 기준으로 분석
    original_today = mf_module._today
    original_ago = mf_module._ago

    from datetime import date, timedelta
    target_date = date(2025, 4, 15)

    mf_module._today = lambda: target_date.strftime("%Y%m%d")
    mf_module._ago = lambda days: (target_date - timedelta(days=days)).strftime("%Y%m%d")

    print("\n  기준일: 2025-04-15 (관세 폭락 직후)")
    print("  (pykrx 과거 데이터 조회 중... 잠시 대기)")

    try:
        markets = await agent.run()
        for name, ctx in markets.items():
            print(f"\n  [{name}]")
            info("장세 상태", ctx.market_status.upper())
            info("점수", ctx.score)
            info("market_bias", f"+{ctx.market_bias}")
            if ctx.market_bias >= 5:
                ok("bear 감지 → market_bias=+5 발동 ✔")
            elif ctx.market_bias >= 2:
                warn(f"sideways 판정 → market_bias=+{ctx.market_bias} (bear 미달)")
            else:
                warn(f"bull 판정 (점수={ctx.score}) — 데이터 또는 임계값 확인 필요")
    finally:
        mf_module._today = original_today
        mf_module._ago = original_ago


# ── 메인 ─────────────────────────────────────────────────────────────────────

async def run_all():
    print("\n" + "=" * 60)
    print("  주식 신호 알림 시스템 — 에이전트 검증")
    print("=" * 60)

    args = sys.argv[1:]
    target = None
    if "--agent" in args:
        idx = args.index("--agent")
        target = args[idx + 1] if idx + 1 < len(args) else None

    markets = tech = vol = None

    if target in (None, "market"):
        markets = await verify_market()
    if target in (None, "universe"):
        verify_universe()
    if target in (None, "volume"):
        vol = await verify_volume()
    if target in (None, "technical"):
        tech = await verify_technical()
    if target in (None, "buy"):
        await verify_buy(markets, tech, vol)
    if target in (None, "sell"):
        await verify_sell()
    if target in (None, "crash"):
        await verify_crash_period()

    print(f"\n{'=' * 60}")
    print("  검증 완료")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all())
