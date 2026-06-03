# Work Log — 2026-06

---

## 2026-06-03 세션 71 — SMA 백테스팅 MVP 전체 구현 + 실행 시작

- 작업:
  - subagent-driven-development로 Task 1~7 전부 구현 완료
  - 텔레그램 "SMA 백테스트" 그룹 생성 + 봇 연결 + VM 환경변수 설정
  - VM에서 백테스트 실행 시작 (screen sma_backtest)
- 변경 사항:
  - `b2072d3` `backtest/sma_config.py` — 23종목, 624 파라미터 조합
  - `43b437a` `backtest/sma_signal.py` + 테스트 — sma_breakout/pullback 판별
  - `24fb8f5` `backtest/sma_backtester.py` + 테스트 — 분할/일괄 진입, 손절/익절
  - `48be286` `backtest/sma_optimizer.py` + 테스트 — Walk-forward + 인샘플 fallback
  - `f873747` `data/db.py` — sma_backtest_results 테이블 VM 적용 (17컬럼)
  - `ccc102d` `backtest/sma_reporter.py` — DuckDB 저장 + 텔레그램 리포트
  - `3f5f234` `scripts/run_sma_backtest.py` — CLI 통합 실행
  - `8e8151b` TELEGRAM_BOT_TOKEN 환경변수명 수정
- 메모:
  - 전체 테스트 37/37 PASS
  - 텔레그램 Chat ID: -5119708094 ("SMA 백테스트" 그룹)
  - VM .env에 TELEGRAM_BACKTEST_CHAT_ID 추가 완료
  - 오늘 휴일로 DuckDB 락 없음 → 즉시 실행 가능했음
  - SMA 21.0년 데이터 (삼성전자), Walk-forward=True 확인
- 다음 아이디어:
  - 텔레그램으로 결과 수신 후 Calmar 기준으로 전략 유효성 평가
  - P5 평가: 6/6 이후 실행

---

## 2026-06-03 세션 70 — SMA 백테스팅 설계 확정 + 구현 계획 완성

- 작업:
  - 브레인스토밍 완료: 전략 로직 전체 확정, 23종목 선정
  - 설계 문서 작성: `docs/superpowers/specs/2026-06-03-sma-backtest-design.md`
  - 구현 계획 작성: `docs/superpowers/plans/2026-06-03-sma-backtest-core.md` (7개 태스크)
- 변경 사항:
  - `84ec20c` SMA 구현 계획 (Phase 1: 코어 + Telegram)
  - `e9f9e3f` SMA 설계 문서 (최종 확정)
- 메모:
  - 두 가지 진입 방식 확정: SMA 최초 진입(3회 분할) + 눌림목 진입(1회 + -8% 손절)
  - 익절: 아기티큐 방식 (+10/25/50%→10%, +100/200%→50%)
  - 파라미터: SMA×confirm×lookback×drawdown = 624조합/종목
  - run_daily/run_collect 영향도 없음 확인 (완전 분리)
  - DuckDB 락 주의: 18:30 KST 이후 실행 권장
  - Phase 2 (대시보드/API)는 별도 계획
- 다음 아이디어:
  - 새 세션에서 subagent-driven-development로 Task 1부터 순서대로 실행
  - 텔레그램 새 채널 생성 + TELEGRAM_BACKTEST_CHAT_ID 환경변수 설정 선행 필요

---

## 2026-06-03 세션 69 — SMA 백테스팅 플랫폼 설계 + 20년치 OHLCV 백필

- 작업:
  - TQQQ 200일선 매매법(아기티큐) 분석 및 한국 주식 적용 방향 설계
  - 20년치 OHLCV 백필 스크립트 작성 및 실행 완료
  - SMA+RSI 백테스팅 플랫폼 브레인스토밍 B안 확정
- 변경 사항:
  - `73d32cb` `scripts/backfill_historical_ohlcv.py` 신규 (yfinance, INSERT OR IGNORE)
  - ohlcv_daily: 232,637행 → 1,540,635행 (+130만행, 2005~2026)
