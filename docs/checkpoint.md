# Checkpoint

## Current Goal
- 로컬 backfill 완료 → backtest_labels 생성 → feature_matrix.parquet → XGBoost 착수

## Current Status
- 세션 22 완료: ML 피처 엔지니어링 설계 + 구현 완료
- 로컬 backfill 백그라운드 실행 중 (PID 579, kospi200_daq150 351종목, 약 60~70분 소요)
- VM은 세션 21 backfill 완료 상태 (signal_history 누적 중)

## Done
- **backtest_labels 스키마 wide format 변환** (`data/db.py`):
  - long format(hold_days/target_return) → wide format(3/5/10일 × max_high/drawdown/return)
  - init_db()에 migration 추가 (기존 테이블 자동 DROP + 재생성)
- **labeler.py 재설계** (`backtest/labeler.py`):
  - label_one(): 단일 패스로 3/5/10일 window 동시 계산
  - label_batch(): hold_days/target_return 파라미터 제거
  - CLI: --days/--pct 제거 → --all 추가 (signal_history 전체 처리)
- **validator.py call site 수정** (`backtest/validator.py`):
  - label_batch() 새 시그니처 대응, max_high_{d}d로 label 동적 파생
- **feature_engineering.py 신규** (`scripts/feature_engineering.py`):
  - signal_history × ohlcv_daily × backtest_labels JOIN
  - 유동성 필터: --min_volume (3/5/10만주), --min_amount (3/5/10억원), NULL-safe 처리
  - grade/pattern 원-핫 인코딩
  - 라벨 9개 동적 생성 (label_3d_3pct ~ label_10d_10pct)
  - 출력: data/feature_matrix.parquet (피처 ~34개 + 라벨 9개)
- **로컬 테스트** (삼성전자 005930 mock 데이터):
  - migration, labeler, feature_engineering 전 구간 동작 확인

## Remaining
- **[대기 중] 로컬 backfill 완료** (PID 579, logs/backfill_local.log)
- **[다음] labeler --all --save 실행**:
  - 로컬: `python backtest/labeler.py --date 2026-05-07 --save` (signal_history 없으므로 날짜 지정)
  - VM: `python backtest/labeler.py --all --save`
- **[다음] feature_engineering 실행**:
  - `python scripts/feature_engineering.py`
- **[다음] XGBoost Walk-forward**: feature_matrix 확보 후 착수
- **[보류] technical.yaml 가중치 재조정** (ichimoku 제거, volume_surge 하향)

## Risks / Blockers
- 로컬 signal_history 없음 → feature_matrix 생성 시 실데이터 없음
  - 해결: VM에서 먼저 full test 권장
- 로컬 backfill 중 공매도 API 경고 발생 (pykrx KeyError 'output') — VM과 동일 이슈, 계속 진행됨
- amount/market_cap NULL이면 유동성 필터 비활성화됨 (NULL-safe 처리로 통과, 경고 출력)
- 60분봉 3개월치로는 ML 학습량 여전히 얇음 (매일 누적 중)

## Next Actions
1. 로컬 backfill 완료 확인: `tail -f logs/backfill_local.log`
2. VM에서 `python backtest/labeler.py --all --save` → `python scripts/feature_engineering.py`
3. feature_matrix shape / 라벨 positive rate 확인 후 XGBoost 착수

## References
- **운영 VM**: `instance-20260505-092414` (us-central1-a), `/opt/stock-monitor`
- **로컬 backfill 로그**: `logs/backfill_local.log` (PID 579)
- **Labeler**: `backtest/labeler.py`
- **피처 엔지니어링**: `scripts/feature_engineering.py`
- **출력**: `data/feature_matrix.parquet`
- **단계별 전략**: `.claude/plans/단계별 발전 전략 260508.md`
- **스케줄**: 수집 07:00 UTC(16:00 KST) / 분석 23:00 UTC(08:00 KST)

## Last Updated
- 2026-05-09 세션 22
