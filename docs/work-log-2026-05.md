# Work Log — 2026-05

---

## 2026-05-16 세션 35 — Soft Voting 전환 + VM 배포
- 작업: ML inference를 model_meta.json 단일모델 선택 → XGB+LGBM+ET 상시 soft voting으로 전환, VM 배포
- 변경 사항:
  - `agents/ml_scorer.py`: `_load_model_meta()`, `_predict_lr_stack()`, `_META_PATH`, `import json` 제거. `score_all_labels()` 단순화 — 항상 3개 모델 평균 (soft voting)
  - VM (`instance-20260505-092414`): git pull + stock-monitor.service 재시작 완료
- 관련 파일: `agents/ml_scorer.py`
- 메모:
  - model_meta.json이 best single model만 사용하는 문제 → 앙상블 전체 활용 안됨
  - soft voting으로 바꾸면 코드 단순화 + 앙상블 효과 항상 적용
  - LR stacker 파일(lr_stacker_*.pkl)은 존재하지만 더이상 inference에서 사용 안 함 (데이터 적을 때 오히려 노이즈)
- 다음 아이디어: 22:00 UTC 실행 후 signal_xgb_probs 분포 확인

---

## 2026-05-16 세션 34 — CatBoost → ExtraTrees + LR Stacking 구현
- 작업: ML 앙상블 3번째 모델 교체 (CatBoost → ExtraTrees), OOF 기반 LR Stacking 추가, 재학습 파이프라인 실행
- 변경 사항:
  - `scripts/train_models.py`: CatBoost 제거 → ExtraTrees (`_ET_PARAMS`, `_et_cv`, `_et_final`), LR Stacking 추가 (TimeSeriesSplit OOF → `lr_stacker_{label}.pkl`, base_keys dict 저장)
  - `agents/ml_scorer.py`: `.cbm` 핸들러 → `.pkl` 핸들러, `_predict_lr_stack()` 신규 (base_keys dict 로드), `score_all_labels()` "lr" 분기 추가
  - `run_ml_pipeline.ps1`: catboost pip install → scikit-learn, `chcp 65001` + OutputEncoding 추가 (콘솔 한글 깨짐 수정)
- 관련 파일: `scripts/train_models.py`, `agents/ml_scorer.py`, `run_ml_pipeline.ps1`
- 메모:
  - ExtraTrees 선택: XGB+LGBM 모두 gradient boosting → 상관관계 높음. ET는 random feature/threshold → 앙상블 다양성 극대화
  - LR Stacking: OOF TimeSeriesSplit 5-fold로 meta-learner 훈련 → leakage 없음. `{"model": lr, "base_keys": [...]}` dict 저장으로 인퍼런스 시 base 모델 자동 조합
  - model_meta.json "best" == "lr" → 3개 base 모델 예측 → LR 통과. 실패 시 soft voting fallback
  - 재학습 백그라운드 실행 중 (`data/train_log.txt`)
- 다음 아이디어: 재학습 완료 후 model_meta.json 확인 → 텔레그램 필터 코드 반영

---

## 2026-05-16 세션 33 — CatBoost 확률 압축 버그 진단 + 텔레그램 리포트 형식 검토
- 작업: VM 로그 확인, signal_xgb_probs 분포 진단, 텔레그램 리포트 테스트, ML 아키텍처 검토
- 변경 사항:
  - `agents/ml_scorer.py`: CatBoost `fillna(0)` → NaN 자체 처리로 수정 (근본 원인은 아니었으나 코드 정확도 개선)
  - `data/model_meta.json`: CatBoost 4개 라벨 교체 (3d_5pct→xgb, 10d_3/5/10pct→lgbm), VM 배포 완료
- 관련 파일: `agents/ml_scorer.py`, `data/model_meta.json`
- 메모:
  - signal_xgb_probs: 0건 정상 (5/15 실행은 배포 전 구버전 코드, 5/16 22:00 UTC가 v2 첫 추론)
  - CatBoost 확률 압축: training 시 Prec@20=90%지만 inference에서 0.500~0.524로 수렴. ranking 자체는 맞을 수 있으나 절대값이 무의미 → model_meta에서 교체
  - 목표가 %: 기술적 저항선 기반 (없으면 일괄 +10%), ML과 무관
  - 현재 필터: S등급 전체 발송 (21종목) — risk_reward/max N 코드 미반영
  - 스케줄 오기 수정: 분석은 21:00 UTC가 아닌 22:00 UTC (다음 실행 2026-05-16 22:00)
- 다음 아이디어:
  - CatBoost → ExtraTrees 대체 (3-model 유지, calibration 안정)
  - 또는 XGB + LGBM + LR stacking (OOF 기반)
  - 텔레그램 필터(risk_reward ≥ 2.0, top 10) 코드 반영

---

