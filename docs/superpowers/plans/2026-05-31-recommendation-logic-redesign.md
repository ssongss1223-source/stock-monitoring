# Recommendation Logic Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대형주 기준을 시총 ≥5조로 변경하고, 그룹 정렬을 필터게이트(volume≥7, trend≥6) + AUC가중 ML확률 순으로 전환하며, 평가 K를 [5,10,20,30]으로 확장한다.

**Architecture:** `BuySignal`에 `market_cap` 필드 추가 → orchestrator에서 pykrx 동일 호출로 cap 값 채움 → report.py의 `_is_large_cap()` / `_four_groups()` 로직 교체 → evaluate_predictions.py K 리스트 확장. 4개 파일, 독립 변경.

**Tech Stack:** Python 3.11, DuckDB, pykrx, pytest

---

## 파일 맵

| 파일 | 변경 내용 |
|------|----------|
| `models/signals.py` | `BuySignal`에 `market_cap: Optional[float] = None` 추가 |
| `agents/orchestrator.py` | `_get_rank_and_market()` → cap 값도 반환; 3곳에서 `s.market_cap` 채움 |
| `agents/report.py` | `_is_large_cap()` 기준 교체; `_passes_gate()` / `_pick_group()` 추가; `_four_groups()` 리팩터 |
| `scripts/evaluate_predictions.py` | `--top-k` nargs="+"; `compute_metrics()` / `print_report()` K 리스트 지원 |
| `tests/test_report_logic.py` | 신규 — `_is_large_cap`, `_passes_gate`, `_pick_group` 단위 테스트 |
| `tests/test_evaluate_predictions.py` | 신규 — `compute_metrics` K 리스트 단위 테스트 |

---

## Task 1: BuySignal에 market_cap 필드 추가

**Files:**
- Modify: `models/signals.py:50-56`

- [ ] **Step 1: `market_cap` 필드 추가**

[models/signals.py](models/signals.py)의 `BuySignal` 데이터클래스에서 `mktcap_rank` 바로 다음에 추가:

```python
    market: str = ""               # "KOSPI" | "KOSDAQ"
    mktcap_rank: Optional[int] = None  # 시장 내 시총 순위
    market_cap: Optional[float] = None  # 시가총액 (원 단위)
    label_probs: dict = field(default_factory=dict)  # {label: prob} 9개 전체
```

- [ ] **Step 2: import 확인**

파일 상단에 이미 `from typing import Optional`이 있는지 확인:
```bash
head -10 models/signals.py
```
없으면 추가. (`Optional`은 기존에 이미 쓰이므로 있을 것.)

- [ ] **Step 3: 문법 검사**

```bash
python -c "from models.signals import BuySignal; s = BuySignal.__dataclass_fields__; print('market_cap' in s)"
```
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add models/signals.py
git commit -m "feat: BuySignal에 market_cap 필드 추가"
```

---

## Task 2: orchestrator — market_cap 값 채우기

**Files:**
- Modify: `agents/orchestrator.py`

`_get_rank_and_market()`이 이미 pykrx DataFrame에서 cap 값을 갖고 있지만 버린다. 반환값을 triple로 확장한다.

- [ ] **Step 1: `_get_rank_and_market()` 반환값에 cap dict 추가**

`agents/orchestrator.py`에서 `_get_rank_and_market()` 함수 전체를 교체:

```python
def _get_rank_and_market() -> tuple[dict[str, int], dict[str, str], dict[str, float]]:
    """시총 순위 + 시장(KOSPI/KOSDAQ) + 시총 절대값 동시 반환. DB 최근 거래일 기준."""
    try:
        conn = get_conn(read_only=True)
        try:
            row = conn.execute("SELECT MAX(date) FROM ohlcv_daily").fetchone()
            trade_date = str(row[0]).replace("-", "") if row[0] else date.today().strftime("%Y%m%d")
        finally:
            conn.close()
        rank_result: dict[str, int] = {}
        market_result: dict[str, str] = {}
        cap_result: dict[str, float] = {}
        for mkt in ("KOSPI", "KOSDAQ"):
            df = stock.get_market_cap_by_ticker(trade_date, market=mkt)
            if df is None or df.empty:
                continue
            cap_col = next((c for c in ("시가총액", "Mktcap") if c in df.columns), None)
            if cap_col is None:
                continue
            df = df.sort_values(cap_col, ascending=False)
            for rank, ticker in enumerate(df.index, 1):
                rank_result[ticker] = rank
                market_result[ticker] = mkt
                cap_result[ticker] = float(df.at[ticker, cap_col])
        return rank_result, market_result, cap_result
    except Exception:
        logger.warning("시총 순위 조회 실패")
        return {}, {}, {}
