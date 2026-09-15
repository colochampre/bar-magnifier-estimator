"""Tests for paginated candle fetching.

The bug these pin cost a whole evaluation before it was caught. Exchanges
disagree about which end of a requested range a full page comes from: Binance
returns the oldest candles in the window, Bybit the newest. Paging forward
against a newest-first endpoint returns exactly one page and stops, because the
cursor jumps straight to the end of the range.

Nothing fails loudly when that happens. The caller asks for a year at 5m, gets
the last four days, and every trade outside those four days is replayed against
price action from the wrong period - producing a 52% signal match and totals in
the thousands of percent, which is what finally gave it away.
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bme.candles import Candle, CandleSeries, HTTPProvider, SECONDS

HOUR = SECONDS["1h"] * 1000
EPOCH = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)


def universe(n):
    base = int(EPOCH.timestamp() * 1000)
    return [Candle(base + i * HOUR, 1.0, 2.0, 0.5, 1.5) for i in range(n)]


class OldestFirst(HTTPProvider):
    """Binance-shaped: a page is the oldest candles in the window."""

    name = "oldest"
    LIMIT = 10

    def __init__(self, candles, **kw):
        HTTPProvider.__init__(self, **kw)
        self.all = candles
        self.calls = 0

    def _window(self, start_ms, end_ms):
        return [c for c in self.all if start_ms <= c.time <= end_ms]

    def _fetch_page(self, symbol, timeframe, start_ms, end_ms):
        self.calls += 1
        return self._window(start_ms, end_ms)[:self.LIMIT]


class NewestFirst(OldestFirst):
    """Bybit-shaped: a page is the newest candles in the window."""

    name = "newest"
    newest_first = True

    def _fetch_page(self, symbol, timeframe, start_ms, end_ms):
        self.calls += 1
        return self._window(start_ms, end_ms)[-self.LIMIT:]


class TestPagination(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.candles = universe(35)
        self.start = EPOCH
        self.end = EPOCH + datetime.timedelta(hours=34)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def provider(self, cls):
        return cls(self.candles, cache_dir=self.dir, throttle=0)

    def test_every_page_direction_covers_the_whole_range(self):
        for cls in (OldestFirst, NewestFirst):
            p = self.provider(cls)
            got = p.series("X", "1h", self.start, self.end).candles
            self.assertEqual(len(got), len(self.candles), cls.__name__)
            self.assertEqual([c.time for c in got], [c.time for c in self.candles],
                             cls.__name__)

    def test_a_newest_first_endpoint_needs_more_than_one_page(self):
        # The regression itself: one page was 10 of 35 candles, and the old
        # cursor rule ended the loop right there.
        p = self.provider(NewestFirst)
        p.series("X", "1h", self.start, self.end)
        self.assertGreater(p.calls, 1)

    def test_the_series_comes_back_in_order_without_repeats(self):
        for cls in (OldestFirst, NewestFirst):
            got = self.provider(cls).series("X", "1h", self.start, self.end).candles
            times = [c.time for c in got]
            self.assertEqual(times, sorted(times), cls.__name__)
            self.assertEqual(len(times), len(set(times)), cls.__name__)

    def test_the_second_call_is_served_from_the_cache(self):
        p = self.provider(NewestFirst)
        p.series("X", "1h", self.start, self.end)
        antes = p.calls
        again = p.series("X", "1h", self.start, self.end).candles
        self.assertEqual(p.calls, antes)
        self.assertEqual(len(again), len(self.candles))

    def test_an_empty_answer_ends_the_walk(self):
        p = NewestFirst([], cache_dir=self.dir, throttle=0)
        self.assertEqual(p.series("X", "1h", self.start, self.end).candles, [])

    def test_a_throttled_page_does_not_end_the_walk(self):
        """An empty page means "no more history" or "you are asking too fast".

        Believing it on the first try truncated a series mid-walk and cached
        the piece as if it were whole, which is how a pair reported 8.544%
        instead of 77%: the missing year was replayed against prices from
        another period.
        """
        class Intermitente(NewestFirst):
            def __init__(self, *a, **kw):
                NewestFirst.__init__(self, *a, **kw)
                self.vacias = 0

            def _fetch_page(self, symbol, timeframe, start_ms, end_ms):
                self.calls += 1
                page = self._window(start_ms, end_ms)[-self.LIMIT:]
                # Se queda sin aire una vez en el medio del recorrido.
                if page and page[0].time != self.all[0].time and self.vacias < 1:
                    self.vacias += 1
                    return []
                return page

        p = Intermitente(self.candles, cache_dir=self.dir, throttle=0)
        got = p.series("X", "1h", self.start, self.end).candles
        self.assertEqual(p.vacias, 1)
        self.assertEqual([c.time for c in got], [c.time for c in self.candles])


class TestSeries(unittest.TestCase):
    def test_at_finds_the_candle_containing_a_timestamp(self):
        s = CandleSeries(universe(5), SECONDS["1h"])
        hit = s.at(EPOCH + datetime.timedelta(minutes=90))
        self.assertEqual(hit.time, int(EPOCH.timestamp() * 1000) + HOUR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
