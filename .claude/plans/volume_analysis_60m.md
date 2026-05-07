# 거래량 분석 60분봉 기반 교체 계획

## Context

`거래량분석_설계문서.md`에서 정의한 60분봉 기반 종목별 행동 프로파일링 방식을
기존 `VolumeAnalysisAgent`(일봉 binary flag 9개)에 통합.

- **현재**: 일봉 OHLCV 기반 9개 rule (vol_5d_above_20d, OBV uptrend 등) → 최대 24점
- **변경**: 60분봉 기반 5개 MVP feature → percentile 캐시로 임계값 판정 → 교체
- **MA alignment** (MVP 6번째): TechnicalAnalysisAgent에 이미 구현됨 → VolumeAnalysisAgent에서 제외

---

## 변경 범위

### 1. `agents/volume_analysis.py` (핵심 교체)

**현재** → `df_daily` 기반 binary flag 9개 계산  
**변경** → `df_60m` 기반 binary flag 5개 계산

#### 새 feature 함수 (교체)

| 기존 제거 | 신규 추가 | 설명 |
|---------|---------|------|
| vol_5d_above_20d | `_same_time_vol_ratio()` | 동일 시간대 대비 거래량 비율 (P95 캐시 사용) |
| vol_consecutive_3d | `_volume_zscore_60m()` | 60분봉 거래량 z-score (>= 2.0) |
| vol_rise_price_flat | `_relative_turnover_60m()` | 당일 거래대금 합산 vs 과거 분포 (P80) |
| obv_uptrend_price_flat | `_vwap_above_60m()` | 마지막 60분봉 종가 > VWAP |
| vol_vs_52w_low_2x | `_obv_slope_60m()` | 60분봉 OBV 기울기 percentile (P70) |
| foreign_inst_buy/sell | — | 외부 API 의존 제거 |
| short_interest_declining | — | 외부 API 의존 제거 |
| vol_trending_w_price | — | 삭제 |
| price_vol_bullish_corr | — | 삭제 |

#### `run()` 시그니처 변경

```python
# 기존
async def run(self, ticker: str, df: pd.DataFrame | None = None) -> VolumeResult

# 변경
async def run(self, ticker: str, df: pd.DataFrame | None = None, df_60m: pd.DataFrame | None = None) -> VolumeResult
```

`df_60m`이 None이면 `VolumeResult(volume_score=0, explosion_imminent=False, smart_money_flow="neutral")` 반환.

#### VolumeProfileCache 클래스 추가 (volume_analysis.py 내부)

```python
class VolumeProfileCache:
    CACHE_FILE = "data/volume_profile_cache.json"

    @classmethod
    def get_or_update(cls, ticker: str, df_60m: pd.DataFrame) -> dict:
        """캐시 로드 or 갱신. 당일 기준 갱신."""
        # cache[ticker]["date"] == today → 캐시 반환
        # 그 외 → _compute(df_60m) 계산 후 저장
    
    @classmethod
    def _compute(cls, df_60m: pd.DataFrame) -> dict:
        """시간대별 P90/P95/P99 거래량 계산"""
        # df_60m을 hour별 그룹 → 각 hour의 volume 분포 계산
        # 반환: { "09": {"p90":..., "p95":..., "p99":...}, "10": {...}, ... }
```

#### 새 feature 계산 로직

**1. same_time_volume_ratio (P95 판정)**
```python
def _same_time_vol_ratio(self, df_60m, cache) -> bool:
    # 전일 마지막 거래 시간대 거래량
    last_row = df_60m[df_60m.index.date == yesterday].iloc[-1]
    hour_key = str(last_row.name.hour).zfill(2)
    p95 = cache.get(hour_key, {}).get("p95", inf)
    ratio = last_row["Volume"] / mean_vol_at_hour  # 최근 20일 동시간 평균
    return ratio >= 1.5 or last_row["Volume"] >= p95
```

**2. volume_zscore (>= 2.0)**
```python
def _volume_zscore_60m(self, df_60m) -> bool:
    # 전일 60분봉 전체 대상
    yesterday_df = df_60m[df_60m.index.date == yesterday]
    if yesterday_df.empty: return False
    hist_vol = df_60m["Volume"].iloc[:-len(yesterday_df)]
    mean, std = hist_vol.mean(), hist_vol.std()
    if std == 0: return False
    zscore = (yesterday_df["Volume"].max() - mean) / std
    return zscore >= 2.0
```

**3. relative_turnover (P80 판정)**
```python
def _relative_turnover_60m(self, df_60m) -> bool:
    # 당일 거래대금 합산 (Close * Volume 또는 별도 컬럼)
    # vs 과거 60일 일별 거래대금 합산의 P80
    daily_turnover = df_60m.groupby(df_60m.index.date)["Volume"].sum()
    # Close가 있으면 Close * Volume 합산
    p80 = daily_turnover.quantile(0.80)
    return daily_turnover.iloc[-1] >= p80
```

**4. VWAP above (마지막 60분봉)**
```python
def _vwap_above_60m(self, df_60m) -> bool:
    yesterday_df = df_60m[df_60m.index.date == yesterday]
    if yesterday_df.empty: return False
    vwap = (yesterday_df["Close"] * yesterday_df["Volume"]).sum() / yesterday_df["Volume"].sum()
    return yesterday_df["Close"].iloc[-1] > vwap
```

