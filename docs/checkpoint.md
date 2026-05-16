# Checkpoint

## Current Goal
- v2 피처 27개 + 멀티모델(XGB/LGBM/CatBoost)로 재학습 → VM 배포

## Current Status
- 로컬: 피처 v2 + 멀티모델 코드 완성, 커밋 완료 (b9352fc)
- VM: 구버전(XGBoost 단일모델, 12피처) 운영 중
- 재학습 미실행 — 로컬 DB에 feature_matrix 아직 없음

## Done
- 멀티모델 학습 파이프라인: `scripts/train_models.py` (XGB/LGBM/CatBoost, walk-forward 5-fold, OOF, Precision@K, `model_meta.json`)
- `signal_xgb_probs` 테이블 추가 + 9-label 동시 추론 (`ml_scorer.py`)
- 피처 v2 확장: 27개 피처 (`feature_engineering.py` + `ml_scorer.py` 동기화)
- 텔레그램 포맷 개선: 별 제거, 시총순위 표시, 06:00 KST 스케줄, 거래일 체크
- XGBoost VM 연동 배포 + scikit-learn 설치 (2026-05-12)

## Remaining

**[즉시] 재학습 & 배포**
- VM DB → 로컬 복사 (scp) → `feature_engineering.py` → `train_models.py` 실행
- `data/models/` 27개 모델 + `model_meta.json` 생성 확인
- VM에 코드 push + 모델 파일 배포 + `python -m data.db` 마이그레이션

**[단기] xgb_prob threshold 결정**
- VM `signal_xgb_probs` 데이터 분포 확인 (3일치 이상 누적됨)
- 분포 기반 threshold 결정 → 텔레그램 필터 적용

**[중기] 신호 사후 검증**
- `signal_history × ohlcv_daily` JOIN → 3거래일 후 max_high 자동 계산
- `/verify N` 텔레그램 명령

**[보류]**
- sector 데이터 적재 (pykrx `get_market_sector_classifications`)
- market_cap NULL (KRX API 차단)

## Risks / Blockers
- 로컬 DB가 오래됐을 수 있음 — VM에서 최신 DB 복사 후 재학습 권장
- `train_models.py`는 backtest_labels 데이터 필요 → `backtest/labeler.py --all` 먼저 실행해야 함
- `get_exchange_business_day_list` pykrx 버전 따라 없을 수 있음 → fallback 평일 기준

## Next Actions
1. VM DB 로컬 복사: `gcloud compute scp instance-20260505-092414:/opt/stock-monitor/data/stock.duckdb data/stock.duckdb`
2. `python backtest/labeler.py --all --save` → `python scripts/feature_engineering.py` → `python scripts/train_models.py`
3. 생성된 모델 파일들 VM 배포 + `python -m data.db` 마이그레이션

## References
- **DB**: `data/stock.duckdb` (VM: `/opt/stock-monitor/data/stock.duckdb`)
- **피처 엔지니어링**: `scripts/feature_engineering.py` (v2, 27피처)
- **멀티모델 학습**: `scripts/train_models.py` → `data/models/`, `data/model_meta.json`
- **라이브 추론**: `agents/ml_scorer.py` (`score_all_labels()`)
- **모델 메타**: `data/model_meta.json` (label별 best model 선택)
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 21:00 UTC(06:00 KST)
- **유니버스**: `kospi200_daq150` = 351종목

## Last Updated
- 2026-05-16 (세션 31)
