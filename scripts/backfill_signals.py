"""
signal_history 소급 생성 — 과거 ohlcv_daily로 기술적 분석 실행 후 저장.

일봉 데이터만 사용 (60분봉 없음). vol_score는 일봉 거래량/수급으로 근사.
기존 signal_history 행은 덮어쓰지 않음 (INSERT OR IGNORE).

메모리 최적화: 종목 1개씩 로드, BATCH_SIZE 종목마다 DB 저장.

Usage:
    python scripts/backfill_signals.py --days 90
    python scripts/backfill_signals.py --days 180 --min_trend 5
    python scripts/backfill_signals.py --days 90 --from_ticker 005930  # 중단 재개
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.technical_analysis import _detect_pattern, _ichimoku_flags, _ma_flags
from data.db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # 종목 단위 배치 저장

# technical.yaml 기준 점수 (일봉)
_MA_W = {
    'price_above_20ma': 1, 'price_above_60ma': 2, 'price_above_120ma': 2,
    'price_above_240ma': 2, 'ma20_above_ma60': 2, 'ma60_above_ma120': 2,
    'ma120_uptrend': 2, 'near_52w_high_5pct': 3,
}
_ICH_W = {
    'ichimoku_triple_positive': 5, 'ichimoku_cloud_support': 1,
    'ichimoku_cloud_break': -3, 'ichimoku_dead_cross': -2,
}


def _trend_score(flags: dict) -> int:
    return (sum(v for k, v in _MA_W.items() if flags.get(k))
            + sum(v for k, v in _ICH_W.items() if flags.get(k)))


def _vol_score_daily(past: pd.DataFrame) -> int:
    """일봉 거래량/수급 기반 vol_score 근사 (최대 6점)."""
    score = 0
    if len(past) >= 21:
        avg20 = past['volume'].iloc[-21:-1].mean()
        if avg20 > 0 and past['volume'].iloc[-1] > avg20 * 1.5:
            score += 3
    if 'foreign_net' in past.columns and 'inst_net' in past.columns:
        fn5 = past['foreign_net'].iloc[-5:].fillna(0).sum()
        in5 = past['inst_net'].iloc[-5:].fillna(0).sum()
        if fn5 + in5 > 0:
            score += 3
    return score


def _grade(trend: int, vol: int) -> str:
    # vol_score 최대 6점 기준으로 임계값 완화
    if trend >= 12 and vol >= 3:
        return 'S'
    if trend >= 8 and vol >= 2:
        return 'A'
    if trend >= 5:
        return 'B'
    return 'NONE'


def _flush(rows: list) -> int:
    """배치 rows를 signal_history에 INSERT OR IGNORE."""
    if not rows:
        return 0
    df_sig = pd.DataFrame(rows)
    conn_w = get_conn()
    conn_w.register('_sig', df_sig)
    conn_w.execute("""
        INSERT OR IGNORE INTO signal_history
            (signal_date, ticker, vol_score, grade, features, entry_price)
        SELECT signal_date, ticker, vol_score, grade, features, entry_price
        FROM _sig
    """)
    conn_w.close()
    return len(rows)


def run(days: int, min_trend: int, from_ticker: str | None = None) -> int:
    # 날짜 범위 계산 (경량 쿼리)
    conn = get_conn(read_only=True)
    max_date = conn.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()[0]
    cutoff = str((pd.Timestamp(max_date) - pd.Timedelta(days=days)).date())
    trade_dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM ohlcv_daily WHERE date > ? ORDER BY date",
            [cutoff]
        ).fetchall()
    ]
    tickers = sorted(
        r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ohlcv_daily").fetchall()
    )
    conn.close()

    if from_ticker:
        tickers = [t for t in tickers if t >= from_ticker]
        logger.info("--from_ticker %s 이후 재개: %d종목", from_ticker, len(tickers))

    logger.info("분석 날짜 %d개 × 종목 %d개", len(trade_dates), len(tickers))

    total_saved = 0
    batch_rows: list[dict] = []

    for i, ticker in enumerate(tickers):
        if i % 20 == 0:
            logger.info("[%d/%d] %s", i + 1, len(tickers), ticker)

        # 종목 1개씩 로드 — 피크 메모리 = 종목 1개 분량
        conn = get_conn(read_only=True)
        df_t = conn.execute(
            "SELECT date, open, high, low, close, volume, foreign_net, inst_net "
            "FROM ohlcv_daily WHERE ticker = ? ORDER BY date",
            [ticker]
        ).df()
        conn.close()

        if df_t.empty:
            continue

        df_t['date'] = pd.to_datetime(df_t['date'])

        for sig_date in trade_dates:
            sig_ts = pd.Timestamp(sig_date)
            past = df_t[df_t['date'] <= sig_ts]
            if len(past) < 80:
                continue

            close = past['close'].astype(float).reset_index(drop=True)
            high  = past['high'].astype(float).reset_index(drop=True)
            low   = past['low'].astype(float).reset_index(drop=True)
            vol   = past['volume'].astype(float).reset_index(drop=True)

            flags = {**_ma_flags(close), **_ichimoku_flags(close, high, low)}
            ts = _trend_score(flags)
            if ts < min_trend:
                continue

            vs = _vol_score_daily(past)
            grade = _grade(ts, vs)
            if grade == 'NONE':
                continue

            pattern = _detect_pattern(close, high, low, vol)
            entry_price = float(past['close'].iloc[-1])
            support = float(past['low'].tail(60).quantile(0.20))
            resist  = float(past['high'].tail(60).quantile(0.80))
            stop    = max(support, entry_price * 0.95)
            target  = resist if resist > entry_price else entry_price * 1.10
            rr = round((target - entry_price) / (entry_price - stop), 2) if entry_price > stop else 0.0

            batch_rows.append({
                'signal_date': sig_ts.date(),
                'ticker': ticker,
                'vol_score': vs,
                'grade': grade,
                'features': json.dumps({
                    'total_score': ts + vs,
                    'trend_score': ts,
                    'pattern': pattern,
                    'pattern_score': 0,
                    'stop_loss': round(stop, 0),
                    'target_price': round(target, 0),
                    'risk_reward': rr,
                }),
                'entry_price': entry_price,
            })

        # BATCH_SIZE 종목마다 저장
        if (i + 1) % BATCH_SIZE == 0 and batch_rows:
            saved = _flush(batch_rows)
            total_saved += saved
            logger.info("  -> %d rows saved (total %d)", saved, total_saved)
            batch_rows = []

    # 나머지 저장
    if batch_rows:
        saved = _flush(batch_rows)
        total_saved += saved
        logger.info("  -> %d rows saved (total %d)", saved, total_saved)

    logger.info("완료: 총 %d건 신호 생성", total_saved)
    return total_saved


def main() -> None:
    p = argparse.ArgumentParser(description="signal_history 소급 생성")
    p.add_argument('--days', type=int, default=90, help='소급 일수 (기본 90일)')
    p.add_argument('--min_trend', type=int, default=5, help='최소 trend_score (기본 5)')
    p.add_argument('--from_ticker', default=None, help='이 종목코드부터 재개 (중단 재시작용)')
    args = p.parse_args()

    logger.info("소급 시작: 최근 %d일, min_trend=%d", args.days, args.min_trend)
    n = run(args.days, args.min_trend, args.from_ticker)
    logger.info("완료: %d건 신호 생성", n)


if __name__ == '__main__':
    main()
