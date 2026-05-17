# 국내 주식 신호 알림 시스템 — 시스템 개요

> Last Updated: 2026-05-17

---

## 1. 서비스 목적

한국 주식시장(KOSPI200 + KOSDAQ150, ~351종목)을 매일 자동 분석하여, **n일 안에 m% 이상 상승할 가능성이 높은 종목**을 텔레그램으로 알림.

- 거래일 마다 장 마감 후 데이터 수집 → 다음 날 장 시작 전 신호 발송
- 규칙 기반 기술적 분석 + ML 앙상블 추론의 2단계 필터링
- S등급 + 손익비 2.0 이상 + ML 확률 기준 상위 10종목만 발송

---

## 2. 사용 데이터

| 데이터 | 출처 | 주기 | 용도 |
|--------|------|------|------|
| 일봉 OHLCV (종가/거래량/외국인순매수 등) | pykrx | 일 1회 | 기술적 분석, ML 피처 |
| 60분봉 OHLCV | yfinance | 일 1회 | 거래량 프로파일 분석 |
| KOSPI/KOSDAQ 지수 | pykrx | 일 1회 | 장세 판단, 시장 피처 |
| 글로벌 매크로 (S&P500/NASDAQ/USDKRW/10Y/WTI/SOX) | yfinance | 일 1회 | 장세 판단 (macro_daily 테이블) |
| 종목 마스터 (이름/섹터/상장일) | pykrx | 최초 1회 | 유니버스 구성 |
| 보유 종목 (portfolio.json) | 수동 입력 | 필요 시 | 매도신호 생성 |

---

## 3. 전체 아키텍쳐

```
┌─────────────────────────────────────────────────────────────┐
│                     GCP VM (us-central1-a)                  │
│                                                             │
│  ┌──────────────┐    ┌──────────────────────────────────┐   │
│  │  Scheduler   │    │           main.py                │   │
│  │  (APSched)   │───▶│  run_collect() / run_daily()     │   │
│  └──────────────┘    └────────────┬─────────────────────┘   │
│                                   │                         │
│                       ┌───────────▼───────────┐            │
│                       │     Orchestrator       │            │
│                       └───────────┬───────────┘            │
│                                   │                         │
│          ┌────────────────────────┼────────────────────┐    │
│          ▼                        ▼                    ▼    │
│  ┌──────────────┐    ┌─────────────────────┐  ┌──────────┐ │
│  │MarketFilter  │    │ 종목별 분석 (×351)  │  │  Sell    │ │
│  │Agent         │    │ Technical / Volume  │  │  Signal  │ │
│  └──────────────┘    │ Pattern / BuySignal │  │  Agent   │ │
│                      └──────────┬──────────┘  └──────────┘ │
│                                 ▼                           │
│                      ┌─────────────────────┐               │
│                      │   ML Scorer         │               │
│                      │ (XGB+LGBM+ET voting)│               │
│                      └──────────┬──────────┘               │
│                                 ▼                           │
│                      ┌─────────────────────┐               │
│                      │   DuckDB 저장        │               │
│                      │ signal_history       │               │
│                      │ signal_xgb_probs     │               │
│                      └──────────┬──────────┘               │
│                                 ▼                           │
│                      ┌─────────────────────┐               │
│                      │   ReportAgent        │               │
│                      │ (4그룹×top3=12종목) │               │
│                      └──────────┬──────────┘               │
└─────────────────────────────────┼───────────────────────────┘
                                  ▼
                        ┌─────────────────┐
                        │  텔레그램 Bot    │
                        │  (사용자 수신)   │
                        └─────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                  Claude Code (로컬 Windows)                  │
│                                                             │
│  MCP 서버 3개 (Claude Code 대화 중 직접 사용)               │
│  ├─ mcp/mcp_duckdb.py    — DB 직접 쿼리                     │
│  ├─ mcp/mcp_vm_ssh.py    — VM SSH 실행/로그/서비스 제어     │
│  └─ mcp/mcp_telegram.py  — 텔레그램 메시지 발송             │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 일일 파이프라인 작업순서

### 4-1. 데이터 수집 (07:00 UTC = 16:00 KST, 장 마감 직후)

```
run_collect()
 ├─ UniverseManager → 351종목 목록 결정
 ├─ [종목별 병렬] OhlcvStore.fetch_and_update_daily()   → 일봉 DB 저장
 │                HourlyStore.fetch_and_update_hourly()  → 60분봉 DB 저장
 ├─ MarketIndexStore.fetch_and_update()                 → 지수 DB 저장
 └─ ReportAgent.send_collect_report()                   → 수집 결과 텔레그램
