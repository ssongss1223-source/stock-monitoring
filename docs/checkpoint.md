# Checkpoint

## Current Goal
- **SMA 백테스팅 플랫폼 MVP 구현** — 설계+계획 완료, subagent 실행 대기

## Current Status
- **ML 파이프라인**: 코드 `84ec20c` 배포 완료, 서비스 active
- **ohlcv_daily**: 20년치 백필 완료 (2005-01-03 ~ 2026-06-02, 1,540,635행, 383종목)
- **SMA 플랫폼 설계+계획 완료**:
  - 전략: SMA 최초 진입(3회 분할) + 눌림목 진입(1회 + -8% 손절) + 아기티큐 익절
  - 파라미터: SMA(50~300) × confirm(1,3) × lookback(10,20,40) × drawdown(3,5,10,15%) = 624조합
  - 대상: 23종목 (개인 13 + 시총 추가 10)
  - 최적화: Walk-forward + Calmar Ratio 1순위
  - 출력: 텔레그램 새 채널 (TELEGRAM_BACKTEST_CHAT_ID)
- **P5 평가 대기 중**: 6/6 이후 실행

## Done
- `84ec20c` SMA 구현 계획 (`docs/superpowers/plans/2026-06-03-sma-backtest-core.md`)
- `e9f9e3f` SMA 설계 문서 (`docs/superpowers/specs/2026-06-03-sma-backtest-design.md`)
- `73d32cb` 20년치 OHLCV 백필 완료 (1,540,635행)
- `4d9e6ee` 텔레그램 데이터 품질 검증 메시지 구현
- `a4aed10` universe_daily 라벨 cutoff 달력일→거래일 기준 수정

## Remaining
- **[SMA MVP - 다음 세션]** `superpowers:subagent-driven-development`로 태스크 순서대로 실행
  - Task 1~7: config → signal → backtester → optimizer → DB → reporter → CLI
  - 실행 시간: 16:00~18:30 KST 제외 (DuckDB 락 충돌 방지)
  - 환경변수 필요: `TELEGRAM_BACKTEST_CHAT_ID` (새 채널 ID)
- **[6/6 이후]** P5 평가: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- **[6/8 전후]** 라이브 Prec@K 확인 → 재훈련 여부 판단
- **[P6+]** 피처 변경(31→36개) + ET 파라미터 + 훈련 격리 묶음

## Risks / Blockers
- **DuckDB 락**: run_sma_backtest.py 실행 시 run_daily/collect와 시간대 겹치면 충돌 → 18:30 KST 이후 실행
- SMA 플랫폼과 ML 파이프라인은 **독립 운영** — 통합 전략은 미결정
- `open=0.0` 품질 문제: 검증 WARN 77.1%, P6+ 때 처리
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)

## Next Actions
1. **[다음 세션]** `/load-context` → `subagent-driven-development`로 SMA Task 1부터 실행
2. **[6/6 이후]** P5 평가 실행
3. **[텔레그램]** 새 채널 생성 → `TELEGRAM_BACKTEST_CHAT_ID` 환경변수 VM에 설정

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **SMA 실행 가능 시간**: 18:30 KST 이후 (DuckDB 락 없음)
- **핵심 파일 (SMA)**:
  - `docs/superpowers/plans/2026-06-03-sma-backtest-core.md` (구현 계획)
  - `docs/superpowers/specs/2026-06-03-sma-backtest-design.md` (설계 문서)
  - `scripts/backfill_historical_ohlcv.py` (20년치 백필)
- **핵심 파일 (ML)**:
  - `agents/orchestrator.py`, `scripts/evaluate_predictions.py`
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)
- **ohlcv 범위**: 2005-01-03 ~ 현재 (1,540,635행, 20년치, 383종목)

## Last Updated
- 2026-06-03 15:30 KST