**5. OBV slope (P70 판정)**
```python
def _obv_slope_60m(self, df_60m) -> bool:
    # 최근 20개 60분봉 OBV 계산
    recent = df_60m.tail(20)
    direction = np.sign(recent["Close"].diff()).fillna(0)
    obv = (direction * recent["Volume"]).cumsum()
    # 선형 회귀 기울기
    x = np.arange(len(obv))
    slope = np.polyfit(x, obv.values, 1)[0]
    # 과거 기울기 분포에서 P70 이상 여부 (캐시에서)
    return slope > 0  # MVP: 양수 여부로 단순화, 추후 percentile 추가
```

---

### 2. `config/scoring/v1_baseline/volume.yaml` (교체)

기존 9개 rule 전체 제거, 신규 5개 rule로 교체:

```yaml
version: "2.0"
description: "60분봉 기반 종목별 행동 프로파일링 (v2)"
rules:
  - id: hourly_vol_ratio_p95
    points: 5
    description: "동일 시간대 거래량 P95 이상 (핵심 이상 수급 신호)"
  - id: hourly_vol_zscore_high
    points: 4
    description: "60분봉 거래량 z-score >= 2.0"
  - id: relative_turnover_high
    points: 3
    description: "당일 거래대금 상위 20% (P80 이상)"
  - id: vwap_above_60m
    points: 2
    description: "마지막 60분봉 종가 > 당일 VWAP"
  - id: obv_slope_up_60m
    points: 3
    description: "최근 20개 60분봉 OBV 기울기 양수"
thresholds:
  explosion_imminent: 10   # 구 12점 → 신규 점수 체계 반영
  monitoring: 6            # 구 8점 → 신규 점수 체계 반영
  pass_below: 5
```

최대 점수: 17점

---

### 3. `config/scoring/v1_baseline/buy_grade.yaml` (임계값 조정)

volume_min 값이 구 체계(최대 24점)에 맞춰져 있어 조정 필요:

```yaml
# 기존 → 변경
grade_S:
  volume_min: 12 → 10   # 신규 max 17점 기준 상위
grade_A:
  volume_min: 8  → 6
grade_B:
  volume_min: 5  → 3
```

---

### 4. `agents/orchestrator.py` (df_60m 전달)

[orchestrator.py:121-131](agents/orchestrator.py#L121-L131) `_analyze_stock()` 내 VolumeAnalysisAgent 호출 변경:

```python
# 기존
vol_agent.run(ticker, df=df_daily)

# 변경
vol_agent.run(ticker, df=df_daily, df_60m=df_60m)
```

`df_60m`은 이미 `HourlyStore.fetch_and_update_hourly()`로 조회된 값이 있음 (라인 113-118).

---

### 5. `config.py` (캐시 경로 추가)

```python
VOLUME_PROFILE_CACHE = "data/volume_profile_cache.json"
```

---

## 실행 흐름 변경 후

```
VolumeAnalysisAgent.run(ticker, df_daily, df_60m)
  ├─ df_60m 없음 → volume_score=0 반환 (skip)
  ├─ VolumeProfileCache.get_or_update(ticker, df_60m)
  │   ├─ 당일 캐시 있음 → 반환
  │   └─ 없음 → 시간대별 P90/P95/P99 계산 + 저장
  ├─ _same_time_vol_ratio() → hourly_vol_ratio_p95 flag
  ├─ _volume_zscore_60m() → hourly_vol_zscore_high flag
  ├─ _relative_turnover_60m() → relative_turnover_high flag
  ├─ _vwap_above_60m() → vwap_above_60m flag
  ├─ _obv_slope_60m() → obv_slope_up_60m flag
  └─ ScoringEngine.score_volume(flags) → VolumeResult
```

---

## 수정 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `agents/volume_analysis.py` | 전체 교체: 9개 일봉 feature → 5개 60분봉 feature + VolumeProfileCache 클래스 추가 |
| `config/scoring/v1_baseline/volume.yaml` | rules 전체 교체 (9개→5개), thresholds 조정 |
| `config/scoring/v1_baseline/buy_grade.yaml` | volume_min 임계값 조정 |
| `agents/orchestrator.py` | vol_agent.run()에 df_60m 추가 전달 |
| `config.py` | VOLUME_PROFILE_CACHE 경로 상수 추가 |

---

## 검증 방법

1. `python -c "from agents.volume_analysis import VolumeAnalysisAgent; print('import ok')"` — import 오류 없음 확인
2. 단일 종목 실행: `python -m agents.volume_analysis` (또는 간단한 테스트 스크립트)로 df_60m None 케이스 + 정상 케이스 확인
3. Orchestrator 전체 dry-run: `python main.py --dry-run` 또는 소수 종목으로 실행
4. VolumeResult.volume_score 범위 0~17 확인, explosion_imminent 임계값 작동 확인
5. buy_grade 등급 배분 확인 (이전 대비 S/A/B 비율 유사한지)

---

## 주요 주의사항

- **MA alignment 제외**: TechnicalAnalysisAgent의 trend_score에 이미 포함. 중복 계산 방지.
- **HourlyStore 데이터 필요**: df_60m이 None이면 volume_score=0으로 fallback — 60분봉 데이터 없는 종목은 거래량 신호 없음으로 처리됨.
- **OBV slope MVP 단순화**: 초기에는 slope > 0 판정. 이후 캐시에 과거 slope 분포 추가하여 P70 percentile 기반으로 고도화 가능.
- **volume_profile_cache.json**: 당일 분석 전 갱신, 분석 중 재사용. 없으면 최초 실행 시 자동 생성.
