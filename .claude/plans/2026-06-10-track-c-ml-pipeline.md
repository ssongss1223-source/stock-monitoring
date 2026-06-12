# Track C ML Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track B와 병렬로 운용되는 Track C ML 파이프라인 구축 — 54+ 일봉 피처 + intraday 피처, 22개 후보 라벨 중 positive rate 필터링을 거친 최종 라벨로 자동 스코어링.

**Architecture:** `run_daily_b`(08:30 UTC) 완료 후 +15분 오프셋으로 `run_daily_c`(08:45 UTC) 가동. 신규 파일은 `_c` 접미사 / `models_c/` 네임스페이스 분리. Track B 코드 일절 수정 없음.

**Tech Stack:** Python 3.x, DuckDB, XGBoost, LightGBM, scikit-learn (ExtraTrees, LogisticRegression), pandas, pytest

---

## 파일 구조

| 신규/수정 | 경로 | 역할 |
|-----------|------|------|
| 수정 | `data/db.py` | `backtest_labels_c` 테이블 추가, `live_eval_daily.model_version` 컬럼 추가, `signal_history_c` 테이블 추가 |
| 신규 | `scripts/labeler_c.py` | 22개 후보 라벨 계산 + positive rate 필터링 |
| 신규 | `scripts/feature_engineering_c.py` | 54+ 일봉 + intraday 피처 → `data/fm_c.parquet` |
| 신규 | `scripts/train_models_c.py` | XGB+LGBM+ET+LR 스태킹, ET 파라미터 수정, `data/models_c/` 출력 |
| 신규 | `agents/orchestrator_c.py` | 일일 스코어링 전용 |
| 신규 | `tests/test_labeler_c.py` | 라벨러 단위 테스트 |
| VM만 | crontab | `run_daily` → `run_daily_b` 이름 변경, `run_daily_c` 신규 등록 |

---

## Task 1: cron 이름 변경 — `run_daily` → `run_daily_b` (VM)

**Files:** VM crontab만

- [ ] **Step 1: VM 현재 crontab 확인**

```bash
# MCP SSH로 실행
crontab -l
```

Expected: `run_daily` 또는 `08:30` 항목 확인

- [ ] **Step 2: crontab 수정 (이름 주석만 변경)**

```bash
# 주석 라벨이 있는 경우
crontab -l | sed 's/# run_daily$/# run_daily_b/' > /tmp/ct_new.txt && crontab /tmp/ct_new.txt
# 검증
crontab -l
```

> 만약 crontab에 이름 주석이 없다면 그냥 넘어가도 됨 — 핵심은 실행 명령 자체가 바뀌지 않는 것.

- [ ] **Step 3: 완료 확인**

```bash
crontab -l | grep "08:30"
```

Expected: 기존 `run_daily` 실행 명령 그대로, 이름 레이블만 변경됨

---

## Task 2: DB 스키마 확장

**Files:**
- Modify: `data/db.py`

- [ ] **Step 1: `data/db.py` 읽기 — `_DDL` 문자열 끝부분(live_eval_daily 이후) 확인**

`data/db.py` 라인 307~331 참조. `live_eval_daily` 테이블이 `_DDL` 마지막 부분임.

- [ ] **Step 2: `_DDL`에 세 테이블 추가**

`data/db.py`의 `_DDL` 문자열에서 `"""` 닫힘 직전에 추가:

```sql
CREATE TABLE IF NOT EXISTS signal_history_c (
    signal_date   DATE,
    ticker        VARCHAR,
    model_version VARCHAR,
    label_probs   JSON,
    top_labels    JSON,
    PRIMARY KEY (signal_date, ticker, model_version)
);

CREATE TABLE IF NOT EXISTS backtest_labels_c (
    signal_date   DATE    NOT NULL,
    ticker        VARCHAR NOT NULL,
    PRIMARY KEY (signal_date, ticker),

    entry_price     DOUBLE,
    return_2d       DOUBLE,
    return_3d       DOUBLE,
    return_5d       DOUBLE,
    max_drawdown_2d DOUBLE,
    max_drawdown_3d DOUBLE,
    max_drawdown_5d DOUBLE,

    label_3d_5pct_first          BOOLEAN,
    label_3d_10pct_first_c       BOOLEAN,
    label_3d_trend_start_atr     BOOLEAN,
    label_5d_7pct_first          BOOLEAN,
    label_5d_10pct_first_c       BOOLEAN,
    label_2d_5pct_first          BOOLEAN,
    label_1d_5pct_first          BOOLEAN,
    label_3d_return_top10pct     BOOLEAN,
    label_3d_return_top20pct     BOOLEAN,
    label_5d_return_top10pct     BOOLEAN,
    label_5d_return_top20pct     BOOLEAN,
    label_3d_market_excess_top20pct  BOOLEAN,
    label_3d_sector_excess_top30pct  BOOLEAN,
    label_5d_market_excess_top20pct  BOOLEAN,
    label_5d_sector_excess_top20pct  BOOLEAN,
    label_5d_dual_excess             BOOLEAN,
    label_3d_bb_upper_break          BOOLEAN,
    label_3d_range_breakout_20d      BOOLEAN,
    label_3d_bb_squeeze_breakout     BOOLEAN,
    label_5d_bb_squeeze_breakout     BOOLEAN,
    label_5d_range_breakout_20d      BOOLEAN,
    label_5d_ma20_reclaim_trend      BOOLEAN
);
```

- [ ] **Step 3: `_MIGRATIONS`에 `live_eval_daily.model_version` ALTER 추가**

`data/db.py`에서 `_MIGRATIONS` 문자열을 찾아 끝에 추가:

```python
_MIGRATIONS = """
...기존 내용...
ALTER TABLE live_eval_daily ADD COLUMN IF NOT EXISTS model_version VARCHAR DEFAULT 'track_b';
"""
```

> `live_eval_daily` PK는 `(eval_date, prediction_date, label_key)`. Track C 라벨명(`label_3d_5pct_first`)은 Track B 라벨명(`label_3d_3pct_clean`)과 겹치지 않으므로 PK 변경 불필요.

- [ ] **Step 4: 로컬 구문 검증**

```bash
python3 -c "from data.db import init_db; init_db(); print('OK')"
```

Expected: `OK` (오류 없음)

- [ ] **Step 5: 커밋**

```bash
git add data/db.py
git commit -m "feat: backtest_labels_c, signal_history_c 테이블 추가 + live_eval_daily model_version 컬럼"
```

---

## Task 3: `tests/test_labeler_c.py` — 실패 테스트 작성 (TDD)

**Files:**
- Create: `tests/test_labeler_c.py`

- [ ] **Step 1: 테스트 작성**

