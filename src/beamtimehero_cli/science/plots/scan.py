"""Backend-agnostic figure rendering.

Functions take pandas DataFrames + identifying metadata and return
``(fig, summary_text)``. They never touch disk, the DB, or the SPEC
session — backends are responsible for loading data and then handing
it to a renderer.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def fig_to_base64(fig) -> str:
    """Convert a matplotlib figure to a base64-encoded PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def render_scan(
    df: pd.DataFrame,
    file_name: str,
    scan_number: int,
    counter: Optional[str] = None,
    normalize_by: Optional[str] = None,
    scan_command: Optional[str] = None,
):
    """Render one scan's DataFrame to a matplotlib Figure.

    If ``counter`` is omitted, every column is plotted (useful for
    a quick raw view). ``normalize_by``, if set, divides ``counter``
    pointwise. ``scan_command`` is shown in the title when given.

    Returns ``(fig, summary)``. On error the figure is closed and
    returns ``(None, error_message)``.
    """
    fig, ax = plt.subplots(figsize=(8, 5))
    x_label = df.index.name or "index"

    if counter:
        if counter not in df.columns:
            plt.close(fig)
            return None, (
                f"Counter '{counter}' not found. Available: {list(df.columns)}"
            )
        y = df[counter]
        if normalize_by:
            if normalize_by not in df.columns:
                plt.close(fig)
                return None, f"Normalization counter '{normalize_by}' not found."
            y = y / df[normalize_by]
            y_label = f"{counter}/{normalize_by}"
        else:
            y_label = counter
        ax.plot(df.index, y)
        ax.set_ylabel(y_label)
    else:
        for col in df.columns:
            ax.plot(df.index, df[col], label=col)
        ax.legend(fontsize=8)
        y_label = "counts"

    ax.set_xlabel(x_label)
    title = f"{file_name} scan #{scan_number}"
    if scan_command:
        title += f"\n{scan_command}"
    ax.set_title(title, fontsize=10)
    fig.tight_layout()

    parts = [
        f"Plot of {file_name} scan #{scan_number}",
        f"X axis: {x_label} ({len(df)} points)",
    ]
    if counter:
        parts.append(f"Y axis: {y_label}")
        parts.append(f"Range: {float(y.min()):.4g} to {float(y.max()):.4g}")
    else:
        parts.append(f"Counters plotted: {list(df.columns)}")
    if scan_command:
        parts.append(f"Command: {scan_command}")

    summary = ". ".join(parts) + "."
    return fig, summary


