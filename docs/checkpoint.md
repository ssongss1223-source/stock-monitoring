# Checkpoint

## Current Goal
- **SMA 백테스팅 플랫폼 MVP 구현** — 설계 완료, 구현 착수 전

## Current Status
- **ML 파이프라인**: 코드 `73d32cb` 배포 완료, 서비스 active
- **ohlcv_daily**: 20년치 백필 완료 (2005-01-03 ~ 2026-06-02, 1,540,635행, 383종목)
- **SMA 플랫폼 설계 완료**: 브레인스토밍 B안 확정
  - 전략: SMA + RSI 조합 (방향 + 타이밍)
  - 파라미터: SMA(50~300) × RSI기간(7,14,21) × RSI임계값(40,50,60,70) = 312조합/종목
  - 최적화: Walk-forward + Calmar Ratio 1순위
  - 출력: 텔레그램 새 채널 + Streamlit 대시보드
  - 대상: 20종목 (개인 관심 10개 + 시총 상위 N개 혼합)
- **P5 평가 대기 중**: 6/6 이후 실행

## Done
- `73d32cb` 20년치 OHLCV 백필 스크립트 (`scripts/backfill_historical_ohlcv.py`) + 실행 완료
- SMA 백테스팅 플랫폼 브레인스토밍 완료 (B안 확정, 설계 문서 작성 예정)
- `4d9e6ee` 텔레그램 데이터 품질 검증 메시지 구현
- `a4aed10` universe_daily 라벨 cutoff 달력일→거래일 기준 수정
- run_daily 스케줄 17:30 KST 자동 실행 확인

## Remaining
- **[SMA MVP - 다음 세션]** 관심종목 20개 확정 (개인 10개 직접 입력)
- **[SMA MVP]** 설계 문서 작성 (`docs/superpowers/specs/`) → writing-plans 실행
- **[SMA MVP]** SMA+RSI 백테스팅 코어 + Walk-forward 구현
- **[SMA MVP]** 텔레그램 새 채널 설정 + 결과 리포트 포맷
- **[SMA MVP]** Streamlit 대시보드
- **[6/6 이후]** P5 평가: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- **[6/8 전후]** 라이브 Prec@K 확인 → 재훈련 여부 판단
- **[P6+]** 피처 변경(31→36개) + ET 파라미터 + 훈련 격리 묶음

## Risks / Blockers
- SMA 플랫폼과 ML 파이프라인은 **독립 운영** — 통합 전략은 미결정 (혼선 방지)
- `open=0.0` 품질 문제: 검증 WARN 77.1%, P6+ 때 처리
- MCP `stock-db`는 로컬 빈 DB — VM 쿼리는 SSH로
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)
- pykrx: 2015년 이전 데이터 없음 / KRX 로그인 없이 일부 기능 제한

## Next Actions
1. **관심종목 20개 확정** → 개인 10개 직접 선정
2. **설계 문서 작성** → brainstorming 마무리 (writing-plans 실행)
3. **6/6 이후**: P5 평가 실행

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **수동 실행**: `sudo -u stock screen -S run_daily -dm .venv/bin/python main.py --run-now`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **핵심 파일 (ML)**:
  - `agents/orchestrator.py`, `agents/report.py`, `scripts/verify_data_quality.py`
  - `scripts/evaluate_predictions.py` (Prec@K)
  - `docs/feature_change_plan.md` (P6+ 피처 계획)
- **핵심 파일 (SMA 플랫폼)**:
  - `scripts/backfill_historical_ohlcv.py` (20년치 백필)
  - `docs/superpowers/specs/` (설계 문서 예정)
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨, data/models/)
- **유니버스**: 383종목 (KOSPI 200 + KOSDAQ 151 + 일부) / 대형주(≥5조) 119
- **ohlcv 범위**: 2005-01-03 ~ 현재 (1,540,635행, 20년치)

## Last Updated
- 2026-06-03 14:15 KST