## 2026-05-16 세션 32 — v2 첫 재학습 + VM 배포 완료
- 작업: VM DB 복사 → labeler → feature_engineering → train_models → VM 배포
- 변경 사항:
  - `agents/ml_scorer.py`: `total_score` 누락 버그 수정 (추론 시 KeyError 방지)
  - `scripts/train_models.py`: lightgbm 미설치 시 em dash 인코딩 오류 수정 (cp949)
  - `run_ml_pipeline.ps1` 신규: 원클릭 재학습·배포 스크립트
- 관련 파일: `agents/ml_scorer.py`, `scripts/train_models.py`, `run_ml_pipeline.ps1`
- 메모:
  - 피처 수: 55개 (체크포인트 "27개" 오기였음 — v1 28 + v2 27 = 55)
  - 학습 결과: CatBoost가 10일 구간 강세, XGB/LGBM은 단기 강세
  - VM 배포 시 `stock` 유저 권한 문제 → `chmod 777` 우회 적용 (다음 배포 시 `chown` 정리 필요)
  - DB 마이그레이션은 서비스 중지 후 실행해야 함 (DuckDB 락 충돌)
- 다음 아이디어: 내일 VM 로그 확인 → signal_xgb_probs 분포 → threshold 결정

---

## 2026-05-16 세션 31 — 멀티모델 + 피처 v2 확장 완성
- 작업: 9-label 멀티모델 학습 파이프라인 + 피처 27개 확장 + 라이브 추론 동기화
- 변경 사항:
  - `data/db.py`: `signal_xgb_probs` 테이블 추가
  - `agents/orchestrator.py`: `score_all_labels()` 호출, `_save_signal_xgb_probs()` 추가
  - `scripts/train_models.py` 신규: XGB/LGBM/CatBoost walk-forward 5-fold, OOF 저장, Precision@K/Return@K, `model_meta.json` 최고모델 선택
  - `scripts/feature_engineering.py`: 피처 v2 확장 — 15개 신규 피처 추가 (price_momentum_3d/10d, close_to_5ma_ratio, ma_cross_5_20, high_low_ratio, body_ratio, amount_surge_ratio, foreign_net_20d, inst_net_20d, combined_net_5d, foreign_exh_change_5d, roe_proxy, short_balance_change_5d, kospi_return_5d, market_volatility_20d)
  - `agents/ml_scorer.py`: `_build_feature_df()` v2로 교체 (27피처, 전 라벨 동시 추론)
- 관련 파일: `data/db.py`, `agents/orchestrator.py`, `scripts/train_models.py`, `scripts/feature_engineering.py`, `agents/ml_scorer.py`
- 메모:
  - 앙상블: Soft Voting (nanmean) + Rank Ensemble. Logistic Stacking은 데이터 6개월+ 후 검토
  - `model_meta.json`: label별 Precision@20 기준 최고모델 저장 → 라이브 추론에서 우선 사용
  - 재학습 미실행 — VM DB 복사 후 labeler → feature_engineering → train_models 순서 필요
- 다음 아이디어: VM DB 최신 복사 → 재학습 → 모델 배포 → xgb_prob threshold 결정

---

## 2026-05-16 세션 30 — 리포트 포맷 개선 + 거래일 체크 + context 관리 구조 개선
- 작업: 텔레그램 포맷 전면 개선, 분석 스케줄 변경, 거래일 체크 추가, work-log 월별 분리
- 변경 사항:
  - `config.py`: `SCHEDULE_HOUR_UTC` 22→21 (07:00→06:00 KST)
  - `models/signals.py`: `BuySignal`에 `market`, `mktcap_rank` 필드 추가
  - `agents/orchestrator.py`: `_is_trading_day()` 추가 (주말+공휴일 스킵), `_get_mktcap_rank()` 추가 (pykrx 시총순위 조회)
  - `agents/report.py`: 별 제거, `[KOSPI N위]` 배지 추가, 정렬 변경 (패턴등급→ML→목표%), 헤더 문구 수정, KST 06:00으로 변경
  - `docs/work-log.md`: 인덱스 파일로 교체
  - `docs/work-log-2026-05.md`: 신규 생성 (세션 12~29 이전)
  - `.claude/commands/load-context.md`: 3일 경과 시 경고 규칙 추가
  - `.claude/commands/save-checkpoint.md`: Done 최대 5개 규칙, work-log 월별 파일 참조로 변경
- 관련 파일: `config.py`, `models/signals.py`, `agents/orchestrator.py`, `agents/report.py`
- 메모:
  - `_is_trading_day()`: weekday>=5 즉시 False, 평일은 pykrx `get_exchange_business_day_list`로 공휴일 체크, API 실패 시 평일=거래일 fallback
  - 시총순위: `_get_mktcap_rank()` → KOSPI/KOSDAQ 각각 sort_values("시가총액") → rank dict
  - 정렬 기준: `_PATTERN_GRADE_ORDER = {HIGH:0, MEDIUM:1, LOW:2, INSUFFICIENT:3}` → xgb_prob 내림차순 → 목표% 내림차순
  - 기존 work-log에 템플릿 블록이 중간에 섞여있던 것 정리하여 월별 파일로 분리
  - 배포 완료: commit 7fa07a5, VM active (running) 확인
