# Checkpoint

## Current Goal
- 운영 유니버스(100~105종목) 한정 lift 재분석 → 가중치 재조정 (옵션 A 실행 단계)

## Current Status
- 세션 18: 옵션 A 구현 완료, 텔레그램 S등급 필터 추가 완료 — GCP 반영 필요
- 기준선 승률: 53.6% (12날짜 × 350종목, 4,212건) — 전체 종목 기준

## Done
- **옵션 A 구현** (세션 18):
  - `backtest/validator.py`: `--universe live` 추가, `_load_all_ohlcv(tickers=None)` ticker 필터 지원
  - `backtest/optimizer.py`: `--universe live` 추가
- **텔레그램 S등급 필터** (세션 18):
  - `agents/orchestrator.py`: `buy_signals` → S등급만 `report_agent.send()` 전달 (A/B등급 로그에는 남음)
- **Scoring 가중치 조정** (세션 16): `near_52w_high_5pct` +1→+3, `ichimoku_cloud_support` +3→+1
- **`buy_grade.yaml` 조정** (세션 16): S등급 `pattern_required: true → false`

## Remaining
- **[즉시] GCP 반영**: `git push` → VM `sudo bash /opt/stock-monitor/deploy/update.sh`
- **[즉시] 옵션 A 실행 & 결과 확인**:
  ```
  python -m backtest.validator --days 3 --pct 0.03 --n-dates 12 --universe live
  python -m backtest.optimizer --days 3 --pct 0.03 --n-dates 12 --universe live
  ```
- **[다음] 옵션 A 결과 반영**: 운영 유니버스 기준 lift로 가중치 재조정
- **[이후] 옵션 B**: 종목별 개별 파라미터 (grade 임계값 + 지표 window), YAML/ScoringEngine 수정 필요
- **[이후] `backtest/feature_matrix.py`**: signal_history 소급 적재용 과거 feature 재계산
- **[이후] Secret Manager 연동**: 현재 .env 직접 기입
- **[중장기] pykrx 공매도 잔량**, **Kiwoom OpenAPI+ 연동**

## Risks / Blockers
- GCP e2-micro 1GB RAM: 파이프라인 병렬 실행 시 메모리 주의
- 운영 100~105종목 중 DuckDB에 60일 이상 이력 없는 종목은 옵션 A에서 자동 제외
- 샘플 기간(2026-01-29 ~ 2026-04-16)이 대체로 상승장 → 하락장 일반화 미검증
- ReportAgent "15723번 발송 시도" 원인 미확인

## Next Actions
1. `git push` → GCP VM `sudo bash /opt/stock-monitor/deploy/update.sh`
2. `python -m backtest.validator --days 3 --pct 0.03 --n-dates 12 --universe live` 실행
3. lift 결과 비교 (전체 vs 운영 유니버스) → 필요 시 `technical.yaml` 재조정

## References
- **운영 VM**: `instance-20260505-092414` (us-central1-a), 앱 경로: `/opt/stock-monitor`
- **DuckDB**: `data/stock.duckdb`
- **Validator**: `backtest/validator.py`
- **Optimizer**: `backtest/optimizer.py`
- **Scoring 설정**: `config/scoring/v1_baseline/technical.yaml`, `buy_grade.yaml`
- **DB 스키마**: `data/db.py`

## Last Updated
- 2026-05-07 세션 18
