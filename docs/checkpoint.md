# Checkpoint

## Current Goal
- **SMA 백테스팅 결과 확인** — MVP 구현 완료, VM에서 실행 중

## Current Status
- **SMA 백테스트**: VM screen `sma_backtest` 실행 중 (18:07 KST 시작, 005930부터 Walk-forward)
- **ML 파이프라인**: 서비스 active, 정상 운영 중
- **텔레그램**: "SMA 백테스트" 그룹 생성 완료, 결과 자동 전송 설정 완료

## Done
- `8e8151b` SMA MVP 전체 구현 (Task 1~7) — config/signal/backtester/optimizer/reporter/CLI
- `f873747` sma_backtest_results 테이블 VM DB 적용 완료 (17컬럼)
- `3f5f234` run_sma_backtest.py VM dry-run 확인 (624조합 × 23종목)
- 텔레그램 "SMA 백테스트" 그룹 + TELEGRAM_BACKTEST_CHAT_ID=-5119708094 VM 설정
- 20년치 OHLCV 백필 완료 (1,540,635행, 383종목)

## Remaining
- **[진행 중]** 백테스트 완료 대기 → 텔레그램 결과 수신 확인
- **[6/6 이후]** P5 평가: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- **[6/8 전후]** 라이브 Prec@K 확인 → 재훈련 여부 판단
- **[P6+]** 피처 변경(31→36개) + ET 파라미터 + 훈련 격리 묶음

## Risks / Blockers
- SMA 결과 해석 기준 미정 — 어떤 Calmar 이상이면 유효한가?
- SMA 플랫폼과 ML 파이프라인 통합 전략 미결정
- `open=0.0` 품질 문제: 검증 WARN 77.1%, P6+ 때 처리
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)

## Next Actions
1. 텔레그램 "SMA 백테스트" 그룹에서 결과 수신 확인
2. **[6/6 이후]** P5 평가 실행
3. 백테스트 결과 기반으로 SMA 전략 유효성 판단

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **SMA 실행 확인**: `sudo -u stock bash -c 'screen -ls'` → sma_backtest 세션
- **핵심 파일 (SMA)**:
  - `backtest/sma_config.py`, `sma_signal.py`, `sma_backtester.py`, `sma_optimizer.py`, `sma_reporter.py`
  - `scripts/run_sma_backtest.py`
- **텔레그램**: "SMA 백테스트" 그룹, Chat ID: -5119708094
- **핵심 파일 (ML)**:
  - `agents/orchestrator.py`, `scripts/evaluate_predictions.py`
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)
- **ohlcv 범위**: 2005-01-03 ~ 현재 (1,540,635행, 20년치, 383종목)

## Last Updated
- 2026-06-03 18:15 KST
