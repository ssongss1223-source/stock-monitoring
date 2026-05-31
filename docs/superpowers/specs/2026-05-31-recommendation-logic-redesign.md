# 추천 로직 재설계 스펙

**작성일**: 2026-05-31  
**범위**: 대형주 기준 변경 + 필터 게이트 도입 + 평가 K 확장  
**관련 로드맵**: D2(평가지표), D6(정렬 기준)

---

## 1. 배경 및 목적

텔레그램 매수 추천의 1차 목적은 **실행 가능한 매수 리스트**다.
현재 세 가지 불일치가 있다:

1. **대형주 기준이 시장별로 불일치** — KOSPI≤100위 vs KOSDAQ≤50위는 동일 시총 기업을 달리 분류.
2. **거래량 점수가 ML을 덮어씀** — `(-volume_score, -prob, -auc, -trend_score)` 정렬이면 거래량 강한 저신뢰 종목이 상단 점유. 그런데 거래량·추세는 이미 ML 피처(`volume_surge_ratio`, `volume_zscore_20d`, `rsi_14`, `price_momentum_3d` 등)에 포함됨 → 이중계산 모순.
3. **평가 K가 실전 추천 구조와 무관** — 기본 K=20이 4그룹×3 = 12종목 구조와 연결되지 않음.

---

## 2. 변경 범위 (이번 스펙)

### 범위 내

| # | 항목 | 파일 |
|---|------|------|
| A | `BuySignal`에 `market_cap` 필드 추가 | `models/signals.py` |
| B | orchestrator에서 `market_cap` 값 채움 | `agents/orchestrator.py` |
| C | `_is_large_cap()` → 시총 ≥5조 기준 | `agents/report.py` |
| D | `_four_groups()` → 필터 게이트 + AUC가중 ML확률 정렬 | `agents/report.py` |
| E | 평가 K=[5,10,20,30] | `scripts/evaluate_predictions.py` |

### 범위 외 (후속 과제)

- 메시지 포맷·표시 정보 변경 (패턴분석 라인 제거 등)
- 정렬 가중치 AUC→라이브 Prec@K 전환 (6/2 이후 `evaluation_history` 누적 후)
- 학습 조기종료 지표 AUC→TopK 검토 (D2·D5 범위)
- VM `UNIVERSE_MODE` 환경변수 확인

---

## 3. 상세 설계

### A+B. BuySignal.market_cap 추가

```python
# models/signals.py — BuySignal 필드 추가
market_cap: Optional[float] = None   # 시가총액 (원 단위)
```

orchestrator의 `_get_rank_and_market()` 호출 시 같은 pykrx DataFrame에서 `market_cap` 값도 추출해 `BuySignal`에 할당한다. 기존 `mktcap_rank` 할당 코드와 동일 위치.

`market_cap`이 None인 경우(pykrx 조회 실패 등) — `_is_large_cap()`에서 `False` 반환 (중소형 처리). 기존 `mktcap_rank` None 처리와 동일 패턴.

### C. _is_large_cap() 기준 변경

```python
# 변경 전
def _is_large_cap(s: BuySignal) -> bool:
    if not s.market or not s.mktcap_rank:
        return False
    if s.market == "KOSPI":
        return s.mktcap_rank <= 100
    if s.market == "KOSDAQ":
        return s.mktcap_rank <= 50
    return False

# 변경 후
def _is_large_cap(s: BuySignal) -> bool:
    return (s.market_cap or 0) >= 5e12
```

근거: 시총 5조 기준 → 대형 119 / 중소형 232 (전체 351). 현재 순위 기준(대형 138)과 큰 차이 없지만 시장 무관 일관성 확보. `mktcap_rank`·`market` 필드 의존 제거.

### D. _four_groups() 정렬 로직 전환

**필터 게이트** (B등급 기준과 동일):
```
volume_score >= 7  AND  trend_score >= 6
```

게이트 미달 → 버킷에서 제외.  
게이트를 통과해도 12종목 미달 시 — 게이트 없이 ML 확률 순으로 보충 (fallback).

**정렬**:
```python
# 변경 전
def sort_key(item):
    s, prob, label = item
    auc = _LABEL_AUC.get(label, 0.5)
    return (-s.volume_score, -prob, -auc, -s.trend_score)

# 변경 후
def sort_key(item):
    s, prob, label = item
    auc = _LABEL_AUC.get(label, 0.5)
    return (-prob * auc, -s.volume_score, -s.trend_score)
```

AUC가중 ML확률이 1순위. 거래량·추세는 동점 처리(타이브레이크)용.

**Fallback 정책**: 그룹별 게이트 통과 종목이 3개 미만이면, 미달 종목도 ML 확률 순으로 보충해 3개를 채운다. (실거래 공백 방지)

### E. 평가 K 확장

```python
# evaluate_predictions.py
# --top-k 인자를 리스트로 변경 (기존: 단일 int)
parser.add_argument("--top-k", type=int, nargs="+", default=[5, 10, 20, 30])
```

`compute_metrics()`는 K값 목록을 받아 각 K에 대해 Prec@K, Lift@K를 계산.  
출력은 열 추가(Prec@5, Prec@10, Prec@20, Prec@30, Lift@5, ...).  
DB 저장(`--save`)은 일단 K=10, K=20만 (기존 `evaluation_history` 컬럼 유지).

K 의미:
- **K=5**: 그룹당 pick 수(3)보다 조금 넓은 기준
- **K=10**: 전체 추천(12)과 근사
- **K=20, 30**: 브로드 벤치마크

---

## 4. 검증 기준

| 변경 | 검증 방법 |
|------|----------|
| A+B | `BuySignal.market_cap` 필드 존재 확인. 재발송 테스트에서 None 아님 확인 |
| C | 유니버스 351종목 중 대형 ~119개 분류 확인 |
| D | 게이트 미달 종목이 상위에 안 오름 확인. ML 확률 높은 종목이 1순위 |
| E | `python scripts/evaluate_predictions.py --top-k 5 10 20 30` 출력 열 4개 확인 |

---

## 5. 후속 연결 (이번 범위 외)

- **P5 평가 후**: `evaluation_history.prec_at_k` 시계열 누적 → 정렬 가중치를 AUC에서 라이브 Prec@K로 교체 검토
- **Feature 트랙**: 거래량/추세 피처 importance 점검 (추가 아님 — 이미 존재). AUC 0.57 천장은 피처 부재보다 신호 노이즈·라벨 정의에 원인 가능성 높음
- **메시지 포맷**: 패턴분석 라인 제거, 거래량/추세 표시 방식 정리 (별도 태스크)
