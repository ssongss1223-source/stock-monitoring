# Checkpoint

## Current Goal
- 백테스트 인프라 완성 → feature별 lift 분석 (Phase 2 진입 준비)

## Current Status
- 세션 15: DuckDB 인프라 + 마이그레이션 + Labeler 구현 완료
- 백테스트 기준선 확인: 3일 3% 승률 29.9% (2026-04-01 기준, 359종목 전체)
- 다음 단계: feature별 lift 분석 (`backtest/validator.py`) 또는 GCP 반영

## Done
- **DuckDB 인프라 구축** (세션 15):
  - `data/db.py`: 5개 테이블 스키마 (ohlcv_min, ohlcv_daily, ticker_master, signal_history, backtest_labels)
  - `backtest_labels` PK: `(signal_date, ticker, hold_days, target_return)` — 다중 파라미터 실험 지원
  - `data/store.py`: Parquet 기반 → DuckDB 전면 교체, `migrate_parquet_to_duckdb()` 포함
  - 마이그레이션 완료: 일봉 359종목 210,297행 / 60분봉 356종목 123,699행
- **Labeler 구현** (세션 15):
  - `backtest/labeler.py`: `label_one()`, `label_batch()`, `save_labels()`, `summarize()`
  - CLI: `python -m backtest.labeler --date YYYY-MM-DD --days N --pct 0.0N --save`
- **백테스트 기준선** (2026-04-01, 359종목):
  - 3일 3%: 승률 29.9%, 손절 생존 후 45.3%, 평균 수익 -2.79%, 평균 낙폭 -6.59%
  - 5일 5%: 승률 31.6%, 손절 생존 후 37.0%, 평균 수익 +0.76%, 평균 낙폭 -7.26%
- 세션 13 완료 사항 (GCP 미반영):
  - `data/kis_client.py`: KIS 당일 1분봉 → 60분 리샘플
  - `agents/orchestrator.py`: DataFrame bool 평가 버그 수정

## Remaining
- **[즉시] `backtest/validator.py`**: feature별 lift 분석
  - lift = P(label=1 | feature=True) / P(label=1 | feature=False)
  - signal_history가 없으므로 먼저 과거 신호 생성 필요 → 또는 전체 종목 날짜 스캔 방식
- **[즉시] GCP VM 반영**: git commit + push → VM pull + systemd 재시작
  - 세션 13 + 세션 15 변경사항 모두 미반영
- **[이후] `backtest/feature_matrix.py`**: 과거 날짜 기준 feature 재계산
- **[이후] `backtest/optimizer.py`**: threshold sweep + walk-forward
- **[이후] Secret Manager 연동**: 현재 .env 직접 기입
- **[이후] pykrx 공매도 잔량 수집**: `short_balance` 컬럼 추가
- **[중장기] Kiwoom OpenAPI+ 연동**: 160일 분봉 소급

## Risks / Blockers
- signal_history 미적재: validator.py 구현 전 과거 신호 재현 방법 결정 필요
  - 옵션 A: 과거 날짜를 순회하며 feature 재계산 (feature_matrix.py 먼저 필요)
  - 옵션 B: 특정 날짜의 전체 종목 라벨만 먼저 계산하고, 신호 필터는 수동 지정
- 60분봉 데이터 60일 한계: 분봉 feature 백테스트 신뢰도 낮음 → 일봉 feature로 먼저 분석
- GCP e2-micro 1GB RAM: 파이프라인 병렬 실행 시 메모리 주의
- ReportAgent "15723번 발송 시도" 원인 미확인

## Next Actions
1. `git commit + push` → GCP VM 반영 (세션 13~15 변경사항)
2. `backtest/validator.py` 구현 — feature별 lift 분석
3. validator 결과로 volume feature 중복 문제 정량 확인 (승률 vs. 랜덤 기준선 비교)

## References
- **설계 플랜**: `.claude/plans/feature-streamed-reddy_new2.md`
- **DuckDB**: `data/stock.duckdb` (단일 파일)
- **DB 스키마**: `data/db.py`
- **데이터 저장소**: `data/store.py`
- **라벨러**: `backtest/labeler.py`
- KIS 클라이언트: `data/kis_client.py`

## Last Updated
- 2026-05-07 세션 15
