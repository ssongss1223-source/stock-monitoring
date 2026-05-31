# Checkpoint

## Current Goal
- **P4 = 운영 라인업 정상화** 진행 중 — ET 복귀 완료(배포됨), 다음은 LR 베이스 추가 검토

## Current Status
- **코드** `c89fbf9` — 로컬/VM 동일 (ET 복귀 배포 완료, 서비스 재시작됨)
- **서비스** active
- **DB**
  - `backtest_labels` 30컬럼 / `universe_daily` 81컬럼
  - `universe_outcomes` backfill 완료 — 575 distinct date (2024-01-02~2026-05-14, 652,191행). 미반영 10일은 최근(05-15~)이라 future price 미확정 = 정상, run_daily가 자동 충원
- **메타 테이블** feature_catalog 48 / model_registry 54 / evaluation_history 54

## P4 진단 결과 (데이터 기반)
- 운영 추론은 그동안 **xgb+lgbm 단순평균만** 사용 (ET는 옛 RAM 제약으로 제외돼 있었음)
- xgb↔lgbm OOF 상관 **0.82** = 사실상 중복 → CatBoost(또 다른 GBDT) 추가는 효과 낮음 → **CatBoost 폐기**
- **ET 복귀가 핵심 레버**: 일별 top-20 Prec@20 24.0%→24.8% (+0.8%p, 18라벨 대부분 개선)
- ET 메모리 실측: peak RSS 1.1GB (가용 3.1GB) / 추론 57초 — 배치라 무방
- rank 앙상블은 0.60 threshold·확률 저장과 충돌 + OOF 동급 → **단순평균 유지**(확률 스케일 보존)

## Done
- `c89fbf9` ml_scorer ET 운영 복귀 — XGB+LGBM+ET soft voting (단순평균, 확률 보존)
- universe_outcomes backfill 완료 확인 (575일)
- `062fa1c` P3: universe_predictions(long) + universe_outcomes(raw 7컬럼) 신설
- `9b815c2` P2-4: verify_data_quality 모델 품질 점검 + report.py AUC DB 연동
- `0a8cf83` P2-1/2/3: feature_catalog + model_registry + evaluation_history

## Remaining
- **6/1 05:00 KST 배치 검증**: ET 추론 정상 작동 (universe ML 추론 로그 / universe_predictions 신규 INSERT / 메모리)
- **요청3 LR 베이스 추가**: LR을 베이스 모델로 학습 → OOF로 다양성·효과 측정 (현재 LR은 stacker 전용이라 OOF 없음)
- **feature 트랙** (나중): ml_additional_features.md 기반 — AUC 0.57 천장 돌파. 알고리즘과 독립이라 병렬 가능
- **[P5]** TopK·Brier·Lift 평가 구현 — universe_predictions JOIN universe_outcomes
- 재훈련 주기: model_registry train_date 90일 기준 알람 미구현

## Risks / Blockers
- **8/4** Cloud Billing 유료 업그레이드 필수 (무료 체험 만료 → VM 자동 stop, **7월 말까지** 클릭)
- ET 추론이 배치당 2회(score_all_labels + score_universe_all) 각각 18×~400MB 디스크 로드 = ~114초 중복 I/O. 향후 모델 전역 캐싱으로 최적화 여지
- 로컬 `mcp__stock-db` DB 락 충돌 — VM `sudo -u stock .venv/bin/python3` 사용

## Next Actions
1. 6/1 05:00 KST 배치 후 ET 추론 검증 (`journalctl` + universe_predictions 신규 날짜)
2. 요청3: LR 베이스 추가 학습 + OOF 효과 측정
3. feature 트랙 아이디에이션 (병렬 시작 가능)

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST)
- **핵심 파일**:
  - `agents/ml_scorer.py` (`_MODEL_TYPES`=xgb/lgbm/et, `score_universe_all`, `score_all_labels` — 단순평균)
  - `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`, `_pipeline` line 271·298 ML 추론 호출)
  - `scripts/train_models.py` (멀티모델 학습 — xgb/lgbm/et/lr-stacker, OOF→model_results.json)
  - `data/oof_predictions.parquet` (OOF 예측값 — 앙상블 전략 백테스트용)
- **모델 성적 (OOF)**: 베이스 AUC 0.57 수준 / 일별 Prec@20 xgb+lgbm+et ≈ 24.8%

## Last Updated
- 2026-05-31 10:35 KST
