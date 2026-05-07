# Checkpoint

## Current Goal
- Scoring 가중치 개선 완료 → GCP 반영 후 실서비스 검증

## Current Status
- 세션 16: validator + optimizer 구현 + scoring 가중치 조정 완료
- GCP VM(instance-20260505-092414)에 세션 15까지 반영됨, 세션 16 변경사항은 미반영
- 기준선 승률: 53.6% (12개 날짜 × 350종목, 4,212건)

## Done
- **GCP 반영** (세션 16): instance-20260505-092414에 세션 13~15 변경사항 pull 완료
  - 운영 VM: `instance-20260505-092414` (us-central1-a) — `stock-monitor-vm`은 빈 VM
- **`backtest/validator.py`** (세션 16): feature별 lift 분석
  - 여러 날짜 샘플링(최근 3개월 매주 목요일 12개), DuckDB 일봉 기반
  - CLI: `python -m backtest.validator --days 3 --pct 0.03`
  - 핵심 결과: ichimoku_triple_positive(1.177), near_52w_high_5pct(1.172) 최고 lift
  - has_pattern lift=1.016 → 사실상 무의미 확인
- **`backtest/optimizer.py`** (세션 16): feature 조합 sweep + walk-forward
  - CLI: `python -m backtest.optimizer --days 3 --pct 0.03`
  - Best OOS combo: ichimoku_triple_positive & near_52w_high_5pct, OOS lift 1.133~1.184
- **Scoring 가중치 조정** (세션 16):
  - `technical.yaml`: `near_52w_high_5pct` +1 → +3 (lift 1.172 반영)
  - `technical.yaml`: `ichimoku_cloud_support` +3 → +1 (lift 1.011, 과대평가)
  - `buy_grade.yaml`: S등급 `pattern_required: true` → `false` (lift 1.016, 무의미)

## Remaining
- **[즉시] GCP 반영**: 세션 16 변경사항 push → VM update.sh
- **[이후] `backtest/feature_matrix.py`**: 과거 날짜 기준 feature 재계산 (signal_history 적재용)
- **[이후] Secret Manager 연동**: 현재 .env 직접 기입
- **[이후] pykrx 공매도 잔량 수집**: `short_balance` 컬럼 추가
- **[중장기] Kiwoom OpenAPI+ 연동**: 160일 분봉 소급

## Risks / Blockers
- GCP e2-micro 1GB RAM: 파이프라인 병렬 실행 시 메모리 주의
- ReportAgent "15723번 발송 시도" 원인 미확인
- validator/optimizer 분석은 일봉 feature만 (60분봉 feature 제외) — 60분봉 lift는 별도 검증 필요
- 샘플 기간(2026-01-29 ~ 2026-04-16)이 대체로 상승장 → 하락장 일반화 미검증

## Next Actions
1. `git push` → GCP VM `sudo bash /opt/stock-monitor/deploy/update.sh`
2. 실서비스 로그 확인 — scoring 변경 후 S등급 신호 수 변화 점검
3. `backtest/feature_matrix.py` 설계 — signal_history 적재를 위한 과거 feature 재계산

## References
- **운영 VM**: `instance-20260505-092414` (us-central1-a), 앱 경로: `/opt/stock-monitor`
- **DuckDB**: `data/stock.duckdb`
- **Validator**: `backtest/validator.py`
- **Optimizer**: `backtest/optimizer.py`
- **Scoring 설정**: `config/scoring/v1_baseline/technical.yaml`, `buy_grade.yaml`
- **DB 스키마**: `data/db.py`

## Last Updated
- 2026-05-07 세션 16