- 다음 아이디어: VM DB에서 xgb_prob 분포 확인 → threshold 결정 → S등급 필터 적용

---

## 2026-05-12 세션 29 — XGBoost 버그 수정 + 텔레그램 정리
- 작업: XGBoost 추론 실패 원인 진단 및 수정, 텔레그램 메세지 정리
- 변경 사항:
  - `agents/report.py`: 전체 분석대상 목록(350종목) 섹션 제거
  - VM: scikit-learn 설치 (`sudo pip install scikit-learn`) — XGBoost가 sklearn 의존
  - VM DB: `signal_history`에 `scoring_version VARCHAR` 컬럼 추가 (ALTER TABLE)
  - VM: git pull + stock-monitor 서비스 재시작
- 관련 파일: `agents/report.py`, VM DB 스키마
- 메모:
  - 이전 XGBoost 실행은 모두 실패 — signal_history에 xgb_prob 없음 (NOT IN FEATURES)
  - 실패 원인 1: `ImportError: sklearn needs to be installed`
  - 실패 원인 2: `scoring_version` 컬럼 없어 INSERT 자체 실패
  - label_3d_5pct = T+1 시가 기준 T+1~T+3 최고가 +5% 터치 여부 (3일 후가 아닌 3일 내)
  - 분석 파이프라인 실행 시간 = 약 90분 (세마포어 2 × 351종목)
- 다음 아이디어: 내일 xgb_prob 분포 확인 후 S등급 필터링 threshold 결정

---

## 2026-05-11 세션 28 — XGBoost 연동 배포 + 스케줄 조정
- 작업: 9개 라벨 전체 학습, label_3d_5pct 선택, 추론 모듈 구현, VM 배포
- 변경 사항:
  - `scripts/train_xgboost.py`: _TARGETS 3개 → 9개 전체 학습
  - `agents/ml_scorer.py`: 신규 — DB ohlcv 피처 + 신호 피처 조합 → xgb 추론, BuySignal.xgb_prob 인플레이스 업데이트
  - `models/signals.py`: BuySignal에 `xgb_prob: Optional[float] = None` 추가
  - `agents/orchestrator.py`: buy_signals 수집 후 `score_signals()` 호출 (실패 시 파이프라인 계속)
  - `agents/report.py`: 상세 섹션에 `ML(3일+5%): XX%` 표시
  - `config.py`: SCHEDULE_HOUR_UTC 23 → 22 (분석 08:00 → 07:00 KST)
- 관련 파일: `agents/ml_scorer.py`, `models/signals.py`, `agents/orchestrator.py`, `agents/report.py`, `config.py`
- 메모:
  - 9개 라벨 AUC: 3d_10pct 0.6627 > 5d_10pct 0.6342 > 3d_5pct 0.6168 (채택)
  - positive율 낮을수록 AUC 높음 — 단, 3d_10pct(14.7%)는 실용성 부족으로 기각
  - VM 배포 시 git 권한 이슈(stock/KHSong 유저 혼재) → `sudo chown -R stock:stock` + `git reset --hard`로 해결
  - xgboost VM에 신규 설치 필요 (v3.2.0)
- 다음 아이디어: 신호 사후 검증(/verify 명령), 종목 그룹별 모델, 장세 레이어 강화

---

## 2026-05-11 세션 27 — vol_score 재설계 + backfill 730일 + 모델 재학습
- 작업: backfill vol_score 분포 불일치 해소, 데이터 확장, XGBoost 재학습
- 변경 사항:
  - `agents/orchestrator.py`: T vs T-1 signal_date 버그 수정 (`MAX(date) FROM ohlcv_daily` 사용)
  - `scripts/backfill_signals.py`: `_vol_score_daily()` 제거 → `_vol_flags_daily()` 추가 (8지표 daily proxy, live_v2 동일 구조), `scoring_version='live_v2'`로 변경
  - `scripts/feature_engineering.py`: scoring_version 원-핫에서 'backfill' 제거 → live_v1/v2만 유지
  - DB: `amount` NULL → `UPDATE ohlcv_daily SET amount=close*volume` (225,841건 채움)
  - DB: `scoring_version='backfill'` → `'live_v2'` 일괄 변환 (56,748건)
- 관련 파일: `agents/orchestrator.py`, `scripts/backfill_signals.py`, `scripts/feature_engineering.py`
- 메모:
  - backfill --days 730 결과: 48,450건 신규 생성 (기존 8,298 포함 총 56,838건)
  - 이전 label_10d_10pct AUC 0.6697은 vol_score 분포 불일치(0~6 vs 7~20)로 인한 허위 성능
  - 재학습 결과: label_3d_5pct AUC 0.6168이 가장 안정적, 권장 타겟으로 변경
  - market_cap NULL은 close×volume으로 대체 불가, KRX API 차단으로 보류 유지
