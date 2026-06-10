# Track C — Parallel ML Pipeline Design

**Date**: 2026-06-10  
**Status**: Draft  
**Branch**: track-a-pullback-timing (작성 시점)

---

## 1. 배경 및 목표

Track B ML 파이프라인(모델 버전 2026-05-30, 18라벨, 30피처)을 운용 중인 상태에서,
다른 피처셋(X)과 라벨 체계(Y)를 가진 Track C를 **병렬로 훈련·운영**한다.

두 트랙을 병렬로 운영하는 목적:
- 60분봉 intraday 피처의 예측 기여도 측정
- 새로운 라벨 체계(상대수익, 섹터 초과수익, 기술적 돌파 등)의 유효성 검증
- Track B와 직접 성능 비교 (동일 `live_eval_daily` 테이블, `model_version` 구분)

---

## 2. 전체 아키텍처

```
[Track B]                          [Track C]
data/models/                       data/models_c/
data/feature_matrix.parquet        data/fm_c.parquet
scripts/feature_engineering.py     scripts/feature_engineering_c.py
scripts/train_models.py            scripts/train_models_c.py
agents/orchestrator.py             agents/orchestrator_c.py (스코어링 전용)

[공유 인프라]
universe_daily           ← 두 트랙 모두 읽기
universe_features_daily  ← 두 트랙 모두 읽기
ohlcv_min                ← Track C 전용 (intraday 피처)
live_eval_daily          ← model_version 컬럼으로 트랙 구분
signal_history           ← model_version 컬럼으로 트랙 구분
```

**핵심 원칙:**
- Track B 코드 일절 수정 없음 (`run_daily_b` 로 cron 이름만 변경)
- 훈련은 수동 실행 전용 (run_daily_c에 포함 안 함)
- 스코어링만 자동화 (run_daily_c, +15분 오프셋)

---

## 3. 스케줄

| 이름 | 시간 | 역할 |
|------|------|------|
| `run_collect` | 07:00 UTC (16:00 KST) | 데이터 수집 (변경 없음) |
| `run_daily_b` | 08:30 UTC (17:30 KST) | Track B 스코어링 + 텔레그램 (기존 run_daily 이름 변경) |
| `run_daily_c` | 08:45 UTC (17:45 KST) | Track C 스코어링 + 텔레그램 (신규) |

`run_daily_b` 완료 후 +15분 오프셋 → DuckDB write lock 충돌 방지, 독립 실패 격리.

---

## 4. 피처 매트릭스 (fm_c.parquet)

### 4.1 훈련 기간

- **시작**: 2023-06-07 (ohlcv_min 백필 시작일)
- **종료**: 훈련 실행 시점 (rolling)
- Track B 대비 ~1년 긴 훈련 기간 (Track B: ~2년, Track C: ~3년)

### 4.2 일봉 피처

| 구분 | 개수 | 소스 |
|------|------|------|
| Section A+B (기존 훈련 피처) | 30 | universe_features_daily |
| Section C (미사용 편입) | 17 | universe_features_daily |
| universe_daily 미사용 컬럼 | 7 | universe_daily |
| 신규 일봉 피처 | TBD | 별도 계산 |

**Section C 17개 (편입 대상):**
```
breakout_distance_60d, breakout_distance_120d, range_80d_pct,
distance_from_ma224, up_days_5d, gap_percent, opening_strength,
intraday_close_strength, recovery_from_low_80d, volume_acceleration,
volume_dryup_ratio, retracement_ratio, pullback_depth,
bb_width_pct_252, turnover_rank_pct, amount_rank_pct, volatility_rank_pct
```

**universe_daily 미사용 7개 (편입 대상):**
```
close_to_52w_high, bb_position, rsi_14, foreign_net_20d,
close_to_20ma_ratio, close_to_60ma_ratio, close_to_5ma_ratio
```

### 4.3 Intraday 피처 (60분봉 → 일별 집계)

