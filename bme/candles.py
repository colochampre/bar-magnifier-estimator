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
        return bisect.bisect_left(self._times, int(ts.timestamp() * 1000))

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

    def series(self, symbol, timeframe, start, end):
        key = self._cache_path(self.name, symbol, timeframe,
                               int(start.timestamp()), int(end.timestamp()))
        if os.path.exists(key):
            raw = json.load(io.open(key, encoding="utf-8"))
            return CandleSeries([Candle(*c) for c in raw], SECONDS[timeframe])

        out, cursor = [], int(start.timestamp() * 1000)
        stop = int(end.timestamp() * 1000)
        while cursor < stop:
            page = self._fetch_page(symbol, timeframe, cursor, stop)
            if not page:
                break
            out += page
            nxt = page[-1].time + 1
            if nxt <= cursor:
                break
            cursor = nxt
            time.sleep(self.throttle)
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
