# Checkpoint

## Current Goal
- **Phase 1**: `verify_data_quality.py` + `/verify-data` skill 작성 — 데이터 품질 자동 검증 토대

## Current Status
- **코드** `e6bfb72` — 로컬/VM 동일
- **서비스** active (PID 411, 17:36 KST 재시작, e2-medium 위)
- **스케줄러** APScheduler 잡 등록 정상
- **VM** `e2-medium` (4GB RAM / 50GB 디스크) — 증설 완료 (2026-05-30)
- **데이터** 05-29까지 모두 적재 완료 (이 주 거래일 완료)
- **다음 자동 배치** 월요일 05:00 KST run_daily

## Done
- VM 증설 e2-micro → e2-medium + 디스크 50GB (2026-05-30)
- 텔레그램 정렬 변경 (`e6bfb72`) — 거래량점수→ML확률→AUC→추세점수, 그룹·중복 정리
- 아키텍처 로드맵 합의 (`docs/architecture-roadmap.md` D1-D8)
- Option B 배포 + 18라벨 ML 추론 안정화

## Remaining (Phase 1 단위)
- **[P1-1]** `scripts/verify_data_quality.py` 작성 (6단계 검증)
- **[P1-2]** `/verify-data` skill 작성 (`.claude/commands/verify-data.md`)
- **[P1-3]** VM 첫 실행 → 임계치 튜닝
- **[P1-4]** (선택) cron 16:35 KST 등록 (run_collect 직후 자동 검증)
- **[검토]** `backtest_labels`에 `label_first_up_*` 3개 vs `_LABEL_COLS` 55개 — 코드는 명시 컬럼 INSERT이므로 안전, 정리 시 deprecated 처리 고려

## Risks / Blockers
- 7월 말 (8/4 만료 전) **Cloud Billing 유료 업그레이드** 필수 — 안 하면 VM 자동 stop
- `_LABEL_AUC` 하드코딩 → 재학습 시 수동 갱신 (P2에서 model_registry로 자동화)
- 로컬 `mcp__stock-db` 사용 금지 — 서비스 write 락 충돌. DB 조회는 VM `sudo -u stock ./.venv/bin/python`

## Next Actions
1. `scripts/verify_data_quality.py` 작성 — 적재 완전성·NULL·라벨 채움 3개 섹션부터
2. `.claude/commands/verify-data.md` skill 작성
3. VM 첫 실행: `cd /opt/stock-monitor && sudo -u stock ./.venv/bin/python scripts/verify_data_quality.py`
4. 결과 보고 임계치 조정 → P2(메타 테이블) 진입 판단

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **서비스**: `stock-monitor.service` (stock user, APScheduler)
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST 다음날)
- **로드맵**: `docs/architecture-roadmap.md` — Phase 1-7 + D1-D8 결정 근거
- **핵심 파일**:
  - `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`)
  - `agents/report.py` (`_LABEL_AUC`, 정렬 키)
  - `agents/ml_scorer.py` (`_FEAT_COLS` 31)
  - `scripts/feature_engineering.py` (`_FEAT_TRAIN_COLS` 31 / `_FEAT_LAYER2_COLS` 16)
  - `backtest/labeler.py` (`_LABEL_COLS` 55)
- **데이터 파이프라인** (유니버스 ~351종목):
  - 16:00 KST → ohlcv_daily → universe_daily(1차 피처) + universe_features_daily(47 파생 피처)
  - 05:00 KST → ML 추론 → universe_daily.pred_* / signal_history / signal_xgb_probs / 텔레그램
  - T+15 → 자동 라벨링 → universe_daily.label_* + backtest_labels
- **검증 2갈래**:
  - A. 유니버스 OOS: `universe_features_daily ⨝ universe_daily.label_*_clean`
  - B. 라이브 신호 정확도: `signal_history ⨝ backtest_labels`

## Last Updated
- 2026-05-30 18:00 KST
