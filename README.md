# bar-magnifier-estimator

Estimate what TradingView's **Bar Magnifier** would do to a strategy, without paying for it — and how
far below *that* the strategy would really land.

Bar Magnifier resolves what happened *inside* each chart candle. Without it, a backtest has to
guess the intrabar path — and when a strategy opens and closes trades inside a single candle,
that guess decides most of the results. On the strategy this was built against, **77% of trades
opened and closed inside the same 4h candle**, and turning the magnifier on removed between 63%
and 79% of the reported profit.

This library replays your exported trades against finer candles from the exchange, applying your
exit rules at that finer resolution. Pure standard library — no dependencies, no API keys.

It answers two different questions, and it matters which one you are asking:

| Question | Replay at | Why |
|---|---|---|
| What would TradingView report with Bar Magnifier on? | TradingView's intrabar timeframe — **30m on a 4h chart** | [documented by TradingView](https://www.tradingview.com/support/solutions/43000669285-what-is-bar-magnifier-backtesting-mode/), and confirmed by calibration |
| What would the strategy actually have done? | the finest timeframe you can afford | the magnifier itself still guesses inside each 30m bar |

---

## What it is not

**It does not reproduce Bar Magnifier exactly. It estimates it.** TradingView documents which
lower timeframe the magnifier uses for each chart timeframe, and replaying at that timeframe
matched six real magnifier exports of one strategy better than any other granularity. Exchange
data and fill details can still move the result; say so when you report numbers produced this way.

**Finer is more realistic, not exact.** Every step down in granularity reveals intrabar moves the
coarser one hid, and the estimate keeps moving — it converges, but slowly. On the reference
strategy, going from 15m to 5m still took 8–22 points off each pair's total. Treat the finest
replay as the closest estimate you have, not as ground truth.

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

# 3. Validate at the chart timeframe, then replay finer: once at TradingView's
#    intrabar timeframe, once as fine as you can afford.
trades = tradingview.read_trades("export.csv", offset)
baseline, magnifier, ok = estimate.compare(
    trades, provider, "AAVEUSDT", "4h", "30m", my_rules, commission_pct=0.1)

print(report.validation(baseline, ok))
if ok:
    realistic = estimate.run(trades, provider, "AAVEUSDT", "5m", my_rules, commission_pct=0.1)
    print(report.comparison(baseline, magnifier, "what TradingView would report"))
    print(report.comparison(baseline, realistic, "closest to reality"))
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