- 다음 아이디어: XGBoost → BuySignalAgent 연동 (xgb_prob 필드 + 텔레그램 "ML:0.68" 표시)

---

## 2026-05-11 세션 26 — VM 점검 + 텔레그램 수집 알림 추가
- 작업: VM 운영 상태 확인, KIS API 키 누락 수정, 수집 완료 텔레그램 알림 구현
- 변경 사항:
  - `agents/orchestrator.py`: `run_collect()`에 일봉/60분봉 성공/실패 카운팅 추가, 완료 후 `send_collect_report()` 호출
  - `agents/report.py`: `send_collect_report()` 메서드 + `_build_collect_message()` 빌더 추가 (일봉/60분봉/지수/소요시간 표시)
  - VM `.env`: KIS_APP_KEY / KIS_APP_SECRET / KIS_BASE_URL 추가, KRX 중복 항목 제거
- 관련 파일: `agents/orchestrator.py`, `agents/report.py`
- 메모:
  - VM은 2026-05-09부터 kospi200_daq150 = 351종목으로 정상 수집 중
  - KIS 60분봉은 당일치만 수집 (과거 히스토리는 yfinance 전담)
  - 60분봉 today skip 로직: last_date >= today면 KIS 시도 없이 스킵 → 재실행해도 교체 불가
- 다음 아이디어: XGBoost를 buy_signal.py에 confidence 점수로 연동

---

## 2026-05-10 세션 25 — 스코어링 전면 정비 + ML 파이프라인 완성
- 작업: 하드코딩 감사, 스코어링 YAML-driven 전환, 데이터 추적, ML 학습까지 엔드-투-엔드 완성
- 변경 사항:
  - `data/store.py`: amount/market_cap 수집 로직 추가 (get_market_cap_by_date), 신규분부터 NULL 해소
  - `agents/volume_analysis.py`: OBV Divergence (+3), OBV Acceleration (+2) 추가
  - `config/scoring/v1_baseline/volume.yaml`: 신규 OBV 규칙, group cap(intraday_vol 9pt, OBV 5pt), explosion_imminent 12→11
  - `config/scoring/v1_baseline/buy_grade.yaml`: bull/sideways/bear 별 임계값 분리 (완전 재설계)
  - `config/scoring/v1_baseline/technical.yaml`: rounding_bottom → falling_box_breakout (패턴명 수정)
  - `core/scoring_engine.py`: group cap 로직, regime-based determine_grade() 구현
  - `agents/buy_signal.py`: has_pattern 제거, market_status 전달
  - `data/db.py`: scoring_version 컬럼 마이그레이션 + 소급 설정(vol_score≤6→backfill, 초과→live_v1)
  - `agents/orchestrator.py`: scoring_version='live_v2' 저장
  - `scripts/backfill_signals.py`: YAML-driven 가중치(_MA_W, _ICH_W), group cap 기반 vol_max 자동 계산, buy_grade.yaml 임계값 비례 스케일, scoring_version='backfill'
  - `scripts/feature_engineering.py`: ticker 특성 피처 3개(hist_volatility_20d, log_avg_volume_20d, avg_foreign_exh_rate_20d), scoring_version 원-핫, sector 원-핫 추가
  - `scripts/train_xgboost.py` 신규: walk-forward TimeSeriesSplit 5-fold + 전체 80/20 최종 모델
- 관련 파일: 위 전체
- 메모:
  - feature_matrix: 11,898건 × 49컬럼, 100% backfill, amount/market_cap 전부 NULL
  - XGBoost 결과: AUC 0.59~0.67 (label_10d_10pct 최고), top feature = hist_volatility_20d
  - sv_live_v1/sv_live_v2 피처 분산 없음 (backfill 100%) → scoring_version 피처 현재 무의미
  - ticker_master 미적재 → sector 피처 생성 안 됨
  - fold 2-3 best_iter=1 (label_5d_5pct): 데이터 부족 시 조기 수렴, 데이터 축적 필요
- 다음 아이디어: XGBoost → live 신호 필터 연동, ticker_master 업종 적재, 데이터 축적 후 재학습

---

## 2026-05-10 세션 24 — backfill 디버깅 + 패턴분석 개선 + 로컬 스냅샷
- 작업: backfill 실패 원인 분석, backfill_signals 메모리 최적화, pattern_learning 개선
- 변경 사항:
  - `scripts/backfill_signals.py`: 전체 로드 → 종목별 로드 + 50종목 배치 저장 (OOM 수정)
  - `agents/pattern_learning.py`: 코사인 유사도 → 정규화 유클리드 거리, SUCCESS_THRESHOLD 3%→5%
  - `data/pattern_cache.json`: 초기화 (threshold 변경으로 캐시 무효)