```python
"""Track C 라벨러 단위 테스트."""
import sys
from pathlib import Path
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_window(closes, lows=None, opens=None):
    """N일치 OHLCV DataFrame (DatetimeIndex)."""
    n = len(closes)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    lows = lows or [c * 0.99 for c in closes]
    opens = opens or closes[:]
    return pd.DataFrame({
        "Open":  opens,
        "High":  [c * 1.005 for c in closes],
        "Low":   lows,
        "Close": closes,
    }, index=dates)


def test_first_to_hit_up_first():
    from scripts.labeler_c import _label_first_to_hit
    # close +5.1% on day 1 → should return 1
    df = _make_window([105.1, 100.0, 100.0], lows=[102.0, 99.0, 99.0])
    assert _label_first_to_hit(df, entry_price=100.0, up_pct=0.05, down_pct=-0.035) == 1


def test_first_to_hit_down_first():
    from scripts.labeler_c import _label_first_to_hit
    # low -3.6% on day 1 → should return 0
    df = _make_window([96.0, 104.0, 106.0], lows=[96.0, 103.5, 105.5])
    assert _label_first_to_hit(df, entry_price=100.0, up_pct=0.05, down_pct=-0.035) == 0


def test_first_to_hit_no_hit():
    from scripts.labeler_c import _label_first_to_hit
    # neither hit → 0
    df = _make_window([101.0, 102.0, 103.0], lows=[100.0, 101.0, 102.0])
    assert _label_first_to_hit(df, entry_price=100.0, up_pct=0.05, down_pct=-0.035) == 0


def test_rank_top_pct():
    from scripts.labeler_c import _rank_top_pct
    s = pd.Series([0.10, 0.05, 0.02, -0.01, -0.05], index=list("ABCDE"))
    result = _rank_top_pct(s, top_pct=0.20)
    assert result["A"] == 1  # 상위 20% (5개 중 1개)
    assert result[["B", "C", "D", "E"]].sum() == 0


def test_rank_top_pct_ties():
    from scripts.labeler_c import _rank_top_pct
    s = pd.Series([0.10, 0.10, 0.05, -0.01], index=list("ABCD"))
    result = _rank_top_pct(s, top_pct=0.25)
    # 상위 25% = 1개, 동점이면 둘 다 포함
    assert result["A"] == 1
    assert result["B"] == 1


def test_label_one_c_basic():
    from scripts.labeler_c import label_one_c
    # T=2024-01-01, future: T+1 open=100, T+3 close=106 (+6%) → label_3d_5pct_first=1
    dates = pd.date_range("2024-01-02", periods=6, freq="B")
    df = pd.DataFrame({
        "Open":  [100, 101, 102, 103, 104, 105],
        "High":  [101, 102, 107, 104, 105, 106],
        "Low":   [99, 100, 101, 102, 103, 104],
        "Close": [100, 102, 106, 103, 104, 105],
    }, index=dates)
    result = label_one_c(df, signal_date="2024-01-01", atr=2.0)
    assert result is not None
    assert result["entry_price"] == pytest.approx(100.0)
    assert result["label_3d_5pct_first"] == 1


def test_filter_labels_by_rate():
    from scripts.labeler_c import filter_labels_by_rate
    df = pd.DataFrame({
        "label_a": [1] * 4 + [0] * 96,   # 4% → 탈락 (< 5%)
        "label_b": [1] * 10 + [0] * 90,  # 10% → 통과
        "label_c": [1] * 46 + [0] * 54,  # 46% → 탈락 (> 45%)
        "non_label": [0] * 100,           # label_ 접두사 아님 → 무시
    })
    passed = filter_labels_by_rate(df)
    assert "label_b" in passed
    assert "label_a" not in passed
    assert "label_c" not in passed
    assert "non_label" not in passed
```

- [ ] **Step 2: 실패 확인**

```bash
python3 -m pytest tests/test_labeler_c.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'scripts.labeler_c'`

---

## Task 4: `scripts/labeler_c.py` — 코어 함수 구현

**Files:**
- Create: `scripts/labeler_c.py`

- [ ] **Step 1: 파일 생성 — imports + 상수 + per-ticker 함수**

```python
"""
Track C 라벨러 — 22개 후보 라벨 계산 + positive rate 필터링.

Usage:
    python scripts/labeler_c.py --build [--start 2023-06-07]
    python scripts/labeler_c.py --filter
    python scripts/labeler_c.py --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn

_START_DATE = "2023-06-07"

# first-to-hit 파라미터: (hold_days, up_pct, down_pct)
_FTH_PARAMS = {
    "label_3d_5pct_first":    (3, 0.050, -0.035),
    "label_3d_10pct_first_c": (3, 0.100, -0.040),
    "label_5d_7pct_first":    (5, 0.070, -0.040),
    "label_5d_10pct_first_c": (5, 0.100, -0.050),
    "label_2d_5pct_first":    (2, 0.050, -0.025),
    "label_1d_5pct_first":    (1, 0.050, -0.025),
}

# Phase 1 라벨 (구현 완료)
_PHASE1_LABELS = list(_FTH_PARAMS.keys()) + [
    "label_3d_trend_start_atr",
    "label_3d_return_top10pct", "label_3d_return_top20pct",
    "label_5d_return_top10pct", "label_5d_return_top20pct",
    "label_3d_market_excess_top20pct", "label_3d_sector_excess_top30pct",
    "label_5d_market_excess_top20pct", "label_5d_sector_excess_top20pct",
    "label_5d_dual_excess",
]

_SAVE_COLS = [
    "signal_date", "ticker", "entry_price",
    "return_2d", "return_3d", "return_5d",
    "max_drawdown_2d", "max_drawdown_3d", "max_drawdown_5d",
] + _PHASE1_LABELS


def _label_first_to_hit(
    window: pd.DataFrame,
    entry_price: float,
    up_pct: float,
    down_pct: float,
) -> int:
    """hold window 내 up_pct 도달이 down_pct 도달보다 먼저인지 반환 (1=상승 먼저, 0=미달/하락 먼저)."""
    up_tgt   = entry_price * (1 + up_pct)
    down_tgt = entry_price * (1 + down_pct)
    for _, row in window.iterrows():
        if row["Close"] >= up_tgt:
            return 1
        if row["Low"] <= down_tgt:
            return 0
    return 0


def _rank_top_pct(series: pd.Series, top_pct: float) -> pd.Series:
    """날짜별 수익률 series에서 상위 top_pct 비율의 종목에 1, 나머지 0 부여."""
    n = max(1, int(len(series) * top_pct))
    threshold = series.nlargest(n).min()
    return (series >= threshold).astype(int)


def label_one_c(
    df_daily: pd.DataFrame,
    signal_date: str,
    atr: float,
) -> Optional[dict]:
    """단일 (ticker, signal_date) per-ticker 라벨 계산.

    df_daily: DatetimeIndex DataFrame (Open/High/Low/Close)
    signal_date: 신호일 T
    atr: signal_date의 ATR14 (universe_features_daily.atr_14)
    """
    sd = pd.Timestamp(signal_date)
    future = df_daily[df_daily.index > sd]
    if len(future) < 5:
        return None

    entry_price = float(future["Open"].iloc[0])
    if entry_price <= 0:
        return None

    result: dict = {"entry_price": entry_price}

    # 원시 수익률 + 낙폭
    for d, col in [(2, "2d"), (3, "3d"), (5, "5d")]:
        w = future.iloc[:d]
        result[f"return_{col}"] = (float(w["Close"].iloc[-1]) - entry_price) / entry_price
        result[f"max_drawdown_{col}"] = (float(w["Low"].min()) - entry_price) / entry_price

    # first-to-hit labels
    for lbl, (hold_d, up_pct, down_pct) in _FTH_PARAMS.items():
        w = future.iloc[:hold_d]
        result[lbl] = _label_first_to_hit(w, entry_price, up_pct, down_pct)

    # ATR-based trend start label
    if atr and atr > 0:
        w3 = future.iloc[:3]
        up_tgt   = entry_price + 1.2 * atr
        down_tgt = entry_price - 1.0 * atr
        val = 0
        for _, row in w3.iterrows():
            if row["Close"] >= up_tgt:
                val = 1; break
            if row["Low"] <= down_tgt:
                val = 0; break
        result["label_3d_trend_start_atr"] = val
    else:
        result["label_3d_trend_start_atr"] = None

    return result


def filter_labels_by_rate(
    df: pd.DataFrame,
    min_rate: float = 0.05,
    max_rate: float = 0.45,
) -> list[str]:
    """라벨 컬럼별 positive_rate 기준 필터. [min_rate, max_rate] 범위 통과 라벨 리스트 반환."""
    passed = []
    for col in df.columns:
        if not col.startswith("label_"):
            continue
        rate = float(df[col].dropna().mean())
        status = "✓" if min_rate <= rate <= max_rate else "✗"
        print(f"  {col}: {rate:.1%} {status}")
        if min_rate <= rate <= max_rate:
            passed.append(col)
    return passed
```

