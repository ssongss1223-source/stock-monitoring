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
