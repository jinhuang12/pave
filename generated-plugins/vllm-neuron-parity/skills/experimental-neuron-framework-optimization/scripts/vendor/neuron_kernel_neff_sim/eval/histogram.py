"""Render a horizontal text histogram of per-test sim-vs-real error percentages.

Used by the eval CLI to produce ``histogram.md`` alongside ``results.csv``.
"""

from typing import Iterable


def histogram(values: Iterable[float], bins: int, low: float, high: float, width: int = 50) -> str:
    """Render a horizontal text histogram of the given values.

    Args:
        values: per-test error percentages (signed).
        bins: number of bins between ``low`` and ``high``.
        low: lower edge of the histogram range (in %).
        high: upper edge of the histogram range (in %).
        width: maximum bar width in characters.
    """
    edges = [low + i * (high - low) / bins for i in range(bins + 1)]
    counts = [0] * bins
    for v in values:
        v_c = max(low, min(high - 0.001, v))
        idx = min(int((v_c - low) / (high - low) * bins), bins - 1)
        counts[idx] += 1
    max_count = max(counts) if counts else 1
    lines = []
    for i, c in enumerate(counts):
        bar = "#" * int(width * c / max_count) if max_count else ""
        lines.append(f"  [{edges[i]:+5.0f}%, {edges[i+1]:+5.0f}%) {c:3d}  {bar}")
    return "\n".join(lines)


def summary_lines(errs: list[float], thresholds: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0)) -> list[str]:
    """Compute mean / median / abs / threshold-counts for a list of error percentages."""
    import statistics as st

    if not errs:
        return ["(no results)"]
    abs_errs = sorted(abs(e) for e in errs)
    n = len(errs)
    out = [
        f"Total: {n}",
        f"Mean error:     {st.mean(errs):+.2f}%",
        f"Median error:   {st.median(errs):+.2f}%",
        f"Mean abs error: {st.mean(abs_errs):.2f}%",
        f"P50 / P90 / P95 abs: "
        f"{abs_errs[n // 2]:.1f}% / "
        f"{abs_errs[int(n * 0.9)]:.1f}% / "
        f"{abs_errs[int(n * 0.95)]:.1f}%",
    ]
    for t in thresholds:
        within = sum(1 for e in abs_errs if e <= t)
        out.append(f"Within {int(t):2d}%: {within}/{n} ({within/n*100:.1f}%)")
    return out
