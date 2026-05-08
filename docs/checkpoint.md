# Checkpoint

## Current Goal
- Phase 1 ML 파이프라인 착수: `backtest_labels` 생성 + `signal_history` 저장 활성화

## Current Status
- 세션 19 완료: 데이터 수집 현황 분석 + 중장기 전략 수립 + store.py 수정
- 기존 rule-based 기준선 승률: 61.0% (운영 101종목)
- VM deploy (세션 18 push분)는 아직 수동 실행 필요

## Done
- **데이터 현황 전수 파악** (세션 19):
  - `ohlcv_daily`: 359종목, 2023-11-17~2026-05-07 (2.5년) ✅
  - `ohlcv_min`: 356종목, 2026-02-03~2026-05-07 (3개월) ✅
  - `signal_history`, `backtest_labels`, `ticker_master`: 전부 0건 ⚠️
  - `foreign_net`, `inst_net`, `short_balance`: 전부 NULL ⚠️
- **`store.py` 수정** (세션 19): foreign_net / inst_net / short_balance 수집 로직 추가
  - 단, KRX 인증(KRX_ID/KRX_PW) 없으면 실제 수집 안 됨 → KRX 계정 등록 필요
- **단계별 발전 전략 문서** (세션 19): `.claude/plans/단계별 발전 전략 260508.md` 저장
- **세션 18 기완료**: validator/optimizer `--universe live`, 텔레그램 S등급 필터, GCP push

## Remaining
- **[즉시] VM deploy**: `sudo bash /opt/stock-monitor/deploy/update.sh`
- **[즉시] KRX 계정 등록**: data.krx.co.kr → KRX_ID/KRX_PW 발급 → GCP .env 등록
- **[다음] Phase 1 ML 착수**:
  1. `backtest/labeler.py` 실행 → `backtest_labels` 데이터 생성
  2. `orchestrator.py`에서 `signal_history` 저장 활성화
  3. 일봉 피처 엔지니어링 스크립트 작성 (OBV slope, 회전율, 이평 배열 등)
  4. XGBoost Walk-forward 파이프라인 구성
- **[보류] `technical.yaml` 가중치 재조정**: lift 결과 반영 (ichimoku_cloud_break 제거 등)
  - ichimoku_cloud_break: lift 0.794 → 제거 또는 마이너스
  - volume_surge: lift 0.944 → 하향
  - has_pattern: 운영 3위 → 상향
  - ichimoku_cloud_support: 운영 유효, 세션16 하향 재검토

## Risks / Blockers
- KRX 인증 없으면 foreign_net/inst_net/short_balance 수집 불가
- 60분봉 3개월치로는 ML 학습에 부족 → 계속 누적 중, 1년 후 의미 있어짐
- `backtest_labels` 0건 → ML 시작 전 labeler 실행 필수
- GCP e2-micro 1GB RAM: 병렬 실행 시 메모리 주의
- 키움 연동은 Windows 전용 → GCP Linux VM 불가, 개인 PC 상시 가동 어려움

## Next Actions
1. VM: `sudo bash /opt/stock-monitor/deploy/update.sh`
2. KRX 계정 등록 후 GCP .env에 KRX_ID/KRX_PW 추가
3. `python -m backtest.labeler` 실행 → backtest_labels 생성
4. `orchestrator.py` signal_history 저장 로직 활성화

## References
- **운영 VM**: `instance-20260505-092414` (us-central1-a), 앱 경로: `/opt/stock-monitor`
- **DuckDB**: `data/stock.duckdb`
- **store.py 수정**: `data/store.py` (foreign_net/inst_net/short_balance 수집 추가)
- **단계별 전략**: `.claude/plans/단계별 발전 전략 260508.md`
- **Scoring 설정**: `config/scoring/v1_baseline/technical.yaml`, `buy_grade.yaml`
- **Labeler**: `backtest/labeler.py`
- **Validator**: `backtest/validator.py`

## Last Updated
- 2026-05-08 세션 19 (데이터 설계 검토 + 중장기 전략 수립)
