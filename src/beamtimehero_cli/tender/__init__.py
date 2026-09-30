"""Tender (SSRL BL 6-2a) tools: the ``tender`` tree.

Andor ``.sif`` detector images reduced to RIXS maps, HERFD and XES spectra,
over the ``tender_analysis`` library (the optional ``[tender]`` extra). Six
read-only leaves; see ``beamtimehero ref tender-analysis`` for the data
format and the reduction, and ``definitions.py`` for the agent-facing
descriptions.

Kept import-light on purpose: ``tool_catalog.lineage`` imports
``tender.definitions`` at catalog build time, so this package must not drag
numpy, scipy or ``tender_analysis`` in. The handlers (``tender.handlers``)
import those, and ``tender_analysis`` itself only inside each call, so a
bth without the extra still registers the leaves and they answer with an
error that names the extra.

Not under ``science/``: these read the filesystem and the environment,
which ``tests/test_science_boundary.py`` forbids there.
"""
