"""Make ``tckdb_chemkin`` importable without an install (dev/test convenience)."""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
