"""Record owner attestations of a software version (issue #305, decision (a)).

Two new, append-only tables:

* ``software_version_attestation`` -- who attested (``attested_by``), whose
  deposits the statement covers (``covers_depositor``), when, which
  version-less ``software_release`` it concerns, the attested version, the
  statement verbatim and the evidence kind (``owner_attestation``).
* ``software_version_attestation_calculation`` -- one row per calculation
  re-pointed under an attestation, with its release before and after.

Append-only the way ``record_review_event`` and ``accepted_science_repair``
are: ``tckdb_reject_mutation`` before UPDATE/DELETE and
``tckdb_reject_truncate`` before TRUNCATE (both defined by ``c6f2a9d4e7b1``).
The triggers are named ``trg_sva_*`` rather than ``trg_append_only_*``: these
tables are provenance, not accepted science, and the ``trg_append_only_`` /
``trg_as_`` namespace is the accepted-science registry that
``tests/db/test_accepted_science_trigger_registry.py`` asserts set equality
over.

Rows are validated on insert, and **only against facts that never change
afterwards**, because an archive restore replays these INSERTs against the
calculations' *final* state (a calculation can be re-pointed again later,
e.g. by DR-0008's name correction):

* an attestation concerns a release whose ``version`` is NULL -- identity
  rows are never filled in place (the fill tools re-point instead), so this
  holds at restore as it did at write;
* a link row's ``before`` is the attestation's release; ``after`` is the same
  program, with ``revision`` and ``build`` exactly ``before``'s and
  ``version`` exactly the attested string; the calculation was deposited by
  the attestation's ``covers_depositor``; and the calculation no longer cites
  ``before`` (nothing re-points a calculation *back* to a version-less row).

"The calculation cites ``after`` now" is deliberately not checked here: it is
true only at write time, and the tool checks it there.

Edited in place while unmerged and undeployed (the brand-new-table rule), to
add ``covers_depositor`` and restore-stable validation after review.

No data is written. The playground's attestations are recorded afterwards by
``scripts/ops/fill_software_release_version.py --attest-version``.

Revision ID: 86ffcd9d3c65
Revises: e7b1c9d4a632
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "86ffcd9d3c65"
down_revision: Union[str, Sequence[str], None] = "e7b1c9d4a632"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVIDENCE_KIND = postgresql.ENUM(
    "owner_attestation", name="software_version_evidence_kind", create_type=False
)

_TABLES = ("software_version_attestation", "software_version_attestation_calculation")


def _trigger_names(table: str) -> tuple[str, str]:
    short = "sva" if table == "software_version_attestation" else "sva_calculation"
    return f"trg_{short}_append_only", f"trg_{short}_no_truncate"


def upgrade() -> None:
    _EVIDENCE_KIND.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "software_version_attestation",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("software_release_id", sa.BigInteger(), nullable=False),
        sa.Column("attested_version", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence_kind", _EVIDENCE_KIND, nullable=False),
        sa.Column("attested_by", sa.BigInteger(), nullable=False),
        sa.Column("covers_depositor", sa.BigInteger(), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_software_version_attestation")),
        sa.ForeignKeyConstraint(
            ["software_release_id"], ["software_release.id"], name="fk_sva_software_release"
        ),
        sa.ForeignKeyConstraint(["attested_by"], ["app_user.id"], name="fk_sva_attested_by"),
        sa.ForeignKeyConstraint(
            ["covers_depositor"], ["app_user.id"], name="fk_sva_covers_depositor"
        ),
        sa.CheckConstraint(
            "length(btrim(attested_version)) > 0",
            name=op.f("ck_software_version_attestation_version_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(statement)) > 0",
            name=op.f("ck_software_version_attestation_statement_nonblank"),
        ),
    )
    op.create_index(
        "ix_software_version_attestation_software_release_id",
        "software_version_attestation",
        ["software_release_id"],
    )

    op.create_table(
        "software_version_attestation_calculation",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("attestation_id", sa.BigInteger(), nullable=False),
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("before_software_release_id", sa.BigInteger(), nullable=False),
        sa.Column("after_software_release_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_software_version_attestation_calculation")),
        sa.ForeignKeyConstraint(
            ["attestation_id"],
            ["software_version_attestation.id"],
            name="fk_sva_calculation_attestation",
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"], ["calculation.id"], name="fk_sva_calculation_calculation"
        ),
        sa.ForeignKeyConstraint(
            ["before_software_release_id"],
            ["software_release.id"],
            name="fk_sva_calculation_before_release",
        ),
        sa.ForeignKeyConstraint(
            ["after_software_release_id"],
            ["software_release.id"],
            name="fk_sva_calculation_after_release",
        ),
        sa.UniqueConstraint(
            "attestation_id", "calculation_id", name="uq_sva_calculation_attestation_calculation"
        ),
        sa.CheckConstraint(
            "before_software_release_id <> after_software_release_id",
            name=op.f("ck_software_version_attestation_calculation_release_changed"),
        ),
    )
    op.create_index(
        "ix_software_version_attestation_calculation_calculation_id",
        "software_version_attestation_calculation",
        ["calculation_id"],
    )

    op.execute(
        """
        CREATE FUNCTION public.tckdb_validate_sva()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF (SELECT version FROM public.software_release WHERE id = NEW.software_release_id) IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation must concern a release whose version is NULL';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_validate_sva_calculation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_attested_release bigint;
            v_attested_version text;
            v_depositor bigint;
            v_before public.software_release%ROWTYPE;
            v_after public.software_release%ROWTYPE;
            v_calc_depositor bigint;
            v_calc_release bigint;
        BEGIN
            SELECT software_release_id, attested_version, covers_depositor
              INTO v_attested_release, v_attested_version, v_depositor
              FROM public.software_version_attestation WHERE id = NEW.attestation_id;
            IF NEW.before_software_release_id IS DISTINCT FROM v_attested_release THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: before must be the attested release';
            END IF;
            SELECT * INTO v_before FROM public.software_release WHERE id = NEW.before_software_release_id;
            SELECT * INTO v_after FROM public.software_release WHERE id = NEW.after_software_release_id;
            IF v_after.software_id IS DISTINCT FROM v_before.software_id
               OR v_after.revision IS DISTINCT FROM v_before.revision
               OR v_after.build IS DISTINCT FROM v_before.build
               OR v_after.version IS DISTINCT FROM v_attested_version THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: after must be before with only the attested version added';
            END IF;
            SELECT created_by, software_release_id INTO v_calc_depositor, v_calc_release
              FROM public.calculation WHERE id = NEW.calculation_id;
            IF v_calc_depositor IS DISTINCT FROM v_depositor THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: the calculation is not the covered depositor''s';
            END IF;
            IF v_calc_release IS NOT DISTINCT FROM NEW.before_software_release_id THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: the calculation was never re-pointed';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sva_validate
        BEFORE INSERT ON public.software_version_attestation
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_sva()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sva_calculation_validate
        BEFORE INSERT ON public.software_version_attestation_calculation
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_sva_calculation()
        """
    )
    for table in _TABLES:
        append_only, no_truncate = _trigger_names(table)
        op.execute(
            f"""
            CREATE TRIGGER {append_only}
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {no_truncate}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def downgrade() -> None:
    # Dropping the tables drops their triggers; a DROP is DDL, so the
    # append-only row triggers do not fire on it.
    op.drop_index(
        "ix_software_version_attestation_calculation_calculation_id",
        table_name="software_version_attestation_calculation",
    )
    op.drop_table("software_version_attestation_calculation")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_validate_sva_calculation()")
    op.drop_index(
        "ix_software_version_attestation_software_release_id",
        table_name="software_version_attestation",
    )
    op.drop_table("software_version_attestation")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_validate_sva()")
    _EVIDENCE_KIND.drop(op.get_bind(), checkfirst=True)
