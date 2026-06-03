# Checkpoint

## Current Goal
- **SMA 백테스트 결과 분석 완료** → 눌림목 독립 전략 or Fine-grid 다음 방향 결정

## Current Status
- **SMA 백테스트**: 23/23 종목 완료, DB 저장 OK, 텔레그램 전송 OK
- **분석 완료**: 눌림목 진입 구조적으로 불가 (사실상 SMA breakout 단독 전략)
- **ML 파이프라인**: 서비스 active, 정상 운영 중

## Done
- `c5e1539` SMA 백테스트 23종목 완료 + 텔레그램 23/23 전송 성공
- `7e4431b` 버그 픽스: `find_best_params` float→int 캐스팅 (rolling() window 오류)
- `eb74c16` `scripts/sma_send_report.py` — DB 결과 기반 재전송 도구
- 분석 완료: 눌림목 0건, SMA 50~80이 최적, vs_buyhold 수치 의미 없음(전체 복리)
- 20년치 OHLCV + SMA MVP 전체 구현 (Task 1~7)

## Remaining
- **[즉시 가능]** P5 평가: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- **[다음 논의]** 눌림목 독립 전략 설계 (재진입 쿨다운 or 피라미딩 방식)
- **[다음 논의]** Fine-grid SMA 탐색 (최적값 ±10 범위, CV 기준 로버스트 구간 확인)
- **[6/8 전후]** 라이브 Prec@K 확인 → ML 재훈련 여부 판단
- **[P6+]** 피처 변경(31→36개) + ET 파라미터 + 훈련 격리 묶음

## Risks / Blockers
- 눌림목 전략: 현 설계로는 구조적으로 발동 불가 → 재설계 필요
- vs_buyhold 수치 과장: 전체 history 복리 적용 → 텔레그램 메시지 개선 필요
- `open=0.0` 품질 문제: WARN 77.1%, P6+ 때 처리
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)

## Next Actions
1. **P5 평가 실행** — `sudo -u stock ... evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
2. **눌림목 독립 전략 설계 논의** — 피라미딩 vs 쿨다운 방식 선택
3. **Fine-grid 탐색** — 전체 종목 최적 SMA ±10 구간에서 CV 기준 로버스트 파라미터 확인

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **SMA 핵심 파일**:
  - `backtest/sma_config.py`, `sma_signal.py`, `sma_backtester.py`, `sma_optimizer.py`, `sma_reporter.py`
  - `scripts/run_sma_backtest.py`, `scripts/sma_send_report.py`
- **텔레그램**: "SMA 백테스트" 그룹, Chat ID: -5119708094
- **SMA 결과 조회**: `sma_backtest_results` 테이블, `run_date='2026-06-03'`
- **안정적 종목 (CV<2, Calmar>5)**: 삼성SDI(SMA50/CV1.31), 기아(SMA50/CV1.45), LS ELECTRIC(SMA50/CV1.77)
- **ML 핵심 파일**: `agents/orchestrator.py`, `scripts/evaluate_predictions.py`
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)

## Last Updated
- 2026-06-03 21:10 KST
