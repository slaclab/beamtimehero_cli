"""Figures for the tools, as base64 PNGs (bth's image contract).

Reuses tender_analysis.plotting wherever it already draws the thing (image
over spectrum, ADU histogram, RIXS map, overlays, averages) so a figure in
chat looks like the same figure in the notebooks and the story. Only the
panels the library has no builder for are drawn here, in its style.
"""

from __future__ import annotations

import numpy as np


def b64(fig) -> str:
    from beamtimehero_cli.science.plots.scan import fig_to_base64
    return fig_to_base64(fig)


def _lib():
    import matplotlib
    matplotlib.use("Agg", force=False)
    from tender_analysis import plotting
    return plotting


def image_with_spectrum(prev: dict, central_pix=None, n=7, image="events", title=None):
    return b64(_lib().image_with_spectrum(prev, central_pix=central_pix, n=n, image=image,
                                          title=title))


def adu_histograms(hist: dict, thresholds) -> str:
    """Three panels, each distribution beside the threshold that gates it:
    3x3-binned per-pixel (low), background-free per-pixel (hi), grains (xray)."""
    pl = _lib()
    fig = pl._figure((9, 7))
    axes = fig.subplots(3, 1)
    spec = (("binned", "low", thresholds.low, "3x3-binned pixel ADU (gated by low)"),
            ("bkg_free", "hi", thresholds.hi, "background-free pixel ADU (cosmics above hi)"),
            ("xray", "xray", thresholds.xray, "grain (event) ADU (kept above xray)"))
    for ax, (key, name, v, label) in zip(axes, spec):
        h = np.asarray(hist[key], float)
        nz = np.flatnonzero(h)
        top = int(nz[-1]) + 1 if nz.size else 10
        xmax = max(int(v * 1.3), min(top, int(3 * thresholds.xray) + 200))
        ax.step(np.arange(h.size), np.where(h > 0, h, np.nan), where="mid",
                color=pl.SERIES[0], linewidth=1.1)
        ax.axvline(v, color=pl.INK_2, linestyle="--", linewidth=1)
        ax.annotate(f"{name} {v:g}", xy=(v, 1), xycoords=("data", "axes fraction"),
                    xytext=(3, -3), textcoords="offset points", va="top",
                    color=pl.INK_2, fontsize=8)
        ax.set_yscale("log")
        ax.set_xlim(0, xmax)
        ax.set_title(label, color=pl.INK, fontsize=9, loc="left")
        pl._style(ax)
    axes[-1].set_xlabel("ADU", color=pl.INK)
    return b64(fig)


def rixs_map_with_profile(res, profile, centre, n, peaks) -> str:
    """The RIXS map with the band, and the emission profile the automatic
    centre was fitted to, with the detected peaks marked."""
    pl = _lib()
    m, E = np.asarray(res.rixs_map), np.asarray(res.E)
    fig = pl._figure((8, 8))
    ax_map, ax = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    # Cells between energy midpoints, as plotting.rixs_map draws them:
    # incident-energy steps are dense across the edge and sparse above it.
    if E.size > 1:
        mid = (E[1:] + E[:-1]) / 2
        e_edges = np.concatenate([[E[0] - (mid[0] - E[0])], mid, [E[-1] + (E[-1] - mid[-1])]])
    else:
        e_edges = np.array([E[0] - 0.5, E[0] + 0.5])
    im = ax_map.pcolormesh(np.arange(m.shape[0] + 1) - 0.5, e_edges, m.T, cmap=pl.MAP_CMAP,
                           shading="flat", rasterized=True)
    fig.colorbar(im, ax=ax_map).set_label("counts / I0" if res.meta.get("i0_corrected")
                                          else "counts", color=pl.INK)
    lo, hi = pl._band(int(centre), int(n))
    ax_map.axvspan(lo, hi, color=pl.ROI, alpha=0.25, linewidth=0)
    ax_map.set_ylabel("incident energy (eV)", color=pl.INK)
    ax_map.set_title(f"RIXS map, HERFD band {int(centre)} ± {n // 2}", color=pl.INK,
                     fontsize=10, loc="left")
    ax_map.tick_params(colors=pl.INK_2, labelcolor=pl.INK_2)
    x = np.arange(len(profile))
    ax.plot(x, profile, color=pl.SERIES[0], linewidth=1.2)
    ax.axvspan(lo, hi, color=pl.ROI, alpha=0.25, linewidth=0)
    for p in peaks:
        ax.axvline(p["pixel"], color=pl.SERIES[1], linewidth=0.8, linestyle=":")
    ax.set_title("emission profile, last 10 points in file order (what the auto-centre "
                 "fits)", color=pl.INK, fontsize=9, loc="left")
    ax.set_xlabel("dispersive pixel", color=pl.INK)
    pl._style(ax)
    return b64(fig)