- 메모:
  - pykrx는 2015년 이전 데이터 지원 안 함 → yfinance(.KS/.KQ)로 해결
  - 역사 데이터 없는 47종목 = 최근 상장 KOSDAQ 소형주 (정상)
  - 4,685행/종목 = 일봉 맞음 (18.7년 × 252거래일 ≈ 4,712)
  - SMA 플랫폼은 ML 파이프라인과 독립 운영 결정 (혼선 방지)
  - 최적화 1순위: Calmar Ratio (CAGR÷MDD) — 손익비/부러질 위험 중심
  - Sharpe는 상승 변동도 페널티 → SMA 비대칭 전략에 부적합
- 다음 아이디어:
  - 관심종목 20개 확정 (개인 10개 + 시총 상위 N개)
  - writing-plans → SMA+RSI Walk-forward 백테스팅 구현
  - 텔레그램 새 채널 + Streamlit 대시보드

---

## 2026-06-03 세션 68 — 텔레그램 검증 메시지 구현 + 신호 후행성 브레인스토밍

- 작업:
  - 텔레그램 데이터 품질 검증 메시지 구현 및 VM 배포 (`4d9e6ee`)
  - 오늘(6/2) 데이터 전체 정상 확인 (351종목, OK 17 / WARN 11 / FAIL 0)
  - 신호 후행성 문제 브레인스토밍 시작
- 변경 사항:
  - `scripts/verify_data_quality.py`: `run_checks()` 추가, `check_label_coverage` 거래일 cutoff 적용
  - `agents/report.py`: `_build_verify_message`, `send_verify_report` 추가
  - `agents/orchestrator.py`: `run_collect` 끝에 verify → 텔레그램 자동 발송
- 메모:
  - run_daily 6/2 정상: 62종목 신호, 351종목 ML 추론, universe_predictions 6318건
  - 신호 후행성 근본 원인: 모든 피처가 모멘텀 추종 편향 (volume_surge, price_momentum 등)
  - 삼성전기·LG이노텍: 10%+ 급등 후 추천 → 다음날 마이너스 — 전형적 후행 패턴
  - ohlcv_daily 실제 범위: 2023-11-21 ~ 2026-06-02 (약 2.5년, 614거래일)
  - 10년치 확장: FinanceDataReader로 가능 (pykrx 대비 빠름)
- 다음 아이디어:
  - 접근법 1: 최근 3일 내 +7% 이상 급등 종목 하드 필터 추가 (빠른 수술)
  - 접근법 2: 눌림목 감지 레이어 (MA5>MA20>MA60 + 고점 대비 -4~12% + 거래량 감소)
  - 접근법 3: 10년치 백필 + 종목별 지표 최적화 (장기 프로젝트)
  - P5 평가: 6/6 이후 `--from-date 2026-05-22`

---

## 2026-06-01 세션 67 — 전체 아키텍처 검증 + cutoff 버그 픽스

- 작업: 재설계 후 첫 배치 전면 검증, 라벨 cutoff 버그 발견 및 픽스
- 변경 사항:
  - `a4aed10` `agents/orchestrator.py` `_auto_label_universe_unlabeled()` cutoff 달력일(15일)→거래일(10일) 기준으로 수정
- 메모:
  - run_collect(16:00) / run_daily(17:30) 모두 정상 — 첫 자동 실행 성공
  - verify_data_quality: OK 22 / WARN 2 (XGB 4개·LGBM 2개 AUC 0.55 미달) / FAIL 0
  - P5 평가 현재 불가: universe_daily 라벨 5/15까지만 채워짐
    - cutoff 버그 픽스로 내일부터 5/18 라벨부터 순차 채워짐
    - 5/18~5/22는 5/23 공휴일로 거래일 9개 → 6/2 데이터 수집 후 채워짐
    - P5 평가 가능 시점: 6/6 이후 (`--from-date 2026-05-22`)
  - open=0.0 데이터 문제: 001340 등 일부 종목 영구 미라벨 (P6+ 때 처리)
  - signal_xgb_probs 5/15~5/21 구버전 라벨: P5 평가 시 `--from-date 2026-05-22` 사용
  - 분봉 수집: 351종목 전종목 최소 1건 이상 수집, 타임아웃 2건은 개별 시간대만
- 다음 아이디어:
  - 텔레그램 검증 메시지 구현 (설계 확정: Option A, 별도 메시지, 4섹션)
  - verify_data_quality.py LABEL_CUTOFF_DAYS=15 → 거래일 기준으로 수정 (검증 메시지 구현 시 포함)
