"""Distribution-free statistics for ci/perf_ab.py (#573 B3).

The paired difference of a cell is a handful of numbers (7 or 15 repetitions), so the interval on
its median must not assume a shape. `sign_interval` is the order-statistic interval that follows
from the sign test; the percentile bootstrap of the median it replaces is anticonservative at
these sizes. A base-vs-base ("null") arm gives each cell a noise floor, and `equivalent` states
what "no regression" means for a control cell: the whole interval inside a margin.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

CONFIDENCE = 0.95
# Owner decision needed (#573 B3): how small a change counts as "the same" for a control cell.
# 3% is the default until the owner sets it; a build or a test may override it by argument.
EQUIVALENCE_MARGIN_PCT = 3.0


def percent(base: float, head: float) -> float:
    """Head relative to base, in percent (a release time can be 0 ms)."""
    if base == 0:
        return 0.0 if head == 0 else 100.0
    return (head - base) / base * 100


def _cdf(n: int, k: int) -> float:
    """P(Binomial(n, 1/2) <= k)."""
    return sum(math.comb(n, j) for j in range(k + 1)) / 2**n


def sign_interval(
    values: Sequence[float], confidence: float = CONFIDENCE
) -> tuple[float, float, float]:
    """(median, low, high): the median and its order-statistic interval.

    With j the largest count such that P(Binomial(n, 1/2) <= j) <= (1 - confidence) / 2, the
    interval [d(j+1), d(n-j)] (1-based) of the sorted values covers the true median with probability at
    least `confidence`. Too few values for that (n < 6 at 95%) give [min, max], whose coverage is
    lower (1 - 2 / 2^n) and is the honest widest statement the data allows.
    """
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    n = len(ordered)
    limit = (1 - confidence) / 2
    j = -1  # the largest count with P(Binomial(n, 1/2) <= j) <= limit
    while j + 1 < n and _cdf(n, j + 1) <= limit:
        j += 1
    k = max(j, 0)  # 0-based order statistic: k-th smallest and k-th largest
    return statistics.median(ordered), ordered[k], ordered[n - 1 - k]


def paired_percent(base: Sequence[float], head: Sequence[float]) -> tuple[float, float, float]:
    """The median paired difference (head vs base, percent) and its sign-test interval."""
    return sign_interval([percent(b, h) for b, h in zip(base, head)])


def noise_floor(null_interval: tuple[float, float, float]) -> float:
    """The size of a difference base-vs-base produced by chance: the interval's larger end."""
    _, low, high = null_interval
    return max(abs(low), abs(high))


def beyond_noise(interval: tuple[float, float, float], noise: float | None) -> bool:
    """A difference is claimed only when its interval excludes 0 and, given a null arm, its
    median is larger than what base-vs-base produced."""
    mid, low, high = interval
    excludes_zero = low > 0 or high < 0
    return excludes_zero and (noise is None or abs(mid) > noise)


def equivalent(
    interval: tuple[float, float, float], margin: float = EQUIVALENCE_MARGIN_PCT
) -> bool:
    """The whole interval lies inside +-margin: no difference larger than the margin (TOST)."""
    _, low, high = interval
    return low >= -margin and high <= margin
