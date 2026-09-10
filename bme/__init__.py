"""Estimate what TradingView's Bar Magnifier would do to a strategy.

Bar Magnifier is a paid TradingView feature that resolves what happened inside
each chart candle. Without it a backtest guesses the intrabar path, and when a
strategy opens and closes trades inside a single candle that guess decides
most of the results.

This library replays the exported trades against finer candles from the
exchange, applying your exit rules at that finer resolution. Validate first,
estimate second - see estimate.validate.
"""
from .tradingview import Trade, read_trades, detect_utc_offset, verify_symbol, infer_timeframe
from .candles import Candle, CandleSeries, Binance, Bybit, get_provider
from .engine import Fill, replay, replay_all
from . import estimate
from .estimate import Result, run, validate, compare, degradation, calibrate, funding_cost
from . import rules

__version__ = "0.1.0"
__all__ = [
    "Trade", "read_trades", "detect_utc_offset", "verify_symbol", "infer_timeframe",
    "Candle", "CandleSeries", "Binance", "Bybit", "get_provider",
    "Fill", "replay", "replay_all",
    "estimate", "Result", "run", "validate", "compare", "degradation", "calibrate", "funding_cost",
    "rules",
]
