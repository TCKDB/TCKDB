"""Make this checkout's ``src/`` authoritative for the test suite.

``tckdb-client`` is a src-layout package. If a *different* checkout is
installed in the environment (an editable install pointing at another
worktree, a released wheel, ...), ``import tckdb_client`` would resolve
there and the suite would silently validate code that is not in this
tree. Prepending the local ``src/`` removes that ambiguity: running
``pytest`` inside ``clients/python`` always tests ``clients/python/src``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir():
    src_path = str(_SRC)
    if sys.path and sys.path[0] == src_path:
        pass
    else:
        while src_path in sys.path:
            sys.path.remove(src_path)
        sys.path.insert(0, src_path)
