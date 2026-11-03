"""seed dispersion, solvation, RI, frozen-core, relativistic and basis-function vocab keys

The Gaussian and ORCA parameter parsers read keywords that change a result and
had no canonical key to land on: the empirical dispersion correction, the
continuum solvation model and solvent, the resolution-of-the-identity
approximation, the frozen-core choice, the relativistic Hamiltonian, the
spherical/Cartesian basis-function choice and the SCF stability request. They
were stored as raw rows only, so "do these two calculations share a setup" and
"does the deck agree with what the depositor typed" could not be asked of them.

``calculation_parameter.canonical_key`` is FK-constrained against
``calculation_parameter_vocab``, so the keys must exist before a parser row can
reference one. This revision only inserts vocabulary rows. It changes no
level-of-theory identity, adds no column and rewrites no existing parameter
row.

Auxiliary fitting basis sets (``def2/J``, ``def2/JK``, ``cc-pVTZ/C``) are
deliberately not given a key here: ``level_of_theory`` already carries an
``aux_basis`` field, and a second home for the same fact would let the two
disagree. The parsers keep those tokens as raw rows with no canonical key.

Downgrade nulls the canonical key on any parameter row that used one of these
keys (the raw key and value stay) and then removes the vocabulary rows.

Revision ID: e5b1c8a3d702
Revises: b4d8e1a7c953
Create Date: 2026-11-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b1c8a3d702"
down_revision: Union[str, Sequence[str], None] = "b4d8e1a7c953"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _row(key: str, description: str, value_type: str, note: str | None, *, numerics: bool = False) -> dict:
    return {
        "canonical_key": key,
        "description": description,
        "expected_value_type": value_type,
        "affects_scientific_result": True,
        "affects_numerics": numerics,
        "affects_resources": False,
        "note": note,
    }


_ROWS = [
    _row(
        "dispersion.correction",
        "Empirical dispersion correction requested as a separate keyword.",
        "enum",
        "Closed set: none, d2, d3_zero, d3_bj, d4. A functional with dispersion built into its name "
        "(wB97X-D) belongs to the method, not here.",
    ),
    _row(
        "solvation.model",
        "Continuum solvation model.",
        "enum",
        "Closed set: smd, cpcm, iefpcm, cosmo, alpb. Absent when the deck names a solvent but no model; "
        "the program default is not assumed.",
    ),
    _row(
        "solvation.solvent",
        "Solvent named for the continuum solvation model.",
        "string",
        "Lower-cased solvent name as the program spells it. Names are not unified across programs.",
    ),
    _row(
        "ri.approximation",
        "Resolution-of-the-identity approximation for the Coulomb and exchange terms.",
        "enum",
        "Closed set: none, rij, rijcosx, rijk, rijonx. Absent when the deck is silent; the program "
        "default is not assumed.",
        numerics=True,
    ),
    _row(
        "core.frozen",
        "Frozen-core choice for a correlated method.",
        "enum",
        "Closed set: frozen_core, all_electron, window. Absent when the deck is silent; the program "
        "default is not assumed.",
    ),
    _row(
        "relativistic.hamiltonian",
        "Scalar relativistic Hamiltonian.",
        "enum",
        "Closed set: none, dkh0, dkh2, dkhso, resc, x2c, zora, iora.",
    ),
    _row(
        "basis.d_function_type",
        "Spherical (5D) or Cartesian (6D) d functions.",
        "enum",
        "Closed set: spherical, cartesian.",
        numerics=True,
    ),
    _row(
        "basis.f_function_type",
        "Spherical (7F) or Cartesian (10F) f functions.",
        "enum",
        "Closed set: spherical, cartesian.",
        numerics=True,
    ),
    _row(
        "scf.stability_analysis",
        "SCF wavefunction stability request.",
        "enum",
        "Closed set: check_only, optimize. This is the request in the deck, not the stability result.",
        numerics=True,
    ),
]

_KEYS = [row["canonical_key"] for row in _ROWS]


def upgrade() -> None:
    """Insert the nine vocabulary rows."""
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
    """Keep the raw parameter rows, drop their canonical key, then remove the vocabulary rows."""
    bind = op.get_bind()
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
