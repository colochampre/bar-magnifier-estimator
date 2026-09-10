"""Exit rules.

A rule watches the trade's excursion from entry and decides when to close.
Excursions are signed so that positive is always in the trade's favour, which
means one rule definition covers both long and short trades.

The engine feeds every rule each price the market visits, in the order it
visits them, and closes the trade on the first rule that fires. Rules are
consulted in list order, so put the tighter exit first: when a trailing stop
has ratcheted above the fixed stop, price crosses the trailing level first on
the way down, and listing it first is what reproduces that.

Writing a rule for a strategy this module does not cover means implementing
two methods:

    class MyRule(Rule):
        def reset(self): ...                       # called once per trade
        def on_price(self, pct): return None       # or (name, exit_pct)
"""


class Rule:
    """Base class. Subclass it to describe an exit your strategy uses."""

    name = "Exit"

    def reset(self):
        """Clear per-trade state. Called before each trade is replayed."""

    def on_price(self, pct):
        """Called with each price the market visits, as % from entry.

        Return ``(name, exit_pct)`` to close the trade at that level, or
        ``None`` to let it run. ``exit_pct`` is the excursion the trade is
        closed at, before costs.
        """
        raise NotImplementedError


class StopLoss(Rule):
    """Fixed stop a set percentage against the entry."""

    name = "SL"

    def __init__(self, pct):
        if pct <= 0:
            raise ValueError("stop loss must be positive")
        self.pct = float(pct)

    def on_price(self, pct):
        if pct <= -self.pct:
            return self.name, -self.pct
        return None


class TakeProfit(Rule):
    """Fixed target a set percentage in favour of the entry."""

    name = "TP"

    def __init__(self, pct):
        if pct <= 0:
            raise ValueError("take profit must be positive")
        self.pct = float(pct)

    def on_price(self, pct):
        if pct >= self.pct:
            return self.name, self.pct
        return None


class TrailingStop(Rule):
    """Stop that arms at ``activation`` then follows the best price.

    ``offset`` is a fixed distance measured from the entry price, matching
    Pine's ``strategy.exit(trail_points=, trail_offset=)``: once armed the
    level sits ``offset`` behind the running best excursion and only ever
    moves in the trade's favour. The floor it can exit at is therefore
    ``activation - offset``, which is why a trailing exit can never lose.
    """

    name = "Trailing"

    def __init__(self, activation, offset):
        if activation <= 0 or offset <= 0:
            raise ValueError("activation and offset must be positive")
        if offset >= activation:
            raise ValueError("offset >= activation would exit at or below entry")
        self.activation = float(activation)
        self.offset = float(offset)
        self.level = None

    def reset(self):
        self.level = None

    def on_price(self, pct):
        if self.level is not None and pct <= self.level:
            return self.name, self.level
        if pct >= self.activation:
            lvl = pct - self.offset
            self.level = lvl if self.level is None else max(self.level, lvl)
        return None


class BreakEven(Rule):
    """Move the stop to entry (plus ``lock``) once ``trigger`` is reached."""

    name = "BE"

    def __init__(self, trigger, lock=0.0):
        if trigger <= 0:
            raise ValueError("trigger must be positive")
        if lock >= trigger:
            raise ValueError("lock >= trigger would exit immediately on arming")
        self.trigger = float(trigger)
        self.lock = float(lock)
        self.armed = False

    def reset(self):
        self.armed = False

    def on_price(self, pct):
        if self.armed and pct <= self.lock:
            return self.name, self.lock
        if pct >= self.trigger:
            self.armed = True
        return None


class TimeStop(Rule):
    """Close after ``bars`` candles regardless of price.

    Counts candles at the granularity being replayed, so pass the count for
    that granularity rather than for the chart timeframe.
    """

    name = "Time"

    def __init__(self, bars):
        if bars <= 0:
            raise ValueError("bars must be positive")
        self.bars = int(bars)
        self.seen = 0

    def reset(self):
        self.seen = 0

    def on_bar_close(self, pct):
        self.seen += 1
        if self.seen >= self.bars:
            return self.name, pct
        return None

    def on_price(self, pct):
        return None