```

- [ ] **Step 2: `_get_mktcap_rank()` 업데이트 (세 값 언패킹)**

```python
def _get_mktcap_rank() -> dict[str, int]:
    rank, _, _cap = _get_rank_and_market()
    return rank
```

- [ ] **Step 3: `resend_signals()`에서 market_cap 채우기**

`resend_signals()` 안의 live_rank/live_market 세팅 블록([orchestrator.py:118-123](agents/orchestrator.py#L118-L123))을 교체:

```python
        live_rank, live_market, live_cap = _get_rank_and_market()
        for s in buy_signals:
            if not s.market:
                s.market = live_market.get(s.ticker, "")
            if s.mktcap_rank is None:
                s.mktcap_rank = live_rank.get(s.ticker)
            if s.market_cap is None:
                s.market_cap = live_cap.get(s.ticker)
```

- [ ] **Step 4: `run_daily()`의 rule 신호에 market_cap 채우기**

`run_daily()` 안의 섹션 4([orchestrator.py:260-265](agents/orchestrator.py#L260-L265))를 교체:

```python
        # ── 4. market / 시총 순위 세팅 (저장 전에 먼저) ──────────────────────
        ticker_market = {t: m for t, m in universe}
        mktcap_rank, _mkt, mktcap_cap = _get_rank_and_market()
        for s in buy_signals:
            s.market = ticker_market.get(s.ticker, "")
            s.mktcap_rank = mktcap_rank.get(s.ticker)
            s.market_cap = mktcap_cap.get(s.ticker)
```

> ⚠️ 주의: 이 변경으로 `_get_mktcap_rank()` 호출이 `_get_rank_and_market()` 직접 호출로 교체된다. 같은 배치 안에서 pykrx 중복 호출을 피하기 위해 반환값을 변수에 저장한다.

- [ ] **Step 5: `run_daily()`의 ML-only 신호에 market_cap 채우기**

ML-only BuySignal 생성 블록([orchestrator.py:322-340](agents/orchestrator.py#L322-L340))에서 `mktcap_rank=mktcap_rank.get(_tk)` 바로 다음에 `market_cap` 추가:

```python
            ml_only_signals.append(BuySignal(
                ticker=_tk,
                name=_get_name(_tk),
                grade="ML",
                total_score=0,
                trend_score=0,
                volume_score=0,
                pattern=None,
                current_price=_price,
                stop_loss=_stop,
                target_price=_target,
                risk_reward=_rr,
                ensemble_prob=_best_prob,
                best_label=_best_label,
                target_is_resistance=_target_is_res,
                market=ticker_market.get(_tk, ""),
                mktcap_rank=mktcap_rank.get(_tk),
                market_cap=mktcap_cap.get(_tk),
                label_probs=_lp,
            ))
```

- [ ] **Step 6: 문법 검사**

```bash
python -c "import agents.orchestrator; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add agents/orchestrator.py
git commit -m "feat: orchestrator — market_cap 값 채우기 (pykrx 동일 호출 활용)"
```

---

## Task 3: report.py — _is_large_cap() 기준 교체

**Files:**
- Modify: `agents/report.py:310-318`
- Create: `tests/test_report_logic.py`

- [ ] **Step 1: 테스트 파일 생성**

```bash
mkdir -p tests
```

`tests/test_report_logic.py` 신규 생성:

```python
import pytest
from dataclasses import dataclass, field
from typing import Optional


