"""End-to-end run on the strategy this library was built and calibrated against.

RSI Divergence v1.3 on Binance perpetual futures, 4h chart. Its exits are price
levels only - a fixed stop plus a trailing stop - which is exactly the shape the
engine handles.

Two configurations appear below, and the distinction matters more than it looks.
LEGACY is the one whose Bar Magnifier effect was measured directly, by exporting
it twice with the feature off and on; its coefficients are the only thing that
can anchor a granularity sweep. CURRENT is the configuration that came out of
tuning. You can estimate either, but you can only *calibrate* against a
reference measured with the same rules - a trailing stop arming at +1% behaves
nothing like one arming at +3%.

    python examples/rsi_divergence.py export.csv AAVEUSDT
    python examples/rsi_divergence.py export.csv AAVEUSDT --legacy
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bme import candles, estimate, report, rules, tradingview

CHART_TF = "4h"
FINE_TF = "15m"
COMMISSION = 0.1                 # 0.05% per side


def current_rules():
    """Tuned configuration: no target, wide stop, late trailing.

    Trailing is listed first because once armed it sits above the stop, so
    price crosses it first on the way down. Listing the stop first misreports
    those trades as stop-outs - it cost 13 points of match rate here.
    """
    return [
        rules.TrailingStop(activation=3.0, offset=0.6),
        rules.StopLoss(5.0),
    ]


def legacy_rules():
    """Original configuration, and the one the reference below describes."""
    return [
        rules.TrailingStop(activation=1.0, offset=0.6),
        rules.StopLoss(1.9),
        rules.TakeProfit(3.1),
    ]


#: Points per trade given up when the real Bar Magnifier was switched on, by
#: the exit the coarse run recorded. Measured on OKX exports of AAVE, BAT and
#: SOL running LEGACY_RULES; the three pairs agreed to within 0.08 points.
#: Only valid for legacy_rules().
LEGACY_REFERENCE = {"TP": -1.433, "Trailing": -0.371, "SL": +0.216}


def main(csv_path, symbol, *flags):
    legacy = "--legacy" in flags
    provider = candles.get_provider("binance")
    exit_rules = legacy_rules if legacy else current_rules
    print("configuration: %s" % ("legacy" if legacy else "current"))

    # Detection runs at a fine timeframe on purpose: on a 4h chart every offset
    # inside the same 4h bin picks the same candle, so the answer is arbitrary.
    offset, hits, tested = tradingview.detect_utc_offset(csv_path, provider, symbol)
    print("timezone: UTC%+g (%d/%d sample fills inside their candle)" % (offset, hits, tested))
    if hits < tested:
        print("  WARNING: not every sample matched. Check the symbol and timeframe -")
        print("  a wrong offset replays every trade against price action that had")
        print("  not happened yet, and the output will still look plausible.")

    confidence = tradingview.verify_symbol(csv_path, provider, symbol, CHART_TF, offset)
    print("symbol check: %.0f%% of sampled fills belong to %s" % (100 * confidence, symbol))
    if confidence < 1.0:
        print("  WARNING: this export may hold another symbol's trades.")

    trades = tradingview.read_trades(csv_path, offset)
    print("loaded %d completed trades" % len(trades))

    baseline, fine, ok = estimate.compare(
        trades, provider, symbol, CHART_TF, FINE_TF, exit_rules(), COMMISSION)

    print(report.validation(baseline, ok, symbol))
    if not ok:
        print("\nStopping: the rules do not reproduce the export, so the %s" % FINE_TF)
        print("estimate would not mean anything. Fix the rules first.")
        return 1

    print(report.comparison(baseline, fine, symbol))
    print(report.degradation_table(estimate.degradation(baseline, fine)))

    paid = estimate.funding_cost(trades, provider, symbol)
    avg = sum(paid) / len(paid)
    print("\nFUNDING\n  %+.4f%% per trade (%s)"
          % (avg, "paid" if avg > 0 else "received - shorts collect more than longs pay"))

    if not legacy:
        print("\nCALIBRATION\n  Skipped: the reference was measured on the legacy")
        print("  configuration, and coefficients do not transfer between parameter")
        print("  sets. Re-run with --legacy on a legacy export to see the sweep.")
        return 0

    print(report.calibration_table(
        estimate.calibrate(trades, provider, symbol, CHART_TF, exit_rules(),
                           LEGACY_REFERENCE, commission_pct=COMMISSION),
        LEGACY_REFERENCE))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
