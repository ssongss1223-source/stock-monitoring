import asyncio
import logging
import socket
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from agents.orchestrator import Orchestrator

class _PykrxNoiseFilter(logging.Filter):
    """pykrx util.py가 root 로거로 뱉는 JSON 파싱 오류 메시지 억제."""
    _KEYWORDS = ("Expecting value", "JSONDecodeError", "not all arguments converted")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            # pykrx가 logging.info(args, kwargs) 형식으로 호출할 때 발생하는
            # TypeError("not all arguments converted") 방지 — 해당 레코드 억제
            return False
        return not any(kw in msg for kw in self._KEYWORDS)


# 모든 소켓 네트워크 호출에 OS 레벨 타임아웃 적용 (pykrx hang 방지)
socket.setdefaulttimeout(30)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("stock_monitor.log", encoding="utf-8"),
    ],
)
# pykrx 내부 로거 노이즈 억제
logging.getLogger("pykrx").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
# pykrx util.py가 root 로거를 직접 호출하는 케이스 필터
logging.getLogger().addFilter(_PykrxNoiseFilter())
logger = logging.getLogger(__name__)


async def main() -> None:
    orchestrator = Orchestrator()

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        orchestrator.run_collect,
        trigger="cron",
        hour=config.COLLECT_HOUR_UTC,
        minute=config.COLLECT_MINUTE_UTC,
        id="daily_collect",
    )
    scheduler.add_job(
        orchestrator.run_daily,
        trigger="cron",
        hour=config.SCHEDULE_HOUR_UTC,
        minute=config.SCHEDULE_MINUTE_UTC,
        id="daily_analysis",
    )
    scheduler.start()
    logger.info(
        "스케줄러 시작: 수집 %02d:%02d UTC (= %02d:%02d KST) / 분석 %02d:%02d UTC (= %02d:%02d KST)",
        config.COLLECT_HOUR_UTC, config.COLLECT_MINUTE_UTC,
        (config.COLLECT_HOUR_UTC + 9) % 24, config.COLLECT_MINUTE_UTC,
        config.SCHEDULE_HOUR_UTC, config.SCHEDULE_MINUTE_UTC,
        (config.SCHEDULE_HOUR_UTC + 9) % 24, config.SCHEDULE_MINUTE_UTC,
    )

    # --resend-last: DB에서 마지막 결과 재발송 (텔레그램 형식 테스트용, ~5초)
    if "--resend-last" in sys.argv:
        logger.info("--resend-last 플래그 감지: 마지막 리포트 재발송")
        await orchestrator.run_resend_last()
        return

    # --run-now 플래그로 즉시 실행 (테스트/수동 실행용) — 거래일 체크 우회
    if "--run-now" in sys.argv:
        logger.info("--run-now 플래그 감지: 즉시 분석 실행 (거래일 체크 우회)")
        await orchestrator.run_daily(force=True)
        return

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("종료 신호 수신")
    finally:
        scheduler.shutdown()
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    asyncio.run(main())
