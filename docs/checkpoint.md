# Checkpoint

## Current Goal
- **P5 완료 + 전체 파이프라인 검증** — P5 평가 스크립트 구현, 버그 2건 수정, 전체 테스트 통과
- 다음: feature 트랙 (AUC 0.57 천장 돌파) + 향후 자동화 논의

## Current Status
- **코드** `3c9bb41` — 로컬/VM 동일 (서비스 재시작됨)
- **서비스** active (2026-05-31 13:38 KST 재시작)
- **DB**
  - `universe_predictions` 최신: 2026-05-31 (6,318행/일 = 351종목×18라벨)
  - `universe_outcomes` 최신: 2026-05-14 (1,053행 추가됨). 2026-05-15~ 라벨은 6/2 배치 후 자동 채움
  - `signal_xgb_probs`: 2026-05-15~ 존재 (라벨 미매칭 → 6/2부터 P5 평가 실행 가능)

## P5 구현 결과
- `scripts/evaluate_predictions.py` 추가 — signal_xgb_probs × universe_daily 라벨 조인
- 지표: Brier score / Prec@10 / Prec@20 / Lift@20 per label
- **데이터 제약**: universe_daily 라벨은 10 거래일 경과 후 채워짐. 2026-05-15 예측 라벨은 6/2 배치 후 확인 가능
- 실행: `sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py [--save]`

## 버그 수정 (이번 세션)
- `c41d580` `_auto_label_universe_unlabeled` UPDATE alias 버그 → DuckDB 미지원 alias 제거
- `3c9bb41` 동 함수 WHERE 절 `label_3d_3pct` 미존재 컬럼 → `label_3d_3pct_clean`으로 교정
- 두 버그로 인해 2026-05-15~ universe_daily 라벨이 전혀 채워지지 않고 있었음

## 전체 파이프라인 테스트 결과 (2026-05-31 수동 실행)
- 장세: KOSPI sideways / KOSDAQ bear
- 매수 신호: 77종목 (규칙) + ML-only 4종목 (threshold=0.60, RR≥2.0)
- ML 추론 (score_all_labels): 77종목 × 18라벨 — XGB+LGBM+ET soft voting ✅
- universe ML 추론 (score_universe_all): 351종목 2026-05-29 ✅
- universe_predictions INSERT: 6,318행 ✅
- universe_outcomes INSERT: 1,053행 (예측일 2026-05-14) ✅
- 텔레그램: 메시지 3,036자 전송 성공 ✅

## P4 진단 결과 (이전 세션)
- 운영 추론: **xgb+lgbm+et 단순평균** (ET 복귀 완료)
- ET 복귀 효과: 일별 Prec@20 24.0%→24.8% (+0.8%p)
- LR base 운영 추가 안 함 (AUC 낮음 + first_touch 다양성 없음)

## Done
- `3c9bb41` _auto_label_universe_unlabeled 쿼리 컬럼명 오류 수정
- `c41d580` _auto_label_universe_unlabeled UPDATE alias 버그 수정 + scripts/evaluate_predictions.py 추가
- `c89fbf9` ml_scorer ET 운영 복귀 — XGB+LGBM+ET soft voting
- `5fb2e6a` train_models 체크포인트 개선
- `602dd54` feature_matrix entry_price KeyError 수정
- universe_outcomes backfill 완료 (575일)
- P3: universe_predictions + universe_outcomes 신설
- P2: verify_data_quality + feature_catalog + model_registry + evaluation_history

## Remaining
- **6/2 배치 검증**: 정규 배치 정상 작동 확인 (universe_predictions 신규일자 / 텔레그램 수신)
- **6/2 이후 P5 실행**: `scripts/evaluate_predictions.py` — 2026-05-15 예측 라벨 채워지면 바로 실행
- **feature 트랙**: ml_additional_features.md 기반 피처 추가 (AUC 0.57 천장 돌파)
- **향후 자동화 논의**: 재훈련 주기 알람, feature 파이프라인 자동화, P5 평가 스케줄링
- 재훈련 주기: model_registry train_date 90일 기준 알람 미구현

## Risks / Blockers
- P5 평가 데이터: signal_xgb_probs(5/15~) × universe_daily 라벨 매칭 가능 일자는 6/2 이후
- ET 추론 중복 I/O (배치당 2회 × ~57초) — 향후 전역 캐싱 최적화 여지
- 로컬 `mcp__stock-db` DB 락 충돌 — VM `sudo -u stock .venv/bin/python3` 사용
- **8/4** GCP Free Trial 크레딧(₩418,177) 만료 → 이후 월 ~₩33,000 실제 과금 시작

## Next Actions
1. **6/2 배치 후** — universe_predictions 신규일자 확인 + `evaluate_predictions.py` 실행
2. **feature 트랙 착수** — ml_additional_features.md 우선순위 피처 선정 후 구현
3. **자동화 논의** — 재훈련 주기 알람, P5 자동 스케줄링, feature 파이프라인 자동화

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST)
- **핵심 파일**:
  - `agents/ml_scorer.py` (`_MODEL_TYPES`=xgb/lgbm/et, soft-voting)
  - `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`, `_auto_label_universe_unlabeled`)
  - `scripts/train_models.py` (xgb/lgbm/et/lr-stacker)
  - `scripts/evaluate_predictions.py` (P5 — Brier/Prec@K/Lift)
- **모델 성적 (OOF)**: 베이스 AUC 0.57 / 일별 Prec@20 ≈ 24.8%

## Last Updated
- 2026-05-31 22:00 KST
