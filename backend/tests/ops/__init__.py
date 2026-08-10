"""Host-ops script tests.

This package marker is load-bearing, for the same reason as the one in
``tests/services/archive/``. Without it pytest imports the sibling
``conftest.py`` under the bare top-level module name ``conftest`` and
prepends this directory to ``sys.path``. Whichever of the two bare
``conftest`` modules a worker imports first wins ``sys.modules`` for the
rest of that process, so a worker that reached ``tests/ops/`` before
``tests/db/`` then failed every test that does ``from conftest import
_database_url`` with an ``ImportError`` naming this file -- a pure
ordering artefact that no amount of re-running would reproduce at a
different seed.

Keeping the directory a package makes the fixture module
``tests.ops.conftest``, which cannot collide. ``tests/test_test_package_layout.py``
enforces the rule for every future conftest.
"""
