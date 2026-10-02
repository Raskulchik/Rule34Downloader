#!/usr/bin/env python3
"""Backwards-compatible launcher.

The original project was a single ``main.py`` run directly from a checkout, so
that keeps working::

    python main.py -t "cute" -n 5

It just delegates to the :mod:`r34dl` package. Installing the project
(``pip install -e .``) and running the ``r34dl`` command is the better option,
because then this file is not needed at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from r34dl.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())