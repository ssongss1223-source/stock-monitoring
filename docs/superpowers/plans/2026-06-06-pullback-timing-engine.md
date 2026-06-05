# Pullback Timing Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한국 기초지수(코스피200/코스닥) 20년 데이터로 추세-눌림 타이밍 엔진을 검증하고, 같은 고정 규칙을 개별주에 무튜닝 전이 시험한다.

**Architecture:** 추세게이트(200d MA + 기울기) → 눌림진입(50d MA 근접) → 청산(200d 이탈) 3단계 상태 머신. `Account` / `compute_metrics` 재사용, 신규 파일 3개(신호·러너·백필 스크립트)만 추가. P0(지수 백필) → P1(단일 백테스트) → P2(robustness 스윕) → P3(무튜닝 전이) 순서로 진행.

**Tech Stack:** Python 3.10, pandas, numpy, pykrx, duckdb, pytest

**Branch:** `track-a-pullback-timing`

---

## 파일 구조

| 파일 | 신규/재사용 | 역할 |
|------|-------------|------|
| `scripts/backfill_index_ohlcv.py` | **신규** | P0: pykrx로 지수 20년치 `market_index` 백필 |
| `backtest/pullback_signal.py` | **신규** | 신호 함수: 추세게이트·눌림진입·청산 조건 |
| `backtest/pullback_runner.py` | **신규** | 백테스트 러너: 단일 실행·파라미터 스윕·구간분할 |
| `tests/test_pullback_signal.py` | **신규** | 신호 함수 단위 테스트 |
| `tests/test_pullback_runner.py` | **신규** | 러너 통합 테스트 |
| `scripts/run_pullback_backtest.py` | **신규** | P1~P3 실행 스크립트 (CLI) |
| `backtest/account.py` | 재사용 | 회계 엔진 |
| `backtest/sma_metrics.py` | 재사용 | Calmar/MDD/CAGR/Sharpe |
| `data/db.py` | 재사용 | DB 접속 (`get_conn`) |

---

## Task 1: P0 — 지수 20년치 백필 스크립트

**Files:**
- Create: `scripts/backfill_index_ohlcv.py`

- [ ] **Step 1: 현재 market_index 행수 확인 (VM)**

```
mcp__vm-ssh__ssh_run: cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 -c "
from data.db import get_conn
conn = get_conn(read_only=True)
r = conn.execute(\"SELECT ticker, MIN(date), MAX(date), COUNT(*) FROM market_index GROUP BY ticker\").fetchall()
for x in r: print(x)
conn.close()
"
```
Expected: `('1001', 2023-11-21, 2026-06-xx, ~620)`, `('2001', ...)`

- [ ] **Step 2: 백필 스크립트 작성**

Create `scripts/backfill_index_ohlcv.py`:

```python
#!/usr/bin/env python3
"""market_index에 코스피(1001)/코스닥(2001) 20년치 백필."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from pykrx import stock

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn, init_db

INDICES = {"1001": "KOSPI", "2001": "KOSDAQ"}
START = "20050103"
END   = pd.Timestamp.today().strftime("%Y%m%d")


def fetch_index(ticker: str) -> pd.DataFrame:
    df = stock.get_index_ohlcv_by_date(START, END, ticker)
    if df is None or df.empty:
        raise RuntimeError(f"pykrx 반환 없음: {ticker}")
    df = df.rename(columns={
        "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume", "거래대금": "amount",
        "상장시가총액": "market_cap",
    })
    df.index.name = "date"
    df.index = pd.to_datetime(df.index)
    df["ticker"] = ticker
    return df[["ticker", "open", "high", "low", "close", "volume", "amount", "market_cap"]]


def backfill(conn) -> None:
    for ticker, name in INDICES.items():
        print(f"[{ticker}] {name} 수집 중 ({START}~{END})...", flush=True)
        df = fetch_index(ticker)
        df = df.reset_index()  # date 컬럼으로
        conn.register("_idx_rows", df)
        inserted = conn.execute("""
            INSERT OR REPLACE INTO market_index
                (ticker, date, open, high, low, close, volume, amount, market_cap)
            SELECT ticker, CAST(date AS DATE), open, high, low, close,
                   CAST(volume AS BIGINT), amount, CAST(market_cap AS BIGINT)
            FROM _idx_rows
        """).rowcount
        print(f"  → {len(df)}행 처리 ({ticker})")


if __name__ == "__main__":
    init_db()
    conn = get_conn()
    try:
        backfill(conn)
    finally:
        conn.close()
    print("완료.")
```

