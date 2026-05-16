# 국내 주식 신호 알림 시스템 — 시스템 개요

> Last Updated: 2026-05-16

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
│  │  (cron)      │───▶│  run_collect() / run_daily()     │   │
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
│                      │ (S등급+RR≥2.0 top10)│               │
│                      └──────────┬──────────┘               │
└─────────────────────────────────┼───────────────────────────┘
                                  ▼
                        ┌─────────────────┐
                        │  텔레그램 Bot    │
                        │  (사용자 수신)   │
                        └─────────────────┘
```

---

## 4. 일일 파이프라인 작업순서

### 4-1. 데이터 수집 (07:00 UTC = 16:00 KST, 장 마감 직후)

```
run_collect()
 ├─ UniverseManager → 351종목 목록 결정
 ├─ [종목별 병렬] OhlcvStore.fetch_and_update_daily()   → 일봉 저장
 │                HourlyStore.fetch_and_update_hourly()  → 60분봉 저장
 ├─ MarketIndexStore.fetch_and_update()                 → 지수 저장
 └─ ReportAgent.send_collect_report()                   → 수집 결과 텔레그램
```

### 4-2. 신호 분석 (21:00 UTC = 06:00 KST, 다음 날 장 시작 전)

```
run_daily() → _pipeline()
 │
 ├─ [병렬 시작]
 │   ├─ MarketFilterAgent.run()  → KOSPI/KOSDAQ 장세 (bullish/sideways/bearish)
 │   └─ SellSignalAgent.run()   → 보유 종목 매도신호 (장세 무관)
 │
 ├─ UniverseManager.get_universe() → 분석 대상 종목 확정
 │
 ├─ [종목별 병렬, Semaphore=2]  _analyze_stock() × 351종목
 │   ├─ TechnicalAnalysisAgent  → 이평선/RSI/BB/일목균형표/지지저항 점수
 │   ├─ VolumeAnalysisAgent     → 거래량 프로파일/이상 거래량 점수
 │   ├─ StockPatternLearner     → 컵핸들/삼각수렴/BB수축/돌파박스 패턴
 │   └─ BuySignalAgent          → 종합 등급 산출 (S/A/B/NONE)
 │
 ├─ score_all_labels()          → XGB+LGBM+ET soft voting (9개 라벨)
 ├─ signal_history 저장
 ├─ signal_xgb_probs 저장
 │
 └─ ReportAgent.send()          → S등급 + RR≥2.0 + top10 텔레그램 발송
```

---

## 5. 시스템 구성

| 구성요소 | 상세 |
|----------|------|
| **운영 환경** | GCP VM `instance-20260505-092414` (us-central1-a, e2-medium) |
| **런타임** | Python 3.10, asyncio 기반 |
| **DB** | DuckDB (`/opt/stock-monitor/data/stock.duckdb`) |
| **모델 파일** | `/opt/stock-monitor/data/models/` (XGB `.json`, LGBM `.txt`, ET `.pkl`) |
| **알림** | Telegram Bot API |
| **스케줄러** | cron (수집 07:00 UTC / 분석 21:00 UTC) |
| **유니버스** | KOSPI200 + KOSDAQ150 = 351종목 (`kospi200_daq150` 모드) |
| **동시성** | 종목 분석 Semaphore=2 (KRX rate-limit 대응) |
| **개발 환경** | Windows 10, Claude Code, DuckDB MCP (`mcp/mcp_duckdb.py`) |

---

## 6. 텔레그램 발송 기준 (필터링 로직)

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
    ▼ 정렬: xgb_prob (ML 확률) → 패턴 등급 → 손익비
    │
    ▼ 상위 10종목 cap
    │
    ▼ 텔레그램 발송
```

---

## 7. 향후 방향성

### 단기 (현재 진행)
- **신호 사후 검증 자동화**: `signal_history × ohlcv_daily` JOIN → 3/5/10일 후 실제 수익률 계산 → `/verify N` 텔레그램 명령

### 중기
- **ML 학습 데이터 누적**: signal_history가 쌓일수록 모델 재학습 품질 향상 → 주기적 재학습 자동화
- **사후 검증 피드백 루프**: 실제 성과를 피처로 역산해 모델 개선
- **섹터 데이터 복구**: KRX API 차단 우회 또는 대체 소스 확보 (현재 NULL)

### 장기
- **매도 신호 고도화**: 현재 규칙 기반 → ML 기반 매도 타이밍 예측
- **포트폴리오 최적화**: 신호 강도 기반 비중 자동 산출
- **market_cap 기반 필터**: 유동성 필터 정교화 (현재 NULL 문제 있음)
