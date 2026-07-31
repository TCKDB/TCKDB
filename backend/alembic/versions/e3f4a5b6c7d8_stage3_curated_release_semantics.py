"""Stage 3: curated product selection and citable dataset releases.

Adds the curation-overlay layer that lets TCKDB answer "what is *the* TCKDB
value?" without ever writing to the scientific record. Five new tables:

1. ``curation_policy`` — a named, versioned expert selection rubric. Identity
   table, deduped on ``(name, version)``. A policy revision is a new row, so a
   published release's stated policy can never change after the fact.
2. ``dataset_release`` — the citable unit. Carries the data/code licenses, the
   citation string, the maintainer contact, the changelog entry, and a
   nullable ``doi`` that stays NULL until a deposit is actually made.
3. ``release_selection`` — one attributed, append-only decision: policy +
   curator + selected candidate + subject + rationale + release.
4. ``release_manifest`` — the frozen, checksummed description of a published
   release, binding it to the Alembic revision, backend and wire-schema
   package versions, curation-policy version, review-policy version, resolved
   read profile, and licenses.
5. ``release_artifact`` — one checksummed (SHA-256 + byte count + record
   count) file inside a manifest.

Nothing here touches an existing table. No product table gains an ``is_best``
column; that is the point. Selections address the science with the same loose
``(record_type, record_id)`` pointer that ``record_review`` and
``scientific_record_supersession`` already use.

Append-only enforcement
-----------------------
``release_selection``, ``release_manifest`` and ``release_artifact`` each get a
BEFORE UPDATE OR DELETE trigger that raises, mirroring
``record_reproducibility_assessment`` (revision ``b4e8c1f6a2d9``). Superseding
a selection means inserting a row whose ``supersedes_selection_id`` names the
row it replaces; that column is UNIQUE, so supersession chains stay linear and
a curator's earlier reasoning is never overwritten.

Backfill design
---------------
None required. All five tables are new and start empty. No existing row gains a
non-nullable column, and no existing constraint is tightened, so there is
nothing to backfill and nothing that can fail on the deployed corpus.

Downgrade guard
---------------
``downgrade()`` refuses to run if any ``dataset_release`` row is ``published``.
A published release is a citable artifact with a frozen checksum; silently
dropping it would turn an outstanding citation into a dangling reference.
Drafts carry no such promise and are dropped freely, so a fresh
upgrade → downgrade → upgrade cycle on an empty database runs clean.

The existing ``submission_record_type`` enum is reused. This revision owns the
new ``dataset_release_status``, ``release_selection_action``,
``release_artifact_kind`` and ``read_profile`` enums.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RELEASE_STATUS_VALUES = ("draft", "published", "withdrawn")
_SELECTION_ACTION_VALUES = ("select", "supersede", "withdraw")
_ARTIFACT_KIND_VALUES = (
    "selected_records",
    "candidate_records",
    "review_history",
    "selection_ledger",
)
_READ_PROFILE_VALUES = ("exploratory", "curated")

# Record types a release selection may name. Mirrors
# ``app.db.models.dataset_release.SELECTABLE_RECORD_TYPES`` (sorted).
_SELECTABLE_RECORD_TYPES = (
    "kinetics",
    "network_solve",
    "statmech",
    "thermo",
    "transition_state_entry",
    "transport",
)

# (table, trigger name, function name) for the append-only guards.
_APPEND_ONLY: tuple[tuple[str, str, str], ...] = (
    (
        "release_selection",
        "trg_release_selection_append_only",
        "reject_release_selection_mutation",
    ),
    (
        "release_manifest",
        "trg_release_manifest_immutable",
        "reject_release_manifest_mutation",
    ),
    (
        "release_artifact",
        "trg_release_artifact_immutable",
        "reject_release_artifact_mutation",
    ),
)


def _record_type_enum() -> postgresql.ENUM:
    """Reference the pre-existing ``submission_record_type`` enum."""
    return postgresql.ENUM(name="submission_record_type", create_type=False)


def upgrade() -> None:
    """Create the curation-policy / release / manifest / artifact layer."""
    bind = op.get_bind()

    release_status = postgresql.ENUM(
        *_RELEASE_STATUS_VALUES, name="dataset_release_status", create_type=False
    )
    selection_action = postgresql.ENUM(
        *_SELECTION_ACTION_VALUES, name="release_selection_action", create_type=False
    )
    artifact_kind = postgresql.ENUM(
        *_ARTIFACT_KIND_VALUES, name="release_artifact_kind", create_type=False
    )
    read_profile = postgresql.ENUM(
        *_READ_PROFILE_VALUES, name="read_profile", create_type=False
    )
    for enum_type in (release_status, selection_action, artifact_kind, read_profile):
        enum_type.create(bind, checkfirst=True)

    # ------------------------------------------------------------------
    # curation_policy — identity: a named, versioned expert rubric
    # ------------------------------------------------------------------
    op.create_table(
        "curation_policy",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "criteria_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(btrim(description)) > 0",
            name=op.f("ck_curation_policy_description_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name=op.f("ck_curation_policy_name_nonblank")
        ),
        sa.CheckConstraint(
            "length(btrim(version)) > 0",
            name=op.f("ck_curation_policy_version_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_curation_policy_created_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curation_policy")),
        sa.UniqueConstraint("name", "version", name="uq_curation_policy_name"),
    )
    op.create_index(
        op.f("ix_curation_policy_public_ref"),
        "curation_policy",
        ["public_ref"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # dataset_release — curation: the citable unit
    # ------------------------------------------------------------------
    op.create_table(
        "dataset_release",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                *_RELEASE_STATUS_VALUES,
                name="dataset_release_status",
                create_type=False,
            ),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("curation_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("data_license", sa.String(length=64), nullable=False),
        sa.Column("code_license", sa.String(length=64), nullable=False),
        sa.Column("citation_text", sa.Text(), nullable=False),
        sa.Column("doi", sa.String(length=128), nullable=True),
        sa.Column("contact", sa.Text(), nullable=False),
        sa.Column("changelog_entry", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "(status <> 'withdrawn') OR (withdrawn_at IS NOT NULL AND "
            "length(btrim(coalesce(withdrawn_reason, ''))) > 0)",
            name=op.f("ck_dataset_release_withdrawn_has_reason"),
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) OR "
            "(status IN ('published', 'withdrawn') AND published_at IS NOT NULL)",
            name=op.f("ck_dataset_release_published_at_matches_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(citation_text)) > 0",
            name=op.f("ck_dataset_release_citation_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(code_license)) > 0",
            name=op.f("ck_dataset_release_code_license_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(contact)) > 0",
            name=op.f("ck_dataset_release_contact_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(data_license)) > 0",
            name=op.f("ck_dataset_release_data_license_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(tag)) > 0", name=op.f("ck_dataset_release_tag_nonblank")
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_dataset_release_title_nonblank")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_dataset_release_created_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["curation_policy_id"],
            ["curation_policy.id"],
            name=op.f("fk_dataset_release_curation_policy_id_curation_policy"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_release")),
        sa.UniqueConstraint("tag", name="uq_dataset_release_tag"),
    )
    op.create_index(
        op.f("ix_dataset_release_public_ref"),
        "dataset_release",
        ["public_ref"],
        unique=True,
    )
    op.create_index(
        "ix_dataset_release_status", "dataset_release", ["status"], unique=False
    )

    # ------------------------------------------------------------------
    # release_manifest — result: frozen, checksummed, immutable
    # ------------------------------------------------------------------
    op.create_table(
        "release_manifest",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_release_id", sa.BigInteger(), nullable=False),
        sa.Column("manifest_schema", sa.String(length=64), nullable=False),
        sa.Column(
            "profile",
            postgresql.ENUM(
                *_READ_PROFILE_VALUES, name="read_profile", create_type=False
            ),
            server_default="curated",
            nullable=False,
        ),
        sa.Column("alembic_revision", sa.String(length=64), nullable=False),
        sa.Column("backend_version", sa.String(length=64), nullable=False),
        sa.Column("schemas_package_version", sa.String(length=64), nullable=False),
        sa.Column("review_policy_version", sa.String(length=64), nullable=False),
        sa.Column("curation_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("recovery_archive_schema", sa.String(length=64), nullable=False),
        # Release-claim snapshot: every field the frozen document asserts is
        # copied here at publication and read back from here afterwards, so a
        # later annotation (a DOI, a withdrawal) cannot move the document or
        # its digest.
        # Snapshotted so rebuilding the frozen document touches only this row.
        sa.Column("release_public_ref", sa.String(length=40), nullable=False),
        sa.Column("release_published_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("release_tag", sa.String(length=64), nullable=False),
        sa.Column("release_title", sa.Text(), nullable=False),
        sa.Column("release_description", sa.Text(), nullable=True),
        sa.Column("release_contact", sa.Text(), nullable=False),
        sa.Column("release_changelog_entry", sa.Text(), nullable=True),
        sa.Column(
            "release_status_at_publication",
            postgresql.ENUM(
                *_RELEASE_STATUS_VALUES,
                name="dataset_release_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("release_doi_at_publication", sa.String(length=128), nullable=True),
        sa.Column("data_license", sa.String(length=64), nullable=False),
        sa.Column("code_license", sa.String(length=64), nullable=False),
        sa.Column("citation_text", sa.Text(), nullable=False),
        # Curation-policy snapshot, for the same reason.
        sa.Column("curation_policy_ref", sa.String(length=40), nullable=False),
        sa.Column("curation_policy_name", sa.String(length=128), nullable=False),
        sa.Column("curation_policy_version", sa.String(length=64), nullable=False),
        sa.Column("curation_policy_description", sa.Text(), nullable=False),
        sa.Column(
            "curation_policy_criteria_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # The ``contract`` block as rendered at publication. Stored rather than
        # rebuilt from module constants, so editing a literal cannot make every
        # past release report unverified.
        sa.Column(
            "contract_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        # The frozen document itself, served verbatim to anyone resolving the
        # citation. ``content_sha256`` is its canonical-serialization digest.
        sa.Column(
            "document_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("selected_record_count", sa.BigInteger(), nullable=False),
        sa.Column("candidate_record_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_release_manifest_content_sha256_hex"),
        ),
        sa.CheckConstraint(
            "candidate_record_count >= 0",
            name=op.f("ck_release_manifest_candidate_count_nonneg"),
        ),
        sa.CheckConstraint(
            "length(btrim(alembic_revision)) > 0",
            name=op.f("ck_release_manifest_alembic_revision_nonblank"),
        ),
        sa.CheckConstraint(
            "selected_record_count >= 0",
            name=op.f("ck_release_manifest_selected_count_nonneg"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_release_manifest_created_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["curation_policy_id"],
            ["curation_policy.id"],
            name=op.f("fk_release_manifest_curation_policy_id_curation_policy"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_release_id"],
            ["dataset_release.id"],
            name=op.f("fk_release_manifest_dataset_release_id_dataset_release"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_manifest")),
        sa.UniqueConstraint(
            "dataset_release_id", name="uq_release_manifest_dataset_release_id"
        ),
    )
    op.create_index(
        op.f("ix_release_manifest_public_ref"),
        "release_manifest",
        ["public_ref"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # release_selection — curation: attributed, append-only
    # ------------------------------------------------------------------
    op.create_table(
        "release_selection",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_release_id", sa.BigInteger(), nullable=False),
        sa.Column("curation_policy_id", sa.BigInteger(), nullable=False),
        sa.Column("record_type", _record_type_enum(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("subject_type", _record_type_enum(), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "action",
            postgresql.ENUM(
                *_SELECTION_ACTION_VALUES,
                name="release_selection_action",
                create_type=False,
            ),
            server_default="select",
            nullable=False,
        ),
        sa.Column("supersedes_selection_id", sa.BigInteger(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("selected_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "(action = 'select' AND supersedes_selection_id IS NULL) OR "
            "(action IN ('supersede', 'withdraw') AND supersedes_selection_id IS NOT NULL)",
            name=op.f("ck_release_selection_supersession_matches_action"),
        ),
        sa.CheckConstraint(
            "record_type IN ("
            + ", ".join(f"'{v}'" for v in _SELECTABLE_RECORD_TYPES)
            + ")",
            name=op.f("ck_release_selection_selectable_record_type"),
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) > 0",
            name=op.f("ck_release_selection_rationale_nonblank"),
        ),
        sa.CheckConstraint(
            "supersedes_selection_id IS NULL OR supersedes_selection_id <> id",
            name=op.f("ck_release_selection_no_self_supersession"),
        ),
        sa.ForeignKeyConstraint(
            ["curation_policy_id"],
            ["curation_policy.id"],
            name=op.f("fk_release_selection_curation_policy_id_curation_policy"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_release_id"],
            ["dataset_release.id"],
            name=op.f("fk_release_selection_dataset_release_id_dataset_release"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["selected_by"],
            ["app_user.id"],
            name=op.f("fk_release_selection_selected_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_selection_id"],
            ["release_selection.id"],
            name=op.f("fk_release_selection_supersedes_selection_id_release_selection"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_selection")),
        sa.UniqueConstraint(
            "supersedes_selection_id",
            name="uq_release_selection_supersedes_selection_id",
        ),
    )
    op.create_index(
        op.f("ix_release_selection_public_ref"),
        "release_selection",
        ["public_ref"],
        unique=True,
    )
    op.create_index(
        "ix_release_selection_record",
        "release_selection",
        ["record_type", "record_id"],
        unique=False,
    )
    op.create_index(
        "ix_release_selection_release_subject",
        "release_selection",
        ["dataset_release_id", "subject_type", "subject_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # release_artifact — result: one checksummed file per manifest
    # ------------------------------------------------------------------
    op.create_table(
        "release_artifact",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("release_manifest_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                *_ARTIFACT_KIND_VALUES,
                name="release_artifact_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        # The frozen bytes. Storing them is what makes a published release
        # survive the next ordinary upload; re-rendering on read made a
        # citation self-destruct as soon as the corpus grew.
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("record_count", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_release_artifact_sha256_hex")
        ),
        sa.CheckConstraint(
            "byte_count >= 0", name=op.f("ck_release_artifact_byte_count_nonneg")
        ),
        sa.CheckConstraint(
            "length(btrim(path)) > 0", name=op.f("ck_release_artifact_path_nonblank")
        ),
        sa.CheckConstraint(
            "record_count >= 0", name=op.f("ck_release_artifact_record_count_nonneg")
        ),
        sa.ForeignKeyConstraint(
            ["release_manifest_id"],
            ["release_manifest.id"],
            name=op.f("fk_release_artifact_release_manifest_id_release_manifest"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_artifact")),
        sa.UniqueConstraint(
            "release_manifest_id", "path", name="uq_release_artifact_release_manifest_id"
        ),
    )

    # ------------------------------------------------------------------
    # Append-only / immutability triggers
    # ------------------------------------------------------------------
    for table, trigger, function in _APPEND_ONLY:
        op.execute(
            f"""
            CREATE FUNCTION {function}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = '{table} is append-only';
            END;
            $$
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION {function}()
            """
        )
        # A row-level trigger does not fire for TRUNCATE, so without this a
        # single ``TRUNCATE release_selection CASCADE`` would silently erase
        # an entire audited curation history that UPDATE and DELETE are
        # forbidden from touching one row at a time. Statement-level, because
        # TRUNCATE has no rows to iterate.
        op.execute(
            f"""
            CREATE TRIGGER {trigger}_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT
            EXECUTE FUNCTION {function}()
            """
        )


def downgrade() -> None:
    """Drop the release layer, refusing to orphan a published citation."""
    bind = op.get_bind()

    # ``withdrawn`` is guarded as tightly as ``published``. A withdrawn
    # release is precisely the case where an outstanding citation must still
    # resolve — to an explicit "this was retracted, and here is why" rather
    # than a 404. Dropping it silently is the one outcome a retraction exists
    # to prevent, so withdrawing is *not* a way to make a downgrade legal.
    ever_published = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM dataset_release "
            "WHERE status IN ('published', 'withdrawn'))"
        )
    ).scalar()
    if ever_published:
        raise RuntimeError(
            "Refusing to downgrade e3f4a5b6c7d8: this database has released "
            "datasets (published or withdrawn). Both are citable artifacts with "
            "frozen checksums, and dropping either would leave outstanding "
            "citations pointing at nothing — a withdrawn release must keep "
            "resolving so a reader discovers the retraction. Export the "
            "manifests and artifacts first (see "
            "backend/docs/deployment/cutting_a_dataset_release.md), then "
            "restore from a pre-release backup rather than downgrading in "
            "place."
        )

    for table, trigger, function in _APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")

    op.drop_table("release_artifact")
    op.drop_index("ix_release_selection_release_subject", table_name="release_selection")
    op.drop_index("ix_release_selection_record", table_name="release_selection")
    op.drop_index(
        op.f("ix_release_selection_public_ref"), table_name="release_selection"
    )
    op.drop_table("release_selection")
    op.drop_index(op.f("ix_release_manifest_public_ref"), table_name="release_manifest")
    op.drop_table("release_manifest")
    op.drop_index("ix_dataset_release_status", table_name="dataset_release")
    op.drop_index(op.f("ix_dataset_release_public_ref"), table_name="dataset_release")
    op.drop_table("dataset_release")
    op.drop_index(op.f("ix_curation_policy_public_ref"), table_name="curation_policy")
    op.drop_table("curation_policy")

    # Drop only the enums this revision owns. ``submission_record_type`` is
    # from the initial schema and stays.
    for name in (
        "release_artifact_kind",
        "release_selection_action",
        "dataset_release_status",
        "read_profile",
    ):
        postgresql.ENUM(name=name, create_type=False).drop(bind, checkfirst=True)
