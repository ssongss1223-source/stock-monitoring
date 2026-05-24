# Checkpoint

## Current Goal
- feature_engineering 진행 전 데이터 수집 현황 + 피처 구성 검토 후 train_models 재학습

## Current Status
- **재라벨링 완료** (21:30 KST) — universe_daily 215,993행, 18 라벨 적용됨
- **파이프라인 대기 중** — feature_engineering 자동 진행 차단됨 (사용자 요청)
- **VM 정상** — 프로세스 없음, 다음 명령 대기

## Done
- **라벨 18개로 재설계** — clean 9 (`label_{d}d_{p}pct_clean`) + first-touch 9 (`label_first_{d}d_{p}pct`), `_DD_THRESH = {3:-0.015, 5:-0.025, 10:-0.04}`
- **청크 체크포인트** — build_historical_matrix 20,000행 단위 parquet 저장/복구
- **라벨별 체크포인트** — train_models 완료된 라벨 skip (OOF + summary 저장)
- **인프라 개선 5종** — PYTHONPATH stock .bashrc, deploy_and_run.sh, notify_pipeline.sh git관리, 로그/체크포인트 /tmp→logs/, sudoers 권한 규칙
- **재라벨링 완료** — 215,993건 UPDATE (945건 스킵), universe_daily 라벨 컬럼 정상 확인

## Remaining
- **[검토 필요]** 데이터 수집 현황 점검 + feature_engineering.py 피처 구성 검토
- **[실행 필요]** feature_engineering.py → train_models.py (18 라벨 × ~21시간)
- docs/system.md 업데이트

## Risks / Blockers
- universe_daily 일부 컬럼 수집 안 됨: `market_cap`, `turnover_rate` = NULL
- `pred_*` 컬럼 (기존 모델 예측값) = NaN — 재학습 전까지 NULL 유지
- `grade_live`, `vol_score_live` = NULL (실시간 미수집)

## Next Actions
1. feature_engineering.py 피처 목록 검토 (데이터 수집 현황과 대조)
2. 이상 없으면 `bash /opt/stock-monitor/scripts/deploy_and_run.sh --skip-relabel` 실행
3. train_models 완료 후 AUC 결과 확인

## References
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **라벨 코드**: `backtest/labeler.py` (`_DD_THRESH = {3:-0.015, 5:-0.025, 10:-0.04}`)
- **파이프라인 실행**: `bash /opt/stock-monitor/scripts/deploy_and_run.sh`
- **로그**: `/opt/stock-monitor/logs/pipeline.log`
- **체크포인트**: `/opt/stock-monitor/logs/labels_checkpoint.parquet`
- **모델 저장**: `/opt/stock-monitor/data/models/`
- **라벨 달성률 (18라벨 기준)**: 미확인 (재학습 후 확인 예정)

## Last Updated
- 2026-05-24 21:50
