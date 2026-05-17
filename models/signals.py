from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MarketContext:
    market: str           # "KOSPI" | "KOSDAQ"
    market_status: str    # "bull" | "sideways" | "bear"
    score: int
    market_bias: int      # 장세 상태값: bull=0, sideways=2, bear=5 (market.yaml 참조)


@dataclass
class TechnicalResult:
    ticker: str
    trend_score: int      # MA 스코어 (max 14)
    ichimoku_score: int   # 일목균형표 스코어 (음수 가능)
    pattern: Optional[str]  # "cup_handle" | "rounding_bottom" | "triangle_convergence" | "bb_squeeze" | None
    support: Optional[float]
    resistance: Optional[float]
    current_price: float = 0.0
    atr: Optional[float] = None  # 14일 Average True Range

    @property
    def total_score(self) -> int:
        return self.trend_score + self.ichimoku_score


@dataclass
class VolumeResult:
    ticker: str
    volume_score: int
    explosion_imminent: bool    # volume_score >= volume.yaml explosion_imminent 임계값
    smart_money_flow: str       # "accumulating" | "neutral" | "distributing"


@dataclass
class BuySignal:
    ticker: str
    name: str
    grade: str              # "S" | "A" | "B"
    total_score: int
    trend_score: int
    volume_score: int
    pattern: Optional[str]
    current_price: float
    stop_loss: float        # 참고 손절가
    target_price: float     # 참고 목표가
    risk_reward: float      # 손익비
    pattern_score: int = 0  # PatternLearningResult 보너스 점수 (0/1/3)
    xgb_prob: Optional[float] = None      # 최고 ML 확률
    best_label: Optional[str] = None      # 최고 확률 라벨 (예: "5d_10pct")
    target_is_resistance: bool = False    # True=저항선 기반, False=+10% 폴백
    market: str = ""               # "KOSPI" | "KOSDAQ"
    mktcap_rank: Optional[int] = None  # 시장 내 시총 순위
    label_probs: dict = field(default_factory=dict)  # {label: prob} 9개 전체


@dataclass
class SellSignal:
    ticker: str
    name: str
    action: str             # "stop_loss" | "full_sell" | "half_sell" | "hold"
    priority: int           # 1~5 (전략서 9-5)
    score: int
    reason: str
    buy_price: float
    current_price: float
    profit_pct: float


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
