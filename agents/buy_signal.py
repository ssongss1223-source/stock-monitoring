import logging
from typing import Optional

from core.scoring_engine import ScoringEngine
from models.signals import BuySignal, MarketContext, PatternLearningResult, TechnicalResult, VolumeResult

logger = logging.getLogger(__name__)


class BuySignalAgent:
    """
    전략서 11장: TechnicalResult + VolumeResult + MarketContext를 앙상블하여
    매수 등급(S/A/B)과 타점(손절가/목표가/손익비)을 산출한다.
    """

    def __init__(self, engine: ScoringEngine):
        self.engine = engine

    def evaluate(
        self,
        ticker: str,
        name: str,
        tech: TechnicalResult,
        vol: VolumeResult,
        market_ctx: MarketContext,
        pattern_result: Optional[PatternLearningResult] = None,
    ) -> Optional[BuySignal]:
        p_bonus = _pattern_bonus(pattern_result)
        effective_trend = tech.total_score + p_bonus

        grade = self.engine.determine_grade(
            trend_score=effective_trend,
            volume_score=vol.volume_score,
            market_status=market_ctx.market_status,
        )
        if grade == "NONE":
            return None

        current_price = tech.current_price
        if current_price <= 0:
            logger.warning("%s 현재가 없음, 매수신호 스킵", ticker)
            return None

        stop_loss = _calc_stop_loss(current_price, tech)
        target_price, target_is_resistance = _calc_target(current_price, tech)
        risk = current_price - stop_loss
        reward = target_price - current_price
        risk_reward = round(reward / risk, 2) if risk > 0 else 0.0

        logger.info(
            "BuySignal: %s(%s) 등급=%s 추세=%d 패턴보너스=%d 거래량=%d bias=%d",
            ticker, name, grade, tech.total_score, p_bonus, vol.volume_score, market_ctx.market_bias,
        )
        return BuySignal(
            ticker=ticker,
            name=name,
            grade=grade,
            total_score=effective_trend + vol.volume_score,
            trend_score=tech.total_score,
            volume_score=vol.volume_score,
            pattern=tech.pattern,
            current_price=current_price,
            stop_loss=stop_loss,
            target_price=target_price,
            target_is_resistance=target_is_resistance,
            risk_reward=risk_reward,
            pattern_score=p_bonus,
        )


def _pattern_bonus(pr: Optional[PatternLearningResult]) -> int:
    """PatternLearningResult 등급에 따른 추세 점수 보너스."""
    if pr is None:
        return 0
    if pr.grade == "HIGH":
        return 3
    if pr.grade == "MEDIUM":
        return 1
    return 0


def _calc_stop_loss(current_price: float, tech: TechnicalResult) -> float:
    """
    손절가: -5% 또는 구름대 하단 중 더 높은 값 (타이트한 쪽 우선).
    지지선이 -5%보다 위에 있으면 그것을 손절선으로 사용.
    """
    default_stop = current_price * 0.95
    if tech.support is not None and float(tech.support) > default_stop:
        return round(float(tech.support), 0)
    return round(default_stop, 0)


def _calc_target(current_price: float, tech: TechnicalResult) -> tuple[float, bool]:
    """목표가: (가격, 저항선기반여부). 저항선이 없거나 너무 가까우면 +10% 폴백."""
    if tech.resistance is not None:
        target = float(tech.resistance)
        if target > current_price * 1.03:
            return round(target, 0), True
    return round(current_price * 1.10, 0), False
