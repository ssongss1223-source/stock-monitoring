import logging
from typing import Optional

from core.scoring_engine import ScoringEngine
from models.signals import BuySignal, MarketContext, TechnicalResult, VolumeResult

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
    ) -> Optional[BuySignal]:
        grade = self.engine.determine_grade(
            trend_score=tech.total_score,
            volume_score=vol.volume_score,
            has_pattern=tech.pattern is not None,
            market_bias=market_ctx.market_bias,
        )
        if grade == "NONE":
            return None

        current_price = tech.current_price
        if current_price <= 0:
            logger.warning("%s 현재가 없음, 매수신호 스킵", ticker)
            return None

        stop_loss = _calc_stop_loss(current_price, tech)
        target_price = _calc_target(current_price, tech)
        risk = current_price - stop_loss
        reward = target_price - current_price
        risk_reward = round(reward / risk, 2) if risk > 0 else 0.0

        logger.info(
            "BuySignal: %s(%s) 등급=%s 추세=%d 거래량=%d bias=%d",
            ticker, name, grade, tech.total_score, vol.volume_score, market_ctx.market_bias,
        )
        return BuySignal(
            ticker=ticker,
            name=name,
            grade=grade,
            total_score=tech.total_score + vol.volume_score,
            trend_score=tech.total_score,
            volume_score=vol.volume_score,
            pattern=tech.pattern,
            current_price=current_price,
            stop_loss=stop_loss,
            target_price=target_price,
            risk_reward=risk_reward,
        )


def _calc_stop_loss(current_price: float, tech: TechnicalResult) -> float:
    """
    손절가: -5% 또는 구름대 하단 중 더 높은 값 (타이트한 쪽 우선).
    지지선이 -5%보다 위에 있으면 그것을 손절선으로 사용.
    """
    default_stop = current_price * 0.95
    if tech.support is not None and float(tech.support) > default_stop:
        return round(float(tech.support), 0)
    return round(default_stop, 0)


def _calc_target(current_price: float, tech: TechnicalResult) -> float:
    """목표가: 다음 저항선 또는 +10% (저항선이 없거나 너무 가까울 때)."""
    if tech.resistance is not None:
        target = float(tech.resistance)
        # 저항선이 현재가 대비 +3% 이상이면 사용
        if target > current_price * 1.03:
            return round(target, 0)
    return round(current_price * 1.10, 0)