def plot_statistics_trend(stats, sample_name="", figsize=(10, 5.4), title=None):
    """Render the merge-convergence trend: one panel, one decision.

    Plots the fractional standard error of the merge of the first n reps
    against the *absolute* counting-statistics floor for the counts that were
    actually recorded, plus the science threshold. Three marks make the
    stopping decision readable at a glance: where the target was met, where
    the curve left the floor, and whether the feature is drifting.

    .. note::

       The reference curve is the floor from the recorded counts
       (``cumulative_floor_pct``), not a 1/sqrt(n) line anchored on a point of
       the data. An earlier version anchored on the rep-1 value, which
       ``analyze_scan_efficiency`` filled with the standard deviation of the
       *whole* stack — so the guide inherited a baseline inflated by every
       later systematic, and any series with a mid-run step appeared to beat
       counting statistics for its first few reps. Nothing does. When no
       counts are available the fallback is a least-squares 1/sqrt(n) fit to
       all the points, labelled as a fit rather than a floor, so it cannot be
       read as a limit either.

    Parameters
    ----------
    stats : dict
        convergence_stats dict stored per-sample in the plan JSON. Uses
        ``cumulative_sem_pct`` (or the ``cumulative_cv_pct`` alias),
        ``cumulative_floor_pct``, ``sem_threshold_pct``/``sem_threshold_frac``,
        ``target_reached_at_rep``, ``plateau_from_rep``, ``limited_by``,
        ``reps_to_target``, ``feature_window_eV``, and — when the feature
        analysis ran — ``is_drifting`` / ``sem_is_rising`` for the drift
        banner.
    sample_name : str
        Sample name for the plot title.
    title : str, optional
        Replaces the whole generated title. The default title names the
        sample, the window and the verdict, which is what the dashboard wants
        beside the other per-sample panels; a caller placing this figure
        somewhere that already supplies that context passes its own.
    figsize : tuple, default (10, 5.4)
        Figure size in inches. Every font size on the panel is a multiple of
        ``rcParams["font.size"]``, so a caller rendering for print sets that
        and the figure size together and the whole panel scales — rather than
        shrinking a dashboard-sized figure and getting unreadable axes.

    Returns
    -------
    (fig, summary_text) or (None, error_text)
    """
    sem_pct = stats.get("cumulative_sem_pct") or stats.get("cumulative_cv_pct")
    if not sem_pct:
        return None, "convergence_stats missing cumulative_sem_pct (or cumulative_cv_pct)"

    sem = np.array([np.nan if v is None else float(v) for v in sem_pct], dtype=float)
    n = sem.size
    reps = np.arange(1, n + 1)
    valid = np.isfinite(sem) & (sem > 0)
    if valid.sum() < 2:
        return None, f"only {int(valid.sum())} usable points on the SEM curve; need 2"

    if stats.get("sem_threshold_pct") is not None:
        threshold = float(stats["sem_threshold_pct"])
    else:
        threshold = float(stats.get("sem_threshold_frac", 0.01)) * 100

    floor = stats.get("cumulative_floor_pct")
    floor_arr = None
    if floor and len(floor) == n:
        floor_arr = np.array([np.nan if v is None else float(v) for v in floor], dtype=float)

    base = float(plt.rcParams.get("font.size", 10.0))
    fig, ax = plt.subplots(figsize=figsize)

    # --- the reference: absolute floor if we have counts, else an honest fit
    if floor_arr is not None and np.isfinite(floor_arr).any():
        ax.plot(reps, floor_arr, "--", color="0.42", lw=1.7, zorder=2,
                label=r"Poisson floor $\propto 1/\sqrt{n}$")
        has_floor = True
    else:
        # Least-squares a/sqrt(n) through every point, so no single rep sets it.
        a = float(np.sum(sem[valid] / np.sqrt(reps[valid])) / np.sum(1.0 / reps[valid]))
        ax.plot(reps, a / np.sqrt(reps), "--", color="0.42", lw=1.7, zorder=2,
                label=r"$1/\sqrt{n}$ fit — not a floor (no counts)")
        has_floor = False

    ax.axhline(threshold, color="#E8830C", lw=1.8, zorder=3,
               label=f"{threshold:.2g}% target")
    ax.plot(reps[valid], sem[valid], "o-", color="#0072B5", lw=2.0, ms=6, zorder=5,
            label="Standard error")

    # --- the decision marks
    target_rep = stats.get("target_reached_at_rep")
    plateau_rep = stats.get("plateau_from_rep")
    if target_rep and 1 <= int(target_rep) <= n:
        t = int(target_rep)
        ax.plot([t], [sem[t - 1]], "o", ms=15, mfc="none", mec="#1a7f37", mew=2.4, zorder=6)
        ax.annotate(f"target met\nrep {t} — stop", xy=(t, sem[t - 1]),
                    xytext=(10, 26), textcoords="offset points", fontsize=0.9 * base,
                    color="#1a7f37", fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color="#1a7f37", lw=1.2))
    if plateau_rep and 1 <= int(plateau_rep) <= n:
        pr = int(plateau_rep)
        ax.axvspan(pr, n, color="#8c1515", alpha=0.07, lw=0, zorder=0)
        ax.axvline(pr, color="#8c1515", ls=":", lw=1.8, zorder=4)
        ax.annotate(f"left the floor at rep {pr}\nsystematics — more reps\nwill not help",
                    xy=(pr, ax.get_ylim()[1]), xytext=(6, -34),
                    textcoords="offset points", fontsize=0.9 * base, color="#8c1515",
                    fontweight="bold", va="top")

    drifting = stats.get("is_drifting") or stats.get("sem_is_rising")
    if drifting:
        ax.text(0.015, 0.04,
                "feature is drifting — averaging a moving target",
                transform=ax.transAxes, fontsize=0.9 * base, color="#8c1515",
                fontweight="bold",
                bbox=dict(fc="#fdeaea", ec="#8c1515", lw=0.9, pad=4.5))

    ax.set_xlabel("Scan reps merged")
    ax.set_ylabel("Standard error (% of signal)")
    ax.set_xlim(0.55, n + 0.45)
    # Start the y axis just under the lowest thing on the panel rather than at
    # zero: the gap between the curve and the floor is the whole message, and
    # anchoring at zero spends half the panel on a region no experiment can
    # enter.
    lo_candidates = [np.nanmin(sem[valid]), threshold]
    if has_floor:
        lo_candidates.append(np.nanmin(floor_arr[np.isfinite(floor_arr)]))
    y_lo = max(0.0, min(lo_candidates) * 0.78)
    y_hi = np.nanmax(sem[valid]) + 0.12 * (np.nanmax(sem[valid]) - y_lo)
    ax.set_ylim(y_lo, y_hi)
    ax.set_xticks(reps if n <= 20 else reps[::2])
    ax.grid(alpha=0.22)
    # Lower left: the curve falls left-to-right, so it is the one corner the
    # data and the annotations both stay out of.
    ax.legend(fontsize=0.80 * base, loc="lower left", framealpha=0.94,
              borderpad=0.55, handlelength=1.9)


    e_min, e_max = (stats.get("feature_window_eV") or [None, None])[:2]
    window_str = f"{e_min:g}–{e_max:g} eV" if e_min is not None else ""
    if title is not None:
        ax.set_title(title, fontsize=0.9 * base, color="#1a1a1a", pad=8)
        fig.tight_layout()
        return fig, _trend_summary(stats, sem, valid, floor_arr, threshold, sample_name)
    bits = []
    if stats.get("efficiency_verdict"):
        bits.append(f"verdict: {stats['efficiency_verdict']}")
    if stats.get("limited_by"):
        bits.append(f"limited by {stats['limited_by'].replace('_', ' ')}")
    title = sample_name
    if window_str:
        title = f"{title}  ({window_str})" if sample_name else window_str
    # Second line rather than a longer first one: the panel is often placed at
    # a fixed width, and a single long title silently clips at both ends.
    ax.set_title(title + ("\n" + ", ".join(bits) if bits else ""),
                 fontsize=0.9 * base, color="#1a1a1a", linespacing=1.35, pad=8)

    fig.tight_layout()

    return fig, _trend_summary(stats, sem, valid, floor_arr, threshold, sample_name)


