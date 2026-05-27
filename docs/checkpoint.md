# Checkpoint

## Current Goal
- Option B (규칙+ML 병렬 게이트) 배포 → 다음 `run_daily` 텔레그램 수신 확인

## Current Status
- **로컬 코드** — `f14dc57` (Option B 구현 완료, VM 미배포)
- **VM 코드** — `2c16e33` (배포 필요)
- **배치 추론** — XGB + LGBM soft voting 18라벨 (ET 제외 유지)
- **텔레그램 정렬** — AUC × prob 가중 (`_LABEL_AUC` 하드코딩)

## Done
- **Option B 구현** (f14dc57) — 규칙+ML 병렬 게이트:
  - 전종목(351) ML 추론을 텔레그램 전으로 이동, `score_universe_all` 재사용
  - 규칙 미통과 종목 중 `best_prob≥0.60 & RR≥2.0` → `grade="ML"` BuySignal 생성
  - 텔레그램 발송: 규칙(RR≥2.0) + ML-only 합산 (기존 S등급 한정 제거)
  - `_update_universe_preds` 이중 추론 방지 (probs_by_ticker 재사용)
  - `report.py` 헤더 변경 + `[ML]` grade 표시 처리
  - DuckDB qualified column 에러 수정 2곳
- **Section C 피처 추가** — `bb_width_pct_252`, `turnover_rank_pct`, `amount_rank_pct`, `volatility_rank_pct` DB 저장 (학습 미포함)
- **amount/turnover NULL 버그 수정** — `close*volume` / `volume/avg_vol_20d` 프록시로 교체 (2c16e33)
- **BinderException 수정** (b228779) — `backtest_labels` 신컬럼 9개 추가
- **report.py 신라벨 18 + AUC 가중 정렬** (eba638c)

## Remaining
- **[필수]** VM에 `git pull` + 서비스 재시작 후 다음 `run_daily` 텔레그램 확인
  - 로그에서 "ML-only 신호: N종목" 메시지 확인
  - 텔레그램 헤더 "규칙+ML 신호" + [ML] 표시 확인
- **[검토]** Section C 피처를 `_FEAT_TRAIN_COLS`에 포함해 재학습할지 결정
- **[조건부]** 텔레그램 정상 수신 후 docs/system.md 갱신

## Risks / Blockers
- VM RAM 969MB — `score_universe_all` (351종목×18모델) + 기존 추론 부담 증가
  - 기존에는 4c에서만 실행했으나 이제 4c에서만 1회 실행(재사용) → 동일
- `_LABEL_AUC` 하드코딩 → 재학습 시 수동 갱신 필요
- ML-only 임계값 0.60 — 첫 배포 후 실제 종목 수 모니터링 필요
- `tech_map`에 없는 종목(OHLCV 없음·타임아웃) → ML-only 후보에서 제외됨 (정상)

## Next Actions
1. VM `git pull` + 서비스 재시작
2. 다음 `run_daily` (05:00 KST) 텔레그램 확인
3. "ML-only 신호: N종목" 로그 + [ML] 표시 확인
4. 종목 수 너무 많으면 임계값 상향 (0.60 → 0.65), 없으면 하향 (0.55)

## References
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **서비스**: `stock-monitor.service` (stock user, APScheduler)
- **스케줄**: `run_collect` 07:00 UTC (16:00 KST) / `run_daily` 20:00 UTC (05:00 KST 다음날)
- **모델**: `/opt/stock-monitor/data/models/` — XGB(.json) / LGBM(.txt)
- **핵심 파일**: `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`), `agents/report.py` (`_LABEL_AUC`), `agents/ml_scorer.py`
- **로그**: `journalctl -u stock-monitor --since "today" --no-pager | tail -30`

## Last Updated
- 2026-05-27 23:30 KST
