"""Run a set of strategies against one shared, compounding pool of capital.

Per-trade results tell you whether a strategy has an edge. They do not tell
you what an account running several of them would have done, because the
strategies compete for the same capital: a signal that arrives while the pool
is fully committed is not a smaller position, it is *no* position.

This module answers that. It takes trades that already carry their result -
from `bme.estimate`, from a backtest, from anywhere - and walks them in entry
order against a pool.

Two admission rules ship on purpose:

  "margin"  Realistic. Margin is posted at entry and frozen at that amount; a
            new order needs free equity *now*. Since equity moves while
            positions are open, the same fraction can admit different trades
            at different leverage. That is a real effect - and it turns
            pathological when the fraction lands on a 1/n boundary, where n
            positions consume exactly the whole pool and a rounding-scale
            move decides whether the next one fits.

  "slots"   Scale-invariant. At most floor(1/fraction) positions, full stop.
            The trade set is then identical across leverage, which isolates
            what leverage actually does.

Report both. Where they agree, the sizing is robust; where they diverge, the
fraction is sitting on a boundary and should be moved.
"""
import collections
import datetime
import math

Trade = collections.namedtuple("Trade", "key entry exit ret")


class Run:
    """One pass of one configuration over the pool."""

    __slots__ = ("equity", "curve", "taken", "skipped", "max_concurrent", "worst_trade",
                 "peak_margin_ratio", "years", "fraction", "leverage", "admission",
                 "reinvest", "drawdown")

    @property
    def cagr(self):
        if self.equity <= 0 or self.years <= 0:
            return float("nan")
        return 100.0 * (self.equity ** (1 / self.years) - 1)

    @property
    def max_drawdown(self):
        return 100.0 * self.drawdown

    @property
    def mar(self):
        """CAGR per point of worst-case drawdown.

        A ratio, not a test: it says nothing about whether the edge is real.
        And max drawdown is a single observation, so it is the least stable
        number here.
        """
        dd = self.max_drawdown
        return self.cagr / dd if dd else 0.0

    @property
    def skip_rate(self):
        total = self.taken + self.skipped
        return 100.0 * self.skipped / total if total else 0.0


def simulate(trades, fraction, leverage=1.0, admission="slots", reinvest=True):
    """Walk `trades` against a shared pool. Returns a Run.

    fraction   margin each order commits, as a share of equity
    leverage   notional is fraction * leverage; profit and loss land on the
               notional, so leverage scales each result without asking for
               more margin - it does not, by itself, buy more positions
    admission  "slots" or "margin" (see the module docstring)
    reinvest   True compounds: margin follows equity. False keeps every order
               at the same size as the first, which is what a fixed-size
               backtest reports and is usually far lower.

    Trades may overlap and may come from different strategies; they are
    processed in entry order, the order a live account sees them.
    """
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if admission not in ("slots", "margin"):
        raise ValueError("admission must be 'slots' or 'margin'")
    trades = sorted(trades, key=lambda t: t.entry)
    if not trades:
        raise ValueError("no trades")

    slots = int(1.0 / fraction) if admission == "slots" else None
    equity = peak = 1.0
    committed = drawdown = worst = peak_ratio = 0.0
    taken = skipped = max_concurrent = 0
    open_pos = []                       # (exit, margin, notional, ret)
    curve = collections.OrderedDict()

    by_entry = collections.defaultdict(list)
    for t in trades:
        by_entry[t.entry].append(t)
    clock = sorted({t.entry for t in trades} | {t.exit for t in trades})

    for now in clock:
        still_open = []
        for exit_at, margin, notional, ret in open_pos:
            if exit_at <= now:
                pnl = notional * ret / 100.0
                worst = min(worst, 100.0 * pnl / equity)
                equity += pnl
                committed -= margin
            else:
                still_open.append((exit_at, margin, notional, ret))
        open_pos = still_open
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)

        for t in by_entry.get(now, ()):
            margin = fraction * (equity if reinvest else 1.0)
            if slots:
                room = len(open_pos) < slots
            else:
                room = committed + margin <= equity + 1e-12
            if room:
                open_pos.append((t.exit, margin, margin * leverage, t.ret))
                committed += margin
                taken += 1
                max_concurrent = max(max_concurrent, len(open_pos))
                peak_ratio = max(peak_ratio, committed / equity)
            else:
                skipped += 1
        curve[now.strftime("%Y-%m")] = equity

    for exit_at, margin, notional, ret in open_pos:     # settle stragglers
        equity += notional * ret / 100.0
    peak = max(peak, equity)
    drawdown = max(drawdown, 1.0 - equity / peak)
    curve[clock[-1].strftime("%Y-%m")] = equity

    r = Run()
    r.equity, r.curve, r.taken, r.skipped = equity, curve, taken, skipped
    r.max_concurrent, r.worst_trade, r.peak_margin_ratio = max_concurrent, worst, peak_ratio
    r.fraction, r.leverage, r.admission, r.reinvest = fraction, leverage, admission, reinvest
    r.drawdown = drawdown
    r.years = (clock[-1] - clock[0]).total_seconds() / 31557600.0
    return r


def monthly(run):
    """Month-end equity as monthly percentage returns."""
    out, prev = [], 1.0
    for month in sorted(run.curve):
        equity = run.curve[month]
        out.append({"month": month, "ret": 100.0 * (equity / prev - 1), "equity": equity})
        prev = equity
    return out


def sweep(trades, fractions, leverages=(1, 2), admissions=("slots", "margin"), **kw):
    """Every combination, so boundary fractions show themselves.

    A fraction whose two admission rules disagree is sitting on a 1/n
    boundary - pick a neighbouring one instead of trusting either number.
    """
    return [simulate(trades, f, lev, mode, **kw)
            for f in fractions for mode in admissions for lev in leverages]


def boundary_fractions(runs, tolerance=1e-9):
    """Fractions whose two admission rules disagree - the ones to avoid.

    Disagreement is measured on the trade set, not on returns. Two rules that
    admit different trades have already diverged; whether that shows up as a
    big or small CAGR gap depends on the sample and on how long it ran, and a
    short backtest can hide a real disagreement behind an annualisation that
    swamps it. Equity is the tiebreak for the rare case where the counts match
    but the timing does not.
    """
    by_key = collections.defaultdict(dict)
    for r in runs:
        by_key[(r.fraction, r.leverage)][r.admission] = r
    out = set()
    for (fraction, _), modes in by_key.items():
        if len(modes) < 2:
            continue
        a, b = modes["slots"], modes["margin"]
        same_trades = (a.taken, a.skipped) == (b.taken, b.skipped)
        same_equity = abs(a.equity - b.equity) <= tolerance * max(abs(a.equity), 1.0)
        if not (same_trades and same_equity):
            out.add(fraction)
    return sorted(out)
