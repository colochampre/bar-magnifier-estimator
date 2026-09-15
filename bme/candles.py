"""Candle providers.

Only public market-data endpoints are used, so no API key is needed. Every
response is cached on disk: a granularity sweep replays the same trades four
or five times over, and without a cache that is thousands of redundant
requests.
"""
import bisect
import collections
import datetime
import io
import json
import os
import time
import urllib.error
import urllib.request

Candle = collections.namedtuple("Candle", "time open high low close")
UTC = datetime.timezone.utc

SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
           "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}


def default_cache_dir():
    return os.environ.get("BME_CACHE", os.path.join(os.path.expanduser("~"), ".bme-cache"))


class CandleSeries:
    """A chronological run of candles with fast lookup by timestamp."""

    def __init__(self, candles, step_seconds):
        self.candles = candles
        self.step = step_seconds
        self._times = [c.time for c in candles]

    def __len__(self):
        return len(self.candles)

    def index_at(self, ts):
        """Index of the candle *containing* ``ts``.

        The timestamp is floored to the timeframe boundary first, the same way
        ``HTTPProvider.candle_at`` does it. Bisecting the raw value returns the
        candle that *starts after* ``ts`` for anything not sitting exactly on a
        boundary, so a lookup at 01:30 answers with the 02:00 candle and a
        replay window silently skips the bar its trade opens in.

        Every current caller passes boundary-aligned timestamps - entries come
        from chart bars, and a 4h boundary is also a 5m one - which is why the
        two semantics never disagreed in practice. That is what makes it worth
        pinning rather than leaving to luck.
        """
        step = self.step * 1000
        ms = (int(ts.timestamp() * 1000) // step) * step
        return bisect.bisect_left(self._times, ms)

    def window(self, ts, count):
        i = self.index_at(ts)
        return self.candles[i:i + count]

    def at(self, ts):
        i = self.index_at(ts)
        return self.candles[i] if i < len(self.candles) else None


class HTTPProvider:
    """Shared plumbing: cached, retried, paginated GETs."""

    name = "http"

    def __init__(self, cache_dir=None, throttle=0.12):
        self.cache_dir = cache_dir or default_cache_dir()
        self.throttle = throttle
        os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_path(self, *parts):
        return os.path.join(self.cache_dir, "_".join(str(p) for p in parts) + ".json")

    def _get(self, url, attempts=5):
        for i in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, ValueError):
                if i == attempts - 1:
                    raise
                time.sleep(1.5 * (i + 1))

    def candle_at(self, symbol, timeframe, ts):
        """The candle *containing* ``ts``.

        The timestamp is floored to the timeframe boundary first. Without
        that, exchanges return the next candle instead of the current one,
        and timezone detection then scores several offsets identically
        because they all land in the same bar.
        """
        step = SECONDS[timeframe] * 1000
        start = (int(ts.timestamp() * 1000) // step) * step
        rows = self._fetch_page(symbol, timeframe, start, start + step)
        return rows[0] if rows else None

    #: Which end of the requested range a full page comes from. Binance returns
    #: the *oldest* candles in the window, so paging walks forward from the
    #: start; Bybit returns the *newest*, so it has to walk backward from the
    #: end. Getting this wrong does not fail loudly: paging forward against a
    #: newest-first endpoint returns one page and stops, because the cursor
    #: jumps straight to the end of the range. The caller then gets the last
    #: 1000 candles whatever it asked for - four days instead of a year at 5m -
    #: and replays trades against price action from the wrong period.
    newest_first = False

    #: How many times an empty page is retried before it is believed.
    #:
    #: An empty page is ambiguous: it means either "no more history" or "you
    #: are asking too fast". Believing it on the first try silently truncates
    #: the series, and a truncated series does not raise - it replays trades
    #: against price action from the wrong period. That cost two rounds of
    #: plausible-looking nonsense before it was caught: 8.544% on a pair whose
    #: honest number was 77%, because a year of candles was missing from the
    #: middle of a run that hammered three timeframes per symbol.
    empty_retries = 3

    def _page(self, symbol, timeframe, start_ms, end_ms):
        """One page, retried while it comes back empty."""
        for attempt in range(self.empty_retries):
            page = self._fetch_page(symbol, timeframe, start_ms, end_ms)
            if page:
                return page
            time.sleep(self.throttle * 4 * (attempt + 1))
        return []

    def series(self, symbol, timeframe, start, end):
        key = self._cache_path(self.name, symbol, timeframe,
                               int(start.timestamp()), int(end.timestamp()))
        if os.path.exists(key):
            raw = json.load(io.open(key, encoding="utf-8"))
            return CandleSeries([Candle(*c) for c in raw], SECONDS[timeframe])

        floor = int(start.timestamp() * 1000)
        ceiling = int(end.timestamp() * 1000)
        out = []
        if self.newest_first:
            cursor = ceiling
            while cursor > floor:
                page = self._page(symbol, timeframe, floor, cursor)
                if not page:
                    break
                out = page + out
                nxt = page[0].time - 1
                if nxt >= cursor:
                    break
                cursor = nxt
                time.sleep(self.throttle)
        else:
            cursor = floor
            while cursor < ceiling:
                page = self._page(symbol, timeframe, cursor, ceiling)
                if not page:
                    break
                out += page
                nxt = page[-1].time + 1
                if nxt <= cursor:
                    break
                cursor = nxt
                time.sleep(self.throttle)

        # Pages can overlap at their boundaries, and a replay silently produces
        # nonsense on a series that is out of order or repeats a candle.
        out = sorted({c.time: c for c in out}.values(), key=lambda c: c.time)
        json.dump([list(c) for c in out], io.open(key, "w", encoding="utf-8"))
        return CandleSeries(out, SECONDS[timeframe])


class Binance(HTTPProvider):
    """Binance USD-M perpetual futures."""

    name = "binance"
    BASE = "https://fapi.binance.com/fapi/v1"

    def _fetch_page(self, symbol, timeframe, start_ms, end_ms):
        url = ("%s/klines?symbol=%s&interval=%s&startTime=%d&endTime=%d&limit=1500"
               % (self.BASE, symbol, timeframe, start_ms, end_ms))
        return [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]))
                for k in self._get(url)]

    def funding(self, symbol, start, end):
        """Historical funding rates as ``[(timestamp_ms, rate), ...]``."""
        key = self._cache_path(self.name, "funding", symbol,
                               int(start.timestamp()), int(end.timestamp()))
        if os.path.exists(key):
            return [tuple(x) for x in json.load(io.open(key, encoding="utf-8"))]
        out, cursor = [], int(start.timestamp() * 1000)
        stop = int(end.timestamp() * 1000)
        while cursor < stop:
            url = ("%s/fundingRate?symbol=%s&startTime=%d&limit=1000"
                   % (self.BASE, symbol, cursor))
            page = self._get(url)
            if not page:
                break
            out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in page]
            nxt = out[-1][0] + 1
            if nxt <= cursor:
                break
            cursor = nxt
            time.sleep(self.throttle)
        json.dump([list(x) for x in out], io.open(key, "w", encoding="utf-8"))
        return out


class Bybit(HTTPProvider):
    """Bybit linear perpetuals - the fallback when a pair is not on Binance."""

    name = "bybit"
    BASE = "https://api.bybit.com/v5/market"
    #: A page is the newest candles in the window, counted back from `end`.
    newest_first = True
    _TF = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
           "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D"}

    def _fetch_page(self, symbol, timeframe, start_ms, end_ms):
        url = ("%s/kline?category=linear&symbol=%s&interval=%s&start=%d&end=%d&limit=1000"
               % (self.BASE, symbol, self._TF[timeframe], start_ms, end_ms))
        rows = (self._get(url).get("result") or {}).get("list") or []
        # Bybit returns newest first.
        return [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]))
                for k in reversed(rows)]


PROVIDERS = {"binance": Binance, "bybit": Bybit}


def get_provider(name, cache_dir=None):
    try:
        return PROVIDERS[name](cache_dir=cache_dir)
    except KeyError:
        raise ValueError("unknown provider %r, expected one of %s"
                         % (name, ", ".join(sorted(PROVIDERS))))
