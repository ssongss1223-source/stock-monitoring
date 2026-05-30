# Checkpoint

## Current Goal
- **Phase 2 진입**: `feature_catalog` + `model_registry` + `evaluation_history` 메타 테이블 신설

## Current Status
- **코드** `3f5f66e` — 로컬/VM 동일
- **서비스** active (e2-medium, PID 411)
- **DB** `backtest_labels` 33컬럼 / `universe_daily` 84컬럼 (deprecated 라벨 제거 완료)
- **검증** verify_data_quality.py: OK 21 / WARN 0 / FAIL 0

## Done
- `3f5f66e` label_Xd_Ypct + c2 계열 25컬럼 폐기 (clean/first 18개로 통합)
- `bea26c8`/`d7db436` _LABEL_COLS _clean/_first 보강 + backfill + 임계치 90%
- `df7f8b8` P1: verify_data_quality.py + /verify-data skill + architecture-roadmap.md
- `e6bfb72` 텔레그램 정렬 변경 (거래량→ML확률→AUC→추세)
- VM 증설 e2-medium 4GB / 50GB (2026-05-30)

## Remaining
- **[P2-1]** `feature_catalog` 테이블 + 기존 47피처 등록
- **[P2-2]** `model_registry` 테이블 + 현재 XGB/LGBM 모델 등록
- **[P2-3]** `evaluation_history` 테이블 + TopK/Brier/Lift 평가 함수
- **[P2-4]** `/verify-data`에서 OOS AUC/TopK 점검 항목 추가
- **[P3+]** `universe_predictions` + `universe_labels` long-format 신설
- **[체크]** `label_first_up_*` 3개 — DB에만 있고 코드 미사용. 정리 검토

## Risks / Blockers
- **7월 말** Cloud Billing 유료 업그레이드 필수 (8/4 무료 체험 만료 → VM 자동 stop)
- `_LABEL_AUC` 하드코딩 → P2 model_registry 도입 시 자동화 예정
- 로컬 `mcp__stock-db` DB 락 충돌 — VM `sudo -u stock ./.venv/bin/python` 사용
- 작업 모델: **Sonnet 4.6 기본**, 설계 결정 시만 Opus

## Next Actions
1. P2 시작: `data/db.py`에 3개 메타 테이블 추가 + VM init_db()
2. 기존 31개 학습 피처를 `feature_catalog`에 bulk INSERT
3. 현재 XGB/LGBM 모델 정보를 `model_registry`에 등록 (status='production')

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **서비스**: `stock-monitor.service` (stock user, APScheduler)
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST)
- **로드맵**: `docs/architecture-roadmap.md` — D1-D8 + Phase 1-7
- **핵심 파일**:
  - `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`)
  - `agents/ml_scorer.py` (`_FEAT_COLS` 31개)
  - `scripts/feature_engineering.py` (`_FEAT_TRAIN_COLS` 31 / `_FEAT_LAYER2_COLS` 16)
  - `backtest/labeler.py` (`_LABEL_COLS` 24개 — clean 9 + first 9 + 원시 측정값 6)
  - `scripts/verify_data_quality.py`
- **라벨 구조** (clean/first 18개 통일):
  - `label_Xd_Ypct_clean` × 9 (목표 달성 + 낙폭 임계치 이내)
  - `label_first_Xd_Ypct` × 9 (낙폭 도달 전 목표 먼저 달성)

## Last Updated
- 2026-05-30 19:15 KST