The engine walks each candle with TradingView's
[documented rule](https://www.tradingview.com/pine-script-docs/concepts/strategies/): the high
first when the open sits closer to the high, the low first otherwise. An earlier version ordered
by candle colour instead. It disagreed on about one candle in six and cost 3–4 points of match
rate — small enough to look fine, large enough to bias every estimate built on top.

**4. Only then, replay finer.** A high match at the chart timeframe and a very different result
at finer granularity is the expected outcome. That gap is what you came to measure.

**5. Calibrate if you can.** If you have one strategy exported both with and without Bar
Magnifier, `calibrate` sweeps granularities and reports which reproduces the real effect. On the
reference strategy — a 4h chart — the answer was **30 minutes**, exactly the intrabar timeframe
TradingView documents for that chart:

| Granularity | Error vs. real magnifier, AAVE + BAT + SOL |
|---|---|
| 1h | 1.150 |
| **30m** | **0.633** |
| 15m | 1.070 |

An earlier version of this README said 15 minutes. That came from calibrating on a single pair,
against an averaged reference, with the colour rule: on that one pair 15m happened to score best,
while the other two already preferred 30m. Calibrate on several pairs, each against its own
reference.

**6. If you want reality, keep going.** Matching the magnifier is not the same as matching what
happened — it still guesses inside every 30m bar. Replay finer and watch the estimate move:

| Pair | 4h | 30m | 15m | 5m |
|---|---|---|---|---|
| AAVE | 428% | 223% | 197% | 182% |
| ALICE | 336% | 206% | 161% | 139% |
| BAT | 346% | 212% | 191% | 173% |
| DOGS | 207% | 118% | 103% | 95% |

Each halving of the granularity moves it by a third to a half of the previous step: converging,
not converged. On a trailing-stop strategy finer is always more pessimistic, because every extra
level of detail exposes retracements that fire the trailing earlier. For decisions with real money,
report the finest replay you can afford and show the magnifier-equivalent beside it — that gap is
exactly what the Strategy Tester will never show you.

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

## The pool

Per-trade results say whether a strategy has an edge. They do not say what an account running
several of them would have done, because they compete for the same capital — a signal arriving
while the pool is committed is not a smaller position, it is *no* position.

```python
from bme.portfolio import Trade, simulate, sweep, boundary_fractions

trades = [Trade("aave", entry, exit, ret), ...]      # from several strategies

r = simulate(trades, fraction=0.30, leverage=2, admission="slots")
print(r.cagr, r.max_drawdown, r.mar, r.skip_rate)
```

`fraction` is the **margin** an order commits. Leverage multiplies the notional without asking
for more margin, so **it does not buy more positions — it scales the result of each one**. And
`reinvest=True` (the default) compounds: margin follows equity. Turn it off and every order stays
the size of the first, which is what a fixed-size backtest reports and is usually far lower — on
the reference strategy, 26% CAGR compounding against 18% flat.

### One position per symbol

Replayed trades take their entries from the export, stamped at chart-bar resolution, and their
exits from the finer replay. That makes some trades look like they overlap the next one on the
same symbol, and there are two very different reasons:

- **The next entry opens inside the bar where the previous trade exited.** A strategy that
  recalculates on fills re-enters right after an intrabar exit, and the export stamps that entry
  with the bar's open. It really happened.
- **The next entry opens in an earlier bar.** Live, the position would still be open and the signal
  would not have fired.

`sequence(trades, chart_seconds)` keeps the first kind (clamping the previous exit) and drops the
second; pass `one_per_key=True` to `simulate` as a guard. The obvious rule — drop every overlap —
threw away legitimate re-entries on the reference strategy and cut per-pair t-statistics by up to
a fifth. Every one of those exports had zero real overlaps.

### Pick a fraction that is not on a boundary

Two admission rules ship, and the gap between them is the point:

- **`slots`** — at most `floor(1/fraction)` positions. Scale-invariant, so the trade set is
  identical across leverage.
- **`margin`** — realistic. Margin is posted at entry and frozen at that amount; a new order needs
  free equity *now*.

They agree almost everywhere. Where they don't, the fraction is sitting on a **1/n boundary** —
n positions consume exactly the whole pool, and a rounding-scale move in equity decides whether
the next one fits. `boundary_fractions(sweep(...))` names them. On the reference strategy — three
pairs, replayed at 5m, sequenced as above:

```
  margen    max  perdido     CAGR   maxDD    MAR    CAGR 2x
     20%      3     0.0%    20.6%   10.2%   2.02      43.4%
     25%      3     0.0%    26.1%   12.6%   2.07      55.6%
     30%      3     0.0%    31.7%   14.9%   2.12      68.2%
     40%      2     2.8%    41.7%   16.0%   2.61      90.6%
     50%      2     2.8%    53.4%   19.7%   2.71     116.7%  <- borde
```

Note that 20% and 25% divide the pool exactly and still are not boundaries here: with three
symbols the fourth and fifth slots never fill, so there is nothing to disagree about. A boundary
only bites when the pool actually reaches it.

A boundary fraction is not wrong, it is *unstable*: its result depends on details that should not
matter. Move to a neighbour. See [`examples/pool.py`](examples/pool.py).

---

## Layout

| File | What it does |
|---|---|
| `bme/tradingview.py` | Read exports, detect timezone, verify symbol |
| `bme/candles.py` | Binance and Bybit providers, disk cache |
| `bme/rules.py` | Exit rules, and the base class for your own |
| `bme/engine.py` | The intrabar path walker |
| `bme/estimate.py` | Validation, estimation, calibration, funding |
| `bme/portfolio.py` | Shared capital pool: sizing, margin, leverage, skipped signals |
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
