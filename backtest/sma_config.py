# backtest/sma_config.py
"""SMA 백테스팅 설정 — 종목 목록, 파라미터 그리드, 고정 상수."""
from itertools import product

UNIVERSE = [
    {"ticker": "005930", "name": "삼성전자"},
    {"ticker": "000660", "name": "SK하이닉스"},
    {"ticker": "005380", "name": "현대차"},
    {"ticker": "034020", "name": "두산에너빌리티"},
    {"ticker": "010170", "name": "대한광통신"},
    {"ticker": "007660", "name": "이수페타시스"},
    {"ticker": "006800", "name": "미래에셋증권"},
    {"ticker": "000270", "name": "기아"},
    {"ticker": "005490", "name": "POSCO홀딩스"},
    {"ticker": "006400", "name": "삼성SDI"},
    {"ticker": "105560", "name": "KB금융"},
    {"ticker": "068270", "name": "셀트리온"},
    {"ticker": "009150", "name": "삼성전기"},
    {"ticker": "042700", "name": "한미반도체"},
    {"ticker": "010120", "name": "LS ELECTRIC"},
    {"ticker": "012450", "name": "한화에어로스페이스"},
    {"ticker": "267250", "name": "롯데에너지머티리얼스"},
    {"ticker": "267260", "name": "HD현대일렉트릭"},
    {"ticker": "207940", "name": "삼성바이오로직스"},
    {"ticker": "307950", "name": "현대오토에버"},
    {"ticker": "402340", "name": "SK스퀘어"},
    {"ticker": "329180", "name": "HD현대중공업"},
    {"ticker": "454910", "name": "두산로보틱스"},
]

# Walk-forward 최소 요구 기간 (년)
MIN_YEARS_FOR_WF = 7

# Walk-forward 파라미터
WF_TRAIN_YEARS = 5
WF_TEST_YEARS  = 2
WF_STEP_YEARS  = 1

# 파라미터 그리드 (624 조합)
PARAM_GRID = {
    "sma_period":    list(range(50, 310, 10)),  # 26
    "confirm_days":  [1, 3],                     # 2
    "lookback_days": [10, 20, 40],               # 3
    "drawdown_pct":  [3, 5, 10, 15],             # 4
}

def all_param_combinations() -> list[dict]:
    keys = list(PARAM_GRID.keys())
    return [
        dict(zip(keys, vals))
        for vals in product(*PARAM_GRID.values())
    ]

# 고정 상수 (최적화 대상 아님)
FIXED = {
    "commission":    0.0003,   # 매수/매도 각 0.03%
    "stop_loss_pct": 8.0,      # 눌림목 진입 후 -8% 손절 (고정)
    # (threshold_pct, sell_fraction_of_holdings)
    "profit_levels": [
        (10,  0.10),
        (25,  0.10),
        (50,  0.10),
        (100, 0.50),
        (200, 0.50),
    ],
    "min_trades":    10,       # 이 미만은 결과 제외
}
