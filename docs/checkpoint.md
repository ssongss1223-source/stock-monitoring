# Checkpoint

## Current Goal
- **VM 배포 후 6/2 배치 검증 + P5 평가 실행**

## Current Status
- **코드** `20f4ba5` — 로컬 완성 (VM 미배포, 배포 필요)
- **서비스** active (2026-05-31 재시작, VM은 `1819fda` 기준)
- **구현 완료**: Task 1~6 모두 완료, 전체 테스트 19/19 PASSED
- **DB 데이터**
  - `universe_predictions` 최신: 2026-05-31 (6,318행/일)
  - `universe_outcomes` 최신: 2026-05-14 → 6/2 배치 후 5/15~ 라벨 자동 채움
  - `signal_xgb_probs`: 5/15~ 존재 → 6/2 이후 P5 평가 가능

## Done
- `20f4ba5` 추천 로직 재설계 구현 완료 (대형주≥5조, 필터게이트, AUC가중 정렬, K=[5,10,20,30])
- `02a4c04` 추천 로직 재설계 스펙+플랜 작성 (대형주 시총≥5조, 필터게이트, AUC가중 정렬, K=[5,10,20,30])
- `1819fda` P5.5 모델 버전관리 마이그레이션 배포
- `c41d580` `_auto_label_universe_unlabeled` 버그 수정 + `evaluate_predictions.py` 추가
- `c89fbf9` ET 운영 복귀 — XGB+LGBM+ET soft voting (Prec@20 24.8%)

## Remaining
- **[다음] VM 배포**: `sudo git reset --hard origin/main` → `sudo git pull` → 서비스 재시작
- **6/2 배치 검증**: universe_predictions 신규일자 + model_ver 스탬프 + 텔레그램 수신 확인
- **6/2 이후 P5 실행**: `sudo -u stock .venv/bin/python3 scripts/evaluate_predictions.py --top-k 5 10 20 30 [--save]`
- **후속**: 메시지 포맷 정리(패턴분석 라인 제거), 정렬 가중치 AUC→라이브Prec@K 전환(데이터 누적 후)

## Risks / Blockers
- P5 평가: 6/2 이후에야 라벨 매칭 가능
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)
- ML-only 신호(`grade="ML"`)는 score=0이라 필터게이트 항상 미달 → fallback 처리됨 (의도된 동작)

## Next Actions
1. **지금**: 서브에이전트로 플랜 Task 1~6 실행
2. **완료 후**: VM 배포 (`sudo git reset --hard origin/main` → `sudo git pull` → 서비스 재시작)
3. **6/2 배치 후**: 검증 + P5 평가 실행

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 20:00 UTC (05:00 KST)
- **핵심 파일**:
  - `agents/report.py` (추천 로직: `_is_large_cap`, `_four_groups`, `_passes_gate`, `_pick_group`)
  - `agents/orchestrator.py` (`_get_rank_and_market`, `_ML_PROB_THRESHOLD=0.60`)
  - `models/signals.py` (`BuySignal` — market_cap 필드 추가 예정)
  - `scripts/evaluate_predictions.py` (P5 — K=[5,10,20,30])
  - `docs/superpowers/specs/2026-05-31-recommendation-logic-redesign.md` (설계 스펙)
  - `docs/superpowers/plans/2026-05-31-recommendation-logic-redesign.md` (구현 플랜)
  - `docs/architecture-roadmap.md` (P1–P5 현황 + P6/P7 설계)
- **모델 성적 (OOF)**: 베이스 AUC 0.57 / 일별 Prec@20 ≈ 24.8%
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨)
- **유니버스**: 351종목 (KOSPI 200 + KOSDAQ 151, `kospi200_daq150` 모드 추정)
- **대형주 기준(변경 예정)**: 시총 ≥5조 → 대형 119 / 중소형 232

## Last Updated
- 2026-05-31 23:30 KST
