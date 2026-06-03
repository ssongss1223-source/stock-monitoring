# SMA 백테스터 코어 재설계 — 설계 문서

- 작성일: 2026-06-03
- 상태: 설계 확정 (사용자 검토 대기)
- 선행 문서: `docs/superpowers/specs/2026-06-03-sma-backtest-design.md` (최초 MVP 설계)

---

## 배경 — 왜 재설계인가

MVP 백테스트(23종목, 624조합)를 실행하고 결과를 분석하던 중, **P&L 엔진의 회계가 근본적으로 깨져 있음**을 발견했다.

### 발견된 치명적 버그 2개

**버그 #1 — 파셜셀 회계 오류 + MDD 미실현 미반영 (치명적)**

`backtest/sma_backtester.py`의 자본 추적이 잘못됨:
- `_partial_sell`에서 `capital *= (proceeds/cost)` — 보유분의 일부만 팔아도 **전체 자본을 그 수익률 배수로 곱함**. +50%에서 10%만 팔아도 전체 자본이 1.5배가 됨.
- `equity_curve`가 보유 중 미실현 손익을 반영하지 않음 (`capital`은 청산/익절 시에만 변동) → **MDD가 실현 손익 기준으로만 계산되어 심각하게 과소평가**.

**버그 #2 — 가짜 walk-forward (방법론 오류)**

`sma_optimizer.py`의 `run_grid_search`는 `train_slice`를 전혀 쓰지 않고, `find_best_params`는 **전 윈도우 평균 Calmar가 최고인 파라미터를 선택** → 미래 정보로 파라미터를 고르는 셈. 진짜 walk-forward가 아님.

### 버그 영향 측정 결과 (올바른 회계 엔진으로 재측정)

| 종목 | 현재 Calmar | 수정 Calmar | 현재 CAGR | 수정 CAGR | 현재 MDD | 수정 MDD |
|------|-----------|-----------|----------|----------|---------|---------|
| 삼성전자 | 1.87 | **0.16** | 59.6% | 7.4% | -31.9% | -47.5% |
| 삼성SDI | 2.62 | **0.14** | 94.6% | 7.6% | -36.1% | -53.7% |
| LS ELECTRIC | 2.36 | **0.24** | 136.6% | 15.2% | -57.9% | -64.1% |
| 한미반도체 | 8.58 | **0.57** | 276.8% | 22.7% | -32.3% | -39.8% |
| 미래에셋증권 | 2.05 | **0.15** | 104.5% | 8.7% | -51.0% | -59.5% |

**Calmar이 약 10~18배 부풀려져 있었다.** 올바르게 재면 전 종목 Calmar < 1.0 → 현재 백테스트 결과는 폐기 대상.

또한 분석 중 **눌림목 진입이 23종목 전부 0건**임을 확인. 원인은 구조적: SMA 위로 올라오는 순간 breakout으로 진입 → 이후 포지션 보유 상태라 pullback 신호가 발동할 기회가 없음. `lookback_days`/`drawdown_pct` 파라미터가 결과에 무영향이었던 이유.

---

## 목표 (Phase 1 범위)

올바른 회계 엔진으로 **전략의 진짜 성과를 재측정**한다. 그 결과를 보고 이후 단계(청산방식 비교, 피라미딩 등) 투자 여부를 결정한다 — 단계적 접근.

Phase 1에 포함:
1. **올바른 포트폴리오 회계 엔진** — 현금 + 미실현 평가액 마크투마켓, 파셜셀 정상 회계, 정확한 MDD
2. **2계좌 분리 모델** — SMA breakout 계좌 / 눌림목 계좌를 독립 운용 (눌림목이 드디어 발동 가능해짐 + 분리 표기 동시 해결)
3. **정확한 지표** — MDD/CAGR/Calmar, vs_buyhold(구간 내 상대), 텔레그램 표시 수정
4. **진짜 walk-forward** — train에서 파라미터 선택 → 다음 test 구간에서 검증

Phase 1에서 **제외**(이후 단계로):
- 청산 방식 3종(래더/트레일링스탑/순수추세) head-to-head 비교 → Phase 2
- 피라미딩(한 포지션에 lot 섞기) → 재측정 결과 보고 결정
- Coarse→Fine SMA 그리드 탐색 → Phase 2

---

## 핵심 설계 결정 (브레인스토밍 확정)

| 결정 | 선택 |
|------|------|
| 범위 | 엔진부터 단계적 (회계+지표+진짜WF만, 재측정 우선) |
| 자본 모델 | All-in (진입 시 가용 현금 100%, 청산 시 전액 현금화) |
| 전략 구조 | SMA 계좌 / 눌림목 계좌 **분리**, 각자 올인 |
| 종합 합산 | 50/50 고정 분배 포트폴리오 |
| 성공 기준 | 같은 구간 buy&hold 대비 **Calmar 우위** |
| 코드 구조 | 접근법 A — Account 프리미티브 + 전략 분리 |

---

## 아키텍처 (접근법 A)

### 컴포넌트

**1. `Account` 프리미티브 — 올인 단일 전략 백테스트**

하나의 자본 풀로 단일 전략을 올인 운용하는 재사용 가능 엔진.