- 관련 파일: `scripts/backfill_signals.py`, `agents/pattern_learning.py`
- 메모:
  - backfill_signals 구버전 실패 원인: VM 메모리 969MB 중 920MB 사용 중 → OOM 킬
  - backfill_amount 실패 원인: KRX API 장기간 반복 요청 차단 (빈 JSON 응답)
  - 최적화 후 backfill_signals 완료: 16,015건, 약 25분 소요, 메모리 안정적
  - 로컬 stock.duckdb 스냅샷 복사 완료 (31MB, 2026-05-10 기준)
  - amount 전체 NULL 문제 미해결 — feature_matrix 생성 시 유동성 필터 일부 무효
- 다음 아이디어: labeler → feature_engineering → XGBoost walk-forward

---

## 2026-05-10 세션 23 — VM 소급 backfill 배포 + 실행
- 작업: backfill 스크립트 2종 + ML 파이프라인 VM 배포, 백그라운드 실행
- 변경 사항:
  - `scripts/backfill_signals.py`: 일봉 ohlcv_daily로 90일치 signal_history 소급 생성 (신규)
  - `scripts/backfill_amount.py`: ohlcv_daily의 amount/market_cap/shares NULL 소급 UPDATE (신규)
  - git push → VM git pull → 두 backfill 프로세스 백그라운드 실행
- 관련 파일: `scripts/backfill_signals.py`, `scripts/backfill_amount.py`
- 메모:
  - VM 확인: signal_history 133건(2026-05-10만), amount 전체 NULL, backtest_labels 0건
  - backfill_signals: 59 거래일 × 383 종목 처리 중 (PID 423400, INSERT OR IGNORE)
  - backfill_amount: pykrx get_market_cap_by_date로 소급 중 (PID 423869)
  - git safe.directory 오류 → git config --global safe.directory 추가로 해결
  - VM git pull/python 실행 시 sudo -u root 필요
- 다음 아이디어: backfill 완료 → labeler --all --save → feature_engineering → XGBoost

---

## 2026-05-09 세션 22 — ML 피처 엔지니어링 구현
- 작업: backtest_labels wide format 전환 + feature_engineering.py 신규 작성
- 변경 사항:
  - `data/db.py`: backtest_labels 스키마 wide format 전환 + migration 로직 추가
  - `backtest/labeler.py`: label_one/batch wide format 재설계, --all 플래그 추가
  - `backtest/validator.py`: label_batch() call site 수정 (max_high_{d}d로 label 파생)
  - `scripts/feature_engineering.py`: 신규 — signal_history × ohlcv_daily × backtest_labels JOIN, 유동성 필터(volume/amount), 라벨 9개 동적 생성, parquet 출력
- 관련 파일: `data/db.py`, `backtest/labeler.py`, `backtest/validator.py`, `scripts/feature_engineering.py`
- 메모:
  - 로컬 amount/market_cap 전부 NULL (backfill 미실행) → NULL-safe 유동성 필터로 대응
  - 로컬 backfill 백그라운드 실행 중 (PID 579, UNIVERSE_MODE=kospi200_daq150)
  - 공매도 API pykrx KeyError 'output' 경고 — 계속 진행됨 (VM과 동일 이슈)
  - 삼성전자 mock 데이터로 전 구간 로컬 테스트 완료
- 다음 아이디어: VM에서 labeler --all --save → feature_engineering → XGBoost walk-forward

---

## 2026-05-09 세션 21 — 데이터 수집 인프라 전면 정비
- 작업: KRX 인증 등록, 신규 데이터 5종 추가, Universe 확장, 스케줄 분리, signal_history 활성화
- 변경 사항:
  - `data/db.py`: ohlcv_daily 신규 컬럼 8개(per/pbr/eps/bps/div_yield/foreign_exh_rate/short_volume/ratio), market_index 테이블 신규, ALTER TABLE 마이그레이션
  - `data/store.py`: fetch_and_update_daily에 fundamental/exhaustion/shorting_volume 수집 추가, MarketIndexStore 클래스 신규
  - `agents/universe_manager.py`: top200_mktcap, kospi200_daq150 모드 추가
  - `agents/orchestrator.py`: run_collect() 신규(16:00 KST 수집 잡), signal_history 저장 활성화
  - `main.py`: 07:00 UTC collect 잡 추가 (기존 23:00 UTC 분석 잡 유지)
  - `config.py`: COLLECT_HOUR_UTC/MINUTE_UTC 추가
  - `scripts/backfill_new_cols.py`: OHLCV 미적재 종목 풀 적재 + 신규 컬럼 소급 UPDATE 통합
- 관련 파일: `data/db.py`, `data/store.py`, `agents/orchestrator.py`, `main.py`
- 메모:
  - VM ohlcv_daily 0건 원인: stock.duckdb가 root 소유 → chown으로 해결
  - short_volume/ratio: VM pykrx 버전 차이로 컬럼명 오류('거래량' KeyError) → 경고 처리
  - market_index: 1,196건(KOSPI 598 + KOSDAQ 598일) 이미 적재 완료
  - Universe: kospi200_daq150 → 351종목(KOSPI200 + KOSDAQ150 + watchlist)
  - backfill 실행 중 (351종목, 약 60~90분 소요), 완료 시 labeler 자동 실행(크론 16e03720)