- [ ] **Step 2: cross-sectional 및 market/sector excess 함수 추가**

같은 파일에 추가:

```python
def _compute_cross_sectional(conn, date_str: str) -> pd.DataFrame:
    """universe_outcomes에서 return_close를 읽어 날짜별 cross-sectional + market/sector excess 라벨 계산.

    Args:
        date_str: 신호일 T. T+5 거래일 이후 데이터가 universe_outcomes에 있어야 함.
    Returns:
        columns: ticker, label_3d_return_top10pct, ..., label_5d_dual_excess
    """
    df3 = conn.execute("""
        SELECT ticker, return_close AS ret_3d, entry_price
        FROM universe_outcomes WHERE date = CAST(? AS DATE) AND hold_days = 3
    """, [date_str]).df()

    df5 = conn.execute("""
        SELECT ticker, return_close AS ret_5d
        FROM universe_outcomes WHERE date = CAST(? AS DATE) AND hold_days = 5
    """, [date_str]).df()

    if df3.empty or df5.empty:
        return pd.DataFrame()

    df = df3.merge(df5, on="ticker", how="inner")

    # cross-sectional rank
    df["label_3d_return_top10pct"] = _rank_top_pct(df["ret_3d"], 0.10)
    df["label_3d_return_top20pct"] = _rank_top_pct(df["ret_3d"], 0.20)
    df["label_5d_return_top10pct"] = _rank_top_pct(df["ret_5d"], 0.10)
    df["label_5d_return_top20pct"] = _rank_top_pct(df["ret_5d"], 0.20)

    # 시장 수익률 (KOSPI, 거래일 기반 N번째)
    mkt = conn.execute("""
        WITH ranked AS (
            SELECT date, close,
                   ROW_NUMBER() OVER (ORDER BY date) AS rn
            FROM market_index
            WHERE ticker = 'KOSPI'
        )
        SELECT (r3.close / r1.close - 1) AS mkt_3d,
               (r5.close / r1.close - 1) AS mkt_5d
        FROM ranked r1
        JOIN ranked r3 ON r3.rn = r1.rn + 3
        JOIN ranked r5 ON r5.rn = r1.rn + 5
        WHERE r1.date = CAST(? AS DATE)
    """, [date_str]).fetchone()

    if mkt:
        mkt_3d, mkt_5d = float(mkt[0] or 0), float(mkt[1] or 0)
        df["excess_3d"] = df["ret_3d"] - mkt_3d
        df["excess_5d"] = df["ret_5d"] - mkt_5d
        df["label_3d_market_excess_top20pct"] = _rank_top_pct(df["excess_3d"], 0.20)
        df["label_5d_market_excess_top20pct"] = _rank_top_pct(df["excess_5d"], 0.20)
    else:
        for c in ["label_3d_market_excess_top20pct", "label_5d_market_excess_top20pct"]:
            df[c] = None

    # 섹터 초과수익: 섹터 평균 수익 대비 상위
    sector = conn.execute("""
        SELECT uo.ticker, tm.sector
        FROM universe_outcomes uo
        JOIN ticker_master tm ON uo.ticker = tm.ticker
        WHERE uo.date = CAST(? AS DATE) AND uo.hold_days = 3
    """, [date_str]).df()

    if not sector.empty:
        df = df.merge(sector[["ticker", "sector"]], on="ticker", how="left")
        sect_avg_3 = df.groupby("sector")["ret_3d"].transform("mean")
        sect_avg_5 = df.groupby("sector")["ret_5d"].transform("mean") if "sector" in df.columns else 0
        df["sect_excess_3d"] = df["ret_3d"] - sect_avg_3
        df["sect_excess_5d"] = df["ret_5d"] - sect_avg_5 if "sect_excess_5d" not in df.columns else df["sect_excess_5d"]
        df["label_3d_sector_excess_top30pct"] = _rank_top_pct(df["sect_excess_3d"], 0.30)
        df["label_5d_sector_excess_top20pct"] = _rank_top_pct(df["sect_excess_5d"], 0.20)
        df["label_5d_dual_excess"] = (
            (df["excess_5d"] > 0) & (df["sect_excess_5d"] > 0)
        ).astype(int) if "excess_5d" in df else 0
    else:
        for c in ["label_3d_sector_excess_top30pct", "label_5d_sector_excess_top20pct", "label_5d_dual_excess"]:
            df[c] = None

    return_cols = ["ticker"] + [c for c in _PHASE1_LABELS if c in df.columns
                                 and c not in list(_FTH_PARAMS.keys()) + ["label_3d_trend_start_atr"]]
    return df[[c for c in return_cols if c in df.columns]]
```

- [ ] **Step 3: build 함수 + save + CLI 추가**

