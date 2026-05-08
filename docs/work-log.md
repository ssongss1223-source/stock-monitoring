# Work Log

## 목적
이 파일은 세션별 작업 이력과 상세 기록을 누적해서 저장한다.

`docs/checkpoint.md`에 남기에는 길거나 상세한 정보,
예를 들어 디버깅 메모, 시도한 방법, 실패 원인, 세션별 진행 내용 등을 기록하는 용도로 사용한다.

## 작성 원칙
- 상세 기록은 이 파일에 누적한다.
- 너무 장황한 일지보다는, 실제로 다음 작업에 도움이 되는 정보를 남긴다.
- 긴 설명이 필요하면 항목별 bullet로 정리한다.
- `checkpoint.md`에는 요약만 남기고, 상세 내용은 이 파일로 분리한다.

## 기록 템플릿

## YYYY-MM-DD HH:mm
- 작업:
- 변경 사항:
- 관련 파일:
- 메모:
- 다음 아이디어:

## 예시

## 2026-05-06 14:10
- 작업: 로그인 리다이렉트 문제 원인 분석
- 변경 사항: 문제를 재현했고, 인증 초기화 흐름을 추적함
- 관련 파일: `src/auth/useAuth.ts`, `src/app/router.tsx`
- 메모: 사용자 상태가 안정되기 전에 redirect가 먼저 발생함

---

## 2026-05-09 (세션 20)
- 작업: store.py 커밋 + VM deploy
- 변경 사항:
  - `data/store.py` + docs 커밋/push (commit `9215818`)
  - `instance-20260505-092414` deploy 완료 — 세션 18·19 변경사항 반영, 서비스 재시작
- 관련 파일: `data/store.py`, `deploy/update.sh`
- 메모: deploy 후 `stock-monitor.service` active (running) 확인

---

## 2026-05-08 (세션 19)
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
- 다음 아이디어: 초기화 순서를 조정한 뒤 로그인 흐름 재검증

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

## 2026-05-07 세션 13
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
