"""Archive service tests.

This package marker is load-bearing. Without it, pytest imports the
sibling ``conftest.py`` under the bare top-level module name ``conftest``
and prepends this directory to ``sys.path`` — shadowing
``tests/conftest.py`` for the tests that do ``from conftest import ...``
(``tests/test_db_name_resolution.py``, ``tests/db/*_migration.py``).
Keeping the directory a package makes the fixture module
``tests.services.archive.conftest`` instead, which cannot collide. The
sibling ``tests/services/scientific_read/`` package does the same.
"""