```

### 4-2. 신호 분석 (21:00 UTC = 06:00 KST, 다음 날 장 시작 전)

```
run_daily() → _pipeline()
 │
 ├─ [병렬 시작]
 │   ├─ MarketFilterAgent.run()  → KOSPI/KOSDAQ 장세 (bull/sideways/bear)
 │   └─ SellSignalAgent.run()   → 보유 종목 매도신호 (장세 무관)
 │
 ├─ UniverseManager.get_universe() → 분석 대상 종목 확정
 │
 ├─ [종목별 병렬, Semaphore=2]  _analyze_stock() × 351종목
 │   ├─ OhlcvStore.load_daily()      ← DB에서만 읽기 (네트워크 없음)
 │   ├─ HourlyStore.load_hourly()    ← DB에서만 읽기
 │   ├─ TechnicalAnalysisAgent  → 이평선/RSI/BB/일목/지지저항
 │   ├─ VolumeAnalysisAgent     → 거래량 프로파일/이상거래량
 │   ├─ StockPatternLearner     → 컵핸들/삼각수렴/BB수축/돌파박스
 │   └─ BuySignalAgent          → 종합 등급 (S/A/B/NONE)
 │
 ├─ score_all_labels()          → XGB+LGBM+ET soft voting (9개 라벨)
 │   └─ 종목별 best_label 선택  → BuySignal.best_label / xgb_prob 업데이트
 ├─ signal_history 저장
 ├─ signal_xgb_probs 저장
 │
 └─ ReportAgent.send()          → S등급 + RR≥2.0, EV/day 4그룹×top3 텔레그램 발송
```

### 4-3. 수동/테스트 실행

```
python main.py --run-now       # 즉시 분석 실행 (거래일 체크 우회, ~21분)
python main.py --resend-last   # 마지막 결과 재발송 (~6초, 형식 테스트용)
```

---

## 5. 시장국면 판단 로직

`agents/market_filter.py` + `config/scoring/v1_baseline/market.yaml`

### 5-1. 플래그 → 점수 계산

KOSPI / KOSDAQ 각각 독립적으로 판단. 10개 플래그 합산, 최대 13점.

| 플래그 | 점수 | 데이터 소스 | 계산 방식 |
|--------|------|-------------|-----------|
| `index_6m_uptrend` | +2 | market_index DB | 현재가 > 126거래일 전 종가 |
| `index_3m_uptrend` | +2 | market_index DB | 현재가 > 63거래일 전 종가 |
| `index_above_60ma` | +1 | market_index DB | 현재가 > 60일 이동평균 |
| `index_above_120ma` | +1 | market_index DB | 현재가 > 120일 이동평균 |
| `foreign_futures_net_buy` | +2 | ohlcv_daily DB | 전종목 foreign_net 합산 > 0 |
| `sector_strong_ratio_70pct` | +1 | pykrx (현재 차단) | 업종 지수 70% 이상 1개월 상승 |
| `sp500_above_20ma` | +1 | macro_daily DB | S&P500 현재가 > 20일 이동평균 |
| `usdkrw_below_ma20` | +1 | macro_daily DB | USD/KRW < 20일 이동평균 (원화 강세) |
| `sox_uptrend` | +1 | macro_daily DB | SOX 현재가 > 21거래일 전 (반도체 강세) |
| `kospi_rv20_low` | +1 | market_index DB | 20일 실현변동성 ≤ 20% (저변동성) |

### 5-2. 점수 → 국면 분류

```
점수 ≥ 10  →  BULL     (매수 등급 기준 완화)
점수  7~9  →  SIDEWAYS (기준 유지)
점수 ≤  6  →  BEAR     (기준 강화, S등급 불가)
```

### 5-3. 국면별 매수 등급 임계값 (`config/scoring/v1_baseline/buy_grade.yaml`)

| 등급 | BULL | SIDEWAYS | BEAR |
|------|------|----------|------|
| S | trend≥12, vol≥13, total≥28 | trend≥13, vol≥14, total≥31 | **불가** |
| A | trend≥9, vol≥10, total≥23 | trend≥10, vol≥11, total≥26 | trend≥13, vol≥13, total≥30 |
| B | trend≥6, vol≥7, total≥17 | trend≥7, vol≥8, total≥20 | trend≥10, vol≥12, total≥27 |

> 텔레그램 발송 기준이 S등급이므로, BEAR 국면에서는 알림이 발송되지 않는다.

### 5-4. 매크로 데이터 수집 (`data/store.py` — `MacroStore`)

```
MacroStore.fetch_and_update()
  yfinance 티커: ^GSPC(S&P500), ^IXIC(NASDAQ), USDKRW=X,
                 ^TNX(10Y금리), CL=F(WTI), ^SOX(SOX)
  → macro_daily 테이블에 일 1회 저장 (수집 파이프라인 07:00 UTC)
