"""Test bootstrap: put the package's ``src`` on ``sys.path``.

Allows ``pytest integrations/mcp/tests`` to run without an editable
install, and without requiring the ``mcp`` SDK at test time (server.py
imports it lazily).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
