"""Tests for the shared-pool simulation.

These pin the invariants that make the numbers trustworthy. If any of them
break, every CAGR the module reports is meaningless, so they are worth more
than the usual coverage.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bme.portfolio import Trade, simulate, sweep, boundary_fractions, monthly, sequence

BASE = datetime.datetime(2024, 1, 1)
HOUR = datetime.timedelta(hours=1)


def t(start_h, hours, ret, key="X"):
    return Trade(key, BASE + start_h * HOUR, BASE + (start_h + hours) * HOUR, ret)


def spaced(returns, gap=10, hold=4):
    """Non-overlapping trades, so admission never blocks anything."""
    return [t(i * gap, hold, r) for i, r in enumerate(returns)]


class TestCompounding(unittest.TestCase):
    def test_reinvest_compounds_the_whole_pool(self):
        r = simulate(spaced([10.0, 10.0]), fraction=1.0)
        self.assertAlmostEqual(r.equity, 1.1 * 1.1, places=12)

    def test_without_reinvest_every_order_is_the_first_size(self):
        r = simulate(spaced([10.0, 10.0]), fraction=1.0, reinvest=False)
        self.assertAlmostEqual(r.equity, 1.0 + 0.1 + 0.1, places=12)

    def test_fraction_scales_the_result(self):
        r = simulate(spaced([10.0]), fraction=0.25)
        self.assertAlmostEqual(r.equity, 1.0 + 0.25 * 0.10, places=12)


class TestLeverage(unittest.TestCase):
    def test_leverage_doubles_the_effect_of_a_trade(self):
        one = simulate(spaced([7.0]), fraction=0.3, leverage=1)
        two = simulate(spaced([7.0]), fraction=0.3, leverage=2)
        self.assertAlmostEqual(two.equity - 1, 2 * (one.equity - 1), places=12)

    def test_leverage_does_not_change_which_trades_are_taken(self):
        # Six overlapping trades, three slots: someone has to be turned away.
        trades = [t(i, 20, -2.0 if i % 2 else 3.0) for i in range(6)]
        one = simulate(trades, fraction=0.3, leverage=1, admission="slots")
        two = simulate(trades, fraction=0.3, leverage=2, admission="slots")
        self.assertEqual((one.taken, one.skipped), (two.taken, two.skipped))
        self.assertGreater(one.skipped, 0)      # the test would be empty otherwise


class TestAdmission(unittest.TestCase):
    def test_slots_cap_is_floor_of_one_over_fraction(self):
        trades = [t(i, 50, 1.0) for i in range(10)]
        for fraction, expected in ((0.5, 2), (0.3, 3), (0.25, 4), (0.2, 5)):
            r = simulate(trades, fraction=fraction, admission="slots")
            self.assertEqual(r.max_concurrent, expected, "fraction %s" % fraction)

    def test_margin_never_exceeds_the_pool(self):
        trades = [t(i, 50, 1.0) for i in range(10)]
        for fraction in (0.2, 0.25, 0.3, 0.4, 0.5):
            r = simulate(trades, fraction=fraction, admission="margin")
            self.assertLessEqual(r.peak_margin_ratio, 1.0 + 1e-9, "fraction %s" % fraction)

    def test_a_full_pool_turns_the_next_signal_away(self):
        trades = [t(i, 50, 5.0) for i in range(4)]
        r = simulate(trades, fraction=0.5, admission="slots")
        self.assertEqual((r.taken, r.skipped), (2, 2))


class TestBoundaryDetection(unittest.TestCase):
    def test_a_boundary_fraction_is_reported(self):
        # Losses shrink the pool while positions are open, so frozen margin
        # stops fitting - only possible where n positions fill it exactly.
        trades = [t(i * 3, 20, -4.0) for i in range(9)]
        runs = sweep(trades, [0.5, 0.3], leverages=(1,))
        self.assertIn(0.5, boundary_fractions(runs))
        self.assertNotIn(0.3, boundary_fractions(runs))


class TestMetrics(unittest.TestCase):
    def test_drawdown_measures_the_worst_fall_from_a_peak(self):
        r = simulate(spaced([50.0, -20.0, 10.0]), fraction=1.0)
        self.assertAlmostEqual(r.max_drawdown, 20.0, places=9)

    def test_worst_trade_is_a_share_of_the_pool_not_the_position(self):
        r = simulate(spaced([-10.0]), fraction=0.25)
        self.assertAlmostEqual(r.worst_trade, -2.5, places=9)

    def test_monthly_returns_chain_back_to_the_final_equity(self):
        r = simulate(spaced([5.0, 5.0, -3.0], gap=800), fraction=1.0)
        chained = 1.0
        for row in monthly(r):
            chained *= 1 + row["ret"] / 100.0
        self.assertAlmostEqual(chained, r.equity, places=9)


class TestGuards(unittest.TestCase):
    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate([], fraction=0.25)

    def test_fraction_outside_zero_to_one_is_rejected(self):
        for bad in (0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                simulate(spaced([1.0]), fraction=bad)

    def test_unknown_admission_rule_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate(spaced([1.0]), fraction=0.25, admission="whatever")


class TestOnePerKey(unittest.TestCase):
    """A symbol running one strategy cannot hold two positions at once."""

    def overlapping(self):
        # A's first trade is still open when A's second signal arrives; B is free.
        return [Trade("A", BASE, BASE + 10 * HOUR, 1.0),
                Trade("A", BASE + 2 * HOUR, BASE + 5 * HOUR, 1.0),
                Trade("B", BASE + 2 * HOUR, BASE + 5 * HOUR, 1.0)]

    def test_a_second_signal_on_a_busy_symbol_is_a_conflict(self):
        r = simulate(self.overlapping(), fraction=0.2, one_per_key=True)
        self.assertEqual((r.taken, r.skipped, r.conflicts), (2, 0, 1))
        self.assertEqual(r.max_concurrent, 2)

    def test_conflicts_do_not_count_as_lost_opportunity(self):
        r = simulate(self.overlapping(), fraction=0.2, one_per_key=True)
        self.assertEqual(r.skip_rate, 0.0)

    def test_off_by_default(self):
        r = simulate(self.overlapping(), fraction=0.2)
        self.assertEqual((r.taken, r.conflicts, r.max_concurrent), (3, 0, 3))

    def test_the_symbol_is_free_again_once_its_trade_closes(self):
        trades = [Trade("A", BASE, BASE + 4 * HOUR, 1.0), Trade("A", BASE + 4 * HOUR, BASE + 8 * HOUR, 1.0)]
        r = simulate(trades, fraction=0.2, one_per_key=True)
        self.assertEqual((r.taken, r.conflicts), (2, 0))


class TestSequence(unittest.TestCase):
    """Replayed exits versus entries stamped at chart-bar resolution."""

    CHART = 4 * 3600
    HALF = datetime.timedelta(minutes=30)

    def test_a_reentry_inside_the_exit_bar_is_kept(self):
        # The first trade exits 4h30m in, inside the second chart bar, and the
        # export re-enters at that bar's open - after the exit, stamped before it.
        a = Trade("A", BASE, BASE + 4 * HOUR + self.HALF, 1.0)
        b = Trade("A", BASE + 4 * HOUR, BASE + 8 * HOUR, 2.0)
        kept, conflicts = sequence([a, b], self.CHART)
        self.assertEqual((len(kept), conflicts), (2, 0))
        self.assertEqual(kept[0].exit, b.entry)

    def test_an_entry_before_the_exit_bar_is_dropped(self):
        a = Trade("A", BASE, BASE + 9 * HOUR, 1.0)          # exits in the third bar
        b = Trade("A", BASE + 4 * HOUR, BASE + 6 * HOUR, 2.0)
        kept, conflicts = sequence([a, b], self.CHART)
        self.assertEqual((len(kept), conflicts), (1, 1))
        self.assertEqual(kept[0], a)

    def test_an_exit_on_a_bar_boundary_belongs_to_the_bar_it_closes(self):
        a = Trade("A", BASE, BASE + 4 * HOUR, 1.0)          # replay stamps the end of bar one
        b = Trade("A", BASE + 4 * HOUR, BASE + 8 * HOUR, 2.0)
        kept, conflicts = sequence([a, b], self.CHART)
        self.assertEqual((len(kept), conflicts), (2, 0))
        self.assertEqual(kept[0].exit, a.exit)

    def test_keys_are_sequenced_independently(self):
        a = Trade("A", BASE, BASE + 9 * HOUR, 1.0)
        b = Trade("B", BASE + 4 * HOUR, BASE + 6 * HOUR, 2.0)
        kept, conflicts = sequence([a, b], self.CHART)
        self.assertEqual((len(kept), conflicts), (2, 0))

    def test_sequenced_trades_never_conflict_in_the_pool(self):
        a = Trade("A", BASE, BASE + 4 * HOUR + self.HALF, 1.0)
        b = Trade("A", BASE + 4 * HOUR, BASE + 8 * HOUR, 2.0)
        kept, _ = sequence([a, b], self.CHART)
        self.assertEqual(simulate(kept, fraction=0.2, one_per_key=True).conflicts, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