```python
def build_labels(start_date: str = _START_DATE) -> pd.DataFrame:
    """훈련 기간 전체 라벨 빌드.

    1. 전체 거래일 × 전체 종목에 대해 per-ticker 라벨 계산
    2. universe_outcomes에서 cross-sectional 라벨 계산 후 merge
    3. backtest_labels_c에 upsert
    """
    conn_r = get_conn(read_only=True)
    try:
        # 신호일 목록: universe_daily 기준 (데이터 있는 날만)
        dates = [r[0] for r in conn_r.execute("""
            SELECT DISTINCT date FROM universe_daily
            WHERE date >= CAST(? AS DATE)
            ORDER BY date
        """, [start_date]).fetchall()]

        tickers = [r[0] for r in conn_r.execute(
            "SELECT DISTINCT ticker FROM universe_daily ORDER BY ticker"
        ).fetchall()]

        # ohlcv_daily 전체 로드 (메모리 캐시)
        df_ohlcv = conn_r.execute("""
            SELECT ticker, date, open, high, low, close
            FROM ohlcv_daily ORDER BY ticker, date
        """).df()
        df_ohlcv["date"] = pd.to_datetime(df_ohlcv["date"])

        # ATR 로드
        df_atr = conn_r.execute("""
            SELECT date, ticker, atr_14
            FROM universe_features_daily
            WHERE date >= CAST(? AS DATE)
        """, [start_date]).df()
        df_atr["date"] = pd.to_datetime(df_atr["date"])
        atr_map = df_atr.set_index(["date", "ticker"])["atr_14"].to_dict()

    finally:
        conn_r.close()

    # per-ticker 라벨 계산
    per_ticker_rows = []
    df_ohlcv_by_ticker = {t: g.set_index("date").rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close"
    }) for t, g in df_ohlcv.groupby("ticker")}

    total = len(dates) * len(tickers)
    processed = 0
    for sd in dates:
        sd_ts = pd.Timestamp(sd)
        for ticker in tickers:
            if ticker not in df_ohlcv_by_ticker:
                continue
            df_t = df_ohlcv_by_ticker[ticker]
            atr = atr_map.get((sd_ts, ticker), 0.0)
            result = label_one_c(df_t, sd, atr=atr)
            if result is not None:
                per_ticker_rows.append({
                    "signal_date": sd,
                    "ticker": ticker,
                    **result,
                })
            processed += 1
            if processed % 50000 == 0:
                print(f"  per-ticker: {processed}/{total} ({processed/total:.0%})")

    if not per_ticker_rows:
        return pd.DataFrame()

    df_per = pd.DataFrame(per_ticker_rows)
    print(f"per-ticker 라벨 완료: {len(df_per)}행")

    # cross-sectional 라벨 merge
    conn_r2 = get_conn(read_only=True)
    try:
        cs_rows = []
        # universe_outcomes에 데이터 있는 날만 cross-sectional 계산
        cs_dates = [r[0] for r in conn_r2.execute("""
            SELECT DISTINCT date FROM universe_outcomes
            WHERE hold_days = 5 AND date >= CAST(? AS DATE)
            ORDER BY date
        """, [start_date]).fetchall()]
        for d in cs_dates:
            cs_df = _compute_cross_sectional(conn_r2, str(d))
            if not cs_df.empty:
                cs_df["signal_date"] = d
                cs_rows.append(cs_df)
    finally:
        conn_r2.close()

    if cs_rows:
        df_cs = pd.concat(cs_rows, ignore_index=True)
        cs_merge_cols = ["signal_date", "ticker"] + [
            c for c in df_cs.columns if c.startswith("label_") and c in _PHASE1_LABELS
        ]
        df_per = df_per.merge(
            df_cs[[c for c in cs_merge_cols if c in df_cs.columns]],
            on=["signal_date", "ticker"], how="left",
        )
        print(f"cross-sectional 라벨 merge 완료: {len(cs_rows)}일")

    return df_per


def save_labels_c(df: pd.DataFrame) -> None:
    """backtest_labels_c에 upsert."""
    if df.empty:
        return
    # 저장할 컬럼만 선택 (DB에 없는 컬럼 제외)
    available = [c for c in _SAVE_COLS if c in df.columns]
    df_save = df[available].copy()
    cols = ", ".join(available)
    conn = get_conn()
    try:
        conn.register("_lbl_c", df_save)
        conn.execute(f"""
            INSERT OR REPLACE INTO backtest_labels_c ({cols})
            SELECT {cols} FROM _lbl_c
        """)
        print(f"backtest_labels_c 저장: {len(df_save)}행")
    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Track C 라벨 계산")
    p.add_argument("--build", action="store_true", help="전 기간 라벨 계산 → DB 저장")
    p.add_argument("--filter", action="store_true", help="positive rate 필터 결과 출력")
    p.add_argument("--check", action="store_true", help="DB 저장 라벨 통계 확인")
    p.add_argument("--start", default=_START_DATE, help="훈련 시작일 (기본: 2023-06-07)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.build:
        print(f"=== Track C 라벨 빌드 시작: {args.start} ~ ===")
        df = build_labels(args.start)
        if not df.empty:
            save_labels_c(df)
    if args.filter:
        conn = get_conn(read_only=True)
        try:
            df = conn.execute("SELECT * FROM backtest_labels_c").df()
        finally:
            conn.close()
        print(f"\n=== positive rate 필터 (5% ≤ rate ≤ 45%) ===")
        passed = filter_labels_by_rate(df)
        print(f"\n통과 라벨 {len(passed)}개: {passed}")
    if args.check:
        conn = get_conn(read_only=True)
        try:
            row = conn.execute("SELECT COUNT(*), MIN(signal_date), MAX(signal_date) FROM backtest_labels_c").fetchone()
            print(f"backtest_labels_c: {row[0]}행, {row[1]} ~ {row[2]}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
python3 -m pytest tests/test_labeler_c.py -v
```

Expected: 6개 테스트 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/labeler_c.py tests/test_labeler_c.py
git commit -m "feat: scripts/labeler_c.py Track C 라벨러 구현 (Phase 1 16개 라벨)"
```

---

## Task 5: 라벨 빌드 실행 (VM)

**Files:** VM만

> **사전조건**: Task 2 커밋이 VM에 배포되어 있어야 함 (`sudo git pull` → `init_db()`)

- [ ] **Step 1: VM 배포**

```bash
# VM에서
cd /opt/stock-monitor
sudo git pull
sudo -u stock python3 -c "from data.db import init_db; init_db(); print('OK')"
```

- [ ] **Step 2: 기존 프로세스 확인**

```bash
pgrep -fa "labeler_c|train_models"
```

Expected: 아무것도 없음

- [ ] **Step 3: 라벨 빌드 실행 (screen)**

```bash
sudo -u stock screen -dm bash -c "cd /opt/stock-monitor && python3 scripts/labeler_c.py --build 2>&1 | tee /tmp/labeler_c_build.log"
```

- [ ] **Step 4: 진행 상황 모니터링**

```bash
tail -f /tmp/labeler_c_build.log
# Ctrl+C로 종료 (screen은 계속 실행됨)
```

- [ ] **Step 5: 완료 후 필터 결과 확인**

```bash
sudo -u stock python3 scripts/labeler_c.py --check
sudo -u stock python3 scripts/labeler_c.py --filter
```

Expected: 16개 라벨 중 positive rate 5~45% 통과 라벨 목록 출력. 기록해둘 것 (최종 훈련 대상 라벨 결정).

---

## Task 6: `tests/test_feature_engineering_c.py` — 실패 테스트 작성 ✅ DONE

**Files:**
- Create: `tests/test_feature_engineering_c.py`

- [x] **Step 1: 테스트 작성**

```python
"""Track C 피처 엔지니어링 단위 테스트."""
import sys
from pathlib import Path
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_ohlcv_min(ticker="A005930", n_days=5, bars_per_day=7):
    """n_days × bars_per_day 60분봉 DataFrame."""
    rows = []
    base = pd.Timestamp("2024-01-02 09:00:00")
    for d in range(n_days):
        for b in range(bars_per_day):
            dt = base + pd.Timedelta(days=d, hours=b)
            price = 100 + d + b * 0.1
            rows.append({
                "ticker": ticker, "dt": dt,
                "open": price, "high": price * 1.005,
                "low": price * 0.995, "close": price,
                "volume": 10000 + b * 1000,
                "amount": (10000 + b * 1000) * price,
            })
    return pd.DataFrame(rows)


def test_intraday_features_returns_df():
    from scripts.feature_engineering_c import compute_intraday_features
    df_min = _make_ohlcv_min()
    result = compute_intraday_features(df_min)
    assert isinstance(result, pd.DataFrame)
    assert "ticker" in result.columns
    assert "date" in result.columns