# BuySignal 최소 mock (실제 import 대신 — DB 없이 테스트)
@dataclass
class _Signal:
    ticker: str = "000000"
    name: str = "테스트"
    grade: str = "B"
    total_score: int = 17
    trend_score: int = 6
    volume_score: int = 7
    pattern: Optional[str] = None
    current_price: float = 10000.0
    stop_loss: float = 9500.0
    target_price: float = 11000.0
    risk_reward: float = 2.0
    pattern_score: int = 0
    ensemble_prob: Optional[float] = 0.5
    best_label: Optional[str] = "5d_5pct_clean"
    target_is_resistance: bool = False
    market: str = "KOSPI"
    mktcap_rank: Optional[int] = None
    market_cap: Optional[float] = None
    label_probs: dict = field(default_factory=dict)


# ── _is_large_cap 테스트 ─────────────────────────────────────────────────────

def _is_large_cap(s) -> bool:
    return (s.market_cap or 0) >= 5e12


def test_large_cap_above_5jo():
    s = _Signal(market_cap=6e12)
    assert _is_large_cap(s) is True


def test_large_cap_exactly_5jo():
    s = _Signal(market_cap=5e12)
    assert _is_large_cap(s) is True


def test_large_cap_below_5jo():
    s = _Signal(market_cap=4.9e12)
    assert _is_large_cap(s) is False


def test_large_cap_none():
    s = _Signal(market_cap=None)
    assert _is_large_cap(s) is False


def test_large_cap_zero():
    s = _Signal(market_cap=0.0)
    assert _is_large_cap(s) is False
```

- [ ] **Step 2: 테스트 실행 (정의만 했으므로 통과해야 함)**

```bash
python -m pytest tests/test_report_logic.py::test_large_cap_above_5jo tests/test_report_logic.py::test_large_cap_exactly_5jo tests/test_report_logic.py::test_large_cap_below_5jo tests/test_report_logic.py::test_large_cap_none tests/test_report_logic.py::test_large_cap_zero -v
```
Expected: 5 PASSED

- [ ] **Step 3: `_is_large_cap()` 교체**

`agents/report.py`의 `_is_large_cap()` 함수([report.py:310-318](agents/report.py#L310-L318)) 전체를 교체:

```python
def _is_large_cap(s: BuySignal) -> bool:
    """시총 5조 이상 = 대형주 (KOSPI/KOSDAQ 무관 일관 기준)."""
    return (s.market_cap or 0) >= 5e12
```

- [ ] **Step 4: 문법 검사**

```bash
python -c "from agents.report import ReportAgent; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add agents/report.py tests/test_report_logic.py
git commit -m "feat: _is_large_cap() 기준 시총 5조 이상으로 변경"
```

---

## Task 4: report.py — 필터 게이트 + AUC가중 정렬

**Files:**
- Modify: `agents/report.py:248-307`
- Modify: `tests/test_report_logic.py`

ML-only 신호(`grade="ML"`)는 `trend_score=0, volume_score=0`이므로 항상 게이트 미달 → fallback 풀로 처리된다. 이는 의도된 동작 (검증 덜 된 신호는 후순위).

- [ ] **Step 1: 게이트/정렬 테스트 추가**

`tests/test_report_logic.py`에 추가:

```python
# ── _passes_gate 테스트 ──────────────────────────────────────────────────────

_GATE_VOLUME_MIN = 7
_GATE_TREND_MIN = 6


def _passes_gate(s) -> bool:
    return s.volume_score >= _GATE_VOLUME_MIN and s.trend_score >= _GATE_TREND_MIN


def test_gate_passes_exactly_at_minimum():
    s = _Signal(volume_score=7, trend_score=6)
    assert _passes_gate(s) is True


