"""Validate the rules, then estimate the finer-granularity result.

The order matters and is not optional. Replaying at the chart's own timeframe
must reproduce the export you already have; that is the only evidence your
rules match the strategy. Only once it does is the finer replay worth reading.
A high match at the chart timeframe with a wildly different finer result is
the expected outcome - that gap is the thing being measured. A *low* match
means the rules are wrong and every other number is noise.
"""
import collections
import datetime
import math
import statistics

from . import engine

UTC = datetime.timezone.utc


class Result:
    """One replay of every trade at one granularity."""

    def __init__(self, timeframe, pairs):
        self.timeframe = timeframe
        self.pairs = pairs                      # [(Trade, Fill or None)]
        self.filled = [(t, f) for t, f in pairs if f]
        self.returns = [f.pct for _, f in self.filled]

    @property
    def unfilled(self):
        return len(self.pairs) - len(self.filled)

    @property
    def total(self):
        return sum(self.returns)

    @property
    def per_trade(self):
        return statistics.mean(self.returns) if self.returns else 0.0

    @property
    def t_stat(self):
        """How many standard errors the mean sits above zero.

        Below about 2 the result is not distinguishable from luck, however
        good the total looks.
        """
        if len(self.returns) < 2:
            return 0.0
        sd = statistics.pstdev(self.returns)
        return self.per_trade / (sd / math.sqrt(len(self.returns))) if sd else 0.0

    @property
    def win_rate(self):
        if not self.returns:
            return 0.0
        return 100.0 * sum(1 for r in self.returns if r > 0) / len(self.returns)

    @property
    def max_drawdown(self):
        eq = peak = 1.0
        worst = 0.0
        for r in self.returns:
            eq *= 1 + r / 100.0
            peak = max(peak, eq)
            worst = max(worst, 1 - eq / peak)
        return 100.0 * worst

    @property
    def equity(self):
        eq = 1.0
        for r in self.returns:
            eq *= 1 + r / 100.0
        return eq

    def cagr(self):
        if not self.filled or self.equity <= 0:
            return float("nan")
        span = (max(t.exit_time for t, _ in self.filled)
                - min(t.entry_time for t, _ in self.filled)).total_seconds()
        years = span / 31557600.0
        return 100.0 * (self.equity ** (1 / years) - 1) if years > 0 else float("nan")

    def signal_match(self):
        """Fraction of trades whose exit reason matches the export.

        Only meaningful at the chart timeframe, where the export is ground
        truth. Rule names must match the export's Signal column to score.
        """
        if not self.pairs:
            return 0.0
        return sum(1 for t, f in self.pairs if f and f.signal == t.signal) / len(self.pairs)

    def confusion(self):
        return collections.Counter(
            (t.signal, f.signal if f else "open") for t, f in self.pairs)

    def by_signal(self):
        out = collections.defaultdict(list)
        for _, f in self.filled:
            out[f.signal].append(f.pct)
        return {k: (len(v), sum(v), statistics.mean(v)) for k, v in out.items()}


def _span(trades, timeframe_seconds, pad_bars):
    lo = min(t.entry_time for t in trades)
    hi = max(t.exit_time for t in trades) + datetime.timedelta(
        seconds=timeframe_seconds * pad_bars)
    return lo, hi


#: Above this many candles, fetching one continuous series costs more requests
#: than fetching a window per trade - trades occupy a fraction of the calendar.
_CONTINUOUS_LIMIT = 200_000


def run(trades, provider, symbol, timeframe, rules, commission_pct=0.0,
        max_hold_hours=240, pad_bars=200):
    """Replay every trade at one granularity.

    Candles are fetched as one continuous series when that is cheap, and as a
    window per trade when it is not. A 5-year span at 1-minute resolution is
    2.4M candles; the same trades need a few hundred windows, because they are
    only in the market a fraction of the time.
    """
    from .candles import SECONDS
    step = SECONDS[timeframe]
    lo, hi = _span(trades, step, pad_bars)
    max_bars = max(2, int(max_hold_hours * 3600 / step))

    if (hi - lo).total_seconds() / step <= _CONTINUOUS_LIMIT:
        series = provider.series(symbol, timeframe, lo, hi)
        pairs = list(engine.replay_all(trades, series, rules, commission_pct, max_bars))
        return Result(timeframe, pairs)

    span = datetime.timedelta(seconds=step * max_bars)
    pairs = []
    for t in trades:
        window = provider.series(symbol, timeframe, t.entry_time, t.entry_time + span)
        pairs.append((t, engine.replay(t.entry_price, t.long, window.candles,
                                       rules, commission_pct)))
    return Result(timeframe, pairs)