def test_intraday_features_columns():
    from scripts.feature_engineering_c import compute_intraday_features, _INTRADAY_FEAT_COLS
    df_min = _make_ohlcv_min()
    result = compute_intraday_features(df_min)
    for col in _INTRADAY_FEAT_COLS:
        assert col in result.columns, f"Missing: {col}"


def test_vwap_close_ratio_direction():
    from scripts.feature_engineering_c import compute_intraday_features
    # close가 VWAP보다 높도록 설계: 마지막 bar close 높게
    rows = _make_ohlcv_min(n_days=1, bars_per_day=7).to_dict("records")
    rows[-1]["close"] = 200.0  # 마지막 bar close 대폭 높임
    df_min = pd.DataFrame(rows)
    result = compute_intraday_features(df_min)
    assert result.iloc[0]["vwap_close_ratio"] > 0  # close > VWAP


def test_vol_ratios_sum_to_one():
    from scripts.feature_engineering_c import compute_intraday_features
    df_min = _make_ohlcv_min(n_days=1, bars_per_day=7)
    result = compute_intraday_features(df_min)
    total = (
        result["vol_front_ratio"].iloc[0]
        + result["vol_mid_ratio"].iloc[0]
        + result["vol_tail_ratio"].iloc[0]
    )
    assert abs(total - 1.0) < 1e-6