- 입력: `close`(종가 시리즈), `entry_signals`(진입 시점), `exit_rules`(청산 규칙), `initial_capital`
- 회계: `cash` + `shares` 추적, 매 거래일 `equity = cash + shares × price` (마크투마켓)
- 진입: 올인 (가용 현금 전액으로 `shares = cash / (price × (1+commission))`)
- 청산: 전량 매도 시 `cash += shares × price × (1−commission)`
- 부분 익절: 보유분의 fraction만 매도 → `cash += qty × price × (1−commission)`, equity는 자연히 정확
- 출력: `equity_curve`(MTM), `trades`(entry/exit/pnl), `metrics`

**책임: 올바른 회계 하나만.** 진입/청산 규칙은 주입받음.

**2. 전략 정의 — 진입/청산 규칙 2종**

- `sma_breakout_strategy`: 진입 = SMA 상향 돌파, 청산 = SMA 하향 이탈 + 기존 익절 래더
- `pullback_strategy`: 진입 = (SMA 위 + 고점 대비 −X% 눌림), 청산 = −8% 손절 or SMA 이탈 + 익절 래더

기존 `sma_signal.py`(시그널 계산)는 검증돼 있으므로 그대로 재사용.

> Phase 1에서는 기존 익절 래더(+10/25/50%→10%, +100/200%→50%)를 그대로 유지해 apples-to-apples 재측정. 래더 자체의 적정성은 Phase 2 청산방식 비교에서 검증.

**3. 종합 합산 — 50/50 포트폴리오**

- SMA 계좌 50% + 눌림목 계좌 50% 초기 자본 배분
- 각 계좌 독립 운용, 두 `equity_curve`를 0.5씩 가중 합성 → 종합 equity_curve
- 종합 지표는 합성 곡선에서 계산

**4. 진짜 Walk-forward 옵티마이저**

- 윈도우: train 5년 / test 2년 / step 1년 (기존 상수 재사용)
- 각 윈도우: **train 구간에서 Calmar 최고 파라미터 선택** → 그 파라미터를 **test 구간에 적용** → test 성과만 기록
- 최종 성과 = 모든 test 구간 성과의 집계 (out-of-sample only)
- 데이터 7년 미만 종목: in-sample 폴백 (명시적으로 라벨)

**5. 지표 모듈**

- Calmar = CAGR / |MDD|, MDD는 MTM equity 기준
- vs_buyhold = **같은 구간** 전략 수익 − buy&hold 수익 (전체 복리 누적 금지)
- win_rate, profit_factor, EV: trades 기반
- 텔레그램 표시는 walk-forward 집계값 사용 (전체 기간 in-sample 재적용 금지)

**6. 리포터 — 3단 표**

종목별로 SMA단독 / 눌림목단독 / 종합(50/50) 3행 + buy&hold 대비 우위 여부.

### 데이터 흐름

```
ohlcv_daily(close)
   → sma_signal (진입 시그널 계산, 기존 재사용)
   → WF 옵티마이저 (train에서 파라미터 선택)
       → Account.run(SMA 전략)     → equity_A, metrics_A
       → Account.run(눌림목 전략)  → equity_B, metrics_B
       → combine 50/50            → equity_C, metrics_C
   → 지표 계산 → DB 저장 → 텔레그램 3단 리포트
```

### DB 스키마

기존 `sma_backtest_results`는 단일 포지션 가정이라 부적합. 새 결과 구조 필요:
- 계좌 구분 컬럼 추가 (`account`: 'sma' | 'pullback' | 'combined') **또는** 신규 테이블
- 결정은 구현 플랜에서 (data/db.py `_MIGRATIONS` + 명시적 컬럼 INSERT 규칙 준수)

---

## 테스트 전략

- `Account` 프리미티브 단위 테스트: 알려진 가격 시퀀스로 회계 정확성 검증
  - 진입→상승→청산 한 사이클의 equity/MDD가 손으로 계산한 값과 일치
  - 부분 익절 시 전체 자본이 비례적으로만 증가 (버그 #1 재발 방지 회귀 테스트)
  - 미실현 손실 구간에서 MDD가 정확히 반영 (버그 #1 재발 방지)
- WF 옵티마이저 테스트: train 선택 파라미터가 test에만 적용되는지 (미래정보 차단 검증)
- 통합: 1개 종목 end-to-end → 3단 표 산출

---

## 검증 기준 (Phase 1 완료 조건)

1. `Account` 회계 단위 테스트 전부 통과 (버그 #1 회귀 방지 포함)
2. WF가 train→test 분리를 지키는지 테스트 통과 (버그 #2 차단)
3. 23종목 재측정 → SMA단독/눌림목단독/종합 3단 표 + buy&hold 대비 우위 여부 산출
4. 텔레그램/DB 지표가 walk-forward 집계값(in-sample 재적용 아님)

---

## 미해결 / 구현 플랜에서 결정

- DB: 신규 테이블 vs `account` 컬럼 추가 — 마이그레이션 영향 검토 후
- 눌림목 계좌 진입 시 "SMA 위" 판정 기준 (어떤 SMA? breakout과 동일 period?)
- 종합 50/50에서 한 계좌가 비어있을 때 그 절반은 현금 대기 (수익 0%) 처리 확인

---

## 이후 단계 (참고, 본 스펙 범위 외)

- **Phase 2**: 청산 방식 3종 비교 (래더 vs 트레일링스탑 vs 순수추세) + Coarse→Fine SMA 그리드
- **Phase 3**: 피라미딩 (재측정 결과가 유효할 때만)
- **Phase 4**: ML 신호 + 눌림목 타이밍 결합 (트리거 메시지 완성형)
