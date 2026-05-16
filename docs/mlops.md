# MLOps 아키텍쳐 — 모델 학습 / 추론 / DB 스키마

> Last Updated: 2026-05-16

---

## 1. MLOps 전체 흐름

```
[운영 파이프라인]                    [모델 학습 파이프라인]
      │                                      │
      ▼                                      ▼
signal_history 저장              scripts/feature_engineering.py
(매일 자동)                         → data/feature_matrix.parquet
      │                                      │
      │                                      ▼
      │                            scripts/train_models.py
      │                              XGB / LGBM / ET 학습
      │                              → data/models/*.json/.txt/.pkl
      │                                      │
      ▼                                      ▼
score_all_labels()  ◀────────── data/models/ (모델 파일 로드)
(라이브 추론)
      │
      ▼
signal_xgb_probs 저장
(라벨별 9개 확률)
```

학습은 수동 실행 (`run_ml_pipeline.ps1`). 운영은 매일 자동.

---

## 2. 예측 목표 (라벨)

9개 이진 분류 라벨 (3가지 보유기간 × 3가지 수익률 임계값):

| | +3% | +5% | +10% |
|--|-----|-----|------|
| **3일** | 3d_3pct | 3d_5pct | 3d_10pct |
| **5일** | 5d_3pct | 5d_5pct | 5d_10pct |
| **10일** | 10d_3pct | 10d_5pct | 10d_10pct |

라벨 정의: `signal_date` 기준 n거래일 내 고가가 진입가 × (1 + m%) 이상 도달 여부 (0/1)

---

## 3. 피처 구성 (약 55개)

### v1 피처 (27개) — signal_history 기반
| 그룹 | 피처 |
|------|------|
| 신호 점수 | `vol_score`, `total_score`, `trend_score`, `pattern_score`, `risk_reward` |
| 패턴 원-핫 | `pattern_cup_handle`, `pattern_falling_box_breakout`, `pattern_triangle_convergence`, `pattern_bb_squeeze` |
| 스냅샷 | `per`, `pbr`, `div_yield`, `foreign_exh_rate`, `short_ratio`, `volume`, `amount`, `market_cap`, `turnover_rate` |
| rolling | `foreign_net_5d`, `inst_net_5d`, `log_avg_volume_20d`, `hist_volatility_20d`, `avg_foreign_exh_rate_20d` |

### v2 피처 (28개) — ohlcv_daily 계산
| 그룹 | 피처 |
|------|------|
| 이동평균 | `close_to_5ma_ratio`, `close_to_20ma_ratio`, `close_to_60ma_ratio`, `close_to_52w_high`, `ma_cross_5_20` |
| 기술 지표 | `rsi_14`, `bb_position`, `obv_slope_5d` |
| 캔들 | `high_low_ratio`, `body_ratio` |
| 공매도 | `short_balance_ratio`, `short_volume_ratio_5d`, `short_balance_change_5d` |
| 거래량 | `volume_surge_ratio`, `amount_surge_ratio` |
| 모멘텀 | `price_momentum_3d`, `price_momentum_10d` |
| 수급 | `foreign_net_20d`, `inst_net_20d`, `combined_net_5d`, `foreign_exh_change_5d` |
| 재무 | `roe_proxy` |
| 시장 | `kospi_return_5d`, `kospi_return_20d`, `kospi_above_ma60`, `market_volatility_20d`, `relative_strength_5d` |

---

## 4. 앙상블 모델 구성

라이브 추론은 3개 모델의 **soft voting** (확률 평균):

```
XGBoost  (xgb_*.json)   ─┐
LightGBM (lgbm_*.txt)  ─┤─▶  평균 확률  →  xgb_prob 저장
ExtraTrees (et_*.pkl)  ─┘
```

- 각 라벨당 모델 파일 3개 × 9라벨 = 총 27개 모델 파일
- 모델 파일 없는 라벨은 조용히 스킵
- 추론 결과: `signal_xgb_probs` 테이블 (ticker × label × 확률)
- 텔레그램 정렬 기준: `3d_5pct` 라벨 확률 (`xgb_prob` 필드)

---

## 5. 모델 학습 절차 (수동)

