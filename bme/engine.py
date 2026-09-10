"""The replay engine: walk a trade forward through candles until a rule fires.

The whole method rests on one idea. A backtest on a 4h chart only knows each
candle's open, high, low and close - not the order the market visited them. It
has to guess, and TradingView's guess is that a bullish candle travelled
open-low-high-close and a bearish one open-high-low-close. When most trades
open and close inside a single candle, that guess decides most of the results,
which is exactly what Bar Magnifier replaces with real lower-timeframe data.

Replaying the same rules against finer candles applies the same guess over a
much shorter span, so the guess matters less and the answer moves towards what
actually happened. That difference is the estimate this library produces.
"""
import collections

Fill = collections.namedtuple("Fill", "signal pct bars")


def path_of(candle, long):
    """The prices a candle visits, in TradingView's assumed order.

    Returns them already signed so positive is in the trade's favour.
    """
    o, h, l, c = candle.open, candle.high, candle.low, candle.close
    up, dn = (h, l) if long else (l, h)
    bullish = (c >= o) if long else (c < o)
    return (dn, up) if bullish else (up, dn)


def replay(entry_price, long, candles, rules, commission_pct=0.0):
    """Replay one trade. Returns a Fill, or None if no rule fired.

    ``candles`` is any iterable of objects with open/high/low/close. ``rules``
    are consulted in order at every price the market visits; the first to fire
    closes the trade. ``commission_pct`` is the full round trip, subtracted
    from the result.

    Returning None means the trade was still open when the candles ran out -
    treat that as missing data, not as a break-even trade.
    """
    for r in rules:
        r.reset()
    sign = 1 if long else -1

    for n, candle in enumerate(candles, 1):
        for price in path_of(candle, long):
            pct = sign * (price - entry_price) / entry_price * 100.0
            for r in rules:
                hit = r.on_price(pct)
                if hit:
                    return Fill(hit[0], hit[1] - commission_pct, n)
        close_pct = sign * (candle.close - entry_price) / entry_price * 100.0
        for r in rules:
            on_close = getattr(r, "on_bar_close", None)
            if on_close:
                hit = on_close(close_pct)
                if hit:
                    return Fill(hit[0], hit[1] - commission_pct, n)
    return None


def replay_all(trades, candles, rules, commission_pct=0.0, max_bars=2000):
    """Replay every trade against a chronological candle series.

    ``candles`` must be a CandleSeries (or anything with ``window(ts, n)``).
    Yields ``(trade, fill_or_None)`` in the order the trades were given.
    """
    for t in trades:
        window = candles.window(t.entry_time, max_bars)
        yield t, replay(t.entry_price, t.long, window, rules, commission_pct)
