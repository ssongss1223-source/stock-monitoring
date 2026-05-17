# Checkpoint

## Current Goal
- 텔레그램 리포트 EV/day 기반 정렬 검증 + 신호 사후 검증 자동화

## Current Status
- 전체 로직 VM 배포 완료 (2026-05-17) — 서비스 정상 기동
- 텔레그램 리포트 EV/day 정렬 + 4그룹 구조 로컬 적용 완료 (VM 미배포)
- MCP 서버 3개 운영 중 (stock-db, vm-ssh, telegram)

## Done
- **VM 배포 완료** (git push → pull → macro_daily 테이블 생성 → 서비스 재시작) — 세션 40
- **텔레그램 4그룹 리포트 구현** — 세션 41
  - 대형주 단기/스윙, 중소형주 단기/스윙 top3씩 (총 12종목)
  - 정렬: EV/day = [prob × gain% - (1-prob) × loss%] / days, tiebreak ML확률
  - 단기(3d/5d), 스윙(10d) 라벨 기준 분류
  - 같은 종목은 그룹 내 EV 최대 라벨 하나로만 표시 (중복 없음)
- **버그 수정** — 세션 41
  - `_pipeline`: market/mktcap_rank 세팅 순서 버그 수정 (저장 전에 먼저 세팅)
  - `_get_mktcap_rank`: 비거래일 오류 방지 → DB 최근 거래일 기준 pykrx 조회
  - `_get_rank_and_market()` 추가: rank + market dict 동시 반환
  - `run_resend_last`: 전체 label_probs 복원 + DB xgb_prob fallback + fresh ML 추론
- ATR 기반 동적 손절 + 시장국면 플래그 10개 + MacroStore — 세션 39~40
- `BuySignal.label_probs` 필드 추가 (9개 라벨 확률 전체 보존)

## Remaining

**[즉시] VM 배포 필요**
- 세션 41 변경사항이 로컬에만 있음: `agents/report.py`, `agents/orchestrator.py`, `models/signals.py`
- `git push` → VM `git pull` → 서비스 재시작

**[확인 필요] signal_xgb_probs 비어있음**
- 다음 정규 실행 후 확인: `SELECT label, COUNT(*), AVG(xgb_prob) FROM signal_xgb_probs GROUP BY label`

**[검토 완료, 미구현] 라벨 정의 개선**
- 현재: `label_Xd_Ypct = max_high_Xd >= entry × (1+Y%)` (장중 최고가 기준, 거래 불가능)
- 제안: `label_Xd_Ypct = max_close_Xd >= entry × (1+Y%)` (기간 내 최고 종가 기준)
- 구현 시 필요: `labeler.py` + `feature_engineering.py` + DB + 재학습 (27모델)
- 우선순위: signal_xgb_probs 누적 후 실측 hit rate 확인 뒤 적용 권장

**[중기] 신호 사후 검증 자동화**
- `signal_history × ohlcv_daily` JOIN → n일 후 실제 수익률 자동 계산
- `/verify N` 텔레그램 명령 구현

**[보류]**
- `sector_strong_ratio_70pct`: pykrx 차단으로 항상 False
- PatternLearningResult DB 저장 미구현
- sklearn 버전 불일치 (1.7.2 학습 → 1.8.0 실행) → 재학습 시 해소

## Risks / Blockers
- sector 플래그 항상 False → 실질 최대 점수 12점 (공칭 13점)
- 라벨이 max_high 기준이라 EV 산식의 gain% 가 실제 포착 가능 수익보다 낙관적

## Next Actions
1. **VM 배포**: `git push` → VM `git pull` → 서비스 재시작
2. 정규 실행 후 텔레그램 리포트 + `signal_xgb_probs` 확인 (EV 정렬 실제 작동 여부)
3. 사후 검증 자동화 구현 (`backtest/labeler.py --all-signals --save` 후 hit rate 측정)

## References
- **리포트 형식 + EV 정렬**: `agents/report.py`
- **파이프라인 + resend**: `agents/orchestrator.py`
- **BuySignal 모델**: `models/signals.py`
- **라벨 정의**: `backtest/labeler.py` (max_high 기준)
- **라벨 파생**: `scripts/feature_engineering.py:409`
- **라이브 ML 추론**: `agents/ml_scorer.py` (`score_all_labels()`)
- **시스템 개요**: `docs/system.md`
- **VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC / 분석 21:00 UTC

## Last Updated
- 2026-05-17 17:10 KST
