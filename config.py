import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# 종목 유니버스 모드
UNIVERSE_MODE: str = os.getenv("UNIVERSE_MODE", "top100_mktcap")
# "watchlist"      — data/watchlist.json에 직접 추가한 종목만
# "top50_mktcap"   — 시총 상위 50개 자동 조회
# "top100_mktcap"  — 시총 상위 100개 자동 조회 (기본값)
# "sector_top5"    — 섹터별 시총 상위 5개 (~50~60 종목)
INCLUDE_WATCHLIST: bool = True  # True면 모드 무관하게 watchlist 항상 포함

# 스코어링 설정
SCORING_VERSION: str = "v1_baseline"
SCORING_CONFIG_DIR: str = f"config/scoring/{SCORING_VERSION}/"

# 데이터 파일 경로
PORTFOLIO_FILE: str = "data/portfolio.json"
WATCHLIST_FILE: str = "data/watchlist.json"

# OHLCV 영속 저장 경로
OHLCV_DAILY_DIR: str = "data/ohlcv/daily"
OHLCV_HOURLY_DIR: str = "data/ohlcv/hourly_60m"

# 거래량 프로파일 캐시 (종목별 시간대별 P90/P95/P99, 당일 1회 갱신)
VOLUME_PROFILE_CACHE: str = "data/volume_profile_cache.json"

# 실행 스케줄 (UTC 기준 — 23:00 UTC = 08:00 KST 다음날)
SCHEDULE_HOUR_UTC: int = 23
SCHEDULE_MINUTE_UTC: int = 0
