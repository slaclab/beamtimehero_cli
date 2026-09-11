"""Beam-diagnostic fitting that is not technique-specific.

``fitter`` holds the knife-edge, aperture and emission-peak fits used during
alignment. It stayed here rather than moving under ``science/`` because it is
diagnostic geometry rather than spectroscopy — see ``science/README.md``.

Everything else that once lived in this package moved:

* ``lcf``               -> ``science.xas.compare``
* ``cosine_similarity`` -> ``science.fitting.similarity``
"""
