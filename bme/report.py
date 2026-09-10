"""Console tables. Nothing here is required to use the library."""


def validation(result, ok, label=""):
    lines = ["", "VALIDATION %s @ %s" % (label, result.timeframe),
             "  signal match   %.1f%%   %s" % (100 * result.signal_match(),
                                               "OK" if ok else "TOO LOW - fix the rules"),
             "  trades         %d (%d never closed)" % (len(result.pairs), result.unfilled),
             "  total return   %+.1f%%" % result.total]
    worst = [(v, k) for k, v in result.confusion().items() if k[0] != k[1]]
    if worst:
        lines.append("  mismatches:")
        for count, (expected, got) in sorted(worst, reverse=True)[:6]:
            lines.append("    %-10s -> %-10s %4d" % (expected, got, count))
    return "\n".join(lines)


def comparison(baseline, fine, label=""):
    drop = (100 * (fine.total / baseline.total - 1)) if baseline.total else float("nan")
    return "\n".join([
        "", "ESTIMATE %s" % label,
        "  %-16s %-12s %-12s" % ("", baseline.timeframe, fine.timeframe),
        "  %-16s %+11.1f%% %+11.1f%%" % ("total return", baseline.total, fine.total),
        "  %-16s %11.3f %11.3f" % ("per trade", baseline.per_trade, fine.per_trade),
        "  %-16s %11.2f %11.2f" % ("t-stat", baseline.t_stat, fine.t_stat),
        "  %-16s %10.1f%% %10.1f%%" % ("win rate", baseline.win_rate, fine.win_rate),
        "  %-16s %10.1f%% %10.1f%%" % ("max drawdown", baseline.max_drawdown, fine.max_drawdown),
        "  %-16s %10.1f%% %10.1f%%" % ("CAGR", baseline.cagr(), fine.cagr()),
        "  estimated change: %+.1f%%" % drop,
    ])


def degradation_table(coefficients):
    lines = ["", "PER-TRADE COST OF FINER RESOLUTION",
             "  %-12s %6s %12s" % ("exit", "n", "points")]
    for name, (n, mean) in sorted(coefficients.items(), key=lambda kv: kv[1][1]):
        lines.append("  %-12s %6d %+12.3f" % (name, n, mean))
    return "\n".join(lines)


def calibration_table(rows, reference):
    lines = ["", "CALIBRATION AGAINST A KNOWN BAR MAGNIFIER RUN",
             "  reference: " + ", ".join("%s %+.3f" % kv for kv in sorted(reference.items())),
             "  %-8s %10s %10s   %s" % ("bars", "error", "change", "coefficients")]
    for i, (tf, err, coef, drop) in enumerate(rows):
        mark = "  <- best" if i == 0 else ""
        detail = " ".join("%s %+.3f" % (k, v) for k, v in sorted(coef.items()))
        lines.append("  %-8s %10.3f %9.1f%%   %s%s" % (tf, err, drop, detail, mark))
    return "\n".join(lines)
