"""Allow ``python -m meddler`` as an alias for the ``meddler`` command."""

from __future__ import annotations

import sys

from meddler.cli import main

if __name__ == "__main__":
    sys.exit(main())