def _trend_summary(stats, sem, valid, floor_arr, threshold, sample_name=""):
    """The text an agent reads instead of looking at the panel.

    Shared by both title paths, because a caller overriding the title is
    changing what the figure is captioned, not what it found.
    """
    final = float(sem[valid][-1])
    bits = [
        f"Merge convergence for {sample_name}: {int(valid.sum())} usable rep counts, "
        f"final standard error {final:.3f}% of signal (target {threshold:.2g}%)"
    ]
    if floor_arr is not None and np.isfinite(floor_arr[-1]) and floor_arr[-1] > 0:
        bits.append(f"{final / float(floor_arr[-1]):.2f}x the counting-statistics floor")
    target_rep = stats.get("target_reached_at_rep")
    plateau_rep = stats.get("plateau_from_rep")
    if target_rep:
        bits.append(f"target met at rep {int(target_rep)}")
    else:
        rtt = stats.get("reps_to_target")
        bits.append("target not met" + (f", ~{int(rtt)} reps needed" if rtt else ""))
    if plateau_rep:
        bits.append(f"left the floor at rep {int(plateau_rep)}")
    if stats.get("is_drifting") or stats.get("sem_is_rising"):
        bits.append("feature is drifting")
    return ". ".join(bits) + "."


# ---------------------------------------------------------------------------
# CITATIONS — method -> reference. ``None`` means the method is implemented
# but not yet attributed; those surface as gaps on the generated science
# index, and filling one in is a welcome contribution. See science/README.md.
# ---------------------------------------------------------------------------

# CITATIONS = {} deliberately: a generic scan render and a statistics trend
# carry no scientific method of their own. The statistics they display are
# attributed in ``science/statistics/``.
CITATIONS = {}