```

- [ ] **Step 2: 실패 확인**

```bash
python3 -m pytest tests/test_feature_engineering_c.py -v 2>&1 | head -20
```

Expected: `ImportError`

- [x] **Step 2: 실패 확인** (완료 — 구현 파일과 함께 작성)

---

## Task 7: `scripts/feature_engineering_c.py` — 구현 ✅ DONE (86 features: 55 기존 + 16 extra daily + 15 intraday)

**Files:**
- Create: `scripts/feature_engineering_c.py`

- [x] **Step 1: 파일 생성 — imports + 상수**

```python
"""
Track C 피처 엔지니어링 — 54개 일봉 + intraday 피처 → data/fm_c.parquet

Usage:
    python scripts/feature_engineering_c.py --mode train [--output data/fm_c.parquet]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.db import get_conn

_DEFAULT_OUTPUT = "data/fm_c.parquet"
_TRAIN_START = "2023-06-07"

# 일봉 피처: Section A (21) + Section B (10) + Section C (17) + universe_daily 미사용 (7) = 55
_DAILY_FEAT_COLS = [
    # Section A
    "ma_cross_5_20", "obv_slope_5d", "high_low_ratio", "body_ratio",
    "short_balance_ratio", "short_volume_ratio_5d", "short_balance_change_5d",
    "volume_surge_ratio", "amount_surge_ratio",
    "price_momentum_3d", "price_momentum_10d",
    "inst_net_20d", "foreign_exh_change_5d", "roe_proxy",
    "relative_strength_5d", "combined_net_5d",
    "kospi_above_ma60", "market_volatility_20d",
    "grade_S", "grade_A", "grade_B",
    # Section B
    "bb_width", "atr_14", "atr_ratio_60d",
    "volume_zscore_20d", "amount_zscore_20d",
    "rs_20d", "rs_rank_pct", "market_breadth",
    "breakout_distance_20d", "box_tightness_20d",
    # Section C
    "breakout_distance_60d", "breakout_distance_120d",
    "range_80d_pct", "distance_from_ma224",
    "up_days_5d", "gap_percent", "opening_strength", "intraday_close_strength",
    "recovery_from_low_80d", "volume_acceleration", "volume_dryup_ratio",
    "retracement_ratio", "pullback_depth",
    "bb_width_pct_252", "turnover_rank_pct", "amount_rank_pct", "volatility_rank_pct",
    # universe_daily 미사용 7개
    "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
    "close_to_20ma_ratio", "close_to_60ma_ratio", "close_to_5ma_ratio",
]

# Intraday 피처 (60분봉 → 일별 집계)
_INTRADAY_FEAT_COLS = [
    "vwap_close_ratio",
    "vol_front_ratio", "vol_tail_ratio", "vol_mid_ratio",
    "am_return", "pm_return", "am_pm_return_diff",
    "intraday_range_ratio", "open_to_high_ratio", "open_to_low_ratio",
    "intraday_close_strength_min",
    "vol_front_ratio_5d", "vwap_consistency_5d",
]
```

- [x] **Step 2: `compute_intraday_features` 구현**

```python
def compute_intraday_features(df_min: pd.DataFrame) -> pd.DataFrame:
    """60분봉 DataFrame에서 일별 intraday 피처 계산.

    Args:
        df_min: columns = [ticker, dt, open, high, low, close, volume, amount]
                dt는 TIMESTAMP (pandas Timestamp 또는 string)
    Returns:
        columns = [ticker, date] + _INTRADAY_FEAT_COLS
    """
    df = df_min.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    df["date"] = df["dt"].dt.date
    df["hour"] = df["dt"].dt.hour
    df = df.sort_values(["ticker", "dt"])

    records = []
    for (ticker, date_), g in df.groupby(["ticker", "date"]):
        total_vol = g["volume"].sum()
        if total_vol == 0:
            continue

        vwap = (g["close"] * g["volume"]).sum() / total_vol
        day_open  = float(g.sort_values("hour")["open"].iloc[0])
        day_close = float(g.sort_values("hour")["close"].iloc[-1])
        day_high  = float(g["high"].max())
        day_low   = float(g["low"].min())

        front_vol = float(g[g["hour"] <= 10]["volume"].sum())
        tail_vol  = float(g[g["hour"] >= 14]["volume"].sum())
        mid_vol   = max(0.0, total_vol - front_vol - tail_vol)

        am_g = g[g["hour"] < 12].sort_values("hour")
        pm_g = g[g["hour"] >= 12].sort_values("hour")
        am_open  = float(am_g["open"].iloc[0])  if not am_g.empty else day_open
        am_close = float(am_g["close"].iloc[-1]) if not am_g.empty else day_open
        pm_open  = float(pm_g["open"].iloc[0])  if not pm_g.empty else am_close
        pm_close = float(pm_g["close"].iloc[-1]) if not pm_g.empty else am_close

        am_ret = (am_close - am_open) / am_open if am_open > 0 else 0.0
        pm_ret = (pm_close - pm_open) / pm_open if pm_open > 0 else 0.0

        records.append({
            "ticker": ticker,
            "date": date_,
            "vwap_close_ratio": (day_close - vwap) / vwap if vwap > 0 else 0.0,
            "vol_front_ratio": front_vol / total_vol,
            "vol_tail_ratio":  tail_vol  / total_vol,
            "vol_mid_ratio":   mid_vol   / total_vol,
            "am_return":           am_ret,
            "pm_return":           pm_ret,
            "am_pm_return_diff":   am_ret - pm_ret,
            "intraday_range_ratio": (day_high - day_low) / day_open if day_open > 0 else 0.0,
            "open_to_high_ratio": (day_high - day_open) / day_open if day_open > 0 else 0.0,
            "open_to_low_ratio":  (day_low  - day_open) / day_open if day_open > 0 else 0.0,
            "intraday_close_strength_min": (
                (day_close - day_low) / (day_high - day_low)
                if day_high > day_low else 0.5
            ),
        })

    if not records:
        return pd.DataFrame(columns=["ticker", "date"] + _INTRADAY_FEAT_COLS)

    result = pd.DataFrame(records)
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["ticker", "date"])

    # 5일 롤링 피처 (window function이므로 ticker 내 순서 유지 필수)
    result["vol_front_ratio_5d"] = result.groupby("ticker")["vol_front_ratio"].transform(
        lambda x: x.rolling(5, min_periods=1).mean()
    )
    result["vwap_consistency_5d"] = result.groupby("ticker")["vwap_close_ratio"].transform(
        lambda x: x.abs().rolling(5, min_periods=1).mean()
    )

    return result[["ticker", "date"] + _INTRADAY_FEAT_COLS]
```

- [x] **Step 3: `build_fm_c` 구현 (train 모드)**

```python
def build_fm_c(output_path: str = _DEFAULT_OUTPUT) -> pd.DataFrame:
    """fm_c.parquet 빌드.

    backtest_labels_c × universe_features_daily × universe_daily × ohlcv_min 피처를 JOIN.
    """
    conn = get_conn(read_only=True)
    try:
        # 라벨 (훈련 타깃 포함)
        df_labels = conn.execute("""
            SELECT * FROM backtest_labels_c
            WHERE signal_date >= CAST(? AS DATE)
        """, [_TRAIN_START]).df()
        print(f"backtest_labels_c: {len(df_labels)}행")

        if df_labels.empty:
            raise RuntimeError("backtest_labels_c 비어 있음. labeler_c.py --build 먼저 실행")

        # 일봉 피처 (universe_features_daily 컬럼)
        feat_ufd_cols = [c for c in _DAILY_FEAT_COLS if c not in {
            "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
            "close_to_20ma_ratio", "close_to_60ma_ratio", "close_to_5ma_ratio",
        }]
        ufd_cols_sql = ", ".join(f"ufd.{c}" for c in feat_ufd_cols)

        # universe_daily 7개 컬럼
        ud_cols = [
            "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
            "close_to_20ma_ratio", "close_to_60ma_ratio", "close_to_5ma_ratio",
        ]
        ud_cols_sql = ", ".join(f"ud.{c}" for c in ud_cols)

        df_feats = conn.execute(f"""
            SELECT ufd.date, ufd.ticker,
                   {ufd_cols_sql},
                   {ud_cols_sql}
            FROM universe_features_daily ufd
            LEFT JOIN universe_daily ud
                   ON ud.date = ufd.date AND ud.ticker = ufd.ticker
            WHERE ufd.date >= CAST(? AS DATE)
        """, [_TRAIN_START]).df()
        print(f"universe_features_daily JOIN: {len(df_feats)}행")

        # 60분봉 intraday 피처 (전체 로드 후 집계)
        df_min = conn.execute("""
            SELECT ticker, dt, open, high, low, close, volume, amount
            FROM ohlcv_min
            WHERE CAST(dt AS DATE) >= CAST(? AS DATE)
        """, [_TRAIN_START]).df()
        print(f"ohlcv_min 로드: {len(df_min)}행")

    finally:
        conn.close()

    df_intraday = compute_intraday_features(df_min)
    df_intraday["date"] = pd.to_datetime(df_intraday["date"])
    print(f"intraday 피처 계산: {len(df_intraday)}행")

    # 병합
    df_labels["signal_date"] = pd.to_datetime(df_labels["signal_date"])
    df_feats["date"] = pd.to_datetime(df_feats["date"])

    df = df_labels.merge(
        df_feats.rename(columns={"date": "signal_date"}),
        on=["signal_date", "ticker"], how="left",
    )
    df = df.merge(
        df_intraday.rename(columns={"date": "signal_date"}),
        on=["signal_date", "ticker"], how="left",
    )

    df.to_parquet(output_path, index=False)
    print(f"fm_c.parquet 저장: {len(df)}행 → {output_path}")
    return df


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["train"], default="train")
    p.add_argument("--output", default=_DEFAULT_OUTPUT)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    build_fm_c(args.output)


if __name__ == "__main__":
    main()
```

- [x] **Step 4: 테스트 통과 확인**

```bash
python3 -m pytest tests/test_feature_engineering_c.py -v
```

Expected: 4개 테스트 모두 PASS → **실제: 11/11 PASS** (4 intraday + 7 extra daily)

- [ ] **Step 5: 커밋**

```bash
git add scripts/feature_engineering_c.py tests/test_feature_engineering_c.py
git commit -m "feat: scripts/feature_engineering_c.py Track C 피처 엔지니어링 (86 features: 55+16+15)"
```

---

## Task 8: fm_c.parquet 빌드 (VM)

- [ ] **Step 1: VM 배포**

```bash
cd /opt/stock-monitor && sudo git pull
```

- [ ] **Step 2: 기존 프로세스 확인**

```bash
pgrep -fa "feature_engineering_c"
```

- [ ] **Step 3: fm_c.parquet 빌드 실행**

```bash
sudo -u stock screen -dm bash -c "cd /opt/stock-monitor && python3 scripts/feature_engineering_c.py --mode train 2>&1 | tee /tmp/fe_c_build.log"
```

- [ ] **Step 4: 완료 확인**

```bash
tail -20 /tmp/fe_c_build.log
sudo -u stock python3 -c "
import pandas as pd
df = pd.read_parquet('data/fm_c.parquet')
print(f'rows: {len(df)}, cols: {len(df.columns)}')
print(df.dtypes.value_counts())
print(df[['signal_date']].agg(['min','max']))
"
```

Expected: `~300k행 이상, 70개 이상 컬럼`

---

## Task 9: `scripts/train_models_c.py` — 구현

**Files:**
- Create: `scripts/train_models_c.py`

`scripts/train_models.py`를 기반으로 다음 사항만 변경:

| 항목 | Track B | Track C |
|------|---------|---------|
| `_ET_PARAMS` | `max_depth=None, min_samples_leaf=10` | `max_depth=10, min_samples_leaf=20, max_features=0.4` |
| 입력 parquet | `data/feature_matrix.parquet` | `data/fm_c.parquet` |
| 출력 디렉토리 | `data/models/` | `data/models_c/` |
| model_meta | `data/model_meta.json` | `data/model_meta_c.json` |
| oof_predictions | `data/oof_predictions.parquet` | `data/oof_predictions_c.parquet` |
| `_TARGETS` | 18개 Track B 라벨 | Task 5 filter 결과 라벨 |

- [ ] **Step 1: train_models.py 복사 후 수정**

```bash
cp scripts/train_models.py scripts/train_models_c.py
```

- [ ] **Step 2: `train_models_c.py` 수정 — 5가지 변경**

변경 1 — `_ET_PARAMS`:
```python
# 기존
_ET_PARAMS = dict(
    n_estimators=300, max_depth=None, min_samples_leaf=10,
    class_weight="balanced", random_state=42, n_jobs=-1,
)

# 변경 후
_ET_PARAMS = dict(
    n_estimators=300, max_depth=10,
    min_samples_leaf=20, max_features=0.4,
    class_weight="balanced", random_state=42, n_jobs=-1,
)
```

변경 2 — `_TARGETS` (Task 5 filter 결과 붙여넣기, 예시):
```python
# Task 5 filter 결과로 교체
_TARGETS = [
    "label_3d_5pct_first",
    "label_3d_10pct_first_c",
    "label_5d_7pct_first",
    "label_5d_10pct_first_c",
    "label_3d_return_top10pct",
    "label_3d_return_top20pct",
    "label_5d_return_top10pct",
    "label_5d_return_top20pct",
    "label_3d_market_excess_top20pct",
    "label_5d_market_excess_top20pct",
    "label_5d_dual_excess",
    # Task 5 실제 결과로 최종 결정
]
```

변경 3 — 경로 상수:
```python
_MODEL_DIR = Path("data/models_c")
_META_PATH = Path("data/model_meta_c.json")
_OOF_PATH  = Path("data/oof_predictions_c.parquet")
_FM_PATH   = "data/fm_c.parquet"
```

변경 4 — `_DROP` 세트에 Track C 원시 측정값 추가:
```python
_DROP = {
    "signal_date", "ticker", "entry_price", "close",
    "return_2d", "return_3d", "return_5d",
    "max_drawdown_2d", "max_drawdown_3d", "max_drawdown_5d",
}
```

변경 5 — argparse `--feature-matrix` 기본값:
```python
p.add_argument("--feature-matrix", default=str(_FM_PATH), ...)
```

- [ ] **Step 3: 로컬 구문 검증**

```bash
python3 -c "import scripts.train_models_c; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: 커밋**

```bash
git add scripts/train_models_c.py
git commit -m "feat: scripts/train_models_c.py Track C 모델 훈련 (ET max_depth=10 수정)"
```

---

## Task 10: 모델 훈련 실행 (VM)

- [ ] **Step 1: VM 배포**

```bash
cd /opt/stock-monitor && sudo git pull
```

- [ ] **Step 2: 훈련 시간 추정 확인**

```bash
sudo -u stock python3 -c "
import pandas as pd
df = pd.read_parquet('data/fm_c.parquet')
print('rows:', len(df), '| targets:', len([c for c in df.columns if c.startswith('label_')]))
"
```

- [ ] **Step 3: 훈련 실행 (screen)**

```bash
sudo -u stock screen -dm bash -c "cd /opt/stock-monitor && python3 scripts/train_models_c.py 2>&1 | tee /tmp/train_c.log"
```

- [ ] **Step 4: 훈련 완료 확인 (~1~2시간 소요)**

```bash
tail -50 /tmp/train_c.log
ls -la data/models_c/ | head -20
cat data/model_meta_c.json | python3 -m json.tool | head -40
```

Expected: 각 라벨별 `xgb_*.json`, `lgbm_*.txt`, `et_*.pkl`, `lr_stacker_*.pkl` 생성 확인

---

## Task 11: `agents/orchestrator_c.py` — 스코어링 전용

**Files:**
- Create: `agents/orchestrator_c.py`

- [ ] **Step 1: 파일 생성**

```python
"""
Track C 일일 스코어링 오케스트레이터 — 스코어링 전용 (훈련 없음).

