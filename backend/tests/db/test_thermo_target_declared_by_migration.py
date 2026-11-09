"""``thermo.thermodynamic_target_declared_by``: a metadata-only addition that leaves existing rows alone.

Every row that had a target before the column existed was declared by a depositor (nothing else
declared one), so the column's default is the honest value for it. The addition is
``NOT NULL DEFAULT 'depositor'`` so no UPDATE runs: ``thermo`` is an accepted-science root, and an
UPDATE of an approved row would be refused.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness
from tests.db.test_thermo_declarations_migration import _alembic, _seed_thermo_rows, harness  # noqa: F401

_REVISION = revision_under_test("e7a3c1d9f482")


def test_existing_rows_read_depositor_and_keep_their_data(harness):  # noqa: F811
    harness.run("upgrade", _REVISION.parent)
    ids = _seed_thermo_rows(harness.engine)
    with harness.engine.begin() as conn:
        conn.execute(
            text("UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble' WHERE id = :i"), {"i": ids[0]}
        )

    harness.run("upgrade", _REVISION.revision)
    with harness.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT thermodynamic_target_kind, thermodynamic_target_declared_by FROM thermo ORDER BY id")
        ).all()
        assert [tuple(r) for r in rows] == [("equilibrium_ensemble", "depositor"), (None, "depositor")]
        assert conn.scalar(text("SELECT h298_kj_mol FROM thermo WHERE id = :i"), {"i": ids[0]}) == -74.6


def test_the_database_refuses_an_unknown_source(harness):  # noqa: F811
    harness.run("upgrade", _REVISION.revision)
    ids = _seed_thermo_rows(harness.engine)
    with pytest.raises(Exception, match="thermo_declaration_source"):
        with harness.engine.begin() as conn:
            conn.execute(
                text("UPDATE thermo SET thermodynamic_target_declared_by = 'someone' WHERE id = :i"), {"i": ids[0]}
            )


def test_downgrade_says_how_many_chemkin_declarations_it_forgets_and_drops_the_type(harness):  # noqa: F811
    harness.run("upgrade", _REVISION.revision)
    ids = _seed_thermo_rows(harness.engine)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble', "
                "thermodynamic_target_declared_by = 'chemkin_format' WHERE id = :i"
            ),
            {"i": ids[0]},
        )
    downgraded = _alembic(harness, "downgrade", _REVISION.parent)
    assert downgraded.returncode == 0, downgraded.stderr[-3000:]
    assert re.search(r"forgets: 1 CHEMKIN-format target declaration", downgraded.stdout + downgraded.stderr)
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'thermo_declaration_source'")) == 0
        assert conn.scalar(text("SELECT count(*) FROM thermo")) == 2
