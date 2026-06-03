# backtest/sma_signal.py
"""SMA + 진입 시그널 계산."""
import pandas as pd


def compute_sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average 계산.

    Args:
        close: 종가 시리즈
        period: SMA 기간

    Returns:
        SMA 값 (처음 period-1개는 NaN)
    """
    return close.rolling(period).mean()


def compute_rolling_high(close: pd.Series, lookback: int) -> pd.Series:
    """지정된 기간의 rolling 최고가.

    Args:
        close: 종가 시리즈
        lookback: 조회 기간 (일)

    Returns:
        Rolling 최고가 (처음 lookback-1개는 NaN)
    """
    return close.rolling(lookback).max()


def get_entry_signals(
    close: pd.Series,
    sma_period: int,
    lookback_days: int,
    drawdown_pct: float,
) -> pd.Series:
    """각 날짜별 진입 시그널 반환.

    Args:
        close: 종가 시리즈
        sma_period: SMA 기간
        lookback_days: rolling high 조회 기간
        drawdown_pct: 눌림목 낙폭 (퍼센트, 예: 5.0 = 5%)

    Returns:
        pd.Series of str|None
        - 'sma_breakout': SMA 아래→위 첫 돌파
        - 'pullback': SMA 위 유지 + 고점 대비 낙폭 조건 충족
        - None: 진입 신호 없음

    우선순위: pullback > sma_breakout (동시 충족 시 pullback)
    """
    sma = compute_sma(close, sma_period)
    rolling_high = compute_rolling_high(close, lookback_days)

    # SMA 돌파 판정
    above_sma = close >= sma
    prev_above = above_sma.shift(1).astype(bool).fillna(False)
    is_breakout = above_sma & ~prev_above  # 어제 아래, 오늘 위

    # 눌림목 판정: SMA 위 유지 + 고점 대비 낙폭
    threshold = rolling_high * (1 - drawdown_pct / 100)
    is_pullback = above_sma & ~is_breakout & (close <= threshold)

    # 결과 생성
    result = pd.Series(None, index=close.index, dtype=object)
    result[is_breakout] = 'sma_breakout'
    # pullback이 breakout보다 나중에 할당되므로 동시 충족 시 pullback 우선
    result[is_pullback] = 'pullback'

    return result