def test_gate_fails_volume_below():
    s = _Signal(volume_score=6, trend_score=6)
    assert _passes_gate(s) is False


def test_gate_fails_trend_below():
    s = _Signal(volume_score=7, trend_score=5)
    assert _passes_gate(s) is False


def test_gate_fails_ml_only_signal():
    """ML-only 신호는 score=0 이므로 항상 게이트 미달."""
    s = _Signal(grade="ML", volume_score=0, trend_score=0)
    assert _passes_gate(s) is False


# ── _pick_group 정렬 테스트 ──────────────────────────────────────────────────

_LABEL_AUC = {"5d_5pct_clean": 0.59, "10d_3pct_clean": 0.54}


def _sort_key(item):
    s, prob, label = item
    auc = _LABEL_AUC.get(label, 0.5)
    return (-prob * auc, -s.volume_score, -s.trend_score)


def _pick_group(bucket, used, n=3):
    available = [v for v in bucket.values() if v[0].ticker not in used]
    gated = sorted([v for v in available if _passes_gate(v[0])], key=_sort_key)
    top = gated[:n]
    if len(top) < n:
        picked = {s.ticker for s, _, _ in top}
        ungated = [v for v in available if not _passes_gate(v[0]) and v[0].ticker not in picked]
        top += sorted(ungated, key=_sort_key)[:n - len(top)]
    return top


def test_pick_group_higher_ml_prob_first():
    """AUC가중 확률 높은 종목이 먼저 와야 한다."""
    s_low = _Signal(ticker="A", volume_score=8, trend_score=7, ensemble_prob=0.40)
    s_high = _Signal(ticker="B", volume_score=8, trend_score=7, ensemble_prob=0.70)
    bucket = {
        "A": (s_low, 0.40, "5d_5pct_clean"),
        "B": (s_high, 0.70, "5d_5pct_clean"),
    }
    result = _pick_group(bucket, used=set())
    assert result[0][0].ticker == "B"


def test_pick_group_gate_fail_goes_to_fallback():
    """게이트 미달 종목은 gated pool 소진 후에만 나온다."""
    s_gated = _Signal(ticker="A", volume_score=8, trend_score=7, ensemble_prob=0.50)
    s_ungated = _Signal(ticker="B", volume_score=3, trend_score=3, ensemble_prob=0.99)
    bucket = {
        "A": (s_gated, 0.50, "5d_5pct_clean"),
        "B": (s_ungated, 0.99, "5d_5pct_clean"),
    }
    result = _pick_group(bucket, used=set())
    # gated 종목이 먼저, ungated는 fallback
    assert result[0][0].ticker == "A"
    assert result[1][0].ticker == "B"


def test_pick_group_respects_used_set():
    """used에 있는 ticker는 반환하지 않는다."""
    s1 = _Signal(ticker="A", volume_score=9, trend_score=8)
    s2 = _Signal(ticker="B", volume_score=8, trend_score=7)
    bucket = {"A": (s1, 0.7, "5d_5pct_clean"), "B": (s2, 0.6, "5d_5pct_clean")}
    result = _pick_group(bucket, used={"A"})
    tickers = [s.ticker for s, _, _ in result]
    assert "A" not in tickers
    assert "B" in tickers


def test_pick_group_fallback_fills_when_gated_insufficient():
    """게이트 통과 종목이 n개 미만이면 ungated로 보충한다."""
    s_gated = _Signal(ticker="A", volume_score=8, trend_score=7)
    s_ungated1 = _Signal(ticker="B", volume_score=2, trend_score=2)
    s_ungated2 = _Signal(ticker="C", volume_score=1, trend_score=1)
    bucket = {
        "A": (s_gated, 0.6, "5d_5pct_clean"),
        "B": (s_ungated1, 0.5, "5d_5pct_clean"),
        "C": (s_ungated2, 0.4, "5d_5pct_clean"),
    }
    result = _pick_group(bucket, used=set(), n=3)
    assert len(result) == 3
    assert result[0][0].ticker == "A"
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
python -m pytest tests/test_report_logic.py -v -k "gate or pick_group"
```
Expected: 6개 중 6개 PASSED (로컬 정의 함수 테스트이므로 바로 통과)

- [ ] **Step 3: `agents/report.py`에 상수 + 헬퍼 함수 추가**

`agents/report.py`의 `_SWING_LABELS` 정의 직후(line 74 이후)에 추가:

```python
_GATE_VOLUME_MIN = 7
_GATE_TREND_MIN = 6


