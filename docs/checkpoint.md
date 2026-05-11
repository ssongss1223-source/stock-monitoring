# Checkpoint

## Current Goal
- XGBoost 모델을 실제 매수 신호 필터(xgb_prob 표시)로 연동

## Current Status
- 세션 27 완료: vol_score 분포 불일치 해소 + backfill 730일 확장 + 모델 재학습
- VM 정상 운영 중: 351종목(kospi200_daq150) 수집 / 분석 스케줄 정상 동작

## Done
- 스코어링 시스템(regime 등급/OBV/group cap), ML 파이프라인(feature_engineering, train_xgboost), 텔레그램 수집 알림
- `orchestrator.py`: T→T-1 signal_date 버그 수정
- `backfill_signals.py`: vol_score 8-indicator daily proxy로 교체 (live_v2 동일 구조)
- `amount` NULL → close×volume 프록시로 225,841건 채움
- backfill --days 730: signal_history 56,838건, feature_matrix 48,240건 × 48컬럼
- **XGBoost 재학습 결과**: label_3d_5pct AUC 0.6168 ← 권장 / label_5d_5pct 0.5968 / label_10d_10pct 0.6038
  - 주요 피처: hist_volatility_20d, div_yield, pbr, vol_score, trend_score

## Remaining
- **[즉시] XGBoost → BuySignalAgent 연동**: xgb_prob 필드 추가, 텔레그램 표시
  - 권장 타겟: label_3d_5pct (AUC 0.6168)
  - 추론 시 피처 벡터: vol_score, trend_score, hist_volatility_20d 등 27개
- **[중기] sector 데이터 적재**: ticker_master에 pykrx 업종 정보 채우기
- **[중기] 라이브 데이터 축적 후 재학습**: live_v2 레코드 충분히 쌓인 후
- **[보류] market_cap NULL**: close×volume 대체 불가, KRX API 차단으로 보류

## Risks / Blockers
- amount=close×volume 프록시 → market_cap NULL은 여전히 해결 안 됨
- ticker_master 비어 있어 sector 피처 없음
- 학습 데이터 전체가 backfill → live 분포와 여전히 차이 존재
- KIS 60분봉은 당일치만 수집 가능

## Next Actions
1. **XGBoost 연동**: `models/signals.py`에 `xgb_prob` 필드 추가
   → `agents/buy_signal.py`에 모델 로드 및 추론 (27개 피처 벡터)
   → `agents/report.py`에 텔레그램 표시 ("ML:0.68")
2. **sector 데이터 적재**: `pykrx.stock.get_market_sector_classifications()` → ticker_master
3. **VM 동기화**: backfill_signals.py, orchestrator.py 수정사항 VM에 배포

## References
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **Feature matrix**: `data/feature_matrix.parquet` (48,240건 × 48컬럼)
- **모델**: `data/models/xgb_{target}.json` (3개: 5d_5pct, 3d_5pct, 10d_10pct)
- **모델 결과**: `data/xgb_results.json`
- **스코어링 설정**: `config/scoring/v1_baseline/`
- **ML 스크립트**: `scripts/feature_engineering.py`, `scripts/train_xgboost.py`
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 23:00 UTC(08:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목 (2026-05-09부터 적용)
- **피처 27개**: vol_score, trend_score, pattern_score, risk_reward, volume, amount, market_cap, per, pbr, div_yield, foreign_exh_rate, short_ratio, turnover_rate, foreign_net_5d, inst_net_5d, log_avg_volume_20d, hist_volatility_20d, avg_foreign_exh_rate_20d, grade_S/A/B, pattern_*(4개), sv_live_v1/v2

## Last Updated
- 2026-05-11 세션 27