- [ ] **Step 3: 로컬 dry-run (pykrx 설치 확인)**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -c "from pykrx import stock; df = stock.get_index_ohlcv_by_date('20250101','20250110','1001'); print(df.head(2))"
```
Expected: 행 2개 출력 (시가·고가·저가·종가·거래량 컬럼).

- [ ] **Step 4: VM에서 백필 실행**

VM에서:
```
cd /opt/stock-monitor && sudo git pull && sudo -u stock .venv/bin/python3 scripts/backfill_index_ohlcv.py
```
Expected: `[1001] KOSPI 수집 중...` → `→ 5200+행 처리`, `[2001] KOSDAQ 수집 중...` → `→ 5200+행 처리`

- [ ] **Step 5: 백필 결과 검증**

```
mcp__vm-ssh__ssh_run: cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 -c "
from data.db import get_conn
conn = get_conn(read_only=True)
r = conn.execute('SELECT ticker, MIN(date), MAX(date), COUNT(*) FROM market_index GROUP BY ticker').fetchall()
for x in r: print(x)
conn.close()
"
```
Expected: `('1001', datetime.date(2005, 1, 3), ..., 5200+)`, `('2001', ...)`

- [ ] **Step 6: 커밋**

```bash
git add scripts/backfill_index_ohlcv.py
git commit -m "feat: 지수 20년치 백필 스크립트 (1001/2001)"
```

---

## Task 2: 신호 함수 구현 (TDD)

**Files:**
- Create: `backtest/pullback_signal.py`
- Create: `tests/test_pullback_signal.py`

- [ ] **Step 1: 실패할 테스트 작성**

Create `tests/test_pullback_signal.py`:

```python
# tests/test_pullback_signal.py
import pandas as pd
import numpy as np
import pytest
from backtest.pullback_signal import (
    regime_gate,
    pullback_entry,
    exit_signal,
    compute_signals,
)


def make_ohlcv(closes: list[float]) -> pd.DataFrame:
    """최소 OHLCV DataFrame 생성 (테스트용). high=close*1.01, low=close*0.99."""
    idx = pd.date_range("2010-01-01", periods=len(closes), freq="B")
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "close": closes,
        "high":  closes * 1.01,
        "low":   closes * 0.99,
    }, index=idx)


# ── regime_gate ─────────────────────────────────────────────────

def test_regime_on_when_above_rising_ma():
    """종가 > MA200이고 MA200이 상승 중이면 True."""
    # 200일 이상 데이터 필요 — 단조 상승 시리즈
    closes = list(range(1, 250))  # 1~249 단조 상승
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    # 마지막 값: close=249, MA200 = mean(50..249) < 249, 상승 중 → True
    assert gate.iloc[-1] == True


def test_regime_off_when_below_ma():
    """종가 < MA200이면 False."""
    closes = list(range(249, 0, -1))  # 단조 하락
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    assert gate.iloc[-1] == False


def test_regime_nan_before_warmup():
    """trend_period 미달 구간은 False (NaN 아님)."""
    df = make_ohlcv([100.0] * 50)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    assert gate.iloc[0] == False
    assert gate.iloc[49] == False


# ── pullback_entry ───────────────────────────────────────────────

def test_pullback_entry_at_mid_ma():
    """추세 ON + 종가가 MA50 ±band 내 → True."""
    # 250일 상승 후 MA50 근처로 눌림
    base = list(range(1, 251))          # 상승 추세 확립
    pullback = [base[-1] * 0.96] * 10  # MA50 근처 눌림 (±5% band)
    closes = base + pullback
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    entry = pullback_entry(df["close"], gate, entry_period=50, band_pct=5.0)
    # 눌림 구간에서 최소 1개 진입 신호
    assert entry.iloc[250:].any()


