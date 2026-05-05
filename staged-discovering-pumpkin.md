# 종목별 패턴 학습 모듈 설계 계획 (v2)

## Context

현재 시스템은 섹터 Top5 자동 선정 방식으로 50-60개 종목을 매일 일봉 데이터로 분석한다.
새로운 가설: 종목별 고유의 세력 패턴이 존재하며, 현재 패턴이 과거 "상승 직전 패턴"과
얼마나 유사한지를 수치화할 수 있다. 가격 외 거래량·투자자 유형(기관/외국인)을 함께 보면
"누가, 어떻게 움직이는가"를 패턴화할 수 있어 예측력이 높아진다.

**사용자 확정 사항:**
- 성공 기준: 패턴 발생 후 **5거래일 내 +5% 이상 상승**
- 통합 방식: 기존 점수에 반영하지 않고 **별도 섹션으로 텔레그램 보고**
- 1차 구현: 윈도우 최적화 + 다채널 패턴(가격·거래량·기관·외국인)
- 2차 구현: 손바뀜 감지 (별도 섹션으로 분리)

---

## 개인/기관/외국인 데이터 활용 판단

### 데이터 접근성
- **함수**: `stock.get_market_trading_value_by_date(start, end, ticker, detail=True)`
- **컬럼**: `외국인합계`, `기관합계`, `개인` (순매수 금액, 양수=매수 음수=매도)
- **주의**: pykrx 1.2.8에서 `detail=True` 엔드포인트 불안정 → 실패 시 fallback 필수
- **기존 사용**: 현재 volume_analysis.py에서 10일 데이터만 사용 중

### 패턴 분석의 의미
| 투자자 | 의미 | 활용 방식 |
|--------|------|-----------|
| **외국인** | 한국 시장에서 가장 예측력 높은 smart money 지표 | 순매수 비율 → 패턴 채널 |
| **기관** | 연기금·자산운용사의 선행 매집 신호 | 순매수 비율 → 패턴 채널 |
| **개인** | 역발상 지표 (FOMO 매수 = 약한 신호) | 외국인+기관으로 역산 가능, 별도 채널 불필요 |

### 결론
포함. 가격+거래량+외국인+기관 = **4채널 패턴**. 데이터 취득 실패 시 2채널(가격+거래량)로 graceful degradation.

---

## 패턴 벡터 설계 (핵심)

```
패턴 = [가격수익률(W), 거래량비율(W), 외국인순매수비율(W), 기관순매수비율(W)]
      = 4 × W 차원  (W = 종목별 최적 윈도우)

데이터 취득 실패 시:
패턴 = [가격수익률(W), 거래량비율(W)]  = 2 × W 차원
```

**정규화 방법:**
- 가격: `(price[t] - price[0]) / price[0]` → 기준일 대비 누적 수익률
- 거래량: `vol[t] / rolling_avg_vol_20d[t]` → 20일 평균 대비 비율 (1.0 = 평균)
- 외국인/기관: `net_buy[t] / abs(total_trading_value[t])` → [-1, 1] 범위

---

## 1차 구현

### Step 0: 종목 고정 테스트 (코드 변경 없음)

이미 지원되는 기능. 두 곳만 수정:
- `data/watchlist.json` → 사용자 지정 종목 코드 입력
  ```json
  {"stocks": ["005930", "000660", "035420"]}
  ```
- `config.py` 또는 `.env` → `UNIVERSE_MODE = "watchlist"`

---

### Step 1: 데이터 모델 추가

**파일**: `models/signals.py` — 기존 `SellSignal` 다음에 추가

```python
@dataclass
class PatternLearningResult:
    ticker: str
    pattern_confidence: float   # top-K 유사 패턴 중 성공 비율 (0.0–1.0)
    similar_count: int          # top-K 중 성공 레이블 패턴 수
    avg_return_5d: float        # 유사 패턴 후 평균 5일 수익률 (%)
    total_patterns: int         # 전체 평가된 역사적 패턴 수
    optimal_window: int         # 선택된 윈도우 크기 (10/15/20/30/40)
    pattern_dim: str            # "FULL(4ch)" | "PARTIAL(2ch)"
    grade: str                  # "HIGH" | "MEDIUM" | "LOW" | "INSUFFICIENT"
```

---

### Step 2: 패턴 학습 모듈 (핵심 신규 파일)

**파일**: `agents/pattern_learning.py`