def herfd_panels(curves: list[dict], norm_rows: list[dict] | None = None) -> str:
    """HERFD (top), TFY (middle) and the normalised HERFD (bottom), one colour
    per ROI choice."""
    pl = _lib()
    fig = pl._figure((8, 9))
    axes = fig.subplots(3, 1, sharex=True)
    for i, c in enumerate(curves):
        col = pl._series_color(i)
        axes[0].plot(c["E"], c["HERFD"], color=col, linewidth=1.6, label=c["label"])
        if i == 0:
            axes[1].plot(c["E"], c["TFY"], color=pl.INK_2, linewidth=1.4, label="TFY")
        if c.get("norm") is not None:
            axes[2].plot(c["E"], c["norm"], color=col, linewidth=1.6, label=c["label"])
    axes[0].set_ylabel("HERFD", color=pl.INK)
    axes[1].set_ylabel("TFY", color=pl.INK)
    axes[2].set_ylabel("normalised", color=pl.INK)
    axes[2].axhline(1.0, color=pl.INK_2, linewidth=0.6, linestyle=":")
    axes[2].axhline(0.0, color=pl.INK_2, linewidth=0.6, linestyle=":")
    for ax in axes:
        pl._style(ax)
    axes[0].legend(frameon=False, labelcolor=pl.INK, fontsize=8)
    axes[-1].set_xlabel("incident energy (eV)", color=pl.INK)
    return b64(fig)


def xes_panels(x, spectrum, per_file_centroids, xlabel, marks=None, unit="px") -> str:
    pl = _lib()
    fig = pl._figure((8, 6))
    ax, ax2 = fig.subplots(2, 1, gridspec_kw={"height_ratios": [3, 1.2]})
    ax.plot(x, spectrum, color=pl.SERIES[0], linewidth=1.3)
    for label, v in (marks or {}).items():
        ax.axvline(v, color=pl.INK_2, linestyle=":", linewidth=1)
        ax.annotate(label, xy=(v, 1), xycoords=("data", "axes fraction"), xytext=(3, -3),
                    textcoords="offset points", va="top", color=pl.INK_2, fontsize=8)
    ax.set_xlabel(xlabel, color=pl.INK)
    ax.set_ylabel("counts", color=pl.INK)
    pl._style(ax)
    if per_file_centroids:
        ax2.plot(range(1, len(per_file_centroids) + 1), per_file_centroids, "o-",
                 color=pl.SERIES[1], linewidth=1.2)
        ax2.set_xlabel("scan (file) in order", color=pl.INK)
        ax2.set_ylabel(f"centroid shift ({unit})", color=pl.INK)
        ax2.set_xticks(range(1, len(per_file_centroids) + 1))
        pl._style(ax2)
    else:
        ax2.set_visible(False)
    return b64(fig)


def dark_profiles(dark_prof, data_prof, dark_name, data_name, verdict) -> str:
    """Column profiles above the pedestal: the dark against a data image."""
    pl = _lib()
    fig = pl._figure((8, 4.5))
    ax = fig.subplots()
    x = np.arange(len(dark_prof))
    ax.plot(x, data_prof, color=pl.SERIES[0], linewidth=1.2, label=f"data: {data_name}")
    ax.plot(x, dark_prof, color=pl.SERIES[1], linewidth=1.2, label=f"dark: {dark_name}")
    ax.axhline(0, color=pl.INK_2, linewidth=0.6, linestyle=":")
    ax.set_xlabel("dispersive pixel", color=pl.INK)
    ax.set_ylabel("ADU / pixel above pedestal\n(column mean, 9-px smoothed)", color=pl.INK)
    ax.set_title(f"is the dark dark?  verdict: {verdict}", color=pl.INK, fontsize=10,
                 loc="left")
    pl._style(ax)
    ax.legend(frameon=False, labelcolor=pl.INK, fontsize=8)
    return b64(fig)


def overlay(curves: list[dict], ylabel: str, band=None) -> str:
    pl = _lib()
    fig = pl._figure((8, 4.5))
    ax = fig.subplots()
    if band is not None:
        ax.fill_between(band["E"], band["lo"], band["hi"], color=pl.SERIES[0], alpha=0.2,
                        linewidth=0, label="±1σ")
    for i, c in enumerate(curves[:8]):
        ax.plot(c["E"], c["y"], color=pl._series_color(i), linewidth=1.5, label=c["label"])
    ax.set_xlabel("incident energy (eV)", color=pl.INK)
    ax.set_ylabel(ylabel, color=pl.INK)
    pl._style(ax)
    ax.legend(frameon=False, labelcolor=pl.INK, fontsize=8)
    return b64(fig)
