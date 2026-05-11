import os
import yaml
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.signals import MarketContext, TechnicalResult, VolumeResult


class ScoringEngine:
    """
    YAML 기반 스코어링 엔진.
    config_dir 폴더의 *.yaml 파일에서 규칙과 임계값을 로드한다.
    파라미터 변경은 YAML 수정으로 완결 — 이 클래스는 건드리지 않는다.
    """

    def __init__(self, config_dir: str = "config/scoring/v1_baseline/"):
        self.config = _load_configs(config_dir)

    def _apply_rules(self, rules: list, flags: dict) -> int:
        return sum(rule["points"] for rule in rules if flags.get(rule["id"], False))

    # ── 장세 판단 ───────────────────────────────────────────────────────────

    def score_market(self, flags: dict) -> "MarketContext":
        """
        flags 키: index_6m_uptrend, index_3m_uptrend, index_1m_uptrend,
                  index_above_20ma, foreign_futures_net_buy,
                  sector_strong_ratio_70pct, market ("KOSPI"|"KOSDAQ")
        """
        from models.signals import MarketContext

        cfg = self.config["market"]
        score = self._apply_rules(cfg["rules"], flags)
        t = cfg["thresholds"]

        if score >= t["bull"]:
            status = "bull"
        elif score >= t["sideways_min"]:
            status = "sideways"
        else:
            status = "bear"

        return MarketContext(
            market=flags.get("market", "KOSPI"),
            market_status=status,
            score=score,
            market_bias=cfg["market_bias"][status],
        )

    # ── 거래량 분석 ─────────────────────────────────────────────────────────

    def score_volume(self, flags: dict) -> "VolumeResult":
        """
        flags 키: vol_5d_above_20d, vol_consecutive_3d, vol_rise_price_flat,
                  obv_uptrend_price_flat, vol_vs_52w_low_2x,
                  foreign_inst_buy, foreign_inst_sell, short_interest_declining,
                  vol_trending_w_price, price_vol_bullish_corr,
                  ticker
        """
        from models.signals import VolumeResult

        cfg = self.config["volume"]
        points_map = {r["id"]: r["points"] for r in cfg["rules"]}
        group_caps = cfg.get("volume_group_cap", {})

        grouped_ids: set = set()
        score = 0
        for grp in group_caps.values():
            raw = sum(points_map[rid] for rid in grp["indicators"] if flags.get(rid) and rid in points_map)
            score += min(raw, grp["max_score"])
            grouped_ids.update(grp["indicators"])
        for rule in cfg["rules"]:
            if rule["id"] not in grouped_ids and flags.get(rule["id"]):
                score += rule["points"]

        if flags.get("foreign_inst_buy"):
            smart_money = "accumulating"
        elif flags.get("foreign_inst_sell", False):
            smart_money = "distributing"
        else:
            smart_money = "neutral"

        return VolumeResult(
            ticker=flags.get("ticker", ""),
            volume_score=score,
            explosion_imminent=score >= cfg["thresholds"]["explosion_imminent"],
            smart_money_flow=smart_money,
        )

    # ── 기술 지표 ───────────────────────────────────────────────────────────

    def score_technical(self, flags: dict) -> "TechnicalResult":
        """
        flags 키 (MA): price_above_20ma, price_above_60ma, price_above_120ma,
                       price_above_240ma, ma20_above_ma60, ma60_above_ma120,
                       ma120_uptrend, near_52w_high_5pct
        flags 키 (일목): ichimoku_triple_positive, ichimoku_cloud_support,
                         ichimoku_cloud_break, ichimoku_dead_cross
        flags 키 (기타): pattern, support, resistance, ticker
        """
        from models.signals import TechnicalResult

        cfg = self.config["technical"]
        ma_score = self._apply_rules(cfg["ma_rules"], flags)
        ichimoku_score = self._apply_rules(cfg["ichimoku_rules"], flags)

        return TechnicalResult(
            ticker=flags.get("ticker", ""),
            trend_score=ma_score,
            ichimoku_score=ichimoku_score,
            pattern=flags.get("pattern"),
            support=flags.get("support"),
            resistance=flags.get("resistance"),
        )

    # ── 매수 등급 산출 ──────────────────────────────────────────────────────

    def determine_grade(
        self,
        trend_score: int,
        volume_score: int,
        market_status: str,
    ) -> str:
        """
        장세(bull/sideways/bear)별 독립 임계값 적용.
        trend_min, volume_min, total_min 세 조건 동시 충족 시 등급 부여.
        """
        grades_cfg = self.config["buy_grade"]["buy_grade"]
        total = trend_score + volume_score

        for grade in ("S", "A", "B"):
            regime = grades_cfg[grade].get(market_status)
            if regime is None:
                continue
            if regime.get("allowed") is False:
                continue
            if (
                trend_score >= regime["trend_min"]
                and volume_score >= regime["volume_min"]
                and total >= regime["total_min"]
            ):
                return grade

        return "NONE"

    # ── 매도 스코어링 ───────────────────────────────────────────────────────

    def check_forced_stoploss(self, flags: dict) -> bool:
        """강제 손절 조건 (9-3): 점수 무관 즉시 발동."""
        conditions = self.config["sell"]["forced_stoploss"]["conditions"]
        return any(flags.get(c["id"], False) for c in conditions)

    def score_sell_profit(self, flags: dict) -> int:
        """익절 시나리오 스코어 (9-1)."""
        return self._apply_rules(self.config["sell"]["profit_taking"]["rules"], flags)

    def score_sell_trend_break(self, flags: dict) -> int:
        """추세 이탈 스코어 (9-2)."""
        return self._apply_rules(self.config["sell"]["trend_break"]["rules"], flags)

    def score_sell_hold_extension(self, flags: dict) -> int:
        """보유 연장 억제 스코어 (9-4). 음수 반환 — 매도 스코어에서 차감."""
        return self._apply_rules(self.config["sell"]["hold_extension"]["rules"], flags)

    def sell_thresholds(self, scenario: str) -> dict:
        return self.config["sell"][scenario]["thresholds"]


def _load_configs(config_dir: str) -> dict:
    configs: dict = {}
    for name in ("market", "volume", "technical", "buy_grade", "sell"):
        path = os.path.join(config_dir, f"{name}.yaml")
        with open(path, encoding="utf-8") as f:
            configs[name] = yaml.safe_load(f)
    return configs
