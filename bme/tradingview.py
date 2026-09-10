"""Read TradingView's "List of Trades" CSV export.

Two things about that export bite every time.

The timestamps are in the chart's display timezone, not UTC, and nothing in
the file says which. Read them as UTC and every candle you fetch is offset by
hours; the replay then closes trades on price action that had not happened
yet. ``detect_utc_offset`` finds the right shift by checking which one puts
the recorded fill prices inside the candles they claim to come from.

And an export taken while the chart is still loading a new symbol can contain
the *previous* symbol's trades under the new name. ``verify_symbol`` catches
that, and it is worth running on every file before trusting any of them.
"""
import collections
import csv
import datetime
import io
import os

Trade = collections.namedtuple(
    "Trade", "number entry_time entry_price exit_time exit_price long signal ret bars")

UTC = datetime.timezone.utc
_FMT = "%Y-%m-%d %H:%M"


def _parse(stamp):
    return datetime.datetime.strptime(stamp, _FMT).replace(tzinfo=UTC)


def read_trades(path, utc_offset_hours=0):
    """Parse an export into Trades, shifting timestamps to real UTC.

    Rows for a position still open at the end of the backtest are skipped:
    they have no exit and would otherwise look like a trade that never closed.
    """
    shift = datetime.timedelta(hours=utc_offset_hours)
    pending = {}
    for row in csv.DictReader(io.open(path, encoding="utf-8-sig")):
        stamp = row.get("Date and time", "")
        if not stamp[:2].isdigit():
            continue
        n = int(row["Trade number"])
        rec = pending.setdefault(n, {})
        if row["Type"].lower().startswith("entry"):
            rec["entry_time"] = _parse(stamp) + shift
            rec["entry_price"] = float(row["Price USDT"])
            rec["long"] = "long" in row["Type"].lower()
        else:
            rec["exit_time"] = _parse(stamp) + shift
            rec["exit_price"] = float(row["Price USDT"])
            rec["signal"] = row["Signal"]
            rec["ret"] = float(row["Return %"])
            rec["bars"] = int(row["Duration (bars)"])

    out = []
    for n in sorted(pending):
        r = pending[n]
        if "entry_time" in r and "exit_time" in r:
            out.append(Trade(number=n, **r))
    return out


def detect_utc_offset(path, provider, symbol, timeframe="15m", samples=12,
                      candidates=None):
    """Find the export's timezone offset by matching fill prices to candles.

    Returns ``(hours, hits, tested)``.

    Detection runs at a *fine* timeframe, not the chart's. On a 4h chart every
    offset inside the same 4h bin resolves to the same candle, so +3 and +5.5
    score identically and the answer is a coin flip - while at 15m they are
    hours apart and the replay lands on completely different price action.

    Containment alone is still not enough, since a candle holds prices from
    either side of it. The tiebreak is how close each fill sits to its
    candle's *open*: TradingView fills on the bar after the signal, so the
    right offset lands on opens and the wrong ones land anywhere in the range.

    Every candidate is scored - the first perfect score is rarely the only one.
    """
    if candidates is None:
        candidates = [0, 1, 2, 3, -3, -4, -5, 4, 5, 5.5, 8, 9, 10, 11, -6, -7, -8, -2, -1]
    trades = read_trades(path)[:samples]
    if not trades:
        raise ValueError("no completed trades in %s" % os.path.basename(path))

    scored = []
    for off in candidates:
        hits, distance = 0, 0.0
        for t in trades:
            ts = t.entry_time + datetime.timedelta(hours=off)
            bar = provider.candle_at(symbol, timeframe, ts)
            if not bar:
                distance += 1.0
                continue
            if bar.low <= t.entry_price <= bar.high:
                hits += 1
            distance += abs(t.entry_price - bar.open) / bar.open
        scored.append((hits, -distance, off))
    scored.sort(reverse=True)
    hits, _, off = scored[0]
    return off, hits, len(trades)


def verify_symbol(path, provider, symbol, timeframe, utc_offset_hours, samples=5):
    """Check the file's fills really belong to ``symbol``.

    Returns the fraction of sampled fills that land inside that symbol's
    candles. Well below 1.0 means the export does not hold this symbol's
    trades - most often a stale export taken mid symbol-switch.
    """
    trades = read_trades(path, utc_offset_hours)[:samples]
    if not trades:
        return 0.0
    hits = 0
    for t in trades:
        bar = provider.candle_at(symbol, timeframe, t.entry_time)
        if bar and bar.low * 0.97 <= t.entry_price <= bar.high * 1.03:
            hits += 1
    return hits / len(trades)


def infer_timeframe(trades):
    """Infer the chart timeframe in seconds from the entry timestamps."""
    stamps = sorted({t.entry_time for t in trades})
    gaps = {int((b - a).total_seconds()) for a, b in zip(stamps, stamps[1:])}
    gaps = {g for g in gaps if g > 0}
    if not gaps:
        raise ValueError("cannot infer timeframe from a single timestamp")
    step = min(gaps)
    for g in gaps:
        while g:
            step, g = g, step % g
    return step