run_daily_c cron에서 호출됨 (08:45 UTC = 17:45 KST).
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

from data.db import get_conn
from scripts.feature_engineering_c import (
    compute_intraday_features,
    _DAILY_FEAT_COLS,
    _INTRADAY_FEAT_COLS,
)

logger = logging.getLogger(__name__)

_MODEL_DIR  = Path("data/models_c")
_META_PATH  = Path("data/model_meta_c.json")
_TOP_K      = 10


def _load_models(label: str) -> dict:
    """label에 대한 xgb/lgbm/et/lr 모델 로드."""
    models = {}
    p = _MODEL_DIR / f"xgb_label_{label}.json"
    if p.exists():
        m = xgb.XGBClassifier(); m.load_model(str(p)); models["xgb"] = m
    p = _MODEL_DIR / f"lgbm_label_{label}.txt"
    if p.exists() and HAS_LGBM:
        models["lgbm"] = lgb.Booster(model_file=str(p))
    p = _MODEL_DIR / f"et_label_{label}.pkl"
    if p.exists():
        models["et"] = joblib.load(p)
    p = _MODEL_DIR / f"lr_stacker_{label}.pkl"
    if p.exists():
        models["lr"] = joblib.load(p)
    return models


def _predict_proba(models: dict, X: pd.DataFrame) -> np.ndarray:
    """4개 모델 예측 → LR 스태킹 (없으면 평균)."""
    X_fill = X.fillna(0)
    preds = {}
    if "xgb" in models:
        preds["xgb"] = models["xgb"].predict_proba(X)[:, 1]
    if "lgbm" in models:
        preds["lgbm"] = models["lgbm"].predict(X_fill)
    if "et" in models:
        preds["et"] = models["et"].predict_proba(X_fill)[:, 1]
    if not preds:
        return np.zeros(len(X))
    stack = np.column_stack(list(preds.values()))
    if "lr" in models:
        return models["lr"].predict_proba(stack)[:, 1]
    return stack.mean(axis=1)


def score_today(today: date | None = None) -> dict[str, pd.DataFrame]:
    """오늘 날짜 기준 Track C 스코어링 실행.

    Returns:
        {label_key: DataFrame(ticker, prob)} 형식
    """
    today = today or date.today()
    today_str = str(today)
    logger.info(f"Track C scoring: {today_str}")

    if not _META_PATH.exists():
        raise FileNotFoundError(f"{_META_PATH} 없음. train_models_c.py 먼저 실행")

    with open(_META_PATH) as f:
        meta = json.load(f)
    labels = list(meta.keys())

    conn = get_conn(read_only=True)
    try:
        # 일봉 피처
        feat_cols_sql = ", ".join(f"ufd.{c}" for c in _DAILY_FEAT_COLS
                                   if c not in {"close_to_52w_high", "bb_position", "rsi_14",
                                                "foreign_net_20d", "close_to_20ma_ratio",
                                                "close_to_60ma_ratio", "close_to_5ma_ratio"})
        ud_cols_sql = ", ".join(f"ud.{c}" for c in [
            "close_to_52w_high", "bb_position", "rsi_14", "foreign_net_20d",
            "close_to_20ma_ratio", "close_to_60ma_ratio", "close_to_5ma_ratio",
        ])
        df_daily = conn.execute(f"""
            SELECT ufd.ticker, {feat_cols_sql}, {ud_cols_sql}
            FROM universe_features_daily ufd
            LEFT JOIN universe_daily ud ON ud.date = ufd.date AND ud.ticker = ufd.ticker
            WHERE ufd.date = CAST(? AS DATE)
        """, [today_str]).df()

        # intraday 피처
        df_min = conn.execute("""
            SELECT ticker, dt, open, high, low, close, volume, amount
            FROM ohlcv_min
            WHERE CAST(dt AS DATE) = CAST(? AS DATE)
        """, [today_str]).df()
    finally:
        conn.close()

    if df_daily.empty:
        logger.warning(f"universe_features_daily 데이터 없음: {today_str}")
        return {}

    df_intraday = compute_intraday_features(df_min) if not df_min.empty else pd.DataFrame()

    df = df_daily.copy()
    if not df_intraday.empty:
        df_intraday["date"] = pd.to_datetime(df_intraday["date"])
        today_ts = pd.Timestamp(today_str)
        df_today_intra = df_intraday[df_intraday["date"] == today_ts][
            ["ticker"] + _INTRADAY_FEAT_COLS
        ]
        df = df.merge(df_today_intra, on="ticker", how="left")

    # 모델에서 사용할 피처 컬럼 (label_ 컬럼 제외)
    feat_cols = [c for c in df.columns if c != "ticker" and not c.startswith("label_")]
    X = df[feat_cols]

    results = {}
    for label in labels:
        models = _load_models(label)
        if not models:
            continue
        probs = _predict_proba(models, X)
        df_result = pd.DataFrame({"ticker": df["ticker"].values, "prob": probs})
        df_result = df_result.sort_values("prob", ascending=False).head(_TOP_K)
        results[label] = df_result

    return results


