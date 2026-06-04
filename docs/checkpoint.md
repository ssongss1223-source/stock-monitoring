# Checkpoint

## Current Goal
- **SMA 백테스터 코어 재설계** — 설계 확정 완료, 다음 세션에 spec 검토 → 구현 플랜 작성

## Current Status
- **재설계 spec 작성 완료**: `docs/superpowers/specs/2026-06-03-sma-backtest-redesign-design.md` (사용자 검토 대기)
- **치명적 버그 2개 발견**: 회계 오류로 Calmar 10~18배 과대평가 (실제 전 종목 Calmar<1.0) → 기존 결과 폐기 대상
- **눌림목 0건 원인 규명**: 단일 포지션 구조 한계 → 2계좌 분리 모델로 해결 예정
- **ML 파이프라인**: 서비스 active, 정상 운영 중

## Done
- 재설계 브레인스토밍 완료 + spec 작성 (접근법 A, 2계좌 50/50, 진짜 WF, buy&hold 기준)
- 버그 영향 측정: 현재 Calmar 1.8~8.6 → 실제 0.14~0.57 (올바른 회계 엔진으로 재측정)
- `c5e1539` SMA 백테스트 23종목 완료 + 텔레그램 23/23 전송
- `7e4431b` 버그 픽스: `find_best_params` float→int 캐스팅
- `eb74c16` `scripts/sma_send_report.py` — DB 결과 기반 재전송 도구

## Remaining
- **[다음 세션 즉시]** spec 검토 → writing-plans 스킬로 구현 플랜 작성
- **[Phase 1]** Account 프리미티브(올바른 회계) + 2계좌 + 진짜 WF + 지표 수정 구현
- **[Phase 2]** 청산 방식 3종 비교 + Coarse→Fine SMA 그리드
- **[병행]** P5 평가: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- **[6/8 전후]** 라이브 Prec@K 확인 → ML 재훈련 판단
- **[P6+]** 피처 31→36개 + ET 파라미터 + 훈련 격리

## Risks / Blockers
- 기존 백테스트 결과(`sma_backtest_results`, run_date=2026-06-03) **신뢰 불가** — 회계 버그
- 재측정 후 전략이 buy&hold 대비 우위 없으면 → Phase 2 청산방식으로 개선 시도
- `open=0.0` 품질 문제: WARN 77.1%, P6+ 때 처리
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 (월 ~₩33,000)

## Next Actions
1. **spec 검토** — `docs/superpowers/specs/2026-06-03-sma-backtest-redesign-design.md` 읽고 수정사항 확인
2. **writing-plans 스킬** — 구현 플랜 작성 (`.claude/plans/`)
3. **Phase 1 구현** — Account 프리미티브 단위 테스트부터 (TDD)

## References
- **재설계 spec**: `docs/superpowers/specs/2026-06-03-sma-backtest-redesign-design.md`
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **SMA 핵심 파일** (재작성 대상): `backtest/sma_backtester.py`(회계 버그), `sma_optimizer.py`(가짜 WF)
- **재사용**: `backtest/sma_signal.py`(시그널 계산, 검증됨)
- **텔레그램**: "SMA 백테스트" 그룹, Chat ID: -5119708094
- **ML 핵심 파일**: `agents/orchestrator.py`, `scripts/evaluate_predictions.py`
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)

## 핵심 설계 결정 (브레인스토밍 확정)
- 범위: 엔진부터 단계적 (회계+지표+진짜WF, 재측정 우선)
- 자본: All-in / 전략 구조: SMA 계좌·눌림목 계좌 분리 / 종합: 50/50 포트폴리오
- 성공 기준: 같은 구간 buy&hold 대비 Calmar 우위
- 코드 구조: 접근법 A (Account 프리미티브 + 전략 분리)

## Last Updated
- 2026-06-04 (세션 73 — 권한 모드 acceptEdits 설정, autocompact 50% 확인)