```powershell
# 원클릭 재학습 (Windows)
.\run_ml_pipeline.ps1

# 내부 순서:
# 1. scripts/feature_engineering.py  → data/feature_matrix.parquet
# 2. backtest/labeler.py             → backtest_labels 갱신
# 3. scripts/train_models.py         → data/models/ 모델 파일 갱신
# 4. VM 배포 (scp)
```

재학습 권장 시점: signal_history 500건 이상 추가 누적 시 (현재 16,699건)

---

## 6. DB 스키마 (DuckDB: data/stock.duckdb)

### ohlcv_daily
일봉 OHLCV + 수급 + 재무 데이터. ML 피처 계산의 주 소스.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| ticker, date | PK | 종목코드, 날짜 |
| open/high/low/close | DOUBLE | 가격 |
| volume, amount | DOUBLE | 거래량, 거래대금 |
| market_cap, shares | DOUBLE | 시가총액, 상장주식수 |
| foreign_net, inst_net | DOUBLE | 외국인/기관 순매수 (주) |
| per, pbr, eps, bps | DOUBLE | 밸류에이션 |
| div_yield | DOUBLE | 배당수익률 |
| foreign_exh_rate | DOUBLE | 외국인 소진율 |
| short_balance, short_volume, short_ratio | DOUBLE | 공매도 |

### ohlcv_min
60분봉 데이터. 거래량 프로파일 분석에 사용.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| ticker, dt | PK | 종목코드, 일시 |
| open/high/low/close/volume/amount | DOUBLE | 60분봉 가격/거래량 |
| source | VARCHAR | 데이터 출처 (yfinance) |

현황: 2026-02-06 ~ 2026-05-15, 64거래일, 135,890행

### market_index
KOSPI/KOSDAQ 지수 일봉. 장세 판단 및 시장 피처 계산.

| 컬럼 | 설명 |
|------|------|
| ticker | 1001=KOSPI, 2001=KOSDAQ |
| date, open/high/low/close/volume/amount/market_cap | 지수 OHLCV |

### signal_history
매일 분석 후 발생한 매수 신호 이력. ML 학습 데이터의 핵심.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| signal_date, ticker | PK | 신호 날짜, 종목 |
| vol_score | INTEGER | 거래량 점수 |
| grade | VARCHAR | S / A / B |
| features | JSON | total_score, trend_score, pattern, risk_reward 등 |
| entry_price | DOUBLE | 진입가 (신호 당일 종가) |
| scoring_version | VARCHAR | live_v1 / live_v2 |
| xgb_prob | DOUBLE | 3d_5pct 라벨 ML 확률 (최신 soft voting) |

현황: 2026-02-09 ~ 2026-05-15, 16,699건

### signal_xgb_probs
라벨별 ML 확률 전체 저장. 사후 분석 및 모델 성능 추적.

| 컬럼 | 설명 |
|------|------|
| signal_date, ticker, label | PK (라벨별 1행) |
| xgb_prob | soft voting 평균 확률 |

### backtest_labels
신호 발생 후 실제 주가 결과. ML 학습 정답 레이블.

| 컬럼 | 설명 |
|------|------|
| signal_date, ticker | PK |
| entry_price | 진입가 |
| max_high_3d/5d/10d | 이후 n일 내 최고가 |
| max_drawdown_3d/5d/10d | 이후 n일 내 최대 낙폭 |
| return_3d/5d/10d | 이후 n일 수익률 |

### ticker_master
종목 기본 정보.

| 컬럼 | 설명 |
|------|------|
| ticker, name | 종목코드, 종목명 |
| market | KOSPI / KOSDAQ |
| sector | 섹터 (현재 대부분 NULL — KRX API 차단) |
| listed_date | 상장일 |

---

## 7. 데이터 흐름 요약

```
pykrx / yfinance
      │
      ▼
ohlcv_daily / ohlcv_min / market_index
      │
      ├──▶ [분석 파이프라인] TechnicalAgent + VolumeAgent + PatternAgent
      │           │
      │           ▼
      │    signal_history ──▶ [feature_engineering] ──▶ feature_matrix.parquet
      │                                                         │
      │                                                         ▼
      │                                               [train_models] → 모델 파일
      │
      ├──▶ [라이브 추론] score_all_labels(signals)
      │           │
      │           ▼
      │    signal_xgb_probs
      │
      └──▶ backtest_labels (labeler.py — 신호 이후 실제 결과 기록)
```
