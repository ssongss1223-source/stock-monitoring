"""
signal_history 소급 생성 — 과거 ohlcv_daily로 기술적 분석 실행 후 저장.

일봉 데이터만 사용 (60분봉 없음). vol_score는 live_v2와 동일한 8개 지표를
일봉으로 근사하여 계산 (최대 19점).
기존 signal_history 행은 덮어쓰지 않음 (INSERT OR IGNORE).

메모리 최적화: 종목 1개씩 로드, BATCH_SIZE 종목마다 DB 저장.

Usage:
    python scripts/backfill_signals.py --days 90
    python scripts/backfill_signals.py --days 730 --min_trend 5
    python scripts/backfill_signals.py --days 90 --from_ticker 005930  # 중단 재개
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml as _yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.technical_analysis import _detect_pattern, _ichimoku_flags, _ma_flags
from core.scoring_engine import ScoringEngine
from data.db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 50

_SCORING_DIR = Path(__file__).parent.parent / "config/scoring/v1_baseline"
_engine = ScoringEngine(str(_SCORING_DIR))


def _load_tech_weights() -> tuple[dict, dict]:
    cfg = _yaml.safe_load((_SCORING_DIR / "technical.yaml").read_text(encoding="utf-8"))
    return (
        {r["id"]: r["points"] for r in cfg["ma_rules"]},
        {r["id"]: r["points"] for r in cfg["ichimoku_rules"]},
    )


_MA_W, _ICH_W = _load_tech_weights()


def _trend_score(flags: dict) -> int:
    return (sum(v for k, v in _MA_W.items() if flags.get(k))
            + sum(v for k, v in _ICH_W.items() if flags.get(k)))


def _vol_flags_daily(past: pd.DataFrame) -> dict:
    """live_v2와 동일한 8개 지표를 일봉으로 근사. ScoringEngine.score_volume()에 전달."""
    close = past['close'].astype(float).values
    vol   = past['volume'].astype(float).values
    high  = past['high'].astype(float).values
    low   = past['low'].astype(float).values
    n = len(past)

    flags: dict = {}

    # 1. hourly_vol_ratio_p95: 오늘 거래량 > 히스토리 P95
    if n >= 20:
        p95 = float(np.percentile(vol[:-1], 95))
        flags['hourly_vol_ratio_p95'] = float(vol[-1]) >= p95
    else:
        flags['hourly_vol_ratio_p95'] = False

    # 2. hourly_vol_zscore_high: z-score >= 2.0
    if n >= 21:
        hist = vol[-21:-1]
        std = float(hist.std())
        flags['hourly_vol_zscore_high'] = (
            std > 0 and (float(vol[-1]) - float(hist.mean())) / std >= 2.0
        )
    else:
        flags['hourly_vol_zscore_high'] = False

    # 3. relative_turnover_high: 오늘 거래대금 >= P80 (amount 있을 때만)
    if 'amount' in past.columns:
        amt = past['amount'].astype(float).values
        today_amt = amt[-1]
        if not np.isnan(today_amt):
            hist_amt = amt[:-1][~np.isnan(amt[:-1])]
            if len(hist_amt) >= 10:
                p80 = float(np.percentile(hist_amt, 80))
                flags['relative_turnover_high'] = p80 > 0 and today_amt >= p80
            else:
                flags['relative_turnover_high'] = False
        else:
            flags['relative_turnover_high'] = False
    else:
        flags['relative_turnover_high'] = False

    # 4. vwap_above_60m proxy: close > typical price (H+L+C)/3
    typical = (float(high[-1]) + float(low[-1]) + float(close[-1])) / 3
    flags['vwap_above_60m'] = float(close[-1]) > typical

    # OBV 기반 지표 (5~7)
    if n >= 20:
        direction = np.sign(np.diff(close))
        obv = np.cumsum(np.concatenate([[0.0], direction * vol[1:]]))

        # 5. obv_slope_up_60m: 20일 OBV 기울기 양수
        x = np.arange(20)
        slope = float(np.polyfit(x, obv[-20:], 1)[0])
        flags['obv_slope_up_60m'] = slope > 0

        # 6. obv_divergence_60m: 가격 횡보(-2%~+3%) + OBV 상향
        price_ret = (float(close[-1]) - float(close[-20])) / float(close[-20]) \
                    if close[-20] > 0 else 0.0
        flags['obv_divergence_60m'] = (
            -0.02 <= price_ret <= 0.03
            and float(obv[-1]) > float(obv[-20])
            and slope > 0
        )

        # 7. obv_acceleration_60m: 최근 10일 기울기 > 이전 10일 × 1.3
        if n >= 21:
            prev_slope = float(np.polyfit(np.arange(10), obv[-20:-10], 1)[0])
            curr_slope = float(np.polyfit(np.arange(10), obv[-10:],    1)[0])
            flags['obv_acceleration_60m'] = (
                prev_slope > 0 and curr_slope > 0 and curr_slope > prev_slope * 1.3
            )
        else:
            flags['obv_acceleration_60m'] = False
    else:
        flags['obv_slope_up_60m']     = False
        flags['obv_divergence_60m']   = False
        flags['obv_acceleration_60m'] = False

    # 8. foreign_inst_buy: 5거래일 순매수
    if 'foreign_net' in past.columns and 'inst_net' in past.columns:
        fn5 = past['foreign_net'].iloc[-5:].fillna(0).sum()
        in5 = past['inst_net'].iloc[-5:].fillna(0).sum()
        flags['foreign_inst_buy'] = (fn5 + in5) > 0
    else:
        flags['foreign_inst_buy'] = False

    return flags


def _flush(rows: list) -> int:
    if not rows:
        return 0
    df_sig = pd.DataFrame(rows)
    conn_w = get_conn()
    conn_w.register('_sig', df_sig)
    conn_w.execute("""
        INSERT OR IGNORE INTO signal_history
            (signal_date, ticker, vol_score, grade, features, entry_price, scoring_version)
        SELECT signal_date, ticker, vol_score, grade, features, entry_price, scoring_version
        FROM _sig
    """)
    conn_w.close()
    return len(rows)


def run(days: int, min_trend: int, from_ticker: str | None = None) -> int:
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

        conn = get_conn(read_only=True)
        df_t = conn.execute(
            "SELECT date, open, high, low, close, volume, amount, foreign_net, inst_net "
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

            vol_flags = _vol_flags_daily(past)
            vs = _engine.score_volume(vol_flags).volume_score
            grade = _engine.determine_grade(ts, vs, 'bull')
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
                'scoring_version': 'live_v2',
            })

        if (i + 1) % BATCH_SIZE == 0 and batch_rows:
            saved = _flush(batch_rows)
            total_saved += saved
            logger.info("  -> %d rows saved (total %d)", saved, total_saved)
            batch_rows = []

    if batch_rows:
        saved = _flush(batch_rows)
        total_saved += saved
        logger.info("  -> %d rows saved (total %d)", saved, total_saved)

    logger.info("완료: 총 %d건 신호 생성", total_saved)
    return total_saved


def main() -> None:
    p = argparse.ArgumentParser(description="signal_history 소급 생성")
    p.add_argument('--days',        type=int, default=90,   help='소급 일수 (기본 90일)')
    p.add_argument('--min_trend',   type=int, default=5,    help='최소 trend_score (기본 5)')
    p.add_argument('--from_ticker', default=None,           help='이 종목코드부터 재개')
    args = p.parse_args()

    logger.info("소급 시작: 최근 %d일, min_trend=%d", args.days, args.min_trend)
    n = run(args.days, args.min_trend, args.from_ticker)
    logger.info("완료: %d건 신호 생성", n)


if __name__ == '__main__':
    main()