def _passes_gate(s: BuySignal) -> bool:
    """B등급 최소 기준(bull 기준)과 동일. ML-only(score=0)는 항상 미달."""
    return s.volume_score >= _GATE_VOLUME_MIN and s.trend_score >= _GATE_TREND_MIN


def _group_sort_key(item: tuple) -> tuple:
    s, prob, label = item
    auc = _LABEL_AUC.get(label, 0.5)
    return (-prob * auc, -s.volume_score, -s.trend_score)


def _pick_group(
    bucket: dict[str, tuple],
    used: set[str],
    n: int = 3,
) -> list[tuple]:
    """버킷에서 n개 선택. 게이트 통과 우선, 미달 시 fallback으로 보충."""
    available = [v for v in bucket.values() if v[0].ticker not in used]
    gated = sorted([v for v in available if _passes_gate(v[0])], key=_group_sort_key)
    top = gated[:n]
    if len(top) < n:
        picked = {s.ticker for s, _, _ in top}
        ungated = [v for v in available if not _passes_gate(v[0]) and v[0].ticker not in picked]
        top += sorted(ungated, key=_group_sort_key)[:n - len(top)]
    return top
```

- [ ] **Step 4: `_four_groups()` 내부 정렬 로직 교체**

`_four_groups()` 안의 `sort_key` 내부 함수 정의와 그룹 선택 블록([report.py:280-307](agents/report.py#L280-L307))을 교체:

```python
    # 그룹 선점 순서: 단기/대형 → 단기/중소형 → 스윙/대형 → 스윙/중소형
    used: set[str] = set()
    large_short = _pick_group(buckets["ls"], used)
    used |= {s.ticker for s, _, _ in large_short}

    small_short = _pick_group(buckets["ss"], used)
    used |= {s.ticker for s, _, _ in small_short}

    large_swing = _pick_group(buckets["lw"], used)
    used |= {s.ticker for s, _, _ in large_swing}

    small_swing = _pick_group(buckets["sw"], used)

    return large_short, large_swing, small_short, small_swing
```

(기존의 `def sort_key(item):` 내부 함수 블록 전체 삭제, `_sort_signals()` 함수는 건드리지 않음.)

- [ ] **Step 5: 문법 + import 검사**

```bash
python -c "from agents.report import _four_groups, _passes_gate, _pick_group; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add agents/report.py tests/test_report_logic.py
git commit -m "feat: _four_groups 필터게이트(vol>=7,trend>=6) + AUC가중 ML확률 정렬 도입"
```

---

## Task 5: evaluate_predictions.py — K 리스트 확장

**Files:**
- Modify: `scripts/evaluate_predictions.py`
- Create: `tests/test_evaluate_predictions.py`

- [ ] **Step 1: 테스트 파일 생성**

`tests/test_evaluate_predictions.py` 신규 생성:

```python
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.evaluate_predictions import compute_metrics, _prec_at_k


# ── _prec_at_k 테스트 ────────────────────────────────────────────────────────

def test_prec_at_k_basic():
    group = pd.DataFrame({"prob": [0.9, 0.7, 0.5, 0.3], "actual": [1.0, 0.0, 1.0, 0.0]})
    # 상위 2개: prob 0.9(actual=1), 0.7(actual=0) → precision = 0.5
    assert _prec_at_k(group, k=2) == pytest.approx(0.5)


