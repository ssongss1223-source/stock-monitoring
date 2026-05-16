# Checkpoint

## Current Goal
- 5/15(금) 데이터 수동 실행 결과로 텔레그램 리포트 확인 + soft voting 분포 검증

## Current Status
- **VM 파이프라인 수동 실행 중** (2026-05-16 11:32 UTC 시작, 약 100분 소요 예정)
  - `_pipeline()` 직접 호출로 거래일 체크 우회, 5/15 DB 데이터 기준 분석
  - 장세: KOSPI sideways(6), KOSDAQ sideways(7)
  - 완료 시 텔레그램 자동 발송
- 코드: XGB+LGBM+ET soft voting 전환 + VM 배포 완료 (세션 35)
- 모델 파일: `et_label_*.pkl` (9개) + `xgb_*.json` (9개) + `lgbm_*.txt` (9개) VM에 확인됨

## Done
- Soft voting 전환 + VM 배포 — 세션 35
- S등급 RR≥2.0 필터 + ML확률 정렬 (orchestrator.py, report.py) — 세션 34
- CatBoost → ExtraTrees 교체 + 재학습 완료 — 세션 34
- v2 멀티모델 파이프라인 구축 — 세션 31~32
- 거래량 분석 60분봉 기반 전환 + scoring YAML-driven 정비 — 세션 12~25

## Remaining

**[진행 중] 수동 실행 결과 확인**
- 완료 후 텔레그램 리포트 내용 확인
- `signal_xgb_probs` 분포 확인 (soft voting 3모델 평균값이 의미있게 분포하는지)

**[중기] 신호 사후 검증**
- `signal_history × ohlcv_daily` JOIN → 3거래일 후 max_high 자동 계산
- `/verify N` 텔레그램 명령

**[보류]**
- sector 데이터 적재 / market_cap NULL (KRX API 차단)

## Risks / Blockers
- 수동 실행은 토요일 — yfinance 60분봉 신규 수집 안 됨 (DB 캐시 사용, 정상 동작)
- `signal_xgb_probs` 누적 데이터 아직 적음 (5/16 수동 실행이 사실상 첫 soft voting 결과)

## Next Actions
1. 파이프라인 완료 알림 확인 → 텔레그램 리포트 검토
2. VM DB에서 `signal_xgb_probs` 분포 쿼리: `SELECT label, AVG(xgb_prob), MIN(xgb_prob), MAX(xgb_prob) FROM signal_xgb_probs GROUP BY label`
3. 다음 월요일(5/19) 정규 실행 결과와 비교

## References
- **라이브 추론**: `agents/ml_scorer.py` (`score_all_labels()` — soft voting 고정)
- **텔레그램 리포트**: `agents/report.py` (`_sort_signals` — ML확률→패턴등급→손익비)
- **필터링**: `agents/orchestrator.py` line ~168 (S등급 + RR≥2.0, top 10 cap)
- **멀티모델 학습**: `scripts/train_models.py` / **원클릭 재학습**: `run_ml_pipeline.ps1`
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 22:00 UTC(07:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목

## Last Updated
- 2026-05-16 20:30 KST (세션 35)
