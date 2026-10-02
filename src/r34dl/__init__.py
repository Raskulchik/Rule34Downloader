"""r34dl - a small, polite tag downloader for rule34.

The package is split so that each concern can be changed or tested on its own:

=====================  =========================================================
:mod:`r34dl.cli`       argument parsing and the two usage modes
:mod:`r34dl.pipeline`  wires fetch -> resolve -> download together
:mod:`r34dl.sources`   pluggable listing backends (JSON API, HTML listing)
:mod:`r34dl.net`       pooled HTTP with retries, backoff and rate limiting
:mod:`r34dl.download`  concurrent, resumable, atomic file writing
:mod:`r34dl.storage`   destination resolution and safe writes
:mod:`r34dl.tagging`   tag query parsing
:mod:`r34dl.naming`    filename sanitising and collision avoidance
:mod:`r34dl.ui`        all console output and prompts
=====================  =========================================================
"""

from __future__ import annotations

__version__ = "2.0.0"

__all__ = ["__version__"]