def test_prec_at_k_all_positive():
    group = pd.DataFrame({"prob": [0.9, 0.8], "actual": [1.0, 1.0]})
    assert _prec_at_k(group, k=2) == pytest.approx(1.0)


def test_prec_at_k_larger_than_group():
    """k가 그룹 크기보다 크면 전체 사용."""
    group = pd.DataFrame({"prob": [0.9, 0.8], "actual": [1.0, 0.0]})
    assert _prec_at_k(group, k=10) == pytest.approx(0.5)


# ── compute_metrics K 리스트 테스트 ──────────────────────────────────────────

def _make_df(n_dates=5, n_tickers=30):
    """라벨 하나에 대한 최소 테스트 데이터."""
    rows = []
    for d in range(n_dates):
        for t in range(n_tickers):
            rows.append({
                "signal_date": pd.Timestamp(f"2026-05-{d+1:02d}"),
                "ticker": f"{t:06d}",
                "label": "5d_5pct_clean",
                "prob": (t + 1) / n_tickers,
                "actual": 1.0 if t >= n_tickers - 5 else 0.0,
            })
    return pd.DataFrame(rows)


def test_compute_metrics_returns_columns_for_all_k():
    df = _make_df()
    base_rates = {"5d_5pct_clean": 5 / 30}
    result = compute_metrics(df, base_rates, top_ks=[5, 10, 20, 30])
    assert "prec_at_5" in result.columns
    assert "prec_at_10" in result.columns
    assert "prec_at_20" in result.columns
    assert "prec_at_30" in result.columns
    assert "lift_at_5" in result.columns
    assert "lift_at_30" in result.columns


def test_compute_metrics_prec_values_are_not_null():
    df = _make_df()
    base_rates = {"5d_5pct_clean": 5 / 30}
    result = compute_metrics(df, base_rates, top_ks=[5, 10])
    row = result[result["label"] == "5d_5pct_clean"].iloc[0]
    assert pd.notna(row["prec_at_5"])
    assert pd.notna(row["prec_at_10"])


def test_compute_metrics_smaller_k_higher_precision():
    """상위 5개가 상위 10개보다 precision이 높거나 같아야 한다 (양성이 상위에 집중)."""
    df = _make_df()
    base_rates = {"5d_5pct_clean": 5 / 30}
    result = compute_metrics(df, base_rates, top_ks=[5, 10])
    row = result[result["label"] == "5d_5pct_clean"].iloc[0]
    assert row["prec_at_5"] >= row["prec_at_10"]
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
python -m pytest tests/test_evaluate_predictions.py -v
```
Expected: `test_compute_metrics_*` 3개 FAIL (`compute_metrics` 시그니처 불일치), `test_prec_at_k_*` 3개 PASS.

- [ ] **Step 3: `compute_metrics()` 시그니처 및 내부 K 루프 변경**

`scripts/evaluate_predictions.py`의 `compute_metrics()` 함수 전체를 교체:

```python
def compute_metrics(df: pd.DataFrame, base_rates: dict[str, float], top_ks: list[int] = None) -> pd.DataFrame:
    """라벨별 Brier / Prec@K / Lift@K 계산. top_ks 리스트의 각 K에 대해 열 생성."""
    if top_ks is None:
        top_ks = [5, 10, 20, 30]

    records = []
    for label in _ALL_LABELS:
        sub = df[df["label"] == label].copy()
        sub_labeled = sub.dropna(subset=["actual"])

        n_dates = sub_labeled["signal_date"].nunique()
        n_pairs = len(sub_labeled)

        if n_pairs == 0:
            record: dict = {
                "label": label, "n_dates": 0, "n_pairs": 0,
                "brier": None, "base_rate": base_rates.get(label),
            }
            for k in top_ks:
                record[f"prec_at_{k}"] = None
                record[f"lift_at_{k}"] = None
            records.append(record)
            continue

        brier = float(((sub_labeled["prob"] - sub_labeled["actual"]) ** 2).mean())
        base = base_rates.get(label)

        record = {
            "label": label, "n_dates": n_dates, "n_pairs": n_pairs,
            "brier": round(brier, 4),
            "base_rate": round(base, 4) if base else None,
        }

        for k in top_ks:
            prec_per_date = (
                sub_labeled.groupby("signal_date")
                .apply(lambda g, _k=k: _prec_at_k(g, _k))
                .dropna()
            )
            prec = float(prec_per_date.mean()) if not prec_per_date.empty else None
            lift = (prec / base) if (prec is not None and base and base > 0) else None
            record[f"prec_at_{k}"] = round(prec, 4) if prec is not None else None
            record[f"lift_at_{k}"] = round(lift, 2) if lift is not None else None

        records.append(record)

    return pd.DataFrame(records)