| 범주 | 피처 |
|------|------|
| VWAP | vwap_close_ratio, vwap_upper_ratio |
| 거래량 분포 | vol_front_ratio (첫 2시간), vol_tail_ratio (마감 1시간), vol_mid_ratio, vol_front_ratio_5d |
| 수익률 흐름 | am_return (09~12시), pm_return (12~15:30), am_pm_return_diff, am_reversal |
| 장중 경로 | intraday_range_ratio, open_to_high_ratio, open_to_low_ratio, intraday_close_strength_min |
| 롤링 | vwap_consistency_5d, vol_concentration_5d |

> 추가 후보 TBD — 구현 단계에서 최대한 탐색 후 feature importance로 가지치기.

---

## 5. 라벨 체계

### 5.1 후보 라벨 22개

**3D 라벨 (10개)**

| # | 이름 | 정의 |
|---|------|------|
| 1 | label_3d_5pct_first | 3거래일 내 +5%가 -3.5%보다 먼저 발생 |
| 2 | label_3d_10pct_first | 3거래일 내 +10%가 -4%보다 먼저 발생 |
| 3 | label_3d_return_top10pct | 3일 수익률 universe 상위 10% |
| 4 | label_3d_return_top20pct | 3일 수익률 universe 상위 20% |
| 5 | label_3d_market_excess_top20pct | 3일 시장 초과수익 상위 20% |
| 6 | label_3d_sector_excess_top30pct | 3일 섹터 초과수익 상위 30% |
| 7 | label_3d_trend_start_atr | 3일 내 +1.2ATR이 -1ATR보다 먼저 발생 |
| 8 | label_3d_bb_upper_break | 3일 내 볼린저밴드 상단 돌파 |
| 9 | label_3d_range_breakout_20d | 3일 내 20일 고점 돌파 |
| 10 | label_3d_bb_squeeze_breakout | 오늘 BB폭 하위 30~40% + 3일 내 BB 상단 돌파 |

**5D 라벨 (10개)**

| # | 이름 | 정의 |
|---|------|------|
| 11 | label_5d_7pct_first | 5거래일 내 +7%가 -4%보다 먼저 발생 |
| 12 | label_5d_10pct_first | 5거래일 내 +10%가 -5%보다 먼저 발생 |
| 13 | label_5d_return_top10pct | 5일 수익률 universe 상위 10% |
| 14 | label_5d_return_top20pct | 5일 수익률 universe 상위 20% |
| 15 | label_5d_market_excess_top20pct | 5일 시장 초과수익 상위 20% |
| 16 | label_5d_sector_excess_top20pct | 5일 섹터 초과수익 상위 20% |
| 17 | label_5d_dual_excess | 5일 수익 > 시장 AND > 섹터 평균 |
| 18 | label_5d_bb_squeeze_breakout | 오늘 BB폭 하위 30% + 5일 내 BB 상단 돌파 |
| 19 | label_5d_range_breakout_20d | 5일 내 20일 고점 돌파 |
| 20 | label_5d_ma20_reclaim_trend | 오늘 MA20 근처 + 5일 후 MA20 위 + 수익 상위 30% |

**단기 라벨 (2개)**

| # | 이름 | 정의 |
|---|------|------|
| 21 | label_2d_5pct_first | 2거래일 내 +5%가 -2.5%보다 먼저 발생 |
| 22 | label_1d_5pct_first | 1거래일 내 +5%가 -2.5%보다 먼저 발생 |

### 5.2 구현 복잡도

| 유형 | 라벨 번호 | 구현 방식 |
|------|----------|----------|
| first-to-hit | 1,2,7,11,12,21,22 | labeler.py 확장 (hold period·임계치 변경) |
| cross-sectional rank | 3,4,13,14 | 날짜별 전체 universe 배치 rank 계산 필요 |
| 시장/섹터 초과수익 | 5,6,15,16,17 | 시장 인덱스 + 섹터 매핑 필요 |
| 기술적 돌파 | 8,9,18,19 | universe_daily 컬럼 활용 |
| 조건부 패턴 | 10,20 | T 상태 조건 + T+N 결과 두 단계 계산 |

### 5.3 필터링 기준

