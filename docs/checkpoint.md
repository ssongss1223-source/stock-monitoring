# Checkpoint

## Current Goal
- **P3 완료** → backfill 완료 후 P4(CatBoost) 또는 P5(TopK 평가) 진입

## Current Status
- **코드** `062fa1c` — 로컬/VM 동일
- **서비스** active (e2-medium, PID 411)
- **DB**
  - `backtest_labels` 30컬럼 / `universe_daily` 81컬럼
  - `universe_predictions` 12,636행 (2일치, 351종목 × 18라벨)
  - `universe_outcomes` 백그라운드 backfill 진행 중 (581일 중 ~20+ 완료, 예상 ~75분)
- **메타 테이블** feature_catalog 48행 / model_registry 54행 / evaluation_history 54행

## Done
- `062fa1c` P3: universe_predictions(long) + universe_outcomes(raw 7컬럼) 신설 + backfill
- `9b815c2` P2-4: verify_data_quality 모델 품질 점검 + report.py _LABEL_AUC DB 연동
- `0a8cf83` P2-1/2/3: feature_catalog(48피처) + model_registry(54모델) + evaluation_history
- `dd4c47c` label_first_up_* 3개 컬럼 DROP (구버전 설계 잔재 정리)
- VM 증설 e2-medium 4GB / 50GB (2026-05-30)

## Remaining
- **backfill 완료 확인**: universe_outcomes 581일 소급 완료 여부 (screen 백그라운드 실행 중)
- **[P4]** CatBoost 추가 — universe_outcomes + universe_predictions 기반 첫 학습·평가
- **[P5]** TopK·Brier·Lift 평가 구현 — universe_predictions JOIN universe_outcomes (+ ohlcv_daily for label_first)
- **재훈련 주기**: model_registry train_date 90일 기준 알람 미구현

## Risks / Blockers
- **8/4** Cloud Billing 유료 업그레이드 필수 (무료 체험 만료 → VM 자동 stop, **7월 말까지** 클릭)
- 로컬 `mcp__stock-db` DB 락 충돌 — VM `sudo -u stock ./.venv/bin/python` 사용
- universe_predictions: 현재 2일치(pred_* 컬럼 있던 날만) — 시간 지나면 자연 증가
- label_first 최적화는 학습 시 ohlcv_daily JOIN으로 처리 (Hybrid, 저장 안 함)

## Next Actions
1. backfill 완료 확인 (`SELECT COUNT(DISTINCT date) FROM universe_outcomes` → 581 기대)
2. run_daily 다음 실행(05:00 KST) 후 universe_predictions/outcomes 신규 날짜 INSERT 확인
3. P4 시작: CatBoost 추가 + universe_outcomes 기반 train_models.py 수정
4. P5: TopK precision/return 평가 함수 구현

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **서비스**: `stock-monitor.service` (stock user, APScheduler)
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST)
- **로드맵**: `docs/architecture-roadmap.md` — D1-D8 + Phase 1-7
- **핵심 파일**:
  - `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`, `_update_universe_preds`, `_pipeline`)
  - `backtest/labeler.py` (`compute_outcomes_universe()` — 전체 유니버스 outcomes)
  - `scripts/backfill_p3.py` (소급 반영, 1회용)
  - `data/db.py` (universe_predictions + universe_outcomes DDL)
- **P3 테이블 구조**:
  - `universe_predictions`: (date, ticker, model_type, label, prob) — model_type='ensemble' (P4에서 xgb/lgbm/catboost 추가)
  - `universe_outcomes`: (date, ticker, hold_days, entry_price, max_close, max_drawdown, return_close) — binary 라벨 없음, 학습 시 동적 계산

## Last Updated
- 2026-05-30 22:30 KST
