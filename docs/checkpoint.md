# Checkpoint

## Current Goal
- **트랙 A (눌림목 타이밍 엔진) P1~P3 완료** — 다음 방향: Layer1 regime gate 추가 or 추세주 선별 규칙화

## Current Status
- **트랙 A P0~P3 완료** (2026-06-06): 지수 백필 + 신호함수 + 러너 + 검증
- **트랙 B ML 파이프라인**: 정상 운영 중
- **브랜치**: `track-a-pullback-timing` (main 미병합, 독립 개발 중)

## Done
- 지수 20년치 백필 + 신호함수/러너 TDD 구현 (14 테스트) + P1~P3 CLI 스크립트
- P1: KOSPI Calmar 0.376 vs 베이스라인 0.066 — 눌림 로직 유효 확인
- P2: 165/165 파라미터 조합 Calmar > 0 (100% robust). 5년 구간 2/5 양수 — 추세 국면 의존
- P3 무튜닝 전이 PASS (65.2%). 추세주(SK스퀘어·HD현대일렉) GOOD, 박스권주(현대차·셀트리온) FAIL
- 인사이트: KOSDAQ 부적합. Layer1 regime gate 추가 시 박스권 손실 차단 가능 → Layer1 결합 명분

## Remaining — 트랙 A 다음 단계
- **Layer1 regime gate 추가** — 시장 전체 추세 ON일 때만 개별주 신호 활성화 (박스권 방어)
- **추세주 선별 규칙화** — GOOD 종목(65%)의 공통 특성 → Layer2 필터 힌트
- **main 병합 판단** — P4 레버리지 적용/Layer1 결합 전에 판단

## Remaining — 트랙 B (ML 파이프라인)
- P5 평가: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- 6/8 전후 라이브 Prec@K 확인 → ML 재훈련 판단

## Risks / Blockers
- `open=0.0` 품질 문제: WARN 77.1%, P6+ 때 처리
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 (월 ~₩33,000)

## Next Actions
1. **Layer1 regime gate 추가** — 코스피 200d 추세 ON일 때만 개별주 진입 허용
2. **ML Prec@K 평가** (`evaluate_predictions.py`) — 트랙 B 병행
3. **main 병합 여부 판단** — 트랙 A가 Layer1 결합까지 완성 후 고려

## References
- **트랙 A spec**: `docs/superpowers/specs/2026-06-05-pullback-timing-engine-design.md`
- **트랙 A 구현 계획**: `docs/superpowers/plans/2026-06-06-pullback-timing-engine.md`
- **트랙 A 신규 파일**: `backtest/pullback_signal.py`, `backtest/pullback_runner.py`, `scripts/run_pullback_backtest.py`, `scripts/backfill_index_ohlcv.py`
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **텔레그램**: "SMA 백테스트" 그룹, Chat ID: -5119708094
- **ML 핵심 파일**: `agents/orchestrator.py`, `scripts/evaluate_predictions.py`
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)

## 핵심 설계 결정 (세션 76 확정)
- 트랙 A = 시계열 타이밍 엔진 (WHEN), 트랙 B = 단면 ML 랭킹 (WHICH) — 직교
- 성공 기준: 전체 사이클 Calmar 우위 (vsBH 아님), 파라미터 대역 robust, 무튜닝 전이 생존
- KOSPI 기초 지수 기준 추세-눌림 전략 유효. KOSDAQ 부적합.
- Faber 200d + 50d 눌림 표준 템플릿 기반 (발명 아님)

## Last Updated
- 2026-06-06 (세션 76 — 트랙 A P0~P3 완료, 65% 무튜닝 전이 PASS, Layer1 결합 방향 합의)