def test_no_entry_when_regime_off():
    """추세 OFF면 진입 신호 없음."""
    closes = list(range(249, 0, -1))
    df = make_ohlcv(closes)
    gate = regime_gate(df["close"], trend_period=200, slope_lookback=20)
    entry = pullback_entry(df["close"], gate, entry_period=50, band_pct=5.0)
    assert not entry.any()


# ── exit_signal ──────────────────────────────────────────────────

def test_exit_when_below_trend_ma():
    """종가 < MA200이면 청산 신호."""
    closes = list(range(1, 251)) + [50.0] * 5  # 급락
    df = make_ohlcv(closes)
    exits = exit_signal(df["close"], trend_period=200)
    # 급락 구간에서 청산 신호
    assert exits.iloc[252:].any()


# ── compute_signals (통합) ───────────────────────────────────────

def test_compute_signals_returns_correct_columns():
    closes = list(range(1, 260))
    df = make_ohlcv(closes)
    sig = compute_signals(df)
    for col in ("regime", "entry", "exit"):
        assert col in sig.columns, f"컬럼 누락: {col}"


def test_no_entry_and_exit_same_day():
    """같은 날 진입+청산 신호 동시 발생 불가."""
    closes = list(range(1, 260))
    df = make_ohlcv(closes)
    sig = compute_signals(df)
    both = sig["entry"] & sig["exit"]
    assert not both.any(), "진입·청산 동시 발생"
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -m pytest tests/test_pullback_signal.py -v 2>&1 | head -20
```
Expected: `ImportError: cannot import name 'regime_gate'`

- [ ] **Step 3: 신호 함수 구현**

Create `backtest/pullback_signal.py`:

```python
# backtest/pullback_signal.py
"""추세-눌림 타이밍 엔진 신호 함수."""
from __future__ import annotations

import pandas as pd


def regime_gate(
    close: pd.Series,
    trend_period: int = 200,
    slope_lookback: int = 20,
) -> pd.Series:
    """추세 게이트: 종가 > MA(trend_period) AND MA가 상승중.

    Returns:
        Boolean Series. NaN 없음 — 워밍업 기간은 False.
    """
    ma = close.rolling(trend_period).mean()
    above_ma = close > ma
    ma_rising = ma > ma.shift(slope_lookback)
    gate = above_ma & ma_rising
    return gate.fillna(False)


def pullback_entry(
    close: pd.Series,
    gate: pd.Series,
    entry_period: int = 50,
    band_pct: float = 5.0,
) -> pd.Series:
    """눌림 진입: 추세 ON + 종가가 MA(entry_period) ±band_pct% 내.

    진입 조건:
      1. gate == True (추세 살아있음)
      2. close가 MA(entry_period) 아래로 터치 (close <= ma_entry * (1 + band_pct/100))
      3. close >= ma_entry * (1 - band_pct/100) (너무 깊은 붕괴는 제외)

    Returns:
        Boolean Series.
    """
    ma_entry = close.rolling(entry_period).mean()
    upper = ma_entry * (1 + band_pct / 100)
    lower = ma_entry * (1 - band_pct / 100)
    in_band = (close <= upper) & (close >= lower)
    return (gate & in_band).fillna(False)


def exit_signal(
    close: pd.Series,
    trend_period: int = 200,
) -> pd.Series:
    """청산 신호: 종가 < MA(trend_period) — 추세 붕괴.

    Returns:
        Boolean Series.
    """
    ma = close.rolling(trend_period).mean()
    return (close < ma).fillna(False)


