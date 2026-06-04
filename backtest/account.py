# backtest/account.py
"""올바른 MTM 회계 엔진. 진입/청산 규칙은 전략 함수에서 주입."""
from __future__ import annotations


class Account:
    """단일 전략 올인 백테스트 회계 엔진.

    - 실행가: 중간값 (high + low) / 2  — 호출자가 계산해서 전달
    - equity = cash + shares × close  (매 거래일 MTM)
    - 책임: 올바른 회계만. 언제 사고 팔지는 전략 함수가 결정.
    """

    def __init__(self, initial_capital: float = 1.0, commission: float = 0.0003):
        self._commission = commission
        self._cash = float(initial_capital)
        self._shares = 0.0
        self._avg_entry = 0.0
        self._entry_date: str | None = None
        self._profit_levels_hit: set[float] = set()
        self._trades: list[dict] = []
        self._equity_curve: list[float] = []

    # ── 상태 조회 ──────────────────────────────────────────────

    @property
    def in_position(self) -> bool:
        return self._shares > 1e-9

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def shares(self) -> float:
        return self._shares

    @property
    def avg_entry_price(self) -> float:
        return self._avg_entry

    @property
    def trades(self) -> list[dict]:
        return self._trades

    @property
    def equity_curve(self) -> list[float]:
        return self._equity_curve

    # ── 거래 실행 ──────────────────────────────────────────────

    def buy(self, date: str, mid_price: float, fraction_of_cash: float = 1.0) -> None:
        """가용 현금의 fraction으로 매수. 가중평균 진입가 업데이트."""
        if mid_price <= 0:
            return
        spend = self._cash * fraction_of_cash
        cost_per_share = mid_price * (1 + self._commission)
        qty = spend / cost_per_share

        total = self._shares + qty
        if total > 0:
            self._avg_entry = (self._avg_entry * self._shares + cost_per_share * qty) / total
        self._shares = total
        self._cash -= spend
        if self._entry_date is None:
            self._entry_date = date

    def sell(self, date: str, mid_price: float,
             fraction_of_holdings: float = 1.0, reason: str = '') -> None:
        """보유분의 fraction 매도. 매 매도마다 거래 기록."""
        if mid_price <= 0 or not self.in_position:
            return
        qty = self._shares * fraction_of_holdings
        proceeds = qty * mid_price * (1 - self._commission)
        pnl_pct = (mid_price / self._avg_entry - 1) * 100 if self._avg_entry > 0 else 0.0

        self._trades.append({
            'entry_date':  self._entry_date,
            'entry_price': self._avg_entry,
            'exit_date':   date,
            'exit_price':  mid_price,
            'pnl_pct':     round(pnl_pct, 4),
            'exit_reason': reason,
            'fraction':    fraction_of_holdings,
        })

        self._cash += proceeds
        self._shares -= qty

        if self._shares < 1e-9:
            self._shares = 0.0
            self._avg_entry = 0.0
            self._entry_date = None
            self._profit_levels_hit = set()

    # ── equity 기록 ────────────────────────────────────────────

    def record_equity(self, close: float) -> None:
        """MTM equity를 equity_curve에 추가. 매 거래일 루프 끝에 호출."""
        self._equity_curve.append(self._cash + self._shares * close)

    def equity(self, close: float) -> float:
        """현재 MTM equity 반환 (기록 없이)."""
        return self._cash + self._shares * close

    # ── 익절 레벨 추적 ─────────────────────────────────────────

    def profit_level_hit(self, threshold: float) -> bool:
        return threshold in self._profit_levels_hit

    def mark_profit_level(self, threshold: float) -> None:
        self._profit_levels_hit.add(threshold)
