# Checkpoint

## Current Goal
- **P5.5 배포 완료** — 모델 버전관리 + 예측 추적 마이그레이션
- 다음: 6/2 배치 검증 + P5 평가 실행 → feature 트랙 착수

## Current Status
- **코드** `1819fda` — 로컬/VM 동일 (서비스 재시작됨)
- **서비스** active (2026-05-31 재시작)
- **DB 스키마** (P5.5 마이그레이션 적용 완료)
  - `model_registry`: `version`·`feature_set_hash`·`n_features` 컬럼 추가, 기존 54개 모델 행 `@2026-05-30` 버전 스탬프 완료
  - `universe_predictions`: `model_ver` 컬럼 추가 (다음 배치부터 스탬프 시작)
- **DB 데이터**
  - `universe_predictions` 최신: 2026-05-31 (6,318행/일)
  - `universe_outcomes` 최신: 2026-05-14. 5/15~ 라벨은 6/2 배치 후 자동 채움
  - `signal_xgb_probs`: 5/15~ 존재 → 6/2 이후 P5 평가 가능

## Done
- `1819fda` P5.5 모델 버전관리 마이그레이션 배포 (model_registry 버전화 + universe_predictions.model_ver)
- `3c9bb41` `_auto_label_universe_unlabeled` 컬럼명 오류 수정
- `c41d580` `_auto_label_universe_unlabeled` alias 버그 수정 + `scripts/evaluate_predictions.py` (P5) 추가
- `c89fbf9` ml_scorer ET 운영 복귀 — XGB+LGBM+ET soft voting (Prec@20 24.8%)
- P3: `universe_predictions`·`universe_outcomes` long 마트 신설

## Remaining
- **6/2 배치 검증**: universe_predictions 신규일자 확인 + 텔레그램 수신
- **6/2 이후 P5 실행**: `sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py [--save]`
- **feature 트랙**: `docs/ml_additional_features.md` 기반 피처 추가 (AUC 0.57 천장 돌파)
- **자동화**: 재훈련 주기 알람 (model_registry train_date 90일 기준), P5 평가 스케줄링

## Risks / Blockers
- P5 평가: signal_xgb_probs × universe_daily 라벨 매칭 가능 시점 6/2 이후
- ET 추론 중복 I/O (배치당 score_all_labels + score_universe_all 2회) — 향후 최적화 여지
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)

## Next Actions
1. **6/2 배치 후** — universe_predictions 신규일자 + model_ver 스탬프 확인, P5 평가 실행
2. **feature 트랙 착수** — ml_additional_features.md 우선순위 피처 선정
3. **자동화 설계** — 재훈련 주기 알람, P5 자동 스케줄링

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST)
- **핵심 파일**:
  - `agents/ml_scorer.py` (`_MODEL_TYPES`=xgb/lgbm/et, soft-voting)
  - `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`, `_auto_label_universe_unlabeled`)
  - `scripts/train_models.py` (xgb/lgbm/et/lr-stacker)
  - `scripts/evaluate_predictions.py` (P5 — Brier/Prec@K/Lift)
  - `data/db.py` (`_upsert_model_registry`, `register_models_from_json`)
  - `docs/architecture-roadmap.md` (P1–P5 진행현황 + P6/P7 설계)
  - `.claude/plans/p5_5_model_versioning_migration.md` (P5.5 설계 문서)
- **모델 성적 (OOF)**: 베이스 AUC 0.57 / 일별 Prec@20 ≈ 24.8%
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)

## Last Updated
- 2026-05-31 KST
