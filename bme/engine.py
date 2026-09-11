"""The replay engine: walk a trade forward through candles until a rule fires.

The whole method rests on one idea. A backtest on a 4h chart only knows each
candle's open, high, low and close - not the order the market visited them. It
has to guess. TradingView's broker emulator guesses from where the open sits:
closer to the high, the bar is taken to have gone open-high-low-close; closer
to the low, open-low-high-close. When most trades open and close inside a
single candle, that guess decides most of the results, which is exactly what
Bar Magnifier replaces with real lower-timeframe data.

Replaying the same rules against finer candles applies the same guess over a
much shorter span, so the guess matters less and the answer moves towards what
actually happened. That difference is the estimate this library produces.
"""
import collections

Fill = collections.namedtuple("Fill", "signal pct bars")


def path_of(candle, long):
    """The two extremes a candle visits, in the order TradingView assumes.

    The order follows the documented broker-emulator rule: an open closer to
    the high means the high came first, an open closer to the low means the
    low came first. An open exactly halfway is not specified; it is resolved
    high-first here.

    Only the extremes are returned. The open and close always lie between
    them, so they can never cross a level the extremes do not - they matter
    for the order, not as touch points. Prices are raw, and `long` does not
    change the answer: the order is a property of the candle, not the trade.
    """
    if candle.high - candle.open <= candle.open - candle.low:
        return (candle.high, candle.low)
    return (candle.low, candle.high)


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