훈련 기간(2023-06-07~) 전체 기준:
- **통과**: 5% ≤ positive_rate ≤ 45%
- **탈락**: positive_rate < 5% (샘플 부족) 또는 > 45% (변별력 없음)

예상 탈락 후보: `label_1d_5pct_first` (하루 +5% 발생 빈도 낮음)  
예상 최종 라벨 수: **12~18개**

---

## 6. 모델 훈련

### 6.1 아키텍처

Track B와 동일: XGB + LGBM + ET + LR Stacking (Walk-forward TimeSeriesSplit 5-fold)

### 6.2 파라미터 변경

**ExtraTrees (Track B → Track C)**

```python
# Track B (문제: max_depth=None → 무제한 깊이 → 과적합)
_ET_PARAMS = dict(
    n_estimators=300, max_depth=None, min_samples_leaf=10,
    class_weight="balanced", random_state=42, n_jobs=-1,
)

# Track C (수정)
_ET_PARAMS = dict(
    n_estimators=300, max_depth=10,
    min_samples_leaf=20,
    max_features=0.4,
    class_weight="balanced", random_state=42, n_jobs=-1,
)
```

XGB / LGBM: Track B와 동일하게 시작, 훈련 후 AUC 비교하며 조정.

### 6.3 출력

```
data/models_c/
  xgb_label_{label}.json
  lgbm_label_{label}.txt
  et_label_{label}.pkl
  lr_stacker_{label}.pkl
data/model_meta_c.json
data/oof_predictions_c.parquet
```

### 6.4 훈련 스크립트

`scripts/train_models_c.py`
- 입력: `data/fm_c.parquet`
- 수동 실행 전용 (VM에서 `sudo -u stock python3 scripts/train_models_c.py`)

---

## 7. 일일 스코어링 파이프라인

### 7.1 run_daily_c 흐름

```
agents/orchestrator_c.py
  ├── universe_features_daily 읽기 (오늘 날짜)
  ├── ohlcv_min 집계 → intraday 피처 계산
  ├── data/models_c/ 로드
  ├── 라벨별 예측 확률 계산 → top-K 종목 선정
  ├── signal_history 저장 (model_version='track_c_YYYY-MM-DD')
  ├── live_eval_daily 저장 (model_version='track_c_YYYY-MM-DD')
  └── 텔레그램 발송 (Track C 전용 메시지)
```

### 7.2 평가

두 트랙 비교 쿼리:

```sql
SELECT model_version, label, 
       AVG(prec_at_3) AS prec3, AVG(prec_at_5) AS prec5,
       COUNT(*) AS days
FROM live_eval_daily
GROUP BY model_version, label
ORDER BY model_version, label
```

---

## 8. 구현 단계 (개요)

| 단계 | 내용 |
|------|------|
| 1 | `run_daily` → `run_daily_b` cron 이름 변경 |
| 2 | `scripts/labeler_c.py` — 22개 라벨 계산 + 양성 비율 필터링 |
| 3 | `scripts/feature_engineering_c.py` — 54개 일봉 + intraday 피처 계산 → `fm_c.parquet` |
| 4 | `scripts/train_models_c.py` — 모델 훈련 → `data/models_c/` |
| 5 | `agents/orchestrator_c.py` — 일일 스코어링 전용 |
| 6 | `run_daily_c` cron 등록 (08:45 UTC) |
| 7 | `live_eval_daily` model_version 컬럼 확인 + Track C 백필 |

---

## 9. 리스크 / 제약

| 항목 | 내용 |
|------|------|
| 훈련 시간 | 모델 수 증가 (최대 18라벨 × 4모델 = 72개) — e2-medium에서 1~2시간 예상 |
| 섹터 데이터 | label_sector_excess 계산 시 ticker_master 섹터 정보 완전성 확인 필요 |
| cross-sectional 라벨 | 날짜별 배치 계산 — 기존 labeler 구조 변경 필요 |
| open=0.0 데이터 | Track B와 동일한 미해결 이슈, entry_price 검증 필수 |
| GCP 비용 | 2026-08-04 Free Trial 만료 → 월 ~₩33,000 유료 전환 예정 |
