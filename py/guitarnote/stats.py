"""Does the crest factor follow the gain knob? Statistics with their working.

Spearman's rank correlation is used rather than Pearson's: distortion
saturates as a clipper approaches a square wave, so the relationship should be
monotonic but not a straight line. Spearman's rho asks only whether a higher
setting goes with a lower crest factor.

Both uncertainty estimates are resampling methods, chosen because they can be
explained in a sentence and need no statistical tables:

* p-value: shuffle which crest factor goes with which setting many times and
  count how often chance alone gives a correlation at least as strong.
* 95% interval: resample the notes with replacement many times, recompute rho
  each time, and keep the middle 95% of the results.
"""
from __future__ import annotations

import math
import random
import statistics

PERMUTATIONS = 10_000
BOOTSTRAPS = 2_000
PERMUTATION_SEED = 1
BOOTSTRAP_SEED = 2
MIN_NOTES = 8               #: below this the interval is too unstable to quote


def average_ranks(values: list[float]) -> list[float]:
    """Rank from 1; tied values share the average of the ranks they span."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def pearson(x: list[float], y: list[float]) -> float:
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = math.fsum((a - mx) ** 2 for a in x)
    syy = math.fsum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def spearman(x: list[float], y: list[float]) -> float:
    return pearson(average_ranks(x), average_ranks(y))


def permutation_p(x: list[float], y: list[float], rho: float,
                  n: int = PERMUTATIONS, seed: int = PERMUTATION_SEED) -> tuple[float, int]:
    """Two-sided p-value; returns ``(p, shuffles at least as extreme)``.

    Shuffling the crest factors only reorders their ranks, so the ranks are
    centred once and each shuffle costs a single dot product.
    """
    rx, ry = average_ranks(x), average_ranks(y)
    cx = [r - statistics.fmean(rx) for r in rx]
    cy = [r - statistics.fmean(ry) for r in ry]
    scale = math.sqrt(math.fsum(a * a for a in cx) * math.fsum(b * b for b in cy))
    rng = random.Random(seed)
    target = abs(rho) - 1e-12
    extreme = 0
    for _ in range(n):
        rng.shuffle(cy)
        if abs(math.fsum(a * b for a, b in zip(cx, cy)) / scale) >= target:
            extreme += 1
    return (extreme + 1) / (n + 1), extreme


def bootstrap_interval(x: list[float], y: list[float], n: int = BOOTSTRAPS,
                       seed: int = BOOTSTRAP_SEED) -> tuple[float, float, int]:
    """Percentile 95% interval for rho; returns ``(low, high, resamples used)``.

    A resample that happens to draw notes from a single setting has no
    correlation to compute and is skipped.
    """
    rng = random.Random(seed)
    pairs = list(zip(x, y))
    rhos = []
    for _ in range(n):
        sample = [rng.choice(pairs) for _ in pairs]
        value = spearman([p[0] for p in sample], [p[1] for p in sample])
        if not math.isnan(value):
            rhos.append(value)
    if len(rhos) < 20:
        return float("nan"), float("nan"), len(rhos)
    cuts = statistics.quantiles(rhos, n=40, method="inclusive")   # every 2.5%
    return cuts[0], cuts[-1], len(rhos)


def by_setting(gains: list[float], values: list[float]) -> list[dict]:
    """Count, mean, standard deviation and standard error at each setting."""
    groups: dict[float, list[float]] = {}
    for g, v in zip(gains, values):
        groups.setdefault(g, []).append(v)
    rows = []
    for g in sorted(groups):
        vals = groups[g]
        sd = statistics.stdev(vals) if len(vals) > 1 else float("nan")
        rows.append({"gain": g, "n": len(vals), "values": vals, "sum": math.fsum(vals),
                     "mean": statistics.fmean(vals), "sd": sd,
                     "se": sd / math.sqrt(len(vals)) if len(vals) > 1 else float("nan")})
    return rows


def _strength(rho: float) -> str:
    size = abs(rho)
    if size >= 0.8:
        return "strong"
    if size >= 0.5:
        return "moderate"
    if size >= 0.3:
        return "weak"
    return "little or no"


def analyse(gains: list[float], crest_db: list[float]) -> dict:
    """The full analysis of a set of notes, with working and a plain verdict."""
    n = len(gains)
    groups = by_setting(gains, crest_db)
    result = {"n": n, "settings": len(groups), "groups": groups, "rho": None,
              "p": None, "ci": None, "steps": [], "ranks": [], "warnings": [],
              "verdict": ""}
    if len(groups) < 2 or n < 3:
        result["verdict"] = ("Record notes at two or more gain settings (three or more notes "
                             "in all) to see whether the crest factor follows the knob.")
        return result

    rx, ry = average_ranks(gains), average_ranks(crest_db)
    mean_rank = statistics.fmean(rx)                 # (n + 1) / 2 for both
    sxy = math.fsum((a - mean_rank) * (b - mean_rank) for a, b in zip(rx, ry))
    sxx = math.fsum((a - mean_rank) ** 2 for a in rx)
    syy = math.fsum((b - mean_rank) ** 2 for b in ry)
    if syy == 0:
        result["verdict"] = "Every note has the same crest factor, so there is nothing to rank."
        return result
    rho = sxy / math.sqrt(sxx * syy)
    p, extreme = permutation_p(gains, crest_db, rho)
    low, high, used = bootstrap_interval(gains, crest_db)
    result.update(rho=rho, p=p, ci=[low, high],
                  ranks=[{"gain": g, "crest_db": c, "gain_rank": a, "crest_rank": b}
                         for g, c, a, b in zip(gains, crest_db, rx, ry)])

    result["steps"] = [
        {"title": "Rank the gain settings",
         "formula": "smallest = rank 1; tied values share the average of their ranks",
         "working": f"{n} notes at {len(groups)} settings",
         "result": "see the rank table"},
        {"title": "Rank the crest factors",
         "formula": "smallest = rank 1, the same way",
         "working": f"{n} crest factors",
         "result": "see the rank table"},
        {"title": "Correlate the ranks (Spearman's ρ)",
         "formula": "ρ = Σ(aᵢ − ā)(bᵢ − b̄) / "
                    "√[Σ(aᵢ − ā)² × Σ(bᵢ − b̄)²]"
                    ", a = gain rank, b = crest rank",
         "working": f"mean rank {mean_rank:.2f}; {sxy:,.2f} / √({sxx:,.2f} × {syy:,.2f})",
         "result": f"ρ = {rho:.3f}"},
        {"title": "Could chance alone do this? (permutation test)",
         "formula": f"shuffle the crest factors among the notes {PERMUTATIONS:,} times; "
                    "p = (shuffles with |ρ| at least as large + 1) / (shuffles + 1)",
         "working": f"({extreme:,} + 1) / ({PERMUTATIONS:,} + 1)",
         "result": f"p = {p:.4f}" if p >= 0.0001 else f"p < 0.0001 (none of {PERMUTATIONS:,} shuffles)"},
        {"title": "How precise is ρ? (bootstrap)",
         "formula": f"resample the notes with replacement {BOOTSTRAPS:,} times, recompute ρ, "
                    "keep the middle 95%",
         "working": f"2.5th and 97.5th percentiles of {used:,} resampled values",
         "result": (f"95% interval {low:.3f} to {high:.3f}" if not math.isnan(low)
                    else "too few usable resamples")},
    ]

    if n < MIN_NOTES:
        result["warnings"].append(f"Only {n} notes: treat the interval as rough until there are "
                                  f"at least {MIN_NOTES}.")
    lowest, highest = groups[0], groups[-1]
    direction = ("falls as the gain rises, which is what clipping does" if rho < 0 else
                 "rises as the gain rises, which is the opposite of what clipping does; "
                 "check the gain settings were entered correctly")
    certain = (p < 0.05 and not math.isnan(low) and (high < 0 or low > 0))
    result["verdict"] = (
        f"{_strength(rho).capitalize()} relationship: the crest factor {direction} "
        f"(ρ = {rho:.2f}"
        + (f", 95% interval {low:.2f} to {high:.2f}" if not math.isnan(low) else "")
        + (f", p < 0.001" if p < 0.001 else f", p = {p:.3f}") + "). "
        f"Mean crest factor goes from {lowest['mean']:.2f} dB at setting {lowest['gain']:g} "
        f"to {highest['mean']:.2f} dB at setting {highest['gain']:g}. "
        + ("This is unlikely to be chance." if certain else
           "This could still be chance; record more notes."))
    return result
