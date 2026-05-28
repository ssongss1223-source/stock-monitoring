# Checkpoint

## Current Goal
- 내일(05-29) 05:00 KST run_daily Option B 첫 정상 실행 + 텔레그램 수신 확인

## Current Status
- **로컬/VM 코드** — `ce4c41c` (Option B 배포 완료)
- **서비스** — active (22:13 KST 재시작)
- **DB** — 05-27까지만 수집. 05-28 run_collect 미실행(서비스 중단 기간) → 22:14 KST 수동 실행 중
- **05-28 수동 run_daily** — 06:16~07:30 KST screen 세션으로 실행됨. 로그 미기록(stdout만 출력). 텔레그램 발송 여부 미확인

## Done
- **Option B 배포** (ce4c41c) — VM 배포·서비스 재시작 완료 (2026-05-28)
- **Option B 구현** (f14dc57) — 규칙+ML 병렬 게이트, `[ML]` grade, DuckDB 에러 2곳 수정
- **Section C 피처 추가** (1cbfd82) — `bb_width_pct_252`, `turnover_rank_pct`, `amount_rank_pct`, `volatility_rank_pct`
- **amount/turnover NULL 버그 수정** (2c16e33) — `close*volume` / `volume/avg_vol_20d` 프록시
- **BinderException + 신라벨 18 + AUC 정렬** (b228779, eba638c)

## Remaining
- **[확인]** 05-28 수동 run_daily 텔레그램 수신 여부 확인
- **[확인]** 05-28 run_collect 수동 실행 완료 후 DB 데이터 확인
- **[필수]** 05-29 05:00 KST run_daily 텔레그램 확인 (서비스 자동 실행)
  - 로그: `journalctl -u stock-monitor --since "today" --no-pager | grep -E "ML-only|텔레그램|ERROR"`
- **[검토]** Section C 피처를 `_FEAT_TRAIN_COLS`에 포함해 재학습할지 결정
- **[조건부]** 텔레그램 정상 수신 후 docs/system.md 갱신

## Risks / Blockers
- 05-28 run_collect 수동 실행 완료 여부 미확인 — 완료 후 DB 날짜 체크 필요
- 수동 run_daily 로그 미기록 (screen stdout) — 성공/실패 판단 불가. 텔레그램으로만 확인
- `_LABEL_AUC` 하드코딩 → 재학습 시 수동 갱신 필요
- ML-only 임계값 0.60 — 첫 배포 후 실제 종목 수 모니터링 필요

## Next Actions
1. run_collect 완료 확인: `sudo -u stock python3 -c "from data.db import get_conn; c=get_conn(read_only=True); print(c.execute('SELECT MAX(date), COUNT(*) FROM universe_daily WHERE date=(SELECT MAX(date) FROM universe_daily)').fetchone())"`
2. 05-29 05:00 KST 이후 텔레그램 확인 + 로그 확인
3. ML-only 신호 종목 수 확인 → 임계값 조정 여부 결정 (0.60 → 0.65 or 0.55)

## References
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **서비스**: `stock-monitor.service` (stock user, APScheduler)
- **스케줄**: `run_collect` 07:00 UTC (16:00 KST) / `run_daily` 20:00 UTC (05:00 KST 다음날)
- **모델**: `/opt/stock-monitor/data/models/` — XGB(.json) / LGBM(.txt)
- **핵심 파일**: `agents/orchestrator.py` (`_ML_PROB_THRESHOLD=0.60`), `agents/report.py` (`_LABEL_AUC`), `agents/ml_scorer.py`
- **로그**: `journalctl -u stock-monitor --since "today" --no-pager | tail -30`

## Last Updated
- 2026-05-28 22:15 KST
