# bar-magnifier-estimator

Estimate what TradingView's **Bar Magnifier** would do to a strategy, without paying for it.

Bar Magnifier resolves what happened *inside* each chart candle. Without it, a backtest has to
guess the intrabar path — and when a strategy opens and closes trades inside a single candle,
that guess decides most of the results. On the strategy this was built against, **77% of trades
opened and closed inside the same 4h candle**, and turning the magnifier on removed between 63%
and 79% of the reported profit.

This library replays your exported trades against finer candles from the exchange, applying your
exit rules at that finer resolution. Pure standard library — no dependencies, no API keys.

---

## What it is not

**It does not reproduce Bar Magnifier. It estimates it.** The granularity that best matches the
real feature was found by calibrating against six exports of one strategy on one exchange. For a
different strategy, that calibration may not transfer, and without a reference run there is
nothing to anchor it to. Say so when you report numbers produced this way.

**It only handles price-level exits** — stops, targets, trailing stops, break-even, time stops.
If a strategy exits on an indicator (a moving-average cross, an RSI level, an opposite signal),
you have to reimplement that indicator, and at that point you are rewriting the strategy rather
than using a tool.

**It cannot model re-entries.** The entries come from your export and are fixed. If new parameters
lengthen holding times, some of those entries would never have been taken — the position would
still be open. Widen your stops and you have to re-export, not just re-run this.

---

## Install

```bash
git clone https://github.com/colochampre/bar-magnifier-estimator.git
cd bar-magnifier-estimator
python -m unittest discover -s tests
```

Python 3.9+. Candle and funding responses are cached under `~/.bme-cache` (override with
`BME_CACHE`).

---

## Use

Export **List of Trades** from TradingView's Strategy Tester, then:

```python
from bme import candles, estimate, report, rules, tradingview

provider = candles.get_provider("binance")

# 1. The export's timestamps are in the chart's display timezone, not UTC,
#    and nothing in the file says which. Get this wrong and every trade is
#    replayed against price action that had not happened yet.
offset, hits, tested = tradingview.detect_utc_offset(
    "export.csv", provider, "AAVEUSDT", "4h")

# 2. Describe your exits, tightest first.
my_rules = [
    rules.TrailingStop(activation=3.0, offset=0.6),
    rules.StopLoss(5.0),
]

# 3. Validate at the chart timeframe, then estimate finer.
trades = tradingview.read_trades("export.csv", offset)
baseline, fine, ok = estimate.compare(
    trades, provider, "AAVEUSDT", "4h", "15m", my_rules, commission_pct=0.1)

print(report.validation(baseline, ok))
if ok:
    print(report.comparison(baseline, fine))
```

A full run is in [`examples/rsi_divergence.py`](examples/rsi_divergence.py).

---

## The protocol

The order is the whole point.

**1. Detect the timezone.** `detect_utc_offset` shifts the export against real candles and keeps
the offset whose fills sit closest to their candle opens. Containment alone is not enough — a wide
candle holds prices from hours either side, so several offsets score a perfect match.

**2. Verify the symbol.** `verify_symbol` checks the fills actually belong to the symbol named in
the filename. An export taken while the chart is still loading a new symbol silently contains the
*previous* symbol's trades. This happened during development: 4 of 11 files were wrong, 2 of them
byte-identical duplicates, and nothing but a price check would have caught it.

**3. Validate at the chart timeframe.** Replaying with your rules at the timeframe the strategy
ran on must reproduce the export. This is the only evidence your rules describe the strategy.
Below ~90% signal match, fix the rules — every finer number is noise until this passes.

**4. Only then, replay finer.** A high match at the chart timeframe and a very different result
at finer granularity is the expected outcome. That gap is what you came to measure.

**5. Calibrate if you can.** If you have one strategy exported both with and without Bar
Magnifier, `calibrate` sweeps granularities and reports which reproduces the real effect. On the
reference strategy — a 4h chart — the answer was **15 minutes**:

| Granularity | Error vs. real magnifier | Estimated change |
|---|---|---|
| 1h | 0.752 | −41.1% |
| 30m | 0.366 | −57.7% |
| **15m** | **0.180** | **−65.5%** |
| 1m | 0.399 | −76.1% |

The real magnifier measured −63.2%. Replaying at 1 minute overstates the damage by ~15 points:
finer is not automatically more accurate, it is just finer.

---

## Writing rules

Two methods:

```python
from bme.rules import Rule

class ATRStop(Rule):
    name = "ATR"

    def __init__(self, multiple):
        self.multiple = multiple

    def reset(self):
        """Called once per trade."""
        self.level = None

    def on_price(self, pct):
        """Called with each price the market visits, as % from entry.

        Return (name, exit_pct) to close, or None to let it run.
        """
        if self.level is not None and pct <= self.level:
            return self.name, self.level
        return None
```

Set `name` to match the export's `Signal` column, or validation cannot score that exit.

**Order matters.** Rules are consulted in list order at each price, so the tighter exit goes
first: once a trailing stop has ratcheted above the fixed stop, price crosses the trailing level
first on the way down. Listing them the other way misreports those trades as stop-outs — it cost
13 percentage points of match rate before it was spotted.

Order only breaks ties at the same price, though. Which price the market is assumed to reach
*first* outranks it, and that assumption is exactly what this library exists to question.

---

## Funding

`funding_cost` applies real historical funding rates trade by trade. Worth measuring rather than
assuming: on the reference strategy it came out **negative** — the strategy was short often enough,
in a market where rates are mostly positive, that it collected more than it paid. A cost that had
been flagged as a major risk turned out to be a small credit.

---

## Layout

| File | What it does |
|---|---|
| `bme/tradingview.py` | Read exports, detect timezone, verify symbol |
| `bme/candles.py` | Binance and Bybit providers, disk cache |
| `bme/rules.py` | Exit rules, and the base class for your own |
| `bme/engine.py` | The intrabar path walker |
| `bme/estimate.py` | Validation, estimation, calibration, funding |
| `bme/report.py` | Console tables |

---

## What still is not modelled

**Slippage.** Every result assumes stops fill at their exact price. This is the load-bearing
assumption, and it gets heavier the wider the stop.

**Re-entries**, as above.

**Indicator-based exits.** On the reference strategy an opposite-signal exit existed but never
fired — until wider stops lengthened the holds and it started firing between one and eight times
per pair. The estimate then overstated totals by 2–14%. That is the boundary of the method, and
it is easy to cross without noticing.