```

- [ ] **Step 4: `print_report()` 동적 K 열 출력으로 변경**

`print_report()` 함수 전체를 교체:

```python
def print_report(metrics: pd.DataFrame, from_date: str, to_date: str) -> None:
    top_ks = sorted([int(c.replace("prec_at_", "")) for c in metrics.columns if c.startswith("prec_at_")])

    header = f"{'라벨':22s} {'N일':>4s} {'N건':>6s}  {'Brier':>6s}"
    for k in top_ks:
        header += f"  {'P@' + str(k):>6s}"
    for k in top_ks:
        header += f"  {'L@' + str(k):>5s}"
    header += f"  {'Base':>5s}"

    print(f"\n=== P5 라이브 예측 평가 ({from_date} ~ {to_date}) ===\n")
    print(header)
    print("-" * len(header))

    for _, row in metrics.iterrows():
        label = str(row["label"])
        n_dates = int(row["n_dates"]) if pd.notna(row["n_dates"]) else 0
        n_pairs = int(row["n_pairs"]) if pd.notna(row["n_pairs"]) else 0
        brier = f"{row['brier']:.4f}" if pd.notna(row.get("brier")) else "  N/A "
        base = f"{row['base_rate']:.2%}" if pd.notna(row.get("base_rate")) else " N/A "

        line = f"{label:22s} {n_dates:>4d} {n_pairs:>6d}  {brier:>6s}"
        for k in top_ks:
            v = row.get(f"prec_at_{k}")
            line += f"  {f'{v:.2%}':>6s}" if pd.notna(v) else "    N/A"
        for k in top_ks:
            v = row.get(f"lift_at_{k}")
            line += f"  {f'{v:.2f}x':>5s}" if pd.notna(v) else "   N/A"
        line += f"  {base:>5s}"
        print(line)

    print()
    labeled = metrics[metrics["n_pairs"] > 0]
    if not labeled.empty:
        avg_brier = labeled["brier"].mean()
        k_summary = "  ".join(
            f"P@{k}={labeled[f'prec_at_{k}'].mean():.2%}  L@{k}={labeled[f'lift_at_{k}'].mean():.2f}x"
            for k in top_ks if f"prec_at_{k}" in labeled.columns
        )
        print(f"평균 (라벨 있는 {len(labeled)}개): Brier={avg_brier:.4f}  {k_summary}")
```

- [ ] **Step 5: `main()`의 `--top-k` 인자 변경**

`main()` 안의 `parser.add_argument("--top-k", ...)` 라인을 교체:

```python
    parser.add_argument(
        "--top-k", type=int, nargs="+", default=[5, 10, 20, 30],
        help="Precision@K의 K값 목록 (기본: 5 10 20 30)",
    )
```

`compute_metrics(df, base_rates, top_k=args.top_k)` 호출 라인도 변경:

```python
    metrics = compute_metrics(df, base_rates, top_ks=args.top_k)
