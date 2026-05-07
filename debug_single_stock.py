#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Debug single stock analysis pipeline: 두산에너빌리티 (034020)
Shows all steps from market context to final buy signal.
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Set working directory to project root
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
import os
os.chdir(project_root)

from pykrx import stock as pykrx_stock
from agents.market_filter import MarketFilterAgent
from agents.technical_analysis import TechnicalAnalysisAgent
from agents.volume_analysis import VolumeAnalysisAgent
from agents.pattern_learning import StockPatternLearner
from agents.buy_signal import BuySignalAgent
from agents.sell_signal import SellSignalAgent
from core.scoring_engine import ScoringEngine
from data.store import OhlcvStore, HourlyStore

TICKER = "034020"
STOCK_NAME = "두산에너빌리티"
MARKET_TYPE = "KOSPI"          # ← 034020은 코스피 종목
ANALYSIS_START = "2025-04-01"  # 분석 기준 시작일 (컨텍스트 표시용)

async def main():
    print(f"\n{'='*70}")
    print(f"  종목 상세 분석: {STOCK_NAME} ({TICKER})")
    print(f"  시장: {MARKET_TYPE}")
    print(f"  분석일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  분석 기준 시작일: {ANALYSIS_START}")
    print(f"{'='*70}\n")

    scoring_engine = ScoringEngine("config/scoring/v1_baseline/")

    # ============ STEP 1: 현재 시장 상황 판단 ============
    print("[STEP 1] 시장 환경 분석 (MarketFilterAgent)")
    print("-" * 70)
    market_agent = MarketFilterAgent(scoring_engine)
    market_dict = await market_agent.run()

    # 034020은 코스피 종목 → KOSPI 기준 장세 적용
    market_context = market_dict.get("KOSPI", market_dict.get("KOSDAQ"))

    kospi = market_dict.get("KOSPI")
    kosdaq = market_dict.get("KOSDAQ")
    if kospi:
        print(f"  KOSPI 장세:  {kospi.market_status.upper()} (점수: {kospi.score}, bias: +{kospi.market_bias})")
    if kosdaq:
        print(f"  KOSDAQ 장세: {kosdaq.market_status.upper()} (점수: {kosdaq.score}, bias: +{kosdaq.market_bias})")
    print(f"\n  [이 종목 적용 장세] KOSPI → {market_context.market_status.upper()}")
    print(f"     시장 스코어: {market_context.score}점")
    print(f"     market_bias (매수 기준 조정치): +{market_context.market_bias}점")
    bias_meanings = {"bull": "상승장 - 기준 그대로",
                     "sideways": "횡보장 - +2점 더 높아야 동일 등급",
                     "bear": "하락장 - 매우 강한 신호만 통과"}
    print(f"     >> {bias_meanings.get(market_context.market_status, '')}\n")

    # ============ STEP 2: 종목 시장 분류 ============
    print("[STEP 2] 종목 시장 분류")
    print("-" * 70)
    print(f"  종목명: {STOCK_NAME}")
    print(f"  티커:   {TICKER}")
    print(f"  시장:   {MARKET_TYPE}  (KOSPI → yfinance suffix: .KS)")
    print(f"  >> {MARKET_TYPE} 기준 장세·데이터로 기술/거래량 분석 진행\n")

    # ============ STEP 3: 데이터 로드 ============
    print("[STEP 3] OHLCV 데이터 로드")
    print("-" * 70)
    df_daily = OhlcvStore.fetch_and_update_daily(TICKER)
    df_hourly = HourlyStore.fetch_and_update_hourly(TICKER, MARKET_TYPE)  # .KS suffix

    if df_daily is None or df_daily.empty:
        print(f"  ERROR: {TICKER}의 일봉 데이터를 찾을 수 없습니다.")
        return

    print(f"  일봉 전체:    {len(df_daily)}일  "
          f"({df_daily.index[0].strftime('%Y-%m-%d')} ~ {df_daily.index[-1].strftime('%Y-%m-%d')})")

    # 2025-04-01 이후 데이터 현황
    df_from_start = df_daily[df_daily.index >= ANALYSIS_START]
    close_col = next((c for c in df_daily.columns if str(c).lower() in ("종가", "close")), df_daily.columns[0])
    current_price = float(df_daily[close_col].iloc[-1])

    if not df_from_start.empty:
        price_at_start = float(df_from_start[close_col].iloc[0])
        change_pct = (current_price / price_at_start - 1) * 100
        print(f"  {ANALYSIS_START} 이후: {len(df_from_start)}거래일  "
              f"(기준가 {price_at_start:,.0f}원 → 현재 {current_price:,.0f}원  "
              f"{change_pct:+.1f}%)")
    print(f"  컬럼:         {list(df_daily.columns)}")

    if df_hourly is not None and not df_hourly.empty:
        print(f"  60분봉:       {len(df_hourly)}개봉  "
              f"(최근: {df_hourly.index[-1].strftime('%Y-%m-%d %H:%M')})")
    else:
        print(f"  60분봉:       없음 (yfinance 미제공 또는 조회 실패)")
    print(f"  현재가:       {current_price:,.0f}원\n")

    # ============ STEP 4: 기술 분석 ============
    print("[STEP 4] 기술적 분석 (TechnicalAnalysisAgent)")
    print("-" * 70)
    tech_agent = TechnicalAnalysisAgent(scoring_engine)
    tech_result = await tech_agent.run(ticker=TICKER, df=df_daily)

    if tech_result:
        score = tech_result.trend_score
        print(f"  추세 스코어: {score}/20")
        if score >= 15:
            label = "강한 상승세 (MA 정렬·일목 확인·RSI 상향)"
        elif score >= 10:
            label = "상승 신호 (MA 정배열 or 기술지표 긍정)"
        elif score >= 5:
            label = "약한 신호 (부분적 신호만 확인)"
        else:
            label = "하락 또는 약세"
        print(f"  >> {label}")

        if tech_result.pattern:
            print(f"  패턴:        {tech_result.pattern}")
            print(f"  (우선순위: cup_handle > falling_box_breakout > triangle_convergence > bb_squeeze)")
        else:
            print(f"  패턴:        없음")

        print(f"  지지선:      {tech_result.support:,.0f}원")
        print(f"  저항선:      {tech_result.resistance:,.0f}원")
        pos = ("저항선 상단 (돌파!)" if current_price > tech_result.resistance
               else "지지선~저항선 사이 (중립)" if current_price > tech_result.support
               else "지지선 하단 (약세)")
        print(f"  현재가 위치: {pos}\n")
    else:
        print("  기술 분석 실패\n")
        tech_result = None

    # ============ STEP 5: 거래량 분석 ============
    print("[STEP 5] 거래량 분석 (VolumeAnalysisAgent)")
    print("-" * 70)
    vol_agent = VolumeAnalysisAgent(scoring_engine)
    vol_result = await vol_agent.run(ticker=TICKER, df=df_daily)

    if vol_result:
        vs = vol_result.volume_score
        vol_max = sum(
            r["points"]
            for r in scoring_engine.config["volume"]["rules"]
        )
        print(f"  거래량 스코어: {vs}/{vol_max}")
        if vs >= 15:
            label = "폭발적 거래량 (기관/외국인 순매수, OBV 강세)"
        elif vs >= 10:
            label = "긍정적 거래량 (평균 이상)"
        elif vs >= 5:
            label = "보통 거래량"
        else:
            label = "약한 거래량 (수급 데이터 조회 실패 포함 가능)"
        print(f"  >> {label}")
        print(f"  폭발 임박:     {'예' if vol_result.explosion_imminent else '아니오'}")
        print(f"  스마트머니:    {vol_result.smart_money_flow}")
        # 조건별 상세 출력 (재계산)
        from agents.volume_analysis import (
            _vol_5d_above_20d, _vol_consecutive_3d, _vol_rise_price_flat,
            _obv_uptrend_price_flat, _vol_trending_w_price, _price_vol_bullish_corr,
            _compute_obv,
        )
        _close = df_daily[next(c for c in df_daily.columns if str(c).lower() in ("종가", "close"))].astype(float)
        _vol   = df_daily[next(c for c in df_daily.columns if str(c).lower() in ("거래량", "volume"))].astype(float)
        _obv   = _compute_obv(_close, _vol)
        print(f"\n  [조건별 상세]")
        print(f"    vol_5d_above_20d      : {'✅' if _vol_5d_above_20d(_vol) else '❌'} (2pt) — 5일 평균 > 20일 평균")
        print(f"    vol_consecutive_3d    : {'✅' if _vol_consecutive_3d(_vol) else '❌'} (3pt) — 3일 연속 증가")
        print(f"    vol_rise_price_flat   : {'✅' if _vol_rise_price_flat(_vol, _close) else '❌'} (4pt) — 횡보+거래량 증가")
        print(f"    obv_uptrend_price_flat: {'✅' if _obv_uptrend_price_flat(_obv, _close) else '❌'} (3pt) — OBV 우상향+횡보")
        print(f"    vol_trending_w_price  : {'✅' if _vol_trending_w_price(_vol, _close) else '❌'} (2pt) — 추세 거래량+MA20 위")
        print(f"    price_vol_bullish_corr: {'✅' if _price_vol_bullish_corr(_close, _vol) else '❌'} (3pt) — 상승일 거래량 우세")
        print(f"    (수급/공매도는 API 실패 시 ❌)\n")
    else:
        print("  거래량 분석 실패\n")
        vol_result = None

    # ============ STEP 6: 패턴 학습 ============
    print("[STEP 6] 패턴 학습 분석 (StockPatternLearner)")
    print("-" * 70)
    pattern_learner = StockPatternLearner()
    pattern_result = await pattern_learner.run(TICKER, df_daily, df_hourly)

    if pattern_result:
        print(f"  패턴 등급:       {pattern_result.grade}")
        grade_desc = {
            "HIGH": "과거 유사 패턴 65% 이상 성공 → 추세 점수 +3 보너스",
            "MEDIUM": "과거 유사 패턴 50% 이상 성공 → 추세 점수 +1 보너스",
            "LOW": "과거 유사 패턴 35% 이상 성공 → 보너스 없음",
            "INSUFFICIENT": "데이터 부족 또는 미분석 → 보너스 없음",
        }
        print(f"  >> {grade_desc.get(pattern_result.grade, '')}")
        print(f"  신뢰도:          {pattern_result.pattern_confidence*100:.1f}%")
        print(f"  유사 패턴:       {pattern_result.similar_count}/{pattern_result.total_patterns}")
        print(f"  평균 수익률(5일): {pattern_result.avg_return_5d:+.2f}%")
        print(f"  최적 윈도우:     {pattern_result.optimal_window}일")
        print(f"  사용 차원:       {pattern_result.pattern_dim}\n")
    else:
        print("  패턴 분석: 데이터 부족 (초회 실행 시 발생 가능)\n")
        pattern_result = None

    # ============ STEP 7: 매수 신호 종합 ============
    print("[STEP 7] 매수 신호 (BuySignalAgent)")
    print("-" * 70)

    if tech_result is None or vol_result is None:
        print("  기술/거래량 분석 실패로 매수신호 판단 불가\n")
        buy_signal = None
    else:
        buy_agent = BuySignalAgent(scoring_engine)
        buy_signal = buy_agent.evaluate(
            ticker=TICKER,
            name=STOCK_NAME,
            tech=tech_result,
            vol=vol_result,
            market_ctx=market_context,
            pattern_result=pattern_result,
        )

        if buy_signal:
            p_bonus = buy_signal.pattern_score
            eff_trend = buy_signal.trend_score + p_bonus
            print(f"  [등급] {buy_signal.grade}")
            print(f"\n  [스코어 상세]")
            print(f"    추세 스코어 (기술):    {buy_signal.trend_score}점")
            if p_bonus > 0:
                grade_lbl = "HIGH" if p_bonus == 3 else "MEDIUM"
                print(f"    패턴 보너스:          +{p_bonus}점 ({grade_lbl})")
            print(f"    유효 추세:            {eff_trend}점  (추세+보너스)")
            print(f"    거래량 스코어:         {buy_signal.volume_score}점")
            print(f"    시장 페널티:          +{market_context.market_bias}점  ({market_context.market_status})")
            print(f"    총 점수:              {buy_signal.total_score}점")
            print(f"\n  [등급 기준]")
            print(f"    S급: 추세(+보너스)≥12  거래량≥12  패턴 필수  bias 차감 후")
            print(f"    A급: 추세(+보너스)≥8   거래량≥8")
            print(f"    B급: 추세(+보너스)≥5   거래량≥5")
            print(f"\n  [목표가 & 손절]")
            print(f"    현재가:  {buy_signal.current_price:,.0f}원")
            print(f"    목표가:  {buy_signal.target_price:,.0f}원  ({(buy_signal.target_price/buy_signal.current_price-1)*100:+.1f}%)")
            print(f"    손절가:  {buy_signal.stop_loss:,.0f}원  ({(buy_signal.stop_loss/buy_signal.current_price-1)*100:+.1f}%)")
            print(f"    손익비:  1 : {buy_signal.risk_reward:.2f}\n")
        else:
            print("  매수 신호 없음 (점수 미달 또는 등급 기준 불충족)\n")
            print(f"  [미달 상세]")
            if tech_result:
                p_bonus = 0
                if pattern_result:
                    p_bonus = 3 if pattern_result.grade == "HIGH" else 1 if pattern_result.grade == "MEDIUM" else 0
                print(f"    추세(+보너스): {tech_result.trend_score}+{p_bonus}={tech_result.trend_score+p_bonus}점")
            if vol_result:
                print(f"    거래량:        {vol_result.volume_score}점")
            print(f"    시장 페널티:   +{market_context.market_bias}점\n")

    # ============ STEP 8: 매도 신호 ============
    print("[STEP 8] 매도 신호 (SellSignalAgent)")
    print("-" * 70)
    sell_agent = SellSignalAgent(scoring_engine)
    all_sell_signals = await sell_agent.run()
    sell_signal = next((s for s in all_sell_signals if s.ticker == TICKER), None)

    if sell_signal:
        print(f"  매도 액션:  {sell_signal.action}")
        print(f"  우선순위:   {sell_signal.priority}/5")
        print(f"  점수:       {sell_signal.score}점")
        print(f"  사유:       {sell_signal.reason}\n")
    else:
        print("  매도 신호: 보유 기록 없음 (portfolio.json에 미등록)\n")

    # ============ 최종 요약 ============
    print("=" * 70)
    print(f"  분석 완료: {STOCK_NAME} ({TICKER})  [{MARKET_TYPE}]")
    print("=" * 70)

    if buy_signal:
        print(f"\n  [최종 결론]")
        print(f"     {buy_signal.grade}급 추천")
        print(f"     목표가 {buy_signal.target_price:,.0f}원 / 손절 {buy_signal.stop_loss:,.0f}원")
        print(f"\n  [핵심 근거]")
        print(f"     시장:    {market_context.market_status.upper()} (bias={market_context.market_bias})")
        print(f"     기술:    {tech_result.pattern if tech_result and tech_result.pattern else '특정 패턴 없음'}")
        vol_label = ("폭발적" if vol_result and vol_result.volume_score >= 15
                     else "보통" if vol_result and vol_result.volume_score >= 10
                     else "약함")
        print(f"     거래량:  {vol_label} ({vol_result.volume_score if vol_result else '-'}점)")
        if pattern_result and pattern_result.grade != "INSUFFICIENT":
            print(f"     패턴ML:  {pattern_result.grade} ({pattern_result.pattern_confidence*100:.1f}%)")
    else:
        print(f"\n  [최종 결론]")
        print(f"     매수 신호 없음")
        print(f"     시장: {market_context.market_status.upper()} / "
              f"기술: {tech_result.trend_score if tech_result else '-'}점 / "
              f"거래량: {vol_result.volume_score if vol_result else '-'}점")
    print()


if __name__ == "__main__":
    asyncio.run(main())