def compute_signals(
    ohlcv: pd.DataFrame,
    trend_period: int = 200,
    entry_period: int = 50,
    band_pct: float = 5.0,
    slope_lookback: int = 20,
) -> pd.DataFrame:
    """OHLCV → regime / entry / exit 신호 DataFrame.

    Args:
        ohlcv: DataFrame (index=DatetimeIndex, columns 포함 close)
        trend_period: 추세 MA 기간 (기본 200)
        entry_period: 눌림 MA 기간 (기본 50)
        band_pct: 눌림 band 폭 %, 단방향 (기본 5.0)
        slope_lookback: MA 기울기 확인 기간 (기본 20)

    Returns:
        DataFrame(columns=['regime', 'entry', 'exit'], index=ohlcv.index)
    """
    close = ohlcv["close"]
    gate = regime_gate(close, trend_period, slope_lookback)
    entry = pullback_entry(close, gate, entry_period, band_pct)
    exits = exit_signal(close, trend_period)
    # 진입·청산 동시 불가 — 청산 우선
    entry = entry & ~exits
    return pd.DataFrame({"regime": gate, "entry": entry, "exit": exits},
                        index=ohlcv.index)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -m pytest tests/test_pullback_signal.py -v
```
Expected: 모든 테스트 PASS.

- [ ] **Step 5: 커밋**

```bash
git add backtest/pullback_signal.py tests/test_pullback_signal.py
git commit -m "feat: pullback_signal — regime/entry/exit 신호 함수 + 테스트"
```

---

## Task 3: 백테스트 러너 구현 (TDD)

**Files:**
- Create: `backtest/pullback_runner.py`
- Create: `tests/test_pullback_runner.py`

- [ ] **Step 1: 실패할 테스트 작성**

Create `tests/test_pullback_runner.py`:

```python
# tests/test_pullback_runner.py
import pandas as pd
import numpy as np
import pytest
from backtest.pullback_runner import run_single, sweep_params, split_periods


def make_trending_ohlcv(n: int = 300) -> pd.DataFrame:
    """n일 단조 상승 OHLCV."""
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    closes = pd.Series([100.0 + i * 0.5 for i in range(n)], index=idx)
    return pd.DataFrame({
        "close": closes,
        "high":  closes * 1.005,
        "low":   closes * 0.995,
    })


# ── run_single ───────────────────────────────────────────────────

def test_run_single_returns_metrics_keys():
    df = make_trending_ohlcv(300)
    result = run_single(df)
    for key in ("calmar", "cagr", "mdd", "win_rate", "total_trades", "vs_buyhold"):
        assert key in result, f"키 누락: {key}"


def test_run_single_no_trade_flat_market():
    """횡보장(추세 없음) → regime_gate 미발동 → 거래 0."""
    idx = pd.date_range("2010-01-01", periods=300, freq="B")
    closes = pd.Series([100.0] * 300, index=idx)
    df = pd.DataFrame({"close": closes, "high": closes * 1.001, "low": closes * 0.999})
    result = run_single(df)
    assert result["total_trades"] == 0


def test_run_single_equity_curve_length():
    """equity_curve 길이 = OHLCV 행 수."""
    df = make_trending_ohlcv(300)
    result = run_single(df, return_equity=True)
    assert len(result["equity_curve"]) == len(df)


# ── sweep_params ─────────────────────────────────────────────────

def test_sweep_params_returns_list():
    df = make_trending_ohlcv(300)
    grid = [
        {"trend_period": 150, "entry_period": 40, "band_pct": 5.0},
        {"trend_period": 200, "entry_period": 50, "band_pct": 5.0},
    ]
    results = sweep_params(df, grid)
    assert len(results) == 2
    for r in results:
        assert "calmar" in r
        assert "params" in r


# ── split_periods ─────────────────────────────────────────────────

def test_split_periods_n_chunks():
    df = make_trending_ohlcv(1260)  # ~5년
    chunks = split_periods(df, years=1)
    assert len(chunks) == 5


def test_split_periods_each_chunk_has_data():
    df = make_trending_ohlcv(1260)
    chunks = split_periods(df, years=1)
    for chunk in chunks:
        assert len(chunk) > 0
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -m pytest tests/test_pullback_runner.py -v 2>&1 | head -10
```
Expected: `ImportError: cannot import name 'run_single'`

- [ ] **Step 3: 러너 구현**

Create `backtest/pullback_runner.py`:

```python
# backtest/pullback_runner.py
"""추세-눌림 타이밍 엔진 백테스트 러너."""
from __future__ import annotations

