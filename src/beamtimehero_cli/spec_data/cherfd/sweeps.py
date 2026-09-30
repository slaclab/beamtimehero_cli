"""Loaded + cleaned sweeps: the one call handlers make for sweep data.

Joins the loader (disk) with ``science.frames.clean_sweep`` (logic): the
commanded energy range and trigger rate come from the sweep's own
ScanResults metadata, so outlier rejection and the frame-rate check are
against what was *asked for*, not against the data itself.
"""
from __future__ import annotations

from beamtimehero_cli.spec_data.cherfd import loader
from beamtimehero_cli.spec_data.cherfd.files import SweepFile
from beamtimehero_cli.science.cherfd import command as cmd_sci
from beamtimehero_cli.science.cherfd import frames, policy


def commanded(meta: dict) -> dict:
    """Energy range + requested freq from metadata, when parseable."""
    command = meta.get("command") if meta.get("available") else None
    if not command:
        return {"command": None, "energy_range": None, "freq_hz": None}
    try:
        p = cmd_sci.parse_command(command)
    except ValueError:
        return {"command": command, "energy_range": None, "freq_hz": None}
    from beamtimehero_cli.science.cherfd.beamline import trigger_timing
    return {"command": command, "energy_range": (p["start_ev"], p["final_ev"]),
            "freq_hz": trigger_timing(p["freq_hz"])["real_freq_hz"]}


def load_clean(sf: SweepFile, *, align: str = "trigger",
               detector_frame_offset: int = policy.DETECTOR_FRAME_OFFSET) -> dict:
    fpga, det, prov = loader.load_sweep_arrays(sf)
    meta = loader.load_metadata(sf)
    c = commanded(meta)
    period_us = (1e6 / c["freq_hz"]) if c["freq_hz"] else None
    clean = frames.clean_sweep(fpga, detector=det or None, period_us=period_us,
                               energy_range=c["energy_range"], align=align,
                               detector_frame_offset=detector_frame_offset)
    notes = []
    for name in list(clean["arrays"]):
        if frames.is_adc(name):
            clean["arrays"][name] = frames.adc_signed(clean["arrays"][name])
    if any(frames.is_adc(n) for n in clean["arrays"]):
        notes.append(f"adc_* columns converted from offset-binary (minus {policy.ADC_OFFSET}); "
                     "presentation not yet hardware-verified (cscan_daq checklist item 5)")
    clean.update({"sweep": sf, "meta": meta, "commanded": c, "provenance": prov, "notes": notes})
    return clean


def available_columns(clean: dict) -> list[str]:
    return sorted(clean["arrays"])
