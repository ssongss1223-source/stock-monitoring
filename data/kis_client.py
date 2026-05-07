import json
import logging
import os
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE_PATH = Path("data/kis_token.json")

# KIS 실전 API: 초당 20건 허용. 여유있게 0.06s = ~16건/초
_RATE_DELAY = 0.06


class KisClient:
    """한국투자증권 OpenAPI 클라이언트 (시세 전용).

    토큰: data/kis_token.json 캐시 (24시간 유효)
    분봉: FHKST03010200 — 당일 1분봉 30건씩 역순 페이지네이션 → 60분 리샘플
    """

    _cached_token: str | None = None
    _cached_expires: datetime | None = None

    def __init__(self) -> None:
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 환경변수 미설정")

    # ── 토큰 관리 ──────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        with _TOKEN_LOCK:
            now = datetime.now()
            margin = timedelta(minutes=10)

            if KisClient._cached_token and KisClient._cached_expires:
                if now + margin < KisClient._cached_expires:
                    return KisClient._cached_token

            _load_token_from_file()
            if KisClient._cached_token and KisClient._cached_expires:
                if now + margin < KisClient._cached_expires:
                    return KisClient._cached_token

            return self._issue_token()

    def _issue_token(self) -> str:
        resp = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        token = body["access_token"]
        expires = datetime.now() + timedelta(seconds=int(body.get("expires_in", 86400)))

        KisClient._cached_token = token
        KisClient._cached_expires = expires
        _save_token_to_file(token, expires)
        logger.info("KIS 토큰 발급 완료 (만료: %s)", expires.strftime("%Y-%m-%d %H:%M"))
        return token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # ── 분봉 조회 ──────────────────────────────────────────────────────────────

    def fetch_today_1min(self, ticker: str) -> pd.DataFrame:
        """당일 1분봉 전체 (9:00~15:30) — 30건씩 역순 페이지네이션.

        Returns:
            DataFrame(index=datetime, Open/High/Low/Close/Volume/Turnover)
            비거래일 또는 실패 시 빈 DataFrame
        """
        today_str = datetime.now().strftime("%Y%m%d")
        rows: list[dict] = []
        # 15:30(장 마감) 기준으로 역방향 시작 — 16:00 시작 시 장후 데이터가 섞임
        next_hour = "153000"

        for _ in range(20):  # 안전장치 (390분 / 30봉 = 13회)
            time.sleep(_RATE_DELAY)
            output2 = _call_minute_api(
                self.base_url, self._headers("FHKST03010200"), ticker, next_hour
            )
            if output2 is None:
                break
            if not output2:
                break

            reached_open = False
            last_valid_hstr = ""
            for item in output2:
                bsop = item.get("stck_bsop_date", "")
                hstr = item.get("stck_cntg_hour", "")  # HHMMSS
                if bsop != today_str or len(hstr) < 6:
                    continue
                h, m, s = int(hstr[:2]), int(hstr[2:4]), int(hstr[4:6])
                # 장 시간 필터: 9:00 ~ 15:30
                if h < 9:
                    reached_open = True
                    continue
                if (h == 15 and m > 30) or h > 15:
                    continue
                last_valid_hstr = hstr
                try:
                    dt = datetime(
                        int(today_str[:4]), int(today_str[4:6]), int(today_str[6:]),
                        h, m, s,
                    )
                    rows.append({
                        "Datetime": dt,
                        "Open":   float(item.get("stck_oprc") or 0),
                        "High":   float(item.get("stck_hgpr") or 0),
                        "Low":    float(item.get("stck_lwpr") or 0),
                        "Close":  float(item.get("stck_prpr") or 0),
                        "Volume": float(item.get("cntg_vol") or 0),
                    })
                except Exception:
                    continue

            if reached_open:
                break

            # 다음 페이지 기준: 장 시간 내 마지막으로 처리한 봉의 시간
            pivot = last_valid_hstr or output2[-1].get("stck_cntg_hour", "")
            if not pivot or pivot <= "090100":
                break
            next_hour = pivot

        if not rows:
            logger.debug("%s KIS 당일 1분봉 없음 (비거래일 또는 장전)", ticker)
            return pd.DataFrame()

        df = (
            pd.DataFrame(rows)
            .drop_duplicates("Datetime")
            .set_index("Datetime")
            .sort_index()
        )
        return df

    def fetch_today_60min(self, ticker: str) -> pd.DataFrame:
        """당일 60분봉 — 1분봉 수집 후 리샘플."""
        df_1m = self.fetch_today_1min(ticker)
        if df_1m.empty:
            return pd.DataFrame()
        return _resample_60min(df_1m)


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _call_minute_api(
    base_url: str, headers: dict, ticker: str, hour: str, retries: int = 2
) -> list | None:
    """분봉 API 단일 호출 — 500 에러 시 1회 재시도. None 반환 시 루프 종료."""
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_HOUR_1": hour,
        "FID_PW_DATA_INCU_YN": "N",
    }
    url = f"{base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json().get("output2") or []
        except requests.HTTPError as e:
            if resp.status_code == 500 and attempt < retries - 1:
                time.sleep(0.5)
                continue
            logger.warning("%s KIS 분봉 조회 실패 (hour=%s): %s", ticker, hour, e)
            return None
        except Exception as e:
            logger.warning("%s KIS 분봉 조회 실패 (hour=%s): %s", ticker, hour, e)
            return None
    return None


def _resample_60min(df_1m: pd.DataFrame) -> pd.DataFrame:
    """1분봉 → 60분봉 (9:00, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00)."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    if "Turnover" in df_1m.columns:
        agg["Turnover"] = "sum"
    df = df_1m.resample("60min", closed="left", label="left").agg(agg)
    return df.dropna(subset=["Close"])


def _load_token_from_file() -> None:
    try:
        if _TOKEN_CACHE_PATH.exists():
            data = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            KisClient._cached_token = data.get("token")
            exp = data.get("expires")
            KisClient._cached_expires = datetime.fromisoformat(exp) if exp else None
    except Exception:
        pass


def _save_token_to_file(token: str, expires: datetime) -> None:
    try:
        _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_PATH.write_text(
            json.dumps({"token": token, "expires": expires.isoformat()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("KIS 토큰 파일 저장 실패: %s", e)
