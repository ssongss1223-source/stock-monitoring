# Checkpoint

## Current Goal
- train_models.py 완료 후 AUC 결과 확인 + pred_* 컬럼 업데이트 (17 → 18 라벨)

## Current Status
- **학습 진행 중** — PID 198109, 10/18 라벨 완료 (체크포인트 기준), 약 10시간 후 완료 예상
- **feature_engineering 완료** — universe_features_daily 230,531행, feature_matrix.parquet (46컬럼, signal_date)
- **notify_pipeline.sh 동작 중** — PID 240371, 시간당 진행 알림 + TRAIN_DONE 감지
- **per-label 알림 비작동** — 이번 run train_models.py가 구버전으로 시작됨 (다음 재학습부터 정상)

## Done
- **파이프라인 버그 6종 수정** — DuckDB nested window (atr, up_days), init_db() 누락, signal_date 키에러, 로그 chmod 666, notify 중복 알림
- **feature_engineering BUILD 완료** — universe_features_daily 230,531행 삽입, parquet 저장
- **notify_pipeline.sh 재작성** — 재시작 안전, 라벨별 알림 로직, grep-c→wc-l 수정 (49af2cb)
- **train_models.py 체크포인트 출력 추가** — 라벨 완료 시 `체크포인트: {label_key}` 출력 (82c7dba)
- **VM 배포** — git pull + notify 재시작 완료

## Remaining
- **[대기]** TRAIN_DONE 알림 수신 후 AUC 결과 검토 (`data/models/summary_ckpt_*.json`)
- **[실행 필요]** pred_* 컬럼 업데이트: `db.py` DDL 17→18 라벨, `orchestrator.py` `_update_universe_preds()` 수정
- **[실행 필요]** 서비스 재시작 (새 모델 로드)
- docs/system.md 업데이트

## Risks / Blockers
- 학습 완료 후 orchestrator.py가 DB에 pred_* 컬럼 없는 새 라벨(first_touch 9)을 INSERT 시도 → 컬럼 추가 전까지 오류
- Return@20이 전부 +nan% — pred_* 컬럼이 NULL이라 역수익률 계산 불가 (재학습 후 해소)
- train_models.py PID 198109: 로그 stdout 버퍼링으로 log 줄 수 멈춰있음 (정상, 체크포인트 파일로 확인)

## Next Actions
1. TRAIN_DONE Telegram 알림 수신 (약 10시간 후)
2. `data/models/summary_ckpt_*.json` 열어 18개 라벨 AUC 확인
3. `db.py` + `orchestrator.py` pred_* 컬럼 18개로 업데이트 → 서비스 재시작

## References
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **파이프라인 로그**: `/opt/stock-monitor/logs/pipeline.log`
- **체크포인트 파일**: `/opt/stock-monitor/data/models/oof_ckpt_*.parquet`, `summary_ckpt_*.json`
- **모델 저장**: `/opt/stock-monitor/data/models/`
- **라벨 코드**: `backtest/labeler.py` (`_DD_THRESH = {3:-0.015, 5:-0.025, 10:-0.04}`)
- **배포 스크립트**: `scripts/deploy_and_run.sh`

## Last Updated
- 2026-05-25 08:30
