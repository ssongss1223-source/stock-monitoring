# Checkpoint

## Current Goal
- run_daily 정상 동작 확인 (BinderException 수정 후 첫 자동 배치)

## Current Status
- **코드** — 로컬·VM 모두 `adffee1` (CLAUDE.md Option B) 기준. 서비스 active
- **BinderException 수정 완료** — `b228779`: backtest_labels 9컬럼 추가 + INSERT 명시적 컬럼리스트 + orchestrator try/except 격리
- **로컬 DB 동기화 완료** — universe_daily 101cols/221,150rows + universe_features_daily 46cols/231,233rows (max 2026-05-27)
- **배치 추론** — XGB + LGBM soft voting 18라벨 (ET 제외 유지)
- **텔레그램 정렬** — AUC × prob 가중 (`_LABEL_AUC` 하드코딩)

## Done
- **BinderException 수정** (b228779) — `backtest_labels` 신컬럼 9개 추가, `save_labels()` 명시적 컬럼리스트, orchestrator `_auto_label_*` try/except 격리 → 파이프라인이 라벨링 실패해도 텔레그램 전송
- **CLAUDE.md Option B** (adffee1) — 로컬/VM 역할분리 테이블, MCP SSH 제약, 수정 후 검증 절차, 데이터 무결성 원칙
- **로컬 DB 동기화** — gcloud scp로 VM parquet export → 로컬 import. 로컬에서 feature_engineering/train_models 로직 수정 가능
- **report.py 신라벨 18 + AUC 가중 정렬** (eba638c) — `_LABEL_AUC` 18개, `_parse_label()` first_/clean 접사 처리
- **ml_scorer.py ET 제외** (dfce4c6) — XGB+LGBM soft voting (ET: VM RAM 969MB 한계)

## Remaining
- **[대기]** 2026-05-28 화 05:00 KST `run_daily` 자동 배치 — 텔레그램 수신 확인 (BinderException 수정 검증)
- **[조건부]** 텔레그램 정상 수신 후 docs/system.md 갱신 (라벨 구조 / 정렬 방식)
- **[조건부]** 이상 시 옵션 C (고AUC 라벨만) 또는 ET subprocess 격리 재검토

## Risks / Blockers
- VM RAM 969MB — 추론 시 일시적 swap 가능
- `_LABEL_AUC` 하드코딩 → 재학습 시 수동 갱신 필요
- 로컬 `ohlcv_daily`·`signal_history`는 2026-05-15 기준 (12일 구버전). 운영 영향 없음, 로컬 테스트 시 참고

## Next Actions
1. 2026-05-28 화 05:00 KST `run_daily` 텔레그램 메시지 확인 — 신라벨 표시·AUC 정렬 정상 여부
2. 정상 확인 시 docs/system.md 갱신 후 commit
3. 이상 시 VM 로그 확인 (`journalctl -u stock-monitor --since "today"`)

## References
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **서비스**: `stock-monitor.service` (stock user, APScheduler)
- **스케줄**: `run_collect` 07:00 UTC (16:00 KST) / `run_daily` 20:00 UTC (05:00 KST 다음날)
- **모델**: `/opt/stock-monitor/data/models/` — XGB(.json) / LGBM(.txt)
- **핵심 파일**: `agents/ml_scorer.py`, `agents/report.py` (`_LABEL_AUC`), `agents/orchestrator.py`, `data/db.py`
- **로그**: `journalctl -u stock-monitor --since "today" --no-pager | tail -30`

## Last Updated
- 2026-05-27 21:28 KST
