"""Run several strategies against one shared pool, and find the safe sizings.

Per-trade results say whether a strategy has an edge. They do not say what an
account running four of them would have done, because they compete for the
same capital. This sweeps the margin fraction and reports what a pool would
actually have returned - including the signals it had to turn away.

Feed it a CSV per strategy: entry time, exit time, and the trade's result in
percent. That is what `bme.estimate` produces, and what most backtests export.

    entry,exit,ret
    2024-01-05 12:00,2024-01-05 20:00,2.41
    2024-01-07 04:00,2024-01-08 08:00,-5.10

    python examples/pool.py aave.csv alice.csv bat.csv
"""
import csv
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bme.portfolio import Trade, boundary_fractions, monthly, simulate, sweep

FRACTIONS = (0.20, 0.25, 0.30, 0.40, 0.50)
LEVERAGES = (1, 2)
STAMP = "%Y-%m-%d %H:%M"


def read(path):
    key = os.path.splitext(os.path.basename(path))[0]
    out = []
    for row in csv.DictReader(io.open(path, encoding="utf-8-sig")):
        out.append(Trade(key,
                         datetime.datetime.strptime(row["entry"].strip(), STAMP),
                         datetime.datetime.strptime(row["exit"].strip(), STAMP),
                         float(row["ret"])))
    if not out:
        raise ValueError("no trades in " + path)
    return out


def main(*paths):
    trades = [t for p in paths for t in read(p)]
    keys = sorted({t.key for t in trades})
    print("%d trades across %d strategies: %s" % (len(trades), len(keys), ", ".join(keys)))

    runs = sweep(trades, FRACTIONS, LEVERAGES)
    edges = boundary_fractions(runs)

    print("\n%-9s %6s %9s %8s %7s %6s   %9s %8s %7s %6s"
          % ("margen", "max", "perdido", "CAGR", "maxDD", "MAR", "CAGR 2x", "maxDD", "MAR", ""))
    for fraction in FRACTIONS:
        a = simulate(trades, fraction, 1, "slots")
        b = simulate(trades, fraction, 2, "slots")
        flag = "  <- borde" if fraction in edges else ""
        print("%8.0f%% %6d %8.1f%% %7.1f%% %6.1f%% %6.2f   %8.1f%% %7.1f%% %6.2f%s"
              % (100 * fraction, a.max_concurrent, a.skip_rate, a.cagr, a.max_drawdown,
                 a.mar, b.cagr, b.max_drawdown, b.mar, flag))

    if edges:
        print("\nFracciones de borde: " + ", ".join("%.0f%%" % (100 * f) for f in edges))
        print("Ahi n posiciones consumen el pool exacto y un movimiento minimo del")
        print("capital decide si entra la siguiente. Elegi una fraccion vecina.")
    else:
        print("\nNinguna fraccion cae sobre un borde.")

    safe = [f for f in FRACTIONS if f not in edges]
    if safe:
        best = max(safe, key=lambda f: simulate(trades, f, 1, "slots").mar)
        r = simulate(trades, best, 1, "slots")
        flat = simulate(trades, best, 1, "slots", reinvest=False)
        print("\nMejor MAR entre las fracciones seguras: %.0f%%" % (100 * best))
        print("  con reinversion   %5.2fx   CAGR %.1f%%" % (r.equity, r.cagr))
        print("  sin reinversion   %5.2fx   CAGR %.1f%%" % (flat.equity, flat.cagr))
        rows = monthly(r)
        positive = sum(1 for m in rows if m["ret"] > 0)
        print("  %d de %d meses positivos" % (positive, len(rows)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
