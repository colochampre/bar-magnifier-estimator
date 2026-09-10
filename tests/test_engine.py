"""Tests for the parts that decide results.

These use hand-built candles rather than live data, so they run offline and
pin the behaviour that is easy to get subtly wrong: the order prices are
visited inside a candle, and which of two stops is reached first.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bme.candles import Candle, CandleSeries
from bme.engine import path_of, replay
from bme.rules import BreakEven, StopLoss, TakeProfit, TimeStop, TrailingStop
from bme.tradingview import infer_timeframe, Trade

UTC = datetime.timezone.utc


def bar(o, h, l, c, t=0):
    return Candle(t, o, h, l, c)


class TestIntrabarPath(unittest.TestCase):
    def test_bullish_candle_visits_low_first(self):
        self.assertEqual(path_of(bar(100, 110, 90, 105), long=True), (90, 110))

    def test_bearish_candle_visits_high_first(self):
        self.assertEqual(path_of(bar(100, 110, 90, 95), long=True), (110, 90))

    def test_short_reads_the_candle_inverted(self):
        # A candle closing down is favourable for a short, so its favourable
        # extreme (the low) must be visited last, as for a bullish long.
        self.assertEqual(path_of(bar(100, 110, 90, 95), long=False), (110, 90))


class TestRulePriority(unittest.TestCase):
    def test_trailing_fires_before_the_wider_stop(self):
        # Arms at +3, ratchets to +2.4, then price collapses through both the
        # trailing level and the fixed stop inside one candle.
        rules = [TrailingStop(activation=3.0, offset=0.6), StopLoss(5.0)]
        fill = replay(100.0, True, [bar(100, 103, 103, 103), bar(103, 103, 90, 90)], rules)
        self.assertEqual(fill.signal, "Trailing")
        self.assertAlmostEqual(fill.pct, 2.4, places=6)

    def test_stop_fires_when_trailing_never_armed(self):
        rules = [TrailingStop(activation=3.0, offset=0.6), StopLoss(5.0)]
        fill = replay(100.0, True, [bar(100, 101, 94, 94)], rules)
        self.assertEqual(fill.signal, "SL")
        self.assertAlmostEqual(fill.pct, -5.0, places=6)

    def test_path_order_outranks_rule_order(self):
        # A candle that reaches both levels is decided by which price the
        # market is assumed to visit first, not by how the rules are listed.
        # This is the single assumption Bar Magnifier replaces, so it is the
        # one worth pinning: rule order only breaks ties at the same price.
        bearish = [bar(100, 104, 94, 94)]     # closes down -> high visited first
        bullish = [bar(100, 104, 94, 103)]    # closes up   -> low visited first
        for order in ([StopLoss(5.0), TakeProfit(3.0)], [TakeProfit(3.0), StopLoss(5.0)]):
            self.assertEqual(replay(100.0, True, bearish, order).signal, "TP")
        for order in ([StopLoss(5.0), TakeProfit(3.0)], [TakeProfit(3.0), StopLoss(5.0)]):
            self.assertEqual(replay(100.0, True, bullish, order).signal, "SL")

    def test_finer_candles_can_reverse_a_coarse_result(self):
        # One coarse candle spanning 96 to 104: closing up, it is assumed to
        # dip first, so the stop is recorded as hit. Split it and the rally
        # came first, reaching the target while the dip was still ahead. Same
        # rules, same prices, opposite outcome - the effect this measures.
        rules = lambda: [StopLoss(3.5), TakeProfit(3.0)]
        coarse = [bar(100, 104, 96, 103)]
        fine = [bar(100, 104, 100, 104), bar(104, 104, 96, 96)]
        self.assertEqual(replay(100.0, True, coarse, rules()).signal, "SL")
        self.assertEqual(replay(100.0, True, fine, rules()).signal, "TP")


class TestTrailingFloor(unittest.TestCase):
    def test_trailing_can_never_exit_below_its_floor(self):
        rules = [TrailingStop(activation=3.0, offset=0.6)]
        fill = replay(100.0, True, [bar(100, 103, 100, 103), bar(103, 103, 100, 100)], rules)
        self.assertGreaterEqual(fill.pct, 3.0 - 0.6 - 1e-9)

    def test_offset_wider_than_activation_is_rejected(self):
        with self.assertRaises(ValueError):
            TrailingStop(activation=1.0, offset=1.0)

    def test_level_only_ratchets_forward(self):
        r = TrailingStop(activation=3.0, offset=0.6)
        r.reset()
        r.on_price(5.0)
        self.assertAlmostEqual(r.level, 4.4)
        r.on_price(3.5)          # pulls back but does not trigger
        self.assertAlmostEqual(r.level, 4.4)


class TestOtherRules(unittest.TestCase):
    def test_break_even_arms_then_exits_at_the_lock(self):
        rules = [BreakEven(trigger=2.0, lock=0.2)]
        fill = replay(100.0, True, [bar(100, 102, 100, 102), bar(102, 102, 99, 99)], rules)
        self.assertEqual(fill.signal, "BE")
        self.assertAlmostEqual(fill.pct, 0.2, places=6)

    def test_time_stop_closes_at_the_bar_count(self):
        fill = replay(100.0, True, [bar(100, 101, 99, 100)] * 5, [TimeStop(3)])
        self.assertEqual((fill.signal, fill.bars), ("Time", 3))

    def test_short_trade_profits_when_price_falls(self):
        fill = replay(100.0, False, [bar(100, 100, 96, 96)], [TakeProfit(3.0)])
        self.assertEqual(fill.signal, "TP")


class TestUnclosedAndCosts(unittest.TestCase):
    def test_no_rule_firing_returns_none(self):
        self.assertIsNone(replay(100.0, True, [bar(100, 101, 99, 100)], [StopLoss(5.0)]))

    def test_commission_comes_off_the_result(self):
        fill = replay(100.0, True, [bar(100, 104, 100, 104)], [TakeProfit(3.0)], commission_pct=0.1)
        self.assertAlmostEqual(fill.pct, 2.9, places=6)


class TestCandleSeries(unittest.TestCase):
    def setUp(self):
        step = 900_000
        self.series = CandleSeries([bar(1, 1, 1, 1, i * step) for i in range(10)], 900)

    def test_window_starts_at_the_requested_time(self):
        ts = datetime.datetime.fromtimestamp(3 * 900, UTC)
        self.assertEqual(len(self.series.window(ts, 4)), 4)
        self.assertEqual(self.series.window(ts, 4)[0].time, 3 * 900_000)

    def test_window_past_the_end_is_short_not_an_error(self):
        ts = datetime.datetime.fromtimestamp(9 * 900, UTC)
        self.assertEqual(len(self.series.window(ts, 5)), 1)


class TestInferTimeframe(unittest.TestCase):
    def test_reads_the_spacing_of_entries(self):
        base = datetime.datetime(2024, 1, 1, tzinfo=UTC)
        trades = [Trade(i, base + datetime.timedelta(hours=4 * i), 1.0,
                        base + datetime.timedelta(hours=4 * i + 1), 1.0, True, "TP", 1.0, 0)
                  for i in (0, 1, 3, 7)]
        self.assertEqual(infer_timeframe(trades), 4 * 3600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
