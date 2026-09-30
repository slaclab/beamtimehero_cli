"""Figures for CHERFD sweeps. Arrays/dicts in, matplotlib Figure out.

Handlers encode them with beamtimehero_cli's ``fig_to_base64`` and close
them. No file or network I/O here.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def _label(signal: str, normalize_by: str | None) -> str:
    return f"{signal} / {normalize_by}" if normalize_by else signal


def plot_sweeps(binned: dict, title: str = "", offset: float = 0.0):
    """Every sweep on the common grid, optionally stacked by ``offset``."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    e = binned["energy"]
    cmap = plt.get_cmap("viridis")
    n = max(1, len(binned["per_sweep"]))
    for i, s in enumerate(binned["per_sweep"]):
        ax.plot(s.get("energy", e), s["values"] + i * offset, lw=0.8, color=cmap(i / max(1, n - 1)),
                label=str(s["label"]))
    ax.plot(e, binned["merged"] + (n * offset if offset else 0), color="k", lw=1.4, label="merged")
    ax.set_xlabel("Energy (eV)")
    ax.set_ylabel(_label(binned["signal"], binned["normalize_by"]))
    ax.set_title(title or f"{n} sweeps, {binned['bin_ev']} eV bins")
    if n <= 16:
        ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig


def plot_merged(binned: dict, title: str = "", others: list[tuple[str, dict]] | None = None):
    """Merged spectrum with a 1-sigma band; ``others`` overlays e.g. fwd vs rev."""
    fig, (ax, axn) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    e, y, s = binned["energy"], binned["merged"], binned["sem"]
    ax.plot(e, y, color="k", lw=1.2, label=f"merged ({binned['n_sweeps']} sweeps)")
    ax.fill_between(e, y - s, y + s, color="k", alpha=0.2, lw=0)
    for label, b in others or []:
        ax.plot(b["energy"], b["merged"], lw=0.9, label=label)
    ax.set_ylabel(_label(binned["signal"], binned["normalize_by"]))
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    axn.bar(e, binned["frames_per_bin"], width=binned["bin_ev"], color="tab:gray")
    axn.set_ylabel("frames/bin")
    axn.set_xlabel("Energy (eV)")
    fig.tight_layout()
    return fig


def plot_diagnostics(arrays: dict, signal: str, title: str = "",
                     ideal_gap: np.ndarray | None = None):
    """Energy/time, scan rate, gap tracking and signal for one cleaned sweep."""
    t = arrays["trigger"]
    e = arrays["absev"]
    fig, axs = plt.subplots(2, 2, figsize=(11, 7))
    ax = axs[0, 0]
    ax.plot(t, e, lw=0.8)
    ax.set_xlabel("trigger #")
    ax.set_ylabel("Energy (eV)")
    ax.set_title("energy vs trigger")
    ax = axs[0, 1]
    if len(e) > 3:
        ax.plot(t[1:], np.abs(np.diff(e)), lw=0.6)
    ax.set_xlabel("trigger #")
    ax.set_ylabel("|dE| per frame (eV)")
    ax.set_title("sampling density (lower = denser)")
    ax = axs[1, 0]
    gap = arrays.get("gap")
    if gap is not None and np.isfinite(gap).any():
        m = np.isfinite(gap)
        ax.plot(e[m], gap[m], ".", ms=2, label="measured")
        if ideal_gap is not None:
            ax.plot(e, ideal_gap, lw=0.8, label="ideal (harmonic poly)")
        ax.legend(fontsize=8)
    else:
        ax.text(0.5, 0.5, "no gap readings", ha="center", va="center", transform=ax.transAxes)
    ax.set_xlabel("Energy (eV)")
    ax.set_ylabel("gap (mm)")
    ax.set_title("undulator tracking")
    ax = axs[1, 1]
    y = arrays.get(signal)
    if y is not None:
        ax.plot(e, y, ".", ms=1.5)
    ax.set_xlabel("Energy (eV)")
    ax.set_ylabel(signal)
    ax.set_title("raw signal per frame")
    for a in axs.flat:
        a.grid(alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    return fig


def plot_profile(sim: dict, title: str = ""):
    """Energy and scan rate vs time from a simulate_command result."""
    series = sim.get("series") or {}
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    en = np.asarray(series.get("energy") or [], float)
    if en.size:
        a1.plot(en[:, 0], en[:, 1])
    a1.set_ylabel("Energy (eV)")
    rate = np.asarray(series.get("energy_rate") or [], float)
    if rate.size:
        a2.plot(rate[:, 0], np.abs(rate[:, 1]))
    a2.set_ylabel("|dE/dt| (eV/s)")
    a2.set_xlabel("time (s)")
    for b in sim.get("boundaries") or []:     # region-boundary times (s)
        tb = b.get("time") if isinstance(b, dict) else b
        if isinstance(tb, (int, float)):
            for a in (a1, a2):
                a.axvline(tb, color="gray", lw=0.6, ls="--")
    for a in (a1, a2):
        a.grid(alpha=0.3)
    a1.set_title(title or sim.get("command", ""))
    fig.tight_layout()
    return fig


def plot_convergence(conv: dict, title: str = ""):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    n = [r["n_sweeps"] for r in conv["curve"] if r["relative_noise"]]
    y = [r["relative_noise"] for r in conv["curve"] if r["relative_noise"]]
    ax.loglog(n, y, "o-", label="measured")
    fit = conv.get("fit")
    if fit and n:
        nn = np.linspace(1, max(max(n), conv.get("sweeps_needed") or 0, 2), 50)
        ax.loglog(nn, fit["relative_noise_1"] * nn ** (-fit["alpha"] / 2), "--",
                  label=f"fit alpha={fit['alpha']}")
        ax.loglog(nn, y[0] * nn ** -0.5, ":", color="gray", label="ideal 1/sqrt(N)")
    if conv.get("target_relative_noise"):
        ax.axhline(conv["target_relative_noise"], color="r", lw=0.8, label="target")
    ax.set_xlabel("sweeps merged")
    ax.set_ylabel("relative noise")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    return fig

# CITATIONS = {} deliberately: these render results computed in science/cherfd.
CITATIONS = {}