- 다음 아이디어: ML 피처 엔지니어링 스크립트(OBV slope, 회전율, 이평 배열), XGBoost walk-forward

---

## 2026-05-09 세션 20 — store.py 커밋 + VM deploy
- 작업: store.py 커밋 + VM deploy
- 변경 사항:
  - `data/store.py` + docs 커밋/push (commit `9215818`)
  - `instance-20260505-092414` deploy 완료 — 세션 18·19 변경사항 반영, 서비스 재시작
- 관련 파일: `data/store.py`, `deploy/update.sh`
- 메모: deploy 후 `stock-monitor.service` active (running) 확인

---

## 2026-05-08 세션 19 — 데이터 수집 현황 분석 + 중장기 전략 수립 + store.py 수정
- 작업: 데이터 수집 현황 분석 + 중장기 전략 수립 + store.py 수정
- 변경 사항:
  - `data/store.py`: foreign_net / inst_net / short_balance 수집 로직 추가 (KRX 인증 필요)
  - `.claude/plans/단계별 발전 전략 260508.md`: Phase 1~3 ML 로드맵 문서 생성
- 관련 파일: `data/store.py`, `data/stock.duckdb`
- 메모:
  - DB 실제 상태: ohlcv_daily 2.5년(359종목), ohlcv_min 3개월(356종목), 나머지 3개 테이블 0건
  - foreign_net/inst_net/short_balance: 스키마 있고 코드도 추가했으나 KRX_ID/KRX_PW 없으면 수집 안 됨
  - pykrx `get_market_trading_value_by_date`, `get_shorting_balance_by_date` → KRX 인증 필요
  - 키움 연동: Windows 전용(COM), GCP Linux 불가. GCP Windows VM e2-small 월 ~$11(스케줄 가동 기준)
  - 60분봉은 매일 수집이 곧 자산 — 과거로 돌아갈 방법 없음. KRX 데이터시스템에서 유료 구매 검토 가능
- 다음 아이디어:
  - backtest_labels 생성 → XGBoost walk-forward 파이프라인 (Phase 1)
  - signal_history 저장 활성화 → 실제 신호 성과 추적
  - KRX 계정 등록 → foreign_net/inst_net/short_balance 수집 재개

---

## 2026-05-07 세션 16 — Validator + Optimizer + Scoring 가중치 조정
- 작업: feature lift 분석 → 조합 최적화 → scoring 가중치 데이터 기반 조정
- 변경 사항:
  - `backtest/validator.py` 신규: 여러 날짜 샘플링 기반 feature별 lift 분석
  - `backtest/optimizer.py` 신규: feature 조합 sweep + walk-forward validation
  - `config/scoring/v1_baseline/technical.yaml`: `near_52w_high_5pct` +1→+3, `ichimoku_cloud_support` +3→+1
  - `config/scoring/v1_baseline/buy_grade.yaml`: S등급 `pattern_required: true → false`
  - `.gitignore`: `data/` → `data/*` + `!data/*.py` (Python 소스 추적 허용)
  - GCP VM(instance-20260505-092414)에 세션 13~15 변경사항 반영
- Lift 분석 결과 (3일 3%, 12날짜, 4,212건, 기준선 53.6%):
  - ichimoku_triple_positive: 1.177 (1위)
  - near_52w_high_5pct: 1.172 (2위) → 심각하게 저평가되어 있었음
  - has_pattern: 1.016 → 무의미, S등급 패턴 요건 제거
  - ichimoku_cloud_support: 1.011 → 과대평가, 점수 하향
- Optimizer Walk-forward OOS 결과:
  - Best combo: ichimoku_triple_positive & near_52w_high_5pct
  - Fold1 OOS lift=1.184 / Fold2 OOS lift=1.133
- 메모:
  - 운영 VM이 `stock-monitor-vm`이 아니라 `instance-20260505-092414`임을 확인
  - validator는 pykrx 라이브 API 없이 DuckDB 저장 데이터만 사용
  - 샘플 기간이 상승장(2026-01~04) 편향 — 하락장 검증 필요
- 다음 아이디어: feature_matrix.py로 signal_history 소급 적재 → 실신호 기반 lift 재검증

---

## 2026-05-07 세션 17 — 운영 유니버스 한정 lift 분석 방향 결정
- 작업: 옵션 A/B 설계 및 플랜 작성
- 변경 사항: `.claude/plans/100-105-serene-riddle.md` 신규 작성 (코드 변경 없음)
- 관련 파일: `backtest/validator.py`, `backtest/optimizer.py`, `agents/universe_manager.py`
- 메모:
  - 파라미터 3종류 정의: ① feature 점수(technical.yaml) ② 등급 임계값(buy_grade.yaml) ③ 지표 window(코드 하드코딩)
  - 현재 가중치는 전체 ~350종목 기준. 운영 대상(시총 상위 100)은 특성이 달라 재검증 필요
  - 옵션 A: `--universe live` 추가로 pykrx API → top100_mktcap+watchlist 필터링 후 lift 재계산
  - 옵션 B(이후): 종목별 개별 파라미터. YAML 구조 + ScoringEngine 수정 필요, 복잡도 높음
