"""Per-scan monitor normalization — the stage before technique-specific work.

``edge_step_normalize`` is the naive pre-edge/post-edge anchoring used by the
generic scan tools; ``normalize_series`` selects between it, plain I0
division, and raw counts so off-edge techniques (XRS) are not forced through
an edge jump they do not have.

For the rigorous XAS normalizations (area, MBACK, Athena-style pre/post) see
:mod:`beamtimehero_cli.science.xas.normalize`.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Edge-step normalization
# ---------------------------------------------------------------------------

#: A derived transmission signal, e.g. ``ln(I0/I1)`` (sample) or
#: ``ln(I1/I2)`` (reference foil between I1 and I2).
_LOG_RATIO = re.compile(r"^\s*ln\(\s*(\w+)\s*/\s*(\w+)\s*\)\s*$")


def with_derived_counter(df: pd.DataFrame, counter: str) -> tuple[pd.DataFrame, bool]:
    """Return ``(df, derived)``: *df* with a ``ln(A/B)`` *counter* column added.

    ``derived`` is True when *counter* is such an expression; the result is
    already an absorbance, so callers must not divide it by I0 again.
    Raises KeyError when A or B is not a column.
    """
    m = _LOG_RATIO.match(counter or "")
    if not m or counter in df.columns:
        return df, False
    num, den = m.groups()
    for c in (num, den):
        if c not in df.columns:
            raise KeyError(f"Counter '{c}' (in '{counter}') not found. Available: {list(df.columns)}")
    a = df[num].values.astype(float)
    b = df[den].values.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        mu = np.log(a / b)
    df = df.copy()
    df[counter] = np.where(np.isfinite(mu), mu, np.nan)
    return df, True


def edge_step_normalize(
    df: pd.DataFrame, counter: str, normalize_by: str | None = "I0",
) -> tuple[np.ndarray, np.ndarray]:
    """Apply edge-step normalization to a single counter on one scan.

    Pre-edge anchor = mean of first 10% of points.
    Post-edge anchor = mean of last 10% of points.
    Returns ``(energy, normalized_signal)`` numpy arrays.

    Raises:
        KeyError if ``counter`` or ``normalize_by`` is missing from the
        DataFrame.
    """
    if counter not in df.columns:
        raise KeyError(
            f"Counter '{counter}' not found. Available: {list(df.columns)}"
        )
    if normalize_by and normalize_by not in df.columns:
        raise KeyError(
            f"Normalization counter '{normalize_by}' not found. "
            f"Available: {list(df.columns)}"
        )

    energy = df.index.values.astype(float)
    signal = df[counter].values.astype(float)

    if normalize_by:
        i0 = df[normalize_by].values.astype(float)
        i0_safe = np.where(i0 == 0, 1.0, i0)
        signal = signal / i0_safe

    n = len(signal)
    n10 = max(1, n // 10)
    pre_mean = np.mean(signal[:n10])
    post_mean = np.mean(signal[-n10:])
    denom = post_mean - pre_mean
    if abs(denom) < 1e-15:
        normalized = signal - pre_mean
    else:
        normalized = (signal - pre_mean) / denom

    return energy, normalized


# Normalization modes selectable by the multi-scan tools. ``edge_step`` is the
# XAS default; the others exist so off-edge techniques (XRS) are not forced
# through an edge-jump they don't have. See ``ref counter-selection``.
NORMALIZATION_MODES = ("edge_step", "divide_by_i0", "raw")


def normalize_series(
    df: pd.DataFrame, counter: str, normalize_by: str | None = "I0",
    mode: str = "edge_step",
) -> tuple[np.ndarray, np.ndarray]:
    """Normalize one scan's counter under a selectable mode.

    - ``edge_step`` — pre-edge→0, post-edge→1 (absorption-edge normalization).
      **Wrong for XRS**: there is no edge step to anchor to.
    - ``divide_by_i0`` — signal / I0 only. The right choice for XRS/non-edge
      data when a technique-specific normalization (area, Compton-subtracted)
      hasn't been applied yet — it analyzes the true signal without imposing an
      edge shape.
    - ``raw`` — the counter as recorded, no I0 division.

    Returns ``(energy, values)``. Raises KeyError for a missing counter/monitor,
    ValueError for an unknown mode.
    """
    df, derived = with_derived_counter(df, counter)
    if derived:
        normalize_by = None
    if mode not in NORMALIZATION_MODES:
        raise ValueError(
            f"Unknown normalization mode '{mode}'. "
            f"Use one of {list(NORMALIZATION_MODES)}."
        )
    if counter not in df.columns:
        raise KeyError(
            f"Counter '{counter}' not found. Available: {list(df.columns)}"
        )
    if mode == "edge_step":
        return edge_step_normalize(df, counter, normalize_by=normalize_by)

    energy = df.index.values.astype(float)
    signal = df[counter].values.astype(float)
    if mode == "raw":
        return energy, signal
    # divide_by_i0
    if normalize_by:
        if normalize_by not in df.columns:
            raise KeyError(
                f"Normalization counter '{normalize_by}' not found. "
                f"Available: {list(df.columns)}"
            )
        i0 = df[normalize_by].values.astype(float)
        i0_safe = np.where(i0 == 0, 1.0, i0)
        signal = signal / i0_safe
    return energy, signal


# ---------------------------------------------------------------------------
# CITATIONS — method -> reference. ``None`` means the method is implemented
# but not yet attributed; those surface as gaps on the generated science
# index, and filling one in is a welcome contribution. See science/README.md.
# ---------------------------------------------------------------------------

CITATIONS = {
    # Deliberately naive — the rigorous normalizations, with their references,
    # are in science/xas/normalize.py.
    "Naive per-scan edge-step normalization": None,
    "Monitor (I0) division": None,
}