def save_results(results: dict[str, pd.DataFrame], today: date, model_version: str) -> None:
    """signal_history_c + live_eval_daily 저장."""
    if not results:
        return

    # label_probs JSON 구성 (top-K 종목별 전체 라벨 확률)
    ticker_probs: dict[str, dict] = {}
    for label, df_r in results.items():
        for _, row in df_r.iterrows():
            t = row["ticker"]
            if t not in ticker_probs:
                ticker_probs[t] = {}
            ticker_probs[t][label] = round(float(row["prob"]), 4)

    conn = get_conn()
    try:
        rows = []
        for ticker, probs in ticker_probs.items():
            rows.append({
                "signal_date": today,
                "ticker": ticker,
                "model_version": model_version,
                "label_probs": json.dumps(probs),
                "top_labels": json.dumps(sorted(probs, key=probs.get, reverse=True)[:3]),
            })
        df_sig = pd.DataFrame(rows)
        conn.register("_sig_c", df_sig)
        conn.execute("""
            INSERT OR REPLACE INTO signal_history_c
                (signal_date, ticker, model_version, label_probs, top_labels)
            SELECT signal_date, ticker, model_version, label_probs, top_labels
            FROM _sig_c
        """)
        logger.info(f"signal_history_c 저장: {len(rows)}행")
    finally:
        conn.close()


def run(today: date | None = None) -> None:
    today = today or date.today()
    model_version = f"track_c_{today.strftime('%Y-%m-%d')}"
    results = score_today(today)
    if not results:
        logger.warning("스코어링 결과 없음")
        return
    save_results(results, today, model_version)
    logger.info(f"Track C scoring 완료: {today}, {len(results)}개 라벨")
    # 텔레그램 발송은 run_daily_c.py에서 담당 (별도 구현)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    run()
```

- [ ] **Step 2: 로컬 import 검증**

```bash
python3 -c "from agents.orchestrator_c import run; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add agents/orchestrator_c.py
git commit -m "feat: agents/orchestrator_c.py Track C 일일 스코어링 오케스트레이터"
```

---

## Task 12: `run_daily_c` cron 등록 (VM)

- [ ] **Step 1: VM 배포**

```bash
cd /opt/stock-monitor && sudo git pull
```

- [ ] **Step 2: `run_daily_c.sh` 스크립트 확인 (기존 `run_daily.sh` 구조 참고)**

```bash
cat scripts/run_daily.sh   # 또는 crontab에서 실행 명령 확인
```

- [ ] **Step 3: `run_daily_c.sh` 생성 (run_daily.sh 복사 후 orchestrator 변경)**

```bash
cp scripts/run_daily.sh scripts/run_daily_c.sh
# run_daily_c.sh 내에서 orchestrator.py → orchestrator_c.py 로 변경
# 텔레그램 메시지 접두사에 "[Track C]" 추가
```

- [ ] **Step 4: cron 등록 (08:45 UTC)**

```bash
# 현재 crontab 확인
crontab -l

# 신규 항목 추가
(crontab -l; echo "45 8 * * 1-5 cd /opt/stock-monitor && sudo -u stock python3 agents/orchestrator_c.py >> /tmp/run_daily_c.log 2>&1 # run_daily_c") | crontab -

# 확인
crontab -l | grep "run_daily_c"
```

Expected: `45 8 * * 1-5 ...` 항목 확인

---

## Task 13: `live_eval_daily` model_version 검증 + 작동 확인

- [ ] **Step 1: VM에서 model_version 컬럼 존재 확인**

```bash
sudo -u stock python3 -c "
from data.db import get_conn, init_db
init_db()
conn = get_conn(read_only=True)
cols = [r[0] for r in conn.execute('SELECT * FROM live_eval_daily LIMIT 0').description]
print('model_version' in cols, cols)
conn.close()
"
```

Expected: `True`

- [ ] **Step 2: Track B 기존 데이터 model_version 확인**

```bash
sudo -u stock python3 -c "
from data.db import get_conn
conn = get_conn(read_only=True)
r = conn.execute(\"SELECT model_version, COUNT(*) FROM live_eval_daily GROUP BY model_version\").fetchall()
print(r)
conn.close()
"
```

Expected: `[('track_b', N)]` 또는 `[(None, N)]` → None이면 Track B 오케스트레이터에서 model_version 파라미터 추가 필요

- [ ] **Step 3: 수동 run 테스트 (최근 거래일로 dry-run)**

```bash
sudo -u stock python3 -c "
from agents.orchestrator_c import score_today
from datetime import date
results = score_today(date.today())
for label, df in list(results.items())[:3]:
    print(f'{label}: top-3 = {df.head(3)[\"ticker\"].tolist()}')
"
```

Expected: 3개 이상 라벨에서 ticker 목록 출력

---

## 완료 기준

| 항목 | 검증 |
|------|------|
| 모든 pytest | `python3 -m pytest tests/test_labeler_c.py tests/test_feature_engineering_c.py -v` → PASS |
| backtest_labels_c | `~300k행, 2023-06-07~` |
| fm_c.parquet | `~300k행, 70개 이상 컬럼` |
| data/models_c/ | 라벨별 4개 모델 파일 |
| run_daily_c cron | `crontab -l \| grep "run_daily_c"` → 08:45 UTC |
| live_eval_daily | model_version 컬럼 있음, Track C 결과 누적 시작 |

---

## VM 실행 순서 요약

```
1. Task 2 커밋 → sudo git pull → init_db()
2. Task 5: labeler_c.py --build  (~수십분)
   → --filter 결과로 _TARGETS 결정
3. Task 8: feature_engineering_c.py --mode train (~5~15분)
4. Task 10: train_models_c.py (~1~2시간)
5. Task 12: cron 등록
6. 다음 거래일 17:45 KST에 자동 첫 실행
```

---

## 리스크

| 항목 | 대응 |
|------|------|
| market_index.ticker 값 불확실 | `SELECT DISTINCT ticker FROM market_index` 로 실제 값 확인 후 `'KOSPI'` 수정 |
| universe_outcomes 데이터 없는 날 | cross-sectional 라벨 해당 날짜 skip (label_one_c per-ticker는 독립적으로 계산) |
| ohlcv_min intraday 데이터 누락 | df_min.empty 시 intraday 피처 0-fill, 훈련 가능 |
| ET 훈련 시간 (~72 모델) | e2-medium 기준 1~2시간 예상. screen으로 background 실행 |
| DuckDB single write lock | run_daily_b 완료 후 +15분 offset으로 충돌 방지 |