#### 상수
```python
WINDOW_CANDIDATES = [10, 15, 20, 30, 40]   # 그리드 서치 후보
FUTURE_DAYS = 5
SUCCESS_THRESHOLD = 0.05   # +5%
TOP_K = 20
MIN_DATA_DAYS = 100        # 윈도우 최적화에 충분한 최소 데이터
HOLDOUT_DAYS = 100         # 윈도우 최적화 시 hold-out 기간
CACHE_FILE = "data/pattern_cache.json"  # {"ticker": {"window": W, "date": "YYYYMMDD"}}
```

#### 알고리즘: StockPatternLearner._analyze(ticker, ohlcv_df)

**Step A: 데이터 준비**
1. `ohlcv_df`에서 `종가`/`Close` 및 `거래량`/`Volume` 추출
2. 투자자 데이터 별도 fetch: `get_market_trading_value_by_date(start, end, ticker, detail=True)`
   - 성공: `외국인합계`, `기관합계` 컬럼 추출 → 4채널 모드
   - 실패/빈 데이터: None → 2채널 모드 fallback

**Step B: 윈도우 최적화 (종목별 1회, 캐싱)**
```
CACHE_FILE에서 ticker 조회 → 오늘 날짜와 같으면 캐시 사용
없으면 그리드 서치:
  hold-out 제외 데이터(train)로 각 W ∈ WINDOW_CANDIDATES에 대해 백테스트 실행
  → 정밀도(precision) 가장 높은 W 선택
  → CACHE_FILE에 저장 {"005930": {"window": 15, "date": "20260505"}}
```

**Step C: 역사적 윈도우 생성 (최적 W 사용)**
- 인덱스 `i`마다 슬라이스 `[i : i+W]`, 레이블: `max(close[i+W : i+W+5]) >= close[i+W-1] * 1.05`
- 각 윈도우를 4채널(또는 2채널) 정규화하여 `(4W,)` 또는 `(2W,)` 벡터 생성
- 현재 패턴: 마지막 W일 데이터로 동일 정규화

**Step D: 코사인 유사도 (순수 numpy)**
```python
sims = (corpus @ query) / (np.linalg.norm(corpus, axis=1) * np.linalg.norm(query))
top_k_idx = np.argsort(sims)[-TOP_K:]
```

**Step E: 집계 및 등급**
```python
pattern_confidence = mean(labels[top_k_idx])
similar_count      = sum(labels[top_k_idx])
avg_return_5d      = mean(returns[top_k_idx])

등급:
  HIGH        : confidence >= 0.65 AND similar_count >= 5
  MEDIUM      : confidence >= 0.50 AND similar_count >= 3
  LOW         : confidence >= 0.35 AND similar_count >= 2
  INSUFFICIENT: 그 외 (데이터 부족 포함)
```

#### 인터페이스
```python
class StockPatternLearner:
    async def run(self, ticker: str, df: pd.DataFrame | None = None) -> PatternLearningResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._analyze, ticker, df)

    @staticmethod
    def _insufficient(ticker: str) -> PatternLearningResult:
        return PatternLearningResult(ticker, 0.0, 0, 0.0, 0, 20, "N/A", "INSUFFICIENT")
```

---

### Step 3: TechnicalAnalysisAgent 소수 수정

**파일**: `agents/technical_analysis.py`

`_analyze` 메서드 내 pykrx fetch 직후:
```python
self.last_df: pd.DataFrame | None = df   # 패턴 학습 재사용 (추가 API 호출 방지)
```
fetch 실패 시: `self.last_df = None`

---

### Step 4: 오케스트레이터 수정

**파일**: `agents/orchestrator.py`

`_analyze_stock` 반환 타입: `tuple[BuySignal | None, PatternLearningResult]`

`tech, vol = await asyncio.wait_for(...)` 이후:
```python
pattern_learner = StockPatternLearner()
pattern_result = await pattern_learner.run(ticker, df=tech_agent.last_df)
buy_signal = self.buy_agent.evaluate(ticker, name, tech, vol, market_ctx)
return buy_signal, pattern_result
```

타임아웃 예외 시: `return None, StockPatternLearner._insufficient(ticker)`

`_pipeline`에서 결과 언패킹:
```python
buy_signals, pattern_results = [], []
for r in results:
    if isinstance(r, tuple):
        bs, pr = r
        if bs: buy_signals.append(bs)
        pattern_results.append(pr)
await self.report_agent.send(markets, buy_signals, sell_signals, pattern_results)
```

---

### Step 5: 리포트 에이전트 수정

**파일**: `agents/report_agent.py`

`send(...)` 파라미터: `pattern_results: list[PatternLearningResult] | None = None` 추가

