# Checkpoint

## Current Goal
- 22:00 UTC 실행 후 signal_xgb_probs soft voting 결과 확인

## Current Status
- 코드: XGB+LGBM+ET soft voting 전환 완료, VM 배포 완료 (세션 35)
- 텔레그램 필터: S등급 + risk_reward ≥ 2.0, top 10 cap, ML확률 정렬 — 완료 (세션 34)
- 재학습: ExtraTrees + LR Stacking 모델 파일 VM에 존재 (`et_label_*.pkl`, `lr_stacker_*.pkl`)

## Done
- Soft voting 전환 + VM 배포 — 세션 35
- S등급 risk_reward ≥ 2.0 필터 + ML확률 정렬 (`orchestrator.py`, `report.py`) — 세션 34
- CatBoost → ExtraTrees + LR Stacking 구현 및 재학습 — 세션 34
- run_ml_pipeline.ps1: scikit-learn + chcp 65001 — 세션 34
- v2 멀티모델 파이프라인 + VM 배포 — 세션 31~32

## Remaining

**[모니터링] 오늘 밤 22:00 UTC 실행 결과 확인**
- `signal_xgb_probs` 분포 확인: 3개 모델 soft voting 결과가 0.5~1.0 범위로 분포하는지
- 텔레그램 필터: S등급 + RR≥2.0 종목만 발송됐는지

**[중기] 신호 사후 검증**
- `signal_history × ohlcv_daily` JOIN → 3거래일 후 max_high 자동 계산
- `/verify N` 텔레그램 명령

**[보류]**
- sector 데이터 적재 / market_cap NULL (KRX API 차단)

## Risks / Blockers
- ET 모델은 `et_label_*.pkl` 파일이 VM에 있어야 함 — 재학습 후 배포됐는지 확인 필요
- `signal_xgb_probs` 과거 데이터 적음 — 계속 누적 중

## Next Actions
1. VM에서 `et_label_*.pkl` 존재 확인: `ls /opt/stock-monitor/data/models/*.pkl`
2. 22:00 UTC 실행 후 `signal_xgb_probs` 분포 쿼리로 결과 검증

## References
- **피처 엔지니어링**: `scripts/feature_engineering.py` (55피처: v1 28 + v2 27)
- **멀티모델 학습**: `scripts/train_models.py` → `data/models/`
  - 모델 파일: `xgb_label_{label}.json`, `lgbm_label_{label}.txt`, `et_label_{label}.pkl`
- **라이브 추론**: `agents/ml_scorer.py` (`score_all_labels()` — soft voting 고정)
- **텔레그램 리포트**: `agents/report.py` (`_sort_signals` — ML확률→패턴등급→손익비)
- **필터링 위치**: `agents/orchestrator.py` line ~168 (S등급 + RR≥2.0)
- **원클릭 파이프라인**: `run_ml_pipeline.ps1` (재학습 로그: `data/train_log.txt`)
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 22:00 UTC(07:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목

## Last Updated
- 2026-05-16 (세션 35)