- 다음 아이디어: 옵션 A 구현 후 전체 vs 운영 유니버스 lift 순위 차이 비교

---

## 2026-05-07 세션 18 — 옵션 A 구현 + 텔레그램 S등급 필터
- 작업: `--universe live` 플래그 구현, 텔레그램 S등급 전용 필터 추가
- 변경 사항:
  - `backtest/validator.py`:
    - `_load_all_ohlcv(tickers=None)`: ticker 필터 지원 (IN 절 동적 생성)
    - `build_matrix(..., tickers=None)`: 시그니처 확장, 내부 변수 `tickers_actual`로 명명 충돌 방지
    - `main()`: `--universe live` 인자 추가 → `UniverseManager().get_universe()` 호출
  - `backtest/optimizer.py`:
    - `main()`: 동일 패턴으로 `--universe live` 추가
  - `agents/orchestrator.py`:
    - S등급만 필터(`s_grade_signals`) 후 `report_agent.send()` 전달. A/B등급은 로그에만 집계
- 관련 파일: `backtest/validator.py`, `backtest/optimizer.py`, `agents/orchestrator.py`
- 메모:
  - S등급 필터는 orchestrator 레벨에서 처리 — buy_signal 생성 로직은 변경 없음
  - `--universe live`는 pykrx API 호출 필요 (VM에서만 실행 권장)
- 다음 아이디어: GCP push 후 `--universe live` 실행, 전체 vs 운영 유니버스 lift 순위 비교

---

## 2026-05-07 세션 18b — 옵션 A 실행 결과 확인 + GCP push
- 작업: `--universe live` 실행, 결과 분석, git push
- 변경 사항: `docs/checkpoint.md`, `docs/work-log.md` 갱신
- 결과 요약:
  - 기준선 승률 61.0% (운영 101종목) vs 53.6% (전체 350종목)
  - `has_pattern` 운영 유니버스에서 3위 (lift 1.108) — 전체 기준보다 큰 폭 상승
  - `ichimoku_cloud_support` 세션 16에서 하향했으나 운영서 lift 1.064로 유의미
  - `volume_surge` / `ichimoku_cloud_break` lift < 1 → 대형주에서 역효과
- 관련 파일: `backtest/validator.py`, `config/scoring/v1_baseline/technical.yaml`
- 메모:
  - git push 완료 (843b29c), VM deploy는 SSH 접속 후 수동 실행 필요
- 다음 아이디어: `technical.yaml` 가중치 재조정 후 validator 재실행으로 개선폭 검증

---

## 2026-05-07 세션 15 — DuckDB 인프라 + 백테스트 라벨러
- 작업: DuckDB 마이그레이션 + 라벨러 구현 + 기준선 확인
- 변경 사항:
  - `requirements.txt`: `duckdb>=0.10.0` 추가
  - `data/db.py` 신규: DuckDB 연결 + 5개 테이블 스키마. `backtest_labels` PK에 `hold_days`, `target_return` 포함
  - `data/store.py` 전면 교체: Parquet → DuckDB. `migrate_parquet_to_duckdb()` 추가
  - `backtest/labeler.py` 신규: `label_one()`, `label_batch()`, `save_labels()`, CLI
- 마이그레이션 결과: 일봉 359종목 210,297행 / 60분봉 356종목 123,699행 → `data/stock.duckdb`
- 백테스트 기준선 (2026-04-01 기준, 전체 359종목):
  - 3일 3%: 승률 29.9%, 손절 생존 후 45.3%, 평균 수익 -2.79%
  - 5일 5%: 승률 31.6%, 손절 생존 후 37.0%, 평균 수익 +0.76%
- 메모: 손절 생존 필터(max_drawdown >= -5%)가 승률 차이를 크게 만듦. 리스크 관리가 핵심.
- 다음 아이디어: feature별 lift 분석으로 volume 중복 문제 정량화

---

## 2026-05-07 세션 14 — 시스템 전면 재설계
- 작업: 설계 방향 전환 논의 + 새 아키텍처 설계 확정
- 변경 사항:
  - `.claude/settings.json` 신규: `plansDirectory: ".claude/plans"` (plan 파일 프로젝트 로컬 저장)
  - `~/.claude/plans/feature-streamed-reddy.md`: 전면 재설계 플랜 작성
- 핵심 결정:
  - 기존 설계문서.md / 전략서.md 폐기
  - 설계 원칙 전환: 규칙 먼저 → 데이터 먼저 (백테스트 검증 후 규칙 결정)
  - DB 전환: Parquet 분산 저장 → DuckDB 단일 파일 (GCP e2-micro 메모리 친화적)
  - 백테스트 인프라를 Phase 1으로 우선 구현
  - feature 개선 가설(계층형 volume tier, 가격방향, bull_candle 등)은 백테스트 검증 후 반영