```

- [ ] **Step 6: `save_to_db()` — K=10, K=20만 저장**

`save_to_db()` 함수 내부에서 `prec_at_10`, `prec_at_20` 열을 직접 참조. 기존 코드가 이미 `prec_at_10`, `prec_at_20` 컬럼 이름을 사용하므로 변경 없음. 단 새 시그니처 대응을 위해 `.get()` 방식 확인:

현재 코드 `row.get("prec_at_10")` / `row.get("prec_at_20")` — 그대로 유지. compute_metrics 출력에 해당 열이 top_ks에 포함되면 존재하고 없으면 None.

`save_to_db()` 함수는 **변경 없음**.

- [ ] **Step 7: 테스트 실행 (통과 확인)**

```bash
python -m pytest tests/test_evaluate_predictions.py -v
```
Expected: 6개 PASSED

- [ ] **Step 8: CLI 연기 테스트**

```bash
python scripts/evaluate_predictions.py --help
```
Expected: `--top-k` 가 `nargs="+"`로 나타남.

- [ ] **Step 9: Commit**

```bash
git add scripts/evaluate_predictions.py tests/test_evaluate_predictions.py
git commit -m "feat: evaluate_predictions K 리스트(5,10,20,30) 지원"
```

---

## Task 6: 통합 검증 및 최종 커밋

**Files:**
- Read: `docs/checkpoint.md`

- [ ] **Step 1: 전체 테스트 실행**

```bash
python -m pytest tests/ -v
```
Expected: 전체 PASSED (실패 없음)

- [ ] **Step 2: 모듈 import 체인 검사**

```bash
python -c "
from models.signals import BuySignal
from agents.report import _is_large_cap, _passes_gate, _pick_group, _four_groups
from scripts.evaluate_predictions import compute_metrics
print('모든 import OK')
"
```
Expected: `모든 import OK`

- [ ] **Step 3: checkpoint.md 업데이트**

`docs/checkpoint.md`를 열어 다음 내용으로 업데이트:

- **Current Status** 코드 커밋 해시 갱신
- **Done** 섹션에 추가:
  - `추천 로직 재설계 — 대형주 기준 시총≥5조, 필터게이트(vol≥7,trend≥6), AUC가중 정렬, 평가 K=[5,10,20,30]`
- **Remaining** 섹션에서 완료된 항목 제거, 추가:
  - `메시지 포맷 정리 (패턴분석 라인 제거 등) — 별도 태스크`
  - `정렬 가중치 AUC→라이브Prec@K 전환 (6/2 이후 evaluation_history 누적 후)`
- **Last Updated** 날짜 갱신

- [ ] **Step 4: 최종 커밋**

```bash
git add docs/checkpoint.md
git commit -m "docs: checkpoint 업데이트 — 추천 로직 재설계 완료"
```

---

## 셀프 리뷰

### Spec 커버리지

| 스펙 항목 | 구현 태스크 |
|----------|-----------|
| A: BuySignal.market_cap 추가 | Task 1 |
| B: orchestrator market_cap 채움 | Task 2 |
| C: _is_large_cap() 시총≥5조 | Task 3 |
| D: 필터게이트 + AUC가중 정렬 | Task 4 |
| E: 평가 K=[5,10,20,30] | Task 5 |
| Fallback 정책 (12종목 미달 시 보충) | Task 4 `_pick_group()` |
| ML-only 신호 score=0 → fallback 처리 | Task 4 테스트 `test_gate_fails_ml_only_signal` |
| DB 저장 K=10,20만 (기존 컬럼 유지) | Task 5 Step 6 |

갭 없음.

### 타입 일관성

- `_get_rank_and_market()` → `tuple[dict[str,int], dict[str,str], dict[str,float]]` — Task 2 Step 1에서 정의, Task 2 Step 2/3/4/5에서 동일하게 사용.
- `compute_metrics(df, base_rates, top_ks=list[int])` — Task 5 Step 3에서 정의, Step 5에서 `top_ks=args.top_k`로 호출.
- `_pick_group(bucket, used, n=3)` — Task 4 Step 3에서 정의, Step 4에서 동일 시그니처 호출.
- `_group_sort_key` — Task 4 Step 3에서 정의, `_pick_group` 내부에서 사용.
