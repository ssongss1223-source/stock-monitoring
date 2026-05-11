# Checkpoint

## Current Goal
- live 데이터 축적하며 신호 품질 검증 + 다음 고도화 준비

## Current Status
- XGBoost(label_3d_5pct) 연동 배포 완료, VM 정상 운영 중
- 351종목 수집 16:00 KST / 분석 07:00 KST (변경됨)
- 텔레그램 상세에 `ML(3일+5%): XX%` 표시

## Done
- 스코어링 시스템, ML 파이프라인(feature_engineering, train_xgboost), backfill 730일
- 9개 라벨 전체 학습 → label_3d_5pct 선택 (AUC 0.6168, positive 37.5%)
- `agents/ml_scorer.py`: ohlcv DB 피처 조합 + xgb 추론 → BuySignal.xgb_prob
- `agents/report.py`: 텔레그램 상세에 ML 확률 표시
- 분석 스케줄 08:00 → 07:00 KST 변경 (22:00 UTC)

## Remaining

**[즉시]**
- 없음. 다음 자연스러운 시점: live 신호 수일 축적 후

**[중기] 신호 사후 검증 자동화**
- 방향 A: 별도 텔레그램 채널 파서 — 발령 신호 3일 후 수익률 자동 계산·발송
- 방향 B: `/verify 7` 슬래시 명령 — 최근 N일 신호 성과 조회 (telegram polling)
- 구현: `signal_history × ohlcv_daily` JOIN → 3거래일 후 max_high 계산

**[중기] 종목 그룹별 모델**
- 현재 전종목 단일 모델 → 변동성 구간(고/중/저)으로 그룹 분류 후 그룹별 학습
- 그룹별 최적 라벨도 다를 수 있음 (고변동 → 3d_10pct, 저변동 → 5d_5pct 등)
- 선행 조건: live 데이터 충분히 축적 + sector 데이터 적재

**[중기] 장세 레이어 강화**
- 현재: MA 기반 bull/sideways/bear 단일 레이어
- 추가 후보: VKOSPI(공포지수), 외국인/기관 지수 수급, 섹터 모멘텀
- 단기 실현: 외국인/기관 5일 net을 지수 레벨에서 집계 → market_ctx에 추가

**[중기] sector 데이터 적재**
- `pykrx.stock.get_market_sector_classifications()` → ticker_master
- 이후 장세 레이어·그룹 모델에 필요

**[보류] market_cap NULL**
- KRX API 차단, close×volume 대체 불가

## Risks / Blockers
- 학습 데이터 전체 backfill → live 분포 차이 존재 (AUC 0.62 = 참고용)
- ticker_master 비어 있어 sector 피처 없음

## Next Actions
1. 수일간 live 신호 수신 후 `xgb_prob` 분포 확인 (의미있는 값 나오는지)
2. 신호 사후 검증 구현 (방향 B: `/verify` 텔레그램 명령이 단순)
3. sector 적재 → 장세 레이어 확장

## References
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **모델**: `data/models/xgb_label_3d_5pct.json` (채택) / 9개 전체 `xgb_label_*.json`
- **ML 스크립트**: `scripts/feature_engineering.py`, `scripts/train_xgboost.py`
- **피처 27개**: vol_score, trend_score, pattern_score, risk_reward, volume, amount, market_cap, per, pbr, div_yield, foreign_exh_rate, short_ratio, turnover_rate, foreign_net_5d, inst_net_5d, log_avg_volume_20d, hist_volatility_20d, avg_foreign_exh_rate_20d, grade_S/A/B, pattern_*(4개), sv_live_v1/v2
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 22:00 UTC(07:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목

## Last Updated
- 2026-05-11 22:00