def validate(trades, provider, symbol, chart_timeframe, rules, commission_pct=0.0,
             threshold=0.90, **kw):
    """Replay at the chart timeframe and check it reproduces the export.

    Returns ``(Result, ok)``. When ``ok`` is False the rules do not describe
    this strategy: fix them before reading any finer-granularity estimate.
    """
    res = run(trades, provider, symbol, chart_timeframe, rules, commission_pct, **kw)
    return res, res.signal_match() >= threshold


def compare(trades, provider, symbol, chart_timeframe, fine_timeframe, rules,
             commission_pct=0.0, **kw):
    """Validate, then replay finer. Returns ``(baseline, fine, ok)``."""
    base, ok = validate(trades, provider, symbol, chart_timeframe, rules,
                        commission_pct, **kw)
    fine = run(trades, provider, symbol, fine_timeframe, rules, commission_pct, **kw)
    return base, fine, ok


def degradation(baseline, fine):
    """Per-trade cost of finer resolution, grouped by the baseline's exit.

    These coefficients are the portable part of a measurement: they say what
    each kind of exit gives up when the intrabar guess is replaced, and they
    hold across pairs far better than a single percentage drop does.
    """
    by_exit = collections.defaultdict(list)
    fine_by_trade = {id(t): f for t, f in fine.pairs}
    for t, bf in baseline.pairs:
        ff = fine_by_trade.get(id(t))
        if bf and ff:
            by_exit[bf.signal].append(ff.pct - bf.pct)
    return {k: (len(v), statistics.mean(v)) for k, v in by_exit.items()}


def calibrate(trades, provider, symbol, chart_timeframe, rules, reference,
              candidates=("1h", "30m", "15m", "5m", "1m"), commission_pct=0.0, **kw):
    """Find which granularity best reproduces a known Bar Magnifier run.

    ``reference`` maps exit name to the per-trade points given up when the
    real magnifier was switched on - measured by exporting the same strategy
    twice, with and without it. Without such a reference there is nothing to
    calibrate against and the finer replay is an unanchored guess.

    The reference must come from **these** rules. Coefficients describe what a
    particular exit gives up under a particular configuration: a trailing stop
    that arms at +1% behaves nothing like one that arms at +3%, so borrowing
    coefficients across parameter sets produces a confident wrong answer. Any
    reference exit the rules cannot produce is rejected rather than scored as
    a constant penalty, which would silently rank every granularity equally.

    Returns rows sorted best first, each ``(timeframe, error, coefficients,
    total_drop_pct)``.
    """
    base = run(trades, provider, symbol, chart_timeframe, rules, commission_pct, **kw)
    produced = {f.signal for _, f in base.filled}
    missing = set(reference) - produced
    if missing:
        raise ValueError(
            "reference mentions exits these rules never produce: %s. "
            "Re-measure the reference with the same configuration you are "
            "calibrating, or drop those entries." % ", ".join(sorted(missing)))
    rows = []
    for tf in candidates:
        fine = run(trades, provider, symbol, tf, rules, commission_pct, **kw)
        coef = {k: v[1] for k, v in degradation(base, fine).items()}
        err = sum(abs(coef.get(k, 0.0) - v) for k, v in reference.items())
        drop = (100.0 * (fine.total / base.total - 1)) if base.total else float("nan")
        rows.append((tf, err, coef, drop))
    rows.sort(key=lambda r: r[1])
    return rows


def funding_cost(trades, provider, symbol, hold_hours=None):
    """Funding paid (positive) or received (negative) per trade, in percent.

    Longs pay when the rate is positive and shorts collect, so a strategy that
    is short often enough in a market with mostly positive rates ends up being
    paid to hold. Worth measuring rather than assuming.
    """
    import bisect
    lo = min(t.entry_time for t in trades) - datetime.timedelta(days=1)
    hi = max(t.exit_time for t in trades) + datetime.timedelta(days=1)
    rates = provider.funding(symbol, lo, hi)
    stamps = [r[0] for r in rates]
    out = []
    for t in trades:
        a = int(t.entry_time.timestamp() * 1000)
        if hold_hours is None:
            b = int(t.exit_time.timestamp() * 1000)
        else:
            b = a + int(hold_hours * 3600 * 1000)
        i = bisect.bisect_left(stamps, a)
        j = bisect.bisect_right(stamps, b)
        paid = sum(rates[k][1] for k in range(i, j)) * 100.0
        out.append(paid if t.long else -paid)
    return out