```

---

## 6. 시스템 구성

| 구성요소 | 상세 |
|----------|------|
| **운영 환경** | GCP VM `instance-20260505-092414` (us-central1-a, e2-micro) |
| **런타임** | Python 3.10, asyncio 기반 |
| **서비스** | systemd `stock-monitor.service` (User=stock) |
| **DB** | DuckDB (`/opt/stock-monitor/data/stock.duckdb`) |
| **모델 파일** | `/opt/stock-monitor/data/models/` (XGB `.json`, LGBM `.txt`, ET `.pkl`) |
| **알림** | Telegram Bot API |
| **스케줄러** | APScheduler (수집 07:00 UTC / 분석 21:00 UTC) |
| **유니버스** | KOSPI200 + KOSDAQ150 = 351종목 (`kospi200_daq150` 모드) |
| **동시성** | 종목 분석 Semaphore=2 (KRX rate-limit 대응) |
| **개발 환경** | Windows 10, Claude Code |
| **MCP 서버** | stock-db (DuckDB 쿼리), vm-ssh (SSH 제어), telegram (발송) |
| **Stop hook** | `scripts/auto_checkpoint.py` (세션 종료 시 checkpoint.md 갱신) |

---

## 7. 텔레그램 발송 기준 (필터링 + 정렬)

```
전체 351종목 분석
    │
    ▼ BuySignalAgent: 등급 판정
S / A / B 등급 (NONE은 탈락)
    │
    ▼ 필터 1: grade == "S"
    │
    ▼ 필터 2: risk_reward >= 2.0 (손익비)
    │
    ▼ EV/day 계산 (종목 × 라벨 전체 대상)
       flatten: S등급 종목 × 9개 라벨 → (종목, 라벨) 쌍 전체
       EV/day = [prob × gain_pct - (1-prob) × loss_pct] / days
         · prob    : 해당 (종목, 라벨) ML 확률 (label_probs 필드)
         · gain_pct: 라벨 목표 수익률 (3%/5%/10%)
         · loss_pct: ATR 기반 동적 손절 비율 = ATR × 2 / 현재가
         · days    : 라벨 기간 (3/5/10일)
    │
    ▼ 4그룹 분류 (그룹별 top 3 = 총 12종목)
       ┌─────────────────────────────────────────────┐
       │   단기 라벨: 3d_3/5/10pct, 5d_3/5/10pct     │
       │   스윙 라벨: 10d_3/5/10pct                  │
       ├─────────────────────────────────────────────┤
       │  대형주 단기 top3  │  대형주 스윙 top3       │
       │  중소형주 단기 top3│  중소형주 스윙 top3     │
       └─────────────────────────────────────────────┘
       대형주: KOSPI 시총 100위 이내 OR KOSDAQ 50위 이내
       중소형주: 나머지
    │
    ▼ 그룹 내 중복 제거
       동일 종목이 여러 라벨로 등장하면 EV/day 최대 라벨 하나만 유지
    │
    ▼ 정렬: EV/day 내림차순 → tiebreak: ML 확률 내림차순
    │
    ▼ 텔레그램 발송 (agents/report.py)
       요약 섹션: 4그룹 각 top3 목록 (EV/day, ML 확률 없음)
       상세 섹션: 각 종목 entry에
         [3일+5%] EV: X.X%/일, ML: XX%
       - ★ 접두사: S등급 종목
       - 목표가: 저항선 기반일 때만 표시 (폴백 +10%는 숨김)
```

> **라벨 주의사항**: 현재 라벨은 `max_high_Xd >= entry × (1+Y%)` 기준 (장중 최고가).
> EV 계산의 gain_pct가 실제 체결 가능 수익보다 낙관적일 수 있음.
> 추후 `max_close_Xd` (기간 내 최고 종가) 기준으로 변경 검토 중.
> 변경 시 labeler.py + feature_engineering.py + DB + 27개 모델 재학습 필요.

---

## 8. ML 앙상블 구조

| 모델 | 파일 형식 | 라벨 |
|------|----------|------|
| XGBoost | `.json` | 9개 전체 |
| LightGBM | `.txt` | 9개 전체 |
| ExtraTrees | `.pkl` | 9개 전체 |

- **라벨**: 3d/5d/10d × 3%/5%/10% = 9개
- **앙상블**: 3개 모델 확률 평균 (soft voting)
- **종목별 best_label**: 9개 라벨 중 가장 높은 확률의 라벨을 해당 종목의 대표 ML 점수로 사용
- **재학습**: `run_ml_pipeline.ps1` (로컬 Windows에서 실행 → VM 배포)

---

## 9. 향후 방향성

### 단기 (현재 진행)
- **신호 사후 검증 자동화**: `signal_history × ohlcv_daily` JOIN → 3/5/10일 후 실제 수익률 계산 → `/verify N` 텔레그램 명령

### 중기
- **ML 학습 데이터 누적**: signal_history가 쌓일수록 모델 재학습 품질 향상 → 주기적 재학습 자동화
- **사후 검증 피드백 루프**: 실제 성과를 피처로 역산해 모델 개선
- **섹터 데이터 복구**: KRX API 차단 우회 또는 대체 소스 확보 (현재 NULL)
- **PatternLearningResult DB 저장**: `--resend-last`에서 패턴분석 섹션 표시 가능하도록

### 장기
- **매도 신호 고도화**: 현재 규칙 기반 → ML 기반 매도 타이밍 예측
- **포트폴리오 최적화**: 신호 강도 기반 비중 자동 산출
