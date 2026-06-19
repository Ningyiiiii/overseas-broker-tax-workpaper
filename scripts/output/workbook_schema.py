"""Workbook output naming contract."""

MARKETS = {"HK": "港股", "US": "美股"}
PERIOD_REGIMES = {
    "china_calendar_year": "中国自然年",
    "hong_kong_fiscal_year": "香港财年",
}
COST_METHODS = {
    "fifo": "FIFO",
    "period_weighted_average": "期间加权平均成本法",
}


def output_filename(market: str, period_regime: str, cost_method: str) -> str:
    return f"{MARKETS[market]}_{PERIOD_REGIMES[period_regime]}_{COST_METHODS[cost_method]}_税务底稿.xlsx"
