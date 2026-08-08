"""record the reaction coordinate and judge imaginary modes by magnitude

ADR 0012 replaces the ``n_imag == 1`` gate on transition states with a
magnitude-and-protocol judgement. That judgement needs three things the
schema could not previously hold:

1. **Which mode is the reaction coordinate.** ``calc_freq_result``
   gains ``reaction_coordinate_mode_index``. Designating exactly one
   mode and removing it from the partition function is the contract
   every transition-state-theory code enforces, and it is what a record
   with more than one imaginary mode must supply to be accepted at all.
2. **What the other imaginary modes are.** ``calc_freq_mode`` gains
   ``imaginary_disposition`` over a new ``imaginary_mode_disposition``
   enum. Declared by the depositor, never inferred — see the conflict
   note in ADR 0012.
3. **The tolerance that was applied, and why.** ``calc_freq_result``
   gains ``imaginary_mode_tau_cm1``, ``imaginary_mode_tau_basis`` and
   ``imaginary_mode_structural_flag``. τ is read from recorded execution
   provenance rather than fixed in code, so it must be stored with the
   record: recomputing it later would silently re-decide every
   historical judgement the moment a parser improves.

``freq.hessian_method`` is seeded into ``calculation_parameter_vocab``
in the same revision. ``calculation_parameter.canonical_key`` is
FK-constrained against that table, so the Gaussian ``Freq=Numer`` and
ORCA ``NumFreq``/``AnFreq`` rows this branch teaches the parsers to emit
cannot be written until the key exists.

No backfill. Every column is nullable and every existing row is
correctly NULL: transition states deposited before this existed were
filtered by ``n_imag == 1``, so the corpus contains no accepted
higher-order saddles and no record has a reaction coordinate to
disambiguate. The flag is absent everywhere rather than false anywhere,
which is the honest state — a record judged under the old rule was never
judged under the new one.

Revision ID: c2f7a4e8d1b6
Revises: b8f3d6a1c9e4
Create Date: 2026-08-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2f7a4e8d1b6"
down_revision: Union[str, Sequence[str], None] = "b8f3d6a1c9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DISPOSITION_ENUM = "imaginary_mode_disposition"
_DISPOSITION_VALUES = (
    "rigid_body_residue",
    "torsion",
    "ring_pucker",
    "intermolecular",
    "symmetry_breaking",
    "unassigned",
)
_HESSIAN_METHOD_KEY = "freq.hessian_method"


def upgrade() -> None:
    """Add the reaction-coordinate designation, dispositions and τ record."""
    disposition = sa.Enum(*_DISPOSITION_VALUES, name=_DISPOSITION_ENUM)
    disposition.create(op.get_bind(), checkfirst=False)

    op.add_column(
        "calc_freq_result",
        sa.Column("reaction_coordinate_mode_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "calc_freq_result",
        sa.Column("imaginary_mode_tau_cm1", sa.Float(), nullable=True),
    )
    op.add_column(
        "calc_freq_result",
        sa.Column("imaginary_mode_tau_basis", sa.Text(), nullable=True),
    )
    op.add_column(
        "calc_freq_result",
        sa.Column("imaginary_mode_structural_flag", sa.Boolean(), nullable=True),
    )

    op.add_column(
        "calc_freq_mode",
        sa.Column(
            "imaginary_disposition",
            disposition,
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_calc_freq_mode_imaginary_disposition_requires_imaginary_mode",
        "calc_freq_mode",
        "imaginary_disposition IS NULL OR is_imaginary",
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
    op.bulk_insert(
        vocab,
        [
            {
                "canonical_key": _HESSIAN_METHOD_KEY,
                "description": (
                    "How the frequency job's second derivatives were "
                    "obtained: analytic, finite_difference_gradient, or "
                    "finite_difference_energy."
                ),
                "expected_value_type": "enum",
                "affects_scientific_result": True,
                "affects_numerics": True,
                "affects_resources": True,
                "note": (
                    "Distinct from opt.initial_hessian, which names where the "
                    "optimiser got its starting Hessian rather than how the "
                    "frequency job built its own. ADR 0012's tau keys on this "
                    "value; absence means 'not recorded' and selects the "
                    "conservative tolerance. Emitted from Gaussian "
                    "Freq=Numer and ORCA NumFreq / AnFreq."
                ),
            }
        ],
    )


def downgrade() -> None:
    """Drop the ADR 0012 columns and the vocab key they read."""
    op.execute(
        sa.text(
            "DELETE FROM calculation_parameter WHERE canonical_key = :k"
        ).bindparams(k=_HESSIAN_METHOD_KEY)
    )
    op.execute(
        sa.text(
            "DELETE FROM calculation_parameter_vocab WHERE canonical_key = :k"
        ).bindparams(k=_HESSIAN_METHOD_KEY)
    )

    op.drop_constraint(
        "ck_calc_freq_mode_imaginary_disposition_requires_imaginary_mode",
        "calc_freq_mode",
        type_="check",
    )
    op.drop_column("calc_freq_mode", "imaginary_disposition")

    op.drop_column("calc_freq_result", "imaginary_mode_structural_flag")
    op.drop_column("calc_freq_result", "imaginary_mode_tau_basis")
    op.drop_column("calc_freq_result", "imaginary_mode_tau_cm1")
    op.drop_column("calc_freq_result", "reaction_coordinate_mode_index")

    sa.Enum(name=_DISPOSITION_ENUM).drop(op.get_bind(), checkfirst=False)
