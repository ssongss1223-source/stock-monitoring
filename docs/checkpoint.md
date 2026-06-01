# Checkpoint

## Current Goal
- **Prec@K 1주일 모니터링 + 텔레그램 검증 메시지 구현**

## Current Status
- **코드** `a4aed10` — VM 배포 완료, 서비스 active
- **6/1 배치 전체 정상**: run_collect 16:00 / run_daily 17:30 자동 실행 성공
- **P5 평가 불가 (정상)**: universe_daily 라벨이 5/15까지만 채워짐
  - 원인: cutoff 버그 픽스 완료, 하지만 5/18~5/22는 아직 10 거래일 미달
  - **6/6 이후**부터 5/22 신호의 10d 라벨 확보 → P5 평가 시작 가능
  - P5 실행 시: `evaluate_predictions.py --top-k 5 10 20 30 --from-date 2026-05-22 --save`
- **텔레그램 검증 메시지**: 설계 확정, 구현 대기 중

## Done
- `a4aed10` universe_daily 라벨 cutoff 달력일(15일)→거래일(10일) 기준으로 수정
- 6/1 전체 아키텍처 검증: verify_data_quality OK 22 / WARN 2 / FAIL 0
- run_daily 스케줄 변경: 17:30 KST 자동 실행 확인
- 추천 로직 재설계 (대형주≥5조, AUC가중 정렬, K=[5,10,20,30])
- OOF AUC 파악: XGB 0.576 / LGBM 0.578 / ET 0.565 / Soft 0.570

## Remaining
- **[구현 대기]** 텔레그램 검증 메시지 — run_collect 완료 후 별도 메시지 전송
  - 설계 확정: Option A (orchestrator에서 verify 함수 직접 호출)
  - 포함 섹션: 전체 4개 (적재완전성 / NULL비율 / 라벨커버리지 / 모델품질)
  - 형식: 요약형 + WARN/FAIL 항목 짧은 설명
- **[6/6 이후]** P5 평가 실행 (위 명령어 참고)
- **[1주일 모니터링]** 라이브 Prec@K → 재훈련 여부 판단
- **[재훈련 시 P6+]** 피처 변경(제거 5+추가 10) + ET 파라미터 + 훈련 격리 묶음 적용

## Risks / Blockers
- `verify_data_quality.py` LABEL_CUTOFF_DAYS = 15 (달력일)로 하드코딩 → 위 cutoff 픽스와 불일치, 검증 메시지 구현 시 같이 수정 필요
- `open=0.0` 데이터 품질 문제: 001340 등 일부 종목 ohlcv_daily.open=0 → label_one() None 반환, P6+ 묶음 때 처리
- MCP `stock-db`는 로컬 빈 DB에 연결 중 — VM 쿼리는 SSH로 직접 실행
- **8/4** GCP Free Trial 만료 → 7월 말 유료 전환 필요 (월 ~₩33,000)

## Next Actions
1. **텔레그램 검증 메시지 구현** (Option A): `orchestrator.py` run_collect 끝에 verify 호출 + `report.py` 빌더 추가
2. **6/6 이후**: P5 평가 실행 (`--from-date 2026-05-22` 필수)
3. **6/8 전후**: 라이브 Prec@K 확인 → 재훈련 여부 판단

## References
- **VM**: e2-medium, us-central1-a, `/opt/stock-monitor`
- **수동 실행**: `cd /opt/stock-monitor && sudo -u stock screen -S run_daily -dm .venv/bin/python main.py --run-now`
- **스케줄**: run_collect 07:00 UTC (16:00 KST) / run_daily 08:30 UTC (17:30 KST)
- **핵심 파일**:
  - `agents/orchestrator.py` (`_auto_label_universe_unlabeled` — 거래일 cutoff)
  - `scripts/verify_data_quality.py` (LABEL_CUTOFF_DAYS=15 → 거래일 기준으로 수정 예정)
  - `agents/report.py` (추천 로직, 헤더 빌더, `_build_collect_message`)
  - `scripts/evaluate_predictions.py` (Prec@K — `--from-date 2026-05-22` 사용)
  - `docs/feature_change_plan.md` (피처 변경 계획 P6+)
- **모델 성적 (OOF)**: XGB 0.576 / LGBM 0.578 / ET 0.565 / 앙상블 Soft 0.570
- **모델 버전**: `2026-05-30` (xgb/lgbm/et 각 18라벨, data/models/)
- **유니버스**: 351종목 (KOSPI 200 + KOSDAQ 151) / 대형주(≥5조) 119 / 중소형 232
- **훈련 격리 설계**: `data/models_tmp/` 훈련 → atomic rename, ET 파라미터 조정 후 ~14시간 예상

## Last Updated
- 2026-06-01 23:10 KST