- 데이터 소스 전략:
  - KIS API: 당일 분봉만 → 60분 리샘플 (현재 운영 중)
  - pykrx: 일봉 13개월 → 즉시 백테스트 가능
  - Kiwoom OpenAPI+: 중장기, ~160일 분봉 소급 (KOAPY wrapper)
  - 분봉 장기 히스토리 무료 획득 불가 → 매일 누적이 최선
- 관련 파일: `.claude/settings.json`, `~/.claude/plans/feature-streamed-reddy.md`
- 다음 액션: DuckDB 인프라 구현 → backtest/labeler.py → 현재 신호 승률 확인

---

## 2026-05-07 세션 13 — KIS API 연동 + 파이프라인 버그 수정
- 작업: KIS API 연동, 파이프라인 버그 수정, 전체 동작 확인
- 변경 사항:
  - `data/kis_client.py` 신규: KIS 당일 1분봉 수집(페이지네이션) → 60분 리샘플, 토큰 파일 캐시
  - `data/store.py` HourlyStore 교체: KIS primary + yfinance fallback 구조
  - `agents/orchestrator.py` 버그 수정: `df_daily or tech_agent.last_df` → `df_daily if df_daily is not None else tech_agent.last_df` (DataFrame bool 평가 ValueError)
  - `.gitignore`에 `.env` 추가
  - `scripts/test_kis.py` 신규
- 관련 파일: `data/kis_client.py`, `data/store.py`, `agents/orchestrator.py`
- 메모:
  - orchestrator 버그가 101종목 전체 예외의 원인이었음 (세션 12까지 미발견)
  - KIS 1분봉 391건 → 60분봉 7건 정상 수집 확인 (현대차 기준, 09:00~15:30)
  - KIS 500 에러는 페이지네이션 일부 종목에서 발생, yfinance fallback으로 처리됨
  - 파이프라인 결과: KOSPI=bull / KOSDAQ=sideways / 66종목 매수신호
  - ReportAgent "메시지 15723번 발송 시도" — 숫자 의미 미확인
- 다음 아이디어: 거래량 feature 가격 방향 추가, 중복 정리 후 재설계

---

## 2026-05-07 세션 12 — 거래량 분석 60분봉 기반 교체
- 작업: `VolumeAnalysisAgent` 일봉 binary flag 9개 → 60분봉 종목별 percentile 방식으로 완전 교체
- 변경 사항:
  - `agents/volume_analysis.py` 전체 재작성
    - `VolumeProfileCache` 클래스 추가: 종목별 시간대별 P90/P95/P99 산출, `data/volume_profile_cache.json`에 당일 1회 캐시
    - 신규 5개 feature 함수: `_same_time_vol_ratio`, `_volume_zscore_60m`, `_relative_turnover_60m`, `_vwap_above_60m`, `_obv_slope_60m`
    - `_foreign_inst_flow` (pykrx 일봉 기반) 유지 — 스마트머니 중기 신호 보존
    - `run()` 시그니처 변경: `df_60m` 파라미터 추가. `df_60m=None`이면 `volume_score=0` 반환
  - `config/scoring/v1_baseline/volume.yaml`: 9개 rule → 6개 rule, 최대 점수 24 → 20점
  - `config/scoring/v1_baseline/buy_grade.yaml`: volume_min 재조정 (S: 12→10, A: 8→7, B: 5→4)
  - `agents/orchestrator.py`: `vol_agent.run(df_60m=df_60m)` 전달 추가 (1줄 변경)
  - `config.py`: `VOLUME_PROFILE_CACHE = "data/volume_profile_cache.json"` 추가
- 핵심 설계:
  - 종목마다 자신의 60분봉 히스토리(~60일)로 고유 임계값 산출 → 대형주/소형주 동일 기준 적용 방지
  - `hourly_vol_ratio_p95`: 마지막 거래일 내 어느 봉이든 동일 시간대 P95 초과 시 True (5pt, 핵심 신호)
  - `volume_zscore_high`: 과거 분포 대비 최대 거래량 z-score ≥ 2.0 (4pt)
  - `relative_turnover_high`: 당일 거래대금 합산 과거 P80 이상 (3pt)
  - `vwap_above_60m`: 장 마감 종가 > 당일 VWAP (2pt)
  - `obv_slope_up_60m`: 최근 20개 60분봉 OBV 기울기 양수 (3pt)
  - `foreign_inst_buy`: pykrx 5거래일 순매수 (3pt)
- 메모:
  - yfinance 데이터 없는 종목은 volume_score=0 → 매수 신호 없음 (의도된 동작)
  - OBV slope는 MVP 단순화(slope > 0). 추후 과거 slope 분포 P70 기반으로 고도화 가능
- 다음 아이디어: `python main.py --run-now`으로 60분봉 교체 후 첫 전체 파이프라인 검증