import pandas as pd

from backtest.account import Account
from backtest.sma_metrics import compute_metrics
from backtest.pullback_signal import compute_signals

_COMMISSION = 0.0003


def run_single(
    ohlcv: pd.DataFrame,
    trend_period: int = 200,
    entry_period: int = 50,
    band_pct: float = 5.0,
    slope_lookback: int = 20,
    commission: float = _COMMISSION,
    return_equity: bool = False,
) -> dict:
    """단일 파라미터 백테스트.

    Args:
        ohlcv: DataFrame (index=DatetimeIndex, columns: close, high, low)
        return_equity: True면 결과에 equity_curve 포함

    Returns:
        compute_metrics 결과 dict + params + (선택적) equity_curve
    """
    sig = compute_signals(ohlcv, trend_period, entry_period, band_pct, slope_lookback)
    close = ohlcv["close"]
    high  = ohlcv["high"]
    low   = ohlcv["low"]

    acc = Account(commission=commission)
    in_pos = False

    for i in range(len(ohlcv)):
        dt  = str(ohlcv.index[i].date())
        c   = close.iloc[i]
        mid = (high.iloc[i] + low.iloc[i]) / 2

        if pd.isna(c):
            acc.record_equity(0)
            continue

        if in_pos and sig["exit"].iloc[i]:
            acc.sell(dt, mid, fraction_of_holdings=1.0, reason="trend_break")
            in_pos = False

        if not in_pos and sig["entry"].iloc[i]:
            acc.buy(dt, mid, fraction_of_cash=1.0)
            in_pos = True

        acc.record_equity(c)

    # 마지막날 강제 청산
    if in_pos:
        last_dt  = str(ohlcv.index[-1].date())
        last_mid = (high.iloc[-1] + low.iloc[-1]) / 2
        acc.sell(last_dt, last_mid, fraction_of_holdings=1.0, reason="end_of_period")

    metrics = compute_metrics(acc.trades, acc.equity_curve, close)
    metrics["params"] = {
        "trend_period": trend_period,
        "entry_period": entry_period,
        "band_pct":     band_pct,
        "slope_lookback": slope_lookback,
    }
    if return_equity:
        metrics["equity_curve"] = acc.equity_curve
    return metrics


def sweep_params(
    ohlcv: pd.DataFrame,
    grid: list[dict],
    commission: float = _COMMISSION,
) -> list[dict]:
    """파라미터 그리드 스윕.

    Args:
        grid: [{"trend_period": int, "entry_period": int, "band_pct": float}, ...]

    Returns:
        각 파라미터 조합의 run_single 결과 리스트 (calmar 내림차순)
    """
    results = []
    for p in grid:
        r = run_single(
            ohlcv,
            trend_period=p.get("trend_period", 200),
            entry_period=p.get("entry_period", 50),
            band_pct=p.get("band_pct", 5.0),
            slope_lookback=p.get("slope_lookback", 20),
            commission=commission,
        )
        results.append(r)
    return sorted(results, key=lambda x: x["calmar"], reverse=True)


