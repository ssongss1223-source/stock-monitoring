import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# 종목 유니버스 모드
UNIVERSE_MODE: str = os.getenv("UNIVERSE_MODE", "sector_top5")
# "watchlist"    — data/watchlist.json에 직접 추가한 종목만
# "top50_mktcap" — 시총 상위 50개 자동 조회
# "sector_top5"  — 섹터별 시총 상위 5개 (기본값, ~50~60 종목)
INCLUDE_WATCHLIST: bool = True  # True면 모드 무관하게 watchlist 항상 포함

# 스코어링 설정
SCORING_VERSION: str = "v1_baseline"
SCORING_CONFIG_DIR: str = f"config/scoring/{SCORING_VERSION}/"

# 데이터 파일 경로
PORTFOLIO_FILE: str = "data/portfolio.json"
WATCHLIST_FILE: str = "data/watchlist.json"

# 실행 스케줄 (UTC 기준 — 07:00 UTC = 16:00 KST)
SCHEDULE_HOUR_UTC: int = 7
SCHEDULE_MINUTE_UTC: int = 0
