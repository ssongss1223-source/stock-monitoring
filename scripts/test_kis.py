"""KIS API 연결 테스트 — 현대차(005380) 당일 60분봉 수집 확인.

실행:
    python scripts/test_kis.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    from data.kis_client import KisClient

    ticker = "005380"  # 현대차
    logger.info("=== KIS API 테스트 시작 ===")

    client = KisClient()

    # 1. 토큰 발급
    logger.info("1. 토큰 발급")
    token = client._get_token()
    logger.info("   토큰 앞 20자: %s...", token[:20])

    # 2. 1분봉 수집
    logger.info("2. %s 당일 1분봉 수집", ticker)
    df_1m = client.fetch_today_1min(ticker)
    if df_1m.empty:
        logger.warning("   1분봉 없음 — 비거래일이거나 장 시작 전")
    else:
        logger.info("   1분봉 %d건 수집", len(df_1m))
        logger.info("   최초: %s  최종: %s", df_1m.index[0], df_1m.index[-1])
        logger.info("   컬럼: %s", list(df_1m.columns))
        print(df_1m.head(10).to_string())

    # 3. 60분봉 리샘플
    logger.info("3. 60분봉 리샘플")
    df_60m = client.fetch_today_60min(ticker)
    if df_60m.empty:
        logger.warning("   60분봉 없음")
    else:
        logger.info("   60분봉 %d건", len(df_60m))
        print(df_60m.to_string())

    logger.info("=== 테스트 완료 ===")


if __name__ == "__main__":
    main()
