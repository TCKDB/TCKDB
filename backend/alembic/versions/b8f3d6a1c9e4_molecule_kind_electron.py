"""Let a reaction name the electron it releases.

``validate_reaction_charge_conservation`` refuses ``[OH-] + [H] -> H2O``. That
reaction is associative detachment — ``OH⁻ + H → H₂O + e⁻`` — real, measured
gas-phase chemistry and the sibling of ``H⁻ + H → H₂ + e⁻``. Dissociative
attachment, photoionization and photodetachment are the same family. All of
them balance perfectly well; they simply have an electron on one side, and
there was no way to say so.

That is not merely an inconvenience. Under ADR 0008 a check may block only when
it asserts a **definition**. Charge conservation is definitional only if the
participant list can be complete — and with no way to name the electron, the
rule was in fact asserting "every participant of this reaction was declared",
which is an expectation about the depositor, not a definition of a reaction.
The error message offered ``molecule_kind: pseudo`` as the escape, but nothing
in the codebase creates a pseudo species: ``canonical_species_identity``
refuses every non-``molecule`` kind outright, so both pseudo exemptions in
``reaction_resolution`` were unreachable. The advertised door did not exist.

This revision makes it exist, by adding ``electron`` to the ``molecule_kind``
enum.

Why an enum member rather than a seeded row or a column on the reaction
-----------------------------------------------------------------------
An electron *is* a participant. It appears on one side of the arrow, it can
appear with a coefficient (a two-electron process releases two), and it has to
be part of the reaction's stoichiometry hash or ``A -> B`` and ``A -> B + e⁻``
would collide on one identity. Every one of those properties already belongs to
``reaction_participant``, so the electron is carried there like anything else,
and the enum member is what tells the conservation checks how to count it.

The alternatives were weighed and rejected. A signed ``electrons`` column on
``chem_reaction`` would duplicate stoichiometry machinery, need its own sign
convention to say which side the electron is on, and be invisible to every
reader that iterates participants. A reserved row with no enum member would
have to be recognised by its SMILES string, which makes a sentinel string
load-bearing in comparisons rather than in one lookup.

No row is seeded here. The electron's ``species`` row is resolved or created on
first use by ``resolve_species``, exactly like every other species, and
converges on one row because its identity ``("[e-]", -1, 2)`` is fixed. Seeding
would have meant minting a ``public_ref`` inside a migration for a row that may
never be referenced.

Why ``electron`` is not a flavour of ``pseudo``
-----------------------------------------------
``validate_reaction_elemental_balance`` and
``validate_reaction_charge_conservation`` both return early and skip entirely
when any participant is ``pseudo``. Had the electron been routed through that
exemption, a depositor could switch mass-balance checking off on any reaction
by adding an electron to it — a considerably worse hole than the one being
closed.

An electron is not "pseudo, therefore unknowable". It is precisely known: zero
atoms, charge -1. So ``_element_counts_for_species`` returns an empty count for
it and ``_load_participant_species`` tests for ``pseudo`` specifically rather
than for "not a molecule". Both conservation checks stay live on a reaction
carrying an electron, and both must still pass.

Storage
-------
``species.smiles`` holds the reserved token ``[e-]``, which RDKit cannot parse
and which is never handed to it; the payload schema binds that token and
``molecule_kind: electron`` to each other in both directions, so a structure
can never hide behind the token and the token can never reach RDKit.
``species.inchi_key`` is ``CHAR(27) NOT NULL`` and an electron has no InChI, so
it holds ``ELECTRON-NO-INCHIKEY-EXISTS`` — deliberately not a plausible
InChIKey (a real one is ``[A-Z]{14}-[A-Z]{10}-[A-Z]``), so it can neither
collide with a real key nor be mistaken for one. Relaxing the column to
nullable is the more honest schema and was rejected for reach: half a dozen
read schemas type it ``str``.

The corresponding ``species_entry`` carries ``NULL`` in every graph-derived
column — ``mol``, ``unmapped_smiles``, ``isotope_key`` — all of which are
already nullable, so no table is altered here. Any geometry deposited under an
electron is refused by
``assert_geometry_composition_matches_identity``: an electron has no atoms, and
a structure with atoms contradicts that.

Backfill: none. The enum gains a member; no stored row's value changes, and no
existing row can be an electron because there was no way to write one.

``downgrade()`` rebuilds the enum without ``electron``. PostgreSQL cannot drop
a value from an enum in place, so the type is renamed, recreated and the column
cast across. It will fail — correctly and loudly — if any ``species`` row is an
electron by then: removing the member would otherwise silently destroy the only
record that a deposited reaction released one.

Revision ID: b8f3d6a1c9e4
Revises: e7d1c4b8a3f5
Create Date: 2026-08-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8f3d6a1c9e4"
down_revision: Union[str, Sequence[str], None] = "e7d1c4b8a3f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ENUM_NAME = "molecule_kind"
NEW_VALUE = "electron"
PRIOR_VALUES = ("molecule", "pseudo")


def upgrade() -> None:
    """Add ``electron`` to the ``molecule_kind`` enum.

    ``ADD VALUE`` is safe inside Alembic's transaction on PostgreSQL 12+ so
    long as the new value is not *used* in the same transaction, which it is
    not: nothing here writes a row.
    """

    op.execute(f"ALTER TYPE {ENUM_NAME} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")


def downgrade() -> None:
    """Rebuild ``molecule_kind`` without ``electron``.

    Refuses to run while any ``species`` row still uses the value, rather than
    losing the fact that a deposited reaction released a free electron.
    """

    op.execute(
        f"""
        DO $$
        DECLARE
            offending bigint;
        BEGIN
            SELECT count(*) INTO offending
            FROM species
            WHERE kind = '{NEW_VALUE}';

            IF offending > 0 THEN
                RAISE EXCEPTION
                    'Cannot remove molecule_kind=''{NEW_VALUE}'': % species '
                    'row(s) still declare one. Removing the value would '
                    'silently discard the record that those reactions '
                    'released or consumed a free electron. Re-deposit or '
                    'delete those reactions first.',
                    offending
                    USING ERRCODE = '23514';
            END IF;
        END
        $$;
        """
    )

    prior = ", ".join(f"'{value}'" for value in PRIOR_VALUES)
    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    op.execute(f"CREATE TYPE {ENUM_NAME} AS ENUM ({prior})")
    op.execute(
        f"ALTER TABLE species ALTER COLUMN kind TYPE {ENUM_NAME} "
        f"USING kind::text::{ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {ENUM_NAME}_old")
