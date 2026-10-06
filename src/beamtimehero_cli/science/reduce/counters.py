"""Active-counter selection for a scan DataFrame.

Technique-agnostic: picks which detector channel carries the signal, and
warns when the auto-pick looks like a flat dark/background channel (the
vortDT-vs-vortDT2 trap). See ``beamtimehero ref counter-selection``.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Active-counter selection
# ---------------------------------------------------------------------------

_VORT_CANDIDATES = ("vortDT", "vortDT2", "vortDT3", "vortDT4")


def pick_active_counter(
    df: pd.DataFrame, priority: Sequence[str] = (),
) -> tuple[str, str]:
    """Pick the active fluorescence/absorption counter for a scan DataFrame.

    Returns ``(counter_name, reason)``. Decision logic:

    0. If ``priority`` is given, the first of those names present in the
       scan's columns wins. This is the station-level override hook; the
       caller supplies it (``config.active_counter_priority()`` reads it from
       ``BTH_ACTIVE_COUNTER_PRIORITY``) so that this function stays pure.
    1. If ``SCA_sum`` is a counter (SSRL EXAFS Data Collector frames — the
       parser-synthesized summed Xspress3 fluorescence), it is the active
       counter.
    2. Else if ``ppboff`` is a counter, it is the active counter.
    3. Else among ``vortDT, vortDT2, vortDT3, vortDT4``, the one with the
       highest max wins.
    4. Else if ``xsum`` is a counter (BL4-1's 32-element Ge detector, sum
       of the good elements), it is the active counter.
    5. Otherwise default to ``I1``.

    .. warning::

       This is a *convenience default for the XAS/HERFD/XES case only*. The
       "highest max" heuristic silently picks a flat, high-offset background
       channel over the true signal when they coexist — the exact ``vortDT``
       (dark) vs ``vortDT2`` (signal) failure that corrupted an XRS dataset.
       Any tool that averages/compares/scores repeated scans MUST accept an
       explicit ``counter`` and only fall back here when none is given.
       See ``beamtimehero ref counter-selection``.
    """
    cols = set(df.columns)

    for c in priority:
        if c in cols:
            return c, f"caller-supplied counter priority override ({c})"

    if "SCA_sum" in cols:
        return "SCA_sum", "SCA_sum present (summed Xspress3 fluorescence)"

    if "ppboff" in cols:
        return "ppboff", "ppboff counter present"

    available_vorts = [c for c in _VORT_CANDIDATES if c in cols]
    if available_vorts:
        best = max(available_vorts, key=lambda c: df[c].max())
        return best, f"highest max among {list(available_vorts)}"

    if "xsum" in cols:
        return "xsum", "xsum present (summed 32-element Ge fluorescence)"

    return "I1", "no ppboff or vortDT counters, defaulting to I1"


# ---------------------------------------------------------------------------
# Counter-selection guardrail (the vortDT-vs-vortDT2 trap)
# ---------------------------------------------------------------------------

# A channel whose fractional modulation (peak-to-peak / max) is below this is
# "flat" — the signature of a dark/background channel sitting at a large DC
# offset. See ``beamtimehero ref counter-selection``.
_FLAT_MODULATION_FRAC = 0.15


def _fractional_modulation(series) -> float:
    """(max - min) / max for one counter column. 0 for an all-zero channel."""
    v = np.asarray(series, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0
    hi = float(np.max(v))
    if hi <= 0:
        return 0.0
    return (hi - float(np.min(v))) / hi


def counter_selection_warning(df: pd.DataFrame, chosen: str) -> str | None:
    """Warn when an auto-picked counter looks like a flat dark/background channel.

    Returns a human-readable warning string, or None if the pick looks safe.
    The trap this catches: ``pick_active_counter`` chooses the ``vortDT*``
    channel with the highest max, but a flat dark channel at a large DC offset
    can out-max the real (small) signal channel. If the chosen counter is flat
    while a sibling ``vortDT*`` channel has much higher fractional modulation,
    the sibling is likely the real signal. See ``ref counter-selection``.
    """
    if chosen not in df.columns:
        return None
    chosen_mod = _fractional_modulation(df[chosen])
    if chosen_mod >= _FLAT_MODULATION_FRAC:
        return None
    siblings = [
        c for c in _VORT_CANDIDATES
        if c in df.columns and c != chosen
        and _fractional_modulation(df[c]) >= 2 * max(chosen_mod, 1e-6)
        and _fractional_modulation(df[c]) >= _FLAT_MODULATION_FRAC
    ]
    if not siblings:
        return None
    best_sib = max(siblings, key=lambda c: _fractional_modulation(df[c]))
    return (
        f"Auto-picked counter '{chosen}' is nearly flat "
        f"({chosen_mod * 100:.1f}% peak-to-peak modulation) — the signature of a "
        f"dark/background channel at a large DC offset. Channel '{best_sib}' "
        f"({_fractional_modulation(df[best_sib]) * 100:.1f}% modulation) is more "
        f"likely the real signal. Pass counter='{best_sib}' explicitly if this is "
        f"XRS or any non-edge technique. See `beamtimehero ref counter-selection`."
    )


# ---------------------------------------------------------------------------
# CITATIONS — method -> reference. ``None`` means the method is implemented
# but not yet attributed; those surface as gaps on the generated science
# index, and filling one in is a welcome contribution. See science/README.md.
# ---------------------------------------------------------------------------

CITATIONS = {
    "Active-counter selection by fractional modulation": None,
    "Flat-channel (dark detector) warning heuristic": (
        "Local BL15-2 convention, not a published method: see "
        "`beamtimehero ref counter-selection`."
    ),
}