def split_periods(
    ohlcv: pd.DataFrame,
    years: int = 5,
) -> list[pd.DataFrame]:
    """ohlcv를 years 단위로 분할.

    Returns:
        분할된 DataFrame 리스트 (마지막 조각은 짧을 수 있음)
    """
    days = int(years * 252)
    chunks = []
    start = 0
    while start < len(ohlcv):
        end = min(start + days, len(ohlcv))
        chunk = ohlcv.iloc[start:end]
        if len(chunk) > 0:
            chunks.append(chunk)
        start += days
    return chunks
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -m pytest tests/test_pullback_signal.py tests/test_pullback_runner.py -v
```
Expected: 모든 테스트 PASS.

- [ ] **Step 5: 커밋**

```bash
git add backtest/pullback_runner.py tests/test_pullback_runner.py
git commit -m "feat: pullback_runner — run_single/sweep_params/split_periods + 테스트"
```

---

## Task 4: P1 — 단일 지수 1회 백테스트 (베이스라인 포함)

**Files:**
- Create: `scripts/run_pullback_backtest.py`

- [ ] **Step 1: 실행 스크립트 작성**

Create `scripts/run_pullback_backtest.py`:

```python
#!/usr/bin/env python3
"""P1~P3 풀백 타이밍 엔진 백테스트 실행.

사용:
  python scripts/run_pullback_backtest.py --phase p1
  python scripts/run_pullback_backtest.py --phase p2
  python scripts/run_pullback_backtest.py --phase p3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.pullback_runner import run_single, sweep_params, split_periods
from data.db import get_conn

INDEX_NAMES = {"1001": "KOSPI", "2001": "KOSDAQ"}

# 파라미터 그리드 — robustness 스윕용 (P2)
SWEEP_GRID = [
    {"trend_period": tp, "entry_period": ep, "band_pct": bp}
    for tp in range(150, 260, 10)   # 150~250, 11값
    for ep in range(30, 80, 10)     # 30~70, 5값
    for bp in [3.0, 5.0, 7.0]       # 3값
]  # 11 × 5 × 3 = 165 조합


def load_index(ticker: str, conn) -> pd.DataFrame | None:
    df = conn.execute(
        "SELECT date, open, high, low, close FROM market_index "
        "WHERE ticker = ? ORDER BY date",
        [ticker],
    ).df()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def print_metrics(label: str, m: dict) -> None:
    print(f"\n{'='*55}")
    print(f" {label}")
    print(f"{'='*55}")
    print(f"  Calmar : {m['calmar']:>8.3f}   (목표: BH 대비 우위)")
    print(f"  CAGR   : {m['cagr']:>7.2f}%")
    print(f"  MaxDD  : {m['mdd']:>7.2f}%")
    print(f"  Sharpe : {m.get('sharpe', 0):>8.3f}")
    print(f"  WinRate: {m['win_rate']:>7.1f}%  (참고용)")
    print(f"  Trades : {m['total_trades']:>7d}")
    print(f"  vsBH   : {m['vs_buyhold']:>7.2f}%  (참고용)")


def phase_p1(conn) -> None:
    """단일 파라미터, 기본값(200/50/5%) + 베이스라인(200d 단순교차) 비교."""
    for ticker, name in INDEX_NAMES.items():
        ohlcv = load_index(ticker, conn)
        if ohlcv is None:
            print(f"[{ticker}] 데이터 없음 — P0 먼저 실행하세요")
            continue

        print(f"\n{'#'*55}")
        print(f"# {name} ({ticker}) — {ohlcv.index[0].date()} ~ {ohlcv.index[-1].date()}")
        print(f"# 전체 {len(ohlcv)}일")

        # buy&hold
        bh_cagr = ((ohlcv["close"].iloc[-1] / ohlcv["close"].iloc[0]) **
                   (252 / len(ohlcv)) - 1) * 100
        print(f"\n  Buy&Hold CAGR: {bh_cagr:.2f}%")

        # 베이스라인: 200d 단순교차 (band_pct=0 → entry = regime_gate 즉시 진입)
        baseline = run_single(ohlcv, trend_period=200, entry_period=200,
                              band_pct=0.5, slope_lookback=1)
        print_metrics(f"베이스라인 (200d 교차)", baseline)

        # 눌림 전략: 200/50/5%
        pullback = run_single(ohlcv, trend_period=200, entry_period=50, band_pct=5.0)
        print_metrics(f"눌림 전략 (200/50/5%)", pullback)

        improvement = pullback["calmar"] - baseline["calmar"]
        print(f"\n  눌림 Calmar 개선: {improvement:+.3f}")


def phase_p2(conn) -> None:
    """파라미터 대역 스윕 + 5년 구간 분할."""
    for ticker, name in INDEX_NAMES.items():
        ohlcv = load_index(ticker, conn)
        if ohlcv is None:
            continue

        print(f"\n{'#'*55}")
        print(f"# {name} ROBUSTNESS 스윕 ({len(SWEEP_GRID)}조합)")

        # 전체 기간 스윕
        results = sweep_params(ohlcv, SWEEP_GRID)
        calmars = [r["calmar"] for r in results]
        pos_ratio = sum(1 for c in calmars if c > 0) / len(calmars)
        print(f"\n  전체기간: 양수Calmar비율={pos_ratio:.1%}  "
              f"중앙값={sorted(calmars)[len(calmars)//2]:.3f}  "
              f"상위10%={sorted(calmars)[int(len(calmars)*0.9)]:.3f}")
        best = results[0]
        print(f"  최고: calmar={best['calmar']:.3f}  params={best['params']}")

        # 5년 구간 분할
        print(f"\n  [5년 구간별 기본 파라미터(200/50/5%) 성과]")
        chunks = split_periods(ohlcv, years=5)
        for i, chunk in enumerate(chunks):
            y_start = chunk.index[0].year
            y_end   = chunk.index[-1].year
            m = run_single(chunk)
            flag = "✓" if m["calmar"] > 0 else "✗"
            print(f"  {flag} {y_start}-{y_end}: calmar={m['calmar']:.3f}  "
                  f"cagr={m['cagr']:.1f}%  mdd={m['mdd']:.1f}%")


def phase_p3(conn) -> None:
    """고정 규칙(200/50/5%)을 개별주 23종목에 무튜닝 전이."""
    from backtest.sma_config import UNIVERSE
    print(f"\n{'#'*55}")
    print(f"# P3 무튜닝 전이 시험 (23종목, 고정 200/50/5%)")
    print(f"{'#'*55}")

    rows = []
    for u in UNIVERSE:
        ticker = u["ticker"]
        name   = u["name"]
        df = conn.execute(
            "SELECT date, open, high, low, close FROM ohlcv_daily "
            "WHERE ticker = ? ORDER BY date", [ticker],
        ).df()
        if df.empty or len(df) < 300:
            print(f"  [{ticker}] {name}: 데이터 부족, 스킵")
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        m = run_single(df)
        rows.append({"ticker": ticker, "name": name, **m})

    rows.sort(key=lambda x: x["calmar"], reverse=True)
    print(f"\n  {'종목':<18} {'Calmar':>7} {'CAGR':>7} {'MDD':>7} {'거래':>5}")
    print("  " + "-"*48)
    for r in rows:
        flag = "✓" if r["calmar"] >= 0.2 else "✗"
        print(f"  {flag} {r['name']:<16} {r['calmar']:>7.3f} "
              f"{r['cagr']:>6.1f}% {r['mdd']:>6.1f}% {r['total_trades']:>5}")

    survive = sum(1 for r in rows if r["calmar"] >= 0.2)
    print(f"\n  생존율(Calmar>=0.2): {survive}/{len(rows)} = {survive/len(rows):.1%}")
    print(f"  판정: {'PASS — 무튜닝 전이 성공' if survive/len(rows) >= 0.6 else 'FAIL — 추가 분석 필요'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["p1", "p2", "p3", "all"], default="p1")
    args = parser.parse_args()

    conn = get_conn(read_only=True)
    try:
        if args.phase in ("p1", "all"):
            phase_p1(conn)
        if args.phase in ("p2", "all"):
            phase_p2(conn)
        if args.phase in ("p3", "all"):
            phase_p3(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 로컬 import 확인 (VM 아님)**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -c "from backtest.pullback_signal import compute_signals; from backtest.pullback_runner import run_single; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: 커밋 + push**

```bash
git add scripts/run_pullback_backtest.py
git commit -m "feat: run_pullback_backtest 실행 스크립트 (P1~P3)"
git push origin track-a-pullback-timing
```

---

## Task 5: P1 실행 — 단일 지수 1회 백테스트

- [ ] **Step 1: VM git pull**

```
mcp__vm-ssh__ssh_run: cd /opt/stock-monitor && sudo git fetch origin && sudo git checkout track-a-pullback-timing && sudo git pull origin track-a-pullback-timing
```

- [ ] **Step 2: P1 실행**

```
mcp__vm-ssh__ssh_run: cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 scripts/run_pullback_backtest.py --phase p1
```
Expected 출력 예시:
```
# KOSPI (1001) — 2005-01-03 ~ 2026-xx-xx
  Buy&Hold CAGR: x.xx%
  베이스라인 (200d 교차): calmar=x.xxx ...
  눌림 전략 (200/50/5%): calmar=x.xxx ...
  눌림 Calmar 개선: +x.xxx
```

- [ ] **Step 3: 결과 해석 체크포인트**

다음을 확인:
- 눌림 전략이 베이스라인 대비 Calmar 개선 있나? (없으면 band_pct 조정 고려)
- MaxDD가 buy&hold보다 유의하게 줄었나?
- 거래수가 충분한가? (너무 적으면 통계 부족 — 최소 20회 이상 권장)

결과를 보고 판단. 이상하면 Task 2의 신호 함수 파라미터 재검토.

---

## Task 6: P2 실행 — Robustness 스윕

- [ ] **Step 1: P2 실행 (시간 소요 예상 5~15분)**

```
mcp__vm-ssh__ssh_run (timeout=900): cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 scripts/run_pullback_backtest.py --phase p2
```

- [ ] **Step 2: 합격 기준 확인**

아래 두 조건 모두 충족 시 PASS:
1. **양수Calmar비율 >= 60%** (165조합 중 99개 이상 Calmar > 0)
2. **5년 구간 분할: 5개 구간 중 3개 이상 calmar > 0**

FAIL 시: band_pct 범위 재검토 또는 slope_lookback 조정 후 재실행.

---

## Task 7: P3 실행 — 무튜닝 전이 시험

- [ ] **Step 1: P3 실행**

```
mcp__vm-ssh__ssh_run: cd /opt/stock-monitor && sudo -u stock .venv/bin/python3 scripts/run_pullback_backtest.py --phase p3
```

- [ ] **Step 2: 합격 기준 확인**

**생존율(Calmar >= 0.2) >= 60%** 시 PASS — 무튜닝 전이 성공.

FAIL 시: 생존 종목의 특성(추세형 vs 박스권) 분석 → Layer2 필터 힌트로 기록.

---

## Task 8: 최종 커밋 + checkpoint 업데이트

- [ ] **Step 1: 전체 테스트 통과 확인**

```bash
cd "c:\Users\KHSong\OneDrive\00_claude_code\2605_stock_monitoring" && python -m pytest tests/test_pullback_signal.py tests/test_pullback_runner.py -v
```
Expected: 전체 PASS.

- [ ] **Step 2: checkpoint.md 업데이트**

`docs/checkpoint.md`에 트랙 A 진행 상황 반영:
- Done: 지수 백필, 신호 함수, 러너, P1~P3 결과
- Remaining: P2/P3 결과에 따른 다음 방향

- [ ] **Step 3: 커밋**

```bash
git add docs/checkpoint.md
git commit -m "docs: checkpoint — 트랙 A P1~P3 결과 기록"
```

---

## 자기검토 (Spec Coverage)

| Spec 요구사항 | 구현 Task |
|--------------|-----------|
| P0 지수 20년 백필 | Task 1 |
| 추세게이트(200d+기울기) | Task 2 `regime_gate` |
| 눌림진입(50d±band) | Task 2 `pullback_entry` |
| 청산(200d 이탈) | Task 2 `exit_signal` |
| 포지션 0/1 이진 | Task 3 `run_single` |
| 거래비용 포함 | Task 3 `_COMMISSION=0.0003` |
| 베이스라인 비교(200d 단순교차) | Task 4 `phase_p1` |
| 파라미터 대역 스윕(150~250/30~70) | Task 4 `SWEEP_GRID` + Task 6 |
| 5년 구간 분할 | Task 3 `split_periods` + Task 6 |
| 무튜닝 전이(23종목) | Task 4 `phase_p3` + Task 7 |
| Calmar 중심 평가, 승률은 참고 | Task 4 `print_metrics` |
| account.py 재사용 | Task 3 |
| sma_metrics.py 재사용 | Task 3 |
