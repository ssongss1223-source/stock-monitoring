# Checkpoint

## Current Goal
- live 데이터 축적 후 모델 고도화 + 신호 검증 자동화

## Current Status
- 세션 28 완료: XGBoost(label_3d_5pct) 연동 배포 완료
- VM 정상 운영: 351종목(kospi200_daq150), 분석 07:00 KST / 수집 16:00 KST
- 텔레그램 상세 섹션에 `ML(3일+5%): XX%` 표시

## Done
- 스코어링 시스템(regime 등급/OBV/group cap), ML 파이프라인(feature_engineering, train_xgboost)
- `orchestrator.py`: T→T-1 signal_date 버그 수정
- `backfill_signals.py`: vol_score 8-indicator daily proxy로 교체 (live_v2 동일 구조)
- `amount` NULL → close×volume 프록시로 채움
- backfill --days 730: signal_history 56,838건, feature_matrix 48,240건 × 48컬럼
- 9개 라벨 전체 학습 및 분석, label_3d_5pct 선택 (AUC 0.6168)
- `agents/ml_scorer.py`: ohlcv DB 피처 + 신호 피처 조합 → xgb 추론
- `models/signals.py`: BuySignal.xgb_prob 필드 추가
- `agents/report.py`: 텔레그램 상세에 ML 확률 표시
- 분석 스케줄 08:00 KST → 07:00 KST 변경

## 9개 라벨 학습 결과 (2026-05-11 기준)
| 라벨 | AUC | positive율 |
|---|---|---|
| label_3d_10pct | 0.6627 | 14.7% |
| label_5d_10pct | 0.6342 | 23.7% |
| **label_3d_5pct** | **0.6168** | **37.5%** ← 채택 |
| label_10d_10pct | 0.6038 | 38.3% |
| label_5d_5pct | 0.5968 | 48.7% |
- 주요 피처: hist_volatility_20d, pbr, div_yield, vol_score, trend_score

## Remaining

### [중기] 아이디어 1 — 종목 그룹별 모델 개선
- **배경**: 현재 전종목 풀링 단일 모델 → 대형주/소형주 특성 차이 미반영
- **종목별 별도 모델은 불가** (평균 133건/종목으로 데이터 부족)
- **방향**: 변동성 구간(hist_volatility_20d)으로 3~4그룹 분류 → 그룹별 모델 학습
  - 고변동(코스닥 소형) → label_3d_5pct 또는 label_3d_10pct
  - 저변동(대형주) → label_5d_5pct 또는 label_10d_5pct
  - 추론 시 그룹 판별 후 해당 모델의 xgb_prob 사용
- **선행 조건**: live 데이터 충분히 쌓인 후 그룹별 AUC 비교

### [중기] 아이디어 2 — 신호 사후 검증 자동화
- **배경**: 매수 신호 발령 후 실제 수익률 추적이 수동임
- **방향 A**: 별도 텔레그램 채널 파서 — 발령된 신호를 파싱해 3일 후 수익률 자동 계산 + 발송
- **방향 B**: 같은 채널에서 `/verify` 슬래시 명령으로 최근 N일 신호 성과 조회
  - `/verify 7` → 최근 7일 발령 신호의 3일 수익률 요약
  - backtest_labels 테이블을 live 신호에도 적용 가능
- **구현 포인트**: `signal_history` × `ohlcv_daily` JOIN → 3일 후 max_high 계산
  - signal_date + 3거래일 후 close가 entry_price × 1.05 이상이면 성공
- **텔레그램 Bot API**: `setWebhook` 또는 polling으로 명령 수신 가능

### [아이디어 3] 왜 XGBoost인가 (모델 선택 근거)
- **선택 이유**:
  - 테이블형 금융 데이터에서 딥러닝 대비 성능 우위 (Kaggle 검증)
  - 결측값(NULL) 자체 처리 → 피처 전처리 부담 낮음
  - feature importance 해석 가능 → 어떤 피처가 신호에 기여했는지 설명
  - 학습/추론 속도가 빠름 → VM e2-micro에서도 실시간 추론 가능
- **대안 검토**:
  - LightGBM: 유사 성능, XGBoost와 호환성 유사 → 향후 비교 가능
  - RandomForest: 과적합 강건하나 AUC 열세 경향
  - LSTM/Transformer: 시계열 패턴 학습 가능하나 데이터 부족(종목당 133건)으로 부적합
- **한계**: backfill 데이터로 학습 → live 분포와 차이 존재, AUC 0.62는 참고용 수준

### [아이디어 4] 전체 장세 레이어 쌓기
- **배경**: 현재 KOSPI/KOSDAQ 장세(bull/sideways/bear)는 단순 점수 기반
- **방향**: 여러 장세 지표를 레이어로 쌓아 신호 필터 강화
  - **레이어 1 (현재)**: KOSPI/KOSDAQ 이동평균 기반 bull/sideways/bear 판단
  - **레이어 2**: VIX 대용 — KOSPI200 옵션 IV 또는 VKOSPI 활용 (공포 지수)
  - **레이어 3**: 외국인/기관 수급 지수 — 최근 5일 net 합산으로 시장 전체 흐름
  - **레이어 4**: 업종 모멘텀 — sector 피처 적재 후 강한 섹터 종목 가중치 부여
  - **레이어 5 (ML)**: 장세 상태를 피처로 추가해 xgb_prob 보정
- **단기 실현 가능 항목**: 외국인/기관 5일 net을 지수 레벨에서도 계산해 market_ctx에 추가
- **선행 조건**: sector 데이터 적재 (ticker_master 업종 채우기)

### [중기] sector 데이터 적재
- `pykrx.stock.get_market_sector_classifications()` → ticker_master
- sector 피처 추가 후 feature_engineering 재실행 → 재학습

### [보류] market_cap NULL
- close×volume 대체 불가, KRX API 차단으로 보류

## Risks / Blockers
- 학습 데이터 전체가 backfill → live 분포와 차이 존재 (AUC 0.62 = 참고용)
- ticker_master 비어 있어 sector 피처/장세 레이어 불가
- market_cap NULL 미해결
- KIS 60분봉은 당일치만 수집 가능

## References
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **Feature matrix**: `data/feature_matrix.parquet` (48,240건 × 48컬럼)
- **모델**: `data/models/xgb_label_3d_5pct.json` (채택) / `xgb_label_*.json` (9개 전체)
- **모델 결과**: `data/xgb_results.json`
- **스코어링 설정**: `config/scoring/v1_baseline/`
- **ML 스크립트**: `scripts/feature_engineering.py`, `scripts/train_xgboost.py`
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 22:00 UTC(07:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목 (2026-05-09부터 적용)
- **피처 27개**: vol_score, trend_score, pattern_score, risk_reward, volume, amount, market_cap, per, pbr, div_yield, foreign_exh_rate, short_ratio, turnover_rate, foreign_net_5d, inst_net_5d, log_avg_volume_20d, hist_volatility_20d, avg_foreign_exh_rate_20d, grade_S/A/B, pattern_*(4개), sv_live_v1/v2

## Last Updated
- 2026-05-11 세션 28