새 `_pattern_section` 함수:
- INSUFFICIENT 제외, HIGH → MEDIUM → LOW 순 정렬
- `pattern_dim`으로 채널 표시 (데이터 품질 투명성)

텔레그램 출력 형식:
```
📊 종목별 패턴 분석 (5일 +5% 기준)
🟢 삼성전자 (005930): HIGH | 성공률 68% | 유사패턴 12개 | 평균수익률 +6.2% | W=15 | 4ch
🟡 SK하이닉스 (000660): MEDIUM | 성공률 52% | 유사패턴 7개 | 평균수익률 +4.1% | W=20 | 2ch
🔴 NAVER (035420): LOW | 성공률 38% | 유사패턴 3개 | 평균수익률 +2.1% | W=30 | 4ch
```
- 기존 4096자 분할 로직(`_split`)이 자동 처리

---

### Step 6: 백테스팅 CLI (검증용)

**파일**: `backtest/pattern_backtest.py` (신규)

```
python backtest/pattern_backtest.py --ticker 005930 [--train-days 300]
```

알고리즘:
- 800일 데이터 fetch
- train: 앞 `train_days`일, test: 나머지
- 각 테스트 포지션에서 패턴 학습 실행 → 등급 예측 vs 실제 성공 비교

출력:
```
═══════════════════════════════════════════════════
패턴 백테스트 결과: 005930 (삼성전자)  선택 윈도우: W=15  패턴채널: 4ch
기간: 2023-01-05 ~ 2024-12-31 | 테스트 윈도우: 142개
───────────────────────────────────────────────────
등급          샘플  성공  정밀도  평균수익률(5일)
HIGH            23    17   73.9%         +5.8%
MEDIUM          41    24   58.5%         +3.2%
LOW             38    14   36.8%         +1.1%
INSUFFICIENT    40     —      —             —
───────────────────────────────────────────────────
전체 성공률: 47.2% (67/142)  기준치(랜덤): ~50%
═══════════════════════════════════════════════════
```

---

## 2차 구현 (향후 별도 작업)

### 손바뀜 감지 모듈

**아이디어**: 세력이 교체되면 종목의 패턴 지문이 바뀐다.

**알고리즘**:
1. 전체 데이터를 60거래일 블록으로 분할
2. 각 블록의 평균 패턴 벡터 계산
3. 최근 1블록 vs 직전 3블록 평균의 코사인 유사도 계산
4. 유사도 < 0.6 → 패턴 변화 감지 (손바뀜 가능성)

**출력**: `pattern_drift: float`, `regime_change_flag: bool`

**활용**:
- HIGH 등급 종목도 `regime_change_flag=True`이면 신뢰도 경고 추가
- 텔레그램 메시지에 "⚠️ 최근 패턴 구조 변화 감지" 표시

---

## 수정 파일 목록

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `data/watchlist.json` | 수정 | 테스트 종목 코드 입력 |
| `config.py` | 수정 | `UNIVERSE_MODE = "watchlist"` |
| `data/pattern_cache.json` | **신규** | 종목별 최적 윈도우 캐시 |
| `models/signals.py` | 수정 | `PatternLearningResult` 추가 |
| `agents/pattern_learning.py` | **신규** | 핵심 패턴 학습 모듈 (윈도우 최적화, 4채널) |
| `agents/technical_analysis.py` | 수정 | `self.last_df` attribute 저장 |
| `agents/orchestrator.py` | 수정 | 패턴 학습 단계 통합, 반환 타입 변경 |
| `agents/report_agent.py` | 수정 | `_pattern_section` 추가, `send` 파라미터 확장 |
| `backtest/pattern_backtest.py` | **신규** | CLI 백테스트 도구 |

---

## 검증 방법

1. `data/watchlist.json`에 아는 종목 3-5개 입력 후 `python main.py --run-now`
2. 텔레그램 보고서 하단 "📊 종목별 패턴 분석" 섹션 확인
   - `4ch` 표시 = 투자자 데이터 정상 취득
   - `2ch` 표시 = detail=True 실패, 가격+거래량만 사용
   - W값으로 종목별로 다른 윈도우 선택됐는지 확인
3. `python backtest/pattern_backtest.py --ticker 005930` 실행
   - HIGH 정밀도 > 60% 이면 통계적으로 유의미한 edge 존재
   - 전체 성공률이 랜덤 기준(~50%)과 유의미하게 다른지 확인
4. HIGH 등급 종목의 실제 5일 후 수익률 수동 추적 (라이브 검증)
