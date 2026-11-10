"""record whether a parser row is what was requested or what the program reported

``calculation_parameter`` gains a nullable ``statement_kind`` (new enum ``parameter_statement_kind``:
``requested`` / ``reported``). A ``requested`` row is what an input deck, or the input a log echoes, asked for. A
``reported`` row is a value the output log prints as the one the program used. Only a parser row can carry a kind,
and only an output-log row can be ``reported`` (check constraint).

Backfill: every existing ``source = 'parser'`` row was read from a deck or from an echoed input, which is a request,
so each is stamped ``requested``. That is a statement about the code that wrote them. ``upload`` and ``curated``
rows stay NULL.

Also adds the vocabulary rows for the numbers a log reports. None of them is a ``grid.quality`` or
``scf.convergence`` value: a program prints numbers, and no keyword is invented from them.

Downgrade deletes the ``reported`` rows (they did not exist before this revision), nulls the canonical key on any
row using a new key, removes the vocabulary rows, then drops the constraint, the column and the enum type.

Revision ID: c8e2a6d1f305
Revises: a3d7f1c5b904
Create Date: 2026-11-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2a6d1f305"
down_revision: Union[str, Sequence[str], None] = "a3d7f1c5b904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NAMING_CONVENTION expands the short name to ck_calculation_parameter_statement_kind_reported_from_log.
_CONSTRAINT = "statement_kind_reported_from_log"


def _row(key: str, description: str, value_type: str, note: str) -> dict:
    return {
        "canonical_key": key,
        "description": description,
        "expected_value_type": value_type,
        "affects_scientific_result": True,
        "affects_numerics": True,
        "affects_resources": False,
        "note": note,
    }


_NOTE = "Read from the value an output log prints as used; never derived from a keyword or a manual."
_ROWS = [
    _row("grid.integration_accuracy", "DFT radial integration accuracy the program reports (ORCA IntAcc).", "float", _NOTE),
    _row(
        "grid.angular_max",
        "Largest angular (Lebedev) grid, in points, the program reports for the DFT grid.",
        "int",
        _NOTE + " The COSX grids are not this key.",
    ),
    _row("scf.tol_energy_eh", "SCF energy-change tolerance the program reports, in hartree.", "float", _NOTE),
    _row("scf.tol_orbital_gradient", "SCF orbital-gradient tolerance the program reports (ORCA TolG).", "float", _NOTE),
    _row("scf.tol_orbital_rotation", "SCF orbital-rotation tolerance the program reports (ORCA TolX).", "float", _NOTE),
    _row("scf.tol_diis_error", "SCF DIIS-error tolerance the program reports (ORCA TolErr).", "float", _NOTE),
    _row("scf.tol_rms_density", "SCF RMS density-matrix tolerance the program reports (Gaussian).", "float", _NOTE),
    _row("scf.tol_max_density", "SCF maximum density-matrix tolerance the program reports (Gaussian).", "float", _NOTE),
]
_KEYS = [row["canonical_key"] for row in _ROWS]


def upgrade() -> None:
    """Create the enum and column, stamp existing parser rows ``requested``, add the constraint and vocabulary."""
    kind = postgresql.ENUM("requested", "reported", name="parameter_statement_kind")
    kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "calculation_parameter",
        sa.Column("statement_kind", postgresql.ENUM(name="parameter_statement_kind", create_type=False), nullable=True),
    )
    op.execute("UPDATE calculation_parameter SET statement_kind = 'requested' WHERE source = 'parser'")
    op.create_check_constraint(
        _CONSTRAINT,
        "calculation_parameter",
        "statement_kind IS NULL OR (source = 'parser' AND (statement_kind = 'requested' OR artifact_kind = 'output_log'))",
    )
    vocab = sa.table(
        "calculation_parameter_vocab",
        sa.column("canonical_key", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("expected_value_type", sa.Text()),
        sa.column("affects_scientific_result", sa.Boolean()),
        sa.column("affects_numerics", sa.Boolean()),
        sa.column("affects_resources", sa.Boolean()),
        sa.column("note", sa.Text()),
    )
    op.bulk_insert(vocab, _ROWS)


def downgrade() -> None:
    """Remove the reported rows and vocabulary, then the constraint, the column and the enum type."""
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM calculation_parameter WHERE statement_kind = 'reported'"))
    bind.execute(
        sa.text(
            "UPDATE calculation_parameter SET canonical_key = NULL, canonical_value = NULL "
            "WHERE canonical_key = ANY(:keys)"
        ).bindparams(sa.bindparam("keys", _KEYS, type_=sa.ARRAY(sa.Text())))
    )
    bind.execute(
        sa.text("DELETE FROM calculation_parameter_vocab WHERE canonical_key = ANY(:keys)").bindparams(
            sa.bindparam("keys", _KEYS, type_=sa.ARRAY(sa.Text()))
        )
    )
    op.drop_constraint(op.f("ck_calculation_parameter_" + _CONSTRAINT), "calculation_parameter", type_="check")
    op.drop_column("calculation_parameter", "statement_kind")
    postgresql.ENUM(name="parameter_statement_kind").drop(bind, checkfirst=True)
