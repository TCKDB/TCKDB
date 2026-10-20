"""split every stored name-with-a-version string into a name and an integer version

Versioning PR-4 (the last of four). A versioned thing TCKDB owns is identified by a stable ``name`` and a separate
integer ``version``; no stored value fuses the two. This revision rewrites the stored shapes that did
(``computed_thermo_v2``, ``machine_review_v1``, ``tckdb.dataset_release.v2``, ``gaussian_v1``, ``record_review.v1``,
``tckdb.execution-environment.v1``, ``torsion_basin`` / ``v1`` ...) into ``name`` + ``version`` columns, and the
application, in the same release, resets every TCKDB-owned version to 1. A version owned by somebody else (an ESS
release, a producer's own parser, NIST's ThermoML) stays text in its own field and is never renumbered.

What it rewrites, by table
--------------------------
* ``record_machine_review``: ``prompt_version`` text -> ``prompt_name`` text + ``prompt_version`` int;
  ``rubric_versions_json`` (one key ``<rubric>_v<N>`` valued ``"N"``) -> ``rubric_name`` + ``rubric_version`` int;
  ``context_schema_version`` text ``'v1'`` -> int; ``model`` ``external_cp_comparison_v1`` -> ``external_cp_comparison``.
  **Aborts** if any row's ``rubric_versions_json`` does not hold exactly one key: the machine-review currency rule says
  that cannot happen, and this migration proves it instead of assuming it (a second key would be silently dropped).
  The stored rubric *version* is kept as it was (``computed_thermo`` 2, ``computed_transition_state`` 5, ...): the
  application now runs every rubric at version 1, so every stored review reads **stale** (stored 2 against current 1) --
  the stale-assessment mechanism doing its job, and the context hash changes too.
* ``record_reproducibility_assessment``: ``rubric_version`` text ``'v1'`` -> int, and the two reason tokens that
  ended ``_v1`` lose the suffix inside ``context_json`` / ``missing_json``. The rewritten rows read stale (the context
  hash no longer matches) and are reassessed.
* ``submission_audit_event``: ``details_json['schema_version'] == 'machine_review_v2'`` ->
  ``details_json['format'] == {'name': 'machine_review', 'version': 1}``.
* ``release_manifest`` / ``release_artifact`` / ``dataset_release`` (owner decision 3, design F3): the manifest's format
  tag sits inside a frozen checksum, so the rows cannot be relabelled without breaking the checksum. They are
  **unpublished**: every manifest and artifact row is deleted, each published or withdrawn ``dataset_release`` goes back to
  ``draft`` (``published_at``, ``withdrawn_at``, ``withdrawn_reason`` cleared; ``doi`` and the curated ``release_selection``
  rows are kept) and the owner re-publishes. The three fused columns become ``*_name`` + ``*_version`` pairs.
* ``execution_environment_manifest``: ``schema_version`` ``'tckdb.execution-environment.v1'`` -> ``schema_name`` +
  int ``schema_version``; ``canonical_json`` is rewritten and ``content_digest`` recomputed with a **frozen copy** of the
  canonicaliser (never an import of application code). ``calculation.execution_environment_manifest_id`` is untouched.
* ``calculation.parameters_parser_version``, ``calculation_parameter.parser_version``, ``calc_hessian.parser_version``:
  split into ``*_name`` + text version. TCKDB's own stamps map to (``gaussian_parameters`` | ``orca_parameters`` |
  ``molpro_parameters`` | ``psi4_parameters`` | ``hessian``, ``'1'``); any other non-null stamp is a producer's own and is kept
  verbatim with the name ``producer``.
* ``external_source_record`` / ``external_source``: ThermoML parser and mapping stamps -> (``thermoml_cp`` /
  ``thermoml_cp_mapping``, ``'1'``) with a new ``mapping_name``; the ``.v1`` suffix leaves the rule ids inside
  ``mapping_report_json``; the depositor-upload ``source_release`` ``'v1'`` -> ``'1'``.
* ``molecular_property_observation``: the CCCBDB parser stamp -> (``cccbdb_experimental_species``, ``'1'``) with a new
  ``external_source_parser_name``; the same rule-id suffix leaves ``raw_payload_json``.
* ``conformer_assignment_scheme``: ``torsion_basin`` ``'v1'`` -> ``'1'`` and its content-derived ``public_ref`` recomputed
  from ``cas:name=torsion_basin;version=1;scope=canonical`` with a frozen copy of ``make_content_ref``.

The accepted-science freeze is bypassed, for this one revision only (owner decision 3, 2026-10-24)
-----------------------------------------------------------------------------------------------
Three of the rewritten tables are frozen by trigger: ``calculation``, ``calculation_parameter`` and ``calc_hessian`` are
under accepted-science immutability (``c6f2a9d4e7b1``), ``record_reproducibility_assessment``, ``release_manifest``,
``release_artifact`` and ``execution_environment_manifest`` are append-only or immutable. The relabel is a rewrite of
stored provenance stamps on approved calculations and of frozen documents; no scientific value changes (no energy,
frequency, geometry, correction or result is read or written). The owner decided that the stored data is rewritten freely
here, bypassing the freeze, with the bypass documented in this file and **no declared-repair machinery**
(``e2c9a4f7b163``) for it. The bypass is exactly this:

1. before any rewrite, every user trigger on the touched tables that is currently enabled is recorded and disabled by
   name (``ALTER TABLE ... DISABLE TRIGGER <name>``, transactional: the lock is held to the end of the revision, so
   no other session writes a table while its guard is off);
2. the rewrites run;
3. every trigger recorded in (1) is enabled again, and the revision then **raises** (rolling everything back) if any
   user trigger on a touched table is not enabled. A trigger left off cannot survive this revision.

``downgrade()`` runs the same bypass in reverse. It cannot restore what the upgrade deleted (the unpublished release
manifests and artifacts) and it refuses to run while a release manifest exists, because a manifest frozen after this
revision carries the new format header and cannot be reproduced under the old one. Orca ``orca_v1`` and ``orca_v2``
stamps both became (``orca_parameters``, ``'1'``) and downgrade to ``orca_v2``.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d4a9e2b6f1"
down_revision: Union[str, Sequence[str], None] = "e6b3a9d1f742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every table this revision writes. The bypass disables the user triggers of exactly these.
_TOUCHED_TABLES: tuple[str, ...] = (
    "record_machine_review",
    "record_reproducibility_assessment",
    "submission_audit_event",
    "release_manifest",
    "release_artifact",
    "dataset_release",
    "execution_environment_manifest",
    "calculation",
    "calculation_parameter",
    "calc_hessian",
    "external_source",
    "external_source_record",
    "molecular_property_observation",
    "conformer_assignment_scheme",
)

# Frozen tables of the rewrite (shapes as they are on disk when this revision runs; never imported from the app).
_ENV_NAME_OLD = "tckdb.execution-environment.v1"
_ENV_NAME_NEW = "tckdb.execution-environment"
_SCIENTIFIC_CHECK_PROVIDER = "tckdb.scientific_checks"

#: TCKDB's own parser stamps: old fused text -> (new name). The new version is always '1'.
_PARSER_OLD_TO_NEW: dict[str, str] = {
    "gaussian_v1": "gaussian_parameters",
    "orca_v1": "orca_parameters",
    "orca_v2": "orca_parameters",
    "molpro_v1": "molpro_parameters",
    "psi4-0.1.0": "psi4_parameters",
    "hessian_v1": "hessian",
}
#: The downgrade's inverse (the one old stamp each name goes back to).
_PARSER_NEW_TO_OLD: dict[str, str] = {
    "gaussian_parameters": "gaussian_v1",
    "orca_parameters": "orca_v2",
    "molpro_parameters": "molpro_v1",
    "psi4_parameters": "psi4-0.1.0",
    "hessian": "hessian_v1",
}
_PRODUCER = "producer"

#: ThermoML rule ids that carried ``.v1``; the suffix is removed from the stored reports.
_RULE_IDS = (
    "thermoml.mapping",
    "uncertainty.precedence",
    "origin.smethodname_allowlist",
    "pressure.kpa_to_bar",
    "state_basis.phase_map",
    "origin.prediction_type_map",
)
_THERMOML_PARSER_OLD = "thermoml-cp-parser/0.1.0"
_THERMOML_MAPPING_OLD = "thermoml-cp-mapping/0.1.0"
_CCCBDB_PARSER_OLD = "cccbdb-experimental-species-parser/0.1.0"

_REPRO_TOKENS = ("no_typed_result_contract", "selected_collection_requires_scientific_provenance")

#: Old fused release tags -> (name, version) and back.
_RELEASE_FORMAT_OLD = "tckdb.dataset_release.v2"
_ARCHIVE_FORMAT_OLD = "tckdb.archive.v1"
_REVIEW_POLICY_OLD = "record_review.v1"


# ---------------------------------------------------------------------------
# The documented freeze bypass
# ---------------------------------------------------------------------------


def _disable_guards(bind: sa.engine.Connection) -> dict[str, list[str]]:
    """Disable every enabled user trigger on the touched tables, returning what was disabled (see the docstring)."""
    saved: dict[str, list[str]] = {}
    for table in _TOUCHED_TABLES:
        names = [
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) "
                    "AND NOT tgisinternal AND tgenabled <> 'D' ORDER BY tgname"
                ),
                {"t": table},
            )
        ]
        for name in names:
            op.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER "{name}"')
        saved[table] = names
    return saved


def _enable_guards(bind: sa.engine.Connection, saved: dict[str, list[str]]) -> None:
    """Enable every trigger :func:`_disable_guards` disabled, then refuse to finish if any user trigger is still off."""
    for table, names in saved.items():
        for name in names:
            op.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER "{name}"')
    still_off = bind.execute(
        sa.text(
            "SELECT c.relname || '.' || t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND t.tgenabled = 'D' AND c.relname = ANY(:tables) ORDER BY 1"
        ),
        {"tables": list(_TOUCHED_TABLES)},
    ).scalars().all()
    if still_off:
        raise RuntimeError(
            "revision c7d4a9e2b6f1 would leave a guard trigger disabled: " + ", ".join(still_off)
            + ". Nothing was committed."
        )


# ---------------------------------------------------------------------------
# Frozen helpers (copies of application code as of this revision)
# ---------------------------------------------------------------------------


def _content_ref(prefix: str, canonical_identity: str) -> str:
    """Frozen copy of ``app.services.public_refs.make_content_ref`` (sha256, first 16 bytes, base32, 26 chars)."""
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).digest()[:16]
    return f"{prefix}_{base64.b32encode(digest).decode('ascii').lower().rstrip('=')[:26]}"


def _environment_digest(canonical: dict) -> str:
    """Frozen copy of ``ExecutionEnvironmentManifestPayload.content_digest`` over a canonical payload."""
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    _check_machine_review_rows(bind)
    saved = _disable_guards(bind)
    _upgrade_machine_review(bind)
    _upgrade_reproducibility(bind)
    _upgrade_audit_events(bind)
    _upgrade_releases(bind)
    _upgrade_execution_environments(bind)
    _upgrade_parser_stamps(bind)
    _upgrade_external_sources(bind)
    _upgrade_observations(bind)
    _upgrade_conformer_scheme(bind)
    _enable_guards(bind, saved)


def _check_machine_review_rows(bind: sa.engine.Connection) -> None:
    """Abort on the impossible states the currency rule says cannot exist."""
    bad = bind.execute(
        sa.text(
            "SELECT count(*) FROM record_machine_review WHERE CASE WHEN jsonb_typeof(rubric_versions_json) = 'object' "
            "THEN (SELECT count(*) FROM jsonb_object_keys(rubric_versions_json)) ELSE -1 END <> 1"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"{bad} record_machine_review row(s) hold a rubric_versions_json that is not exactly one key. A review is "
            "stamped with the one rubric that graded it, so this cannot happen; refusing to drop the extra keys "
            "silently. Inspect the rows and decide which rubric each was graded under before upgrading."
        )
    unparsable = bind.execute(
        sa.text(
            "SELECT count(*) FROM record_machine_review r, LATERAL jsonb_each_text(r.rubric_versions_json) AS kv "
            "WHERE CASE WHEN kv.value ~ '^[0-9]+$' THEN kv.value::numeric < 1 ELSE true END"
        )
    ).scalar()
    if unparsable:
        raise RuntimeError(
            f"{unparsable} record_machine_review row(s) hold a rubric version that is not a positive integer."
        )
    bad_schema = bind.execute(
        sa.text("SELECT count(*) FROM record_machine_review WHERE context_schema_version !~ '^v?[0-9]+$'")
    ).scalar()
    if bad_schema:
        raise RuntimeError(f"{bad_schema} record_machine_review row(s) hold a context_schema_version that is not vN.")


def _upgrade_machine_review(bind: sa.engine.Connection) -> None:
    op.add_column("record_machine_review", sa.Column("prompt_name", sa.String(128), nullable=True))
    op.add_column("record_machine_review", sa.Column("rubric_name", sa.String(128), nullable=True))
    op.add_column("record_machine_review", sa.Column("rubric_version", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE record_machine_review r SET
            rubric_name = regexp_replace(kv.key, '_v[0-9]+$', ''),
            rubric_version = kv.value::integer,
            prompt_name = CASE WHEN r.prompt_version ~ '^.+_v[0-9]+$'
                               THEN regexp_replace(r.prompt_version, '_v[0-9]+$', '')
                               ELSE r.prompt_version END
        FROM (SELECT id, (jsonb_each_text(rubric_versions_json)).* FROM record_machine_review) kv
        WHERE kv.id = r.id
        """
    )
    op.execute(
        "UPDATE record_machine_review SET model = regexp_replace(model, '_v[0-9]+$', '') "
        f"WHERE provider = '{_SCIENTIFIC_CHECK_PROVIDER}' AND model ~ '^external_cp_comparison_v[0-9]+$'"
    )
    op.execute(
        "ALTER TABLE record_machine_review ALTER COLUMN prompt_version TYPE integer USING ("
        "CASE WHEN prompt_version ~ '^.+_v[0-9]+$' THEN substring(prompt_version from '_v([0-9]+)$')::integer ELSE 1 END)"
    )
    op.execute(
        "ALTER TABLE record_machine_review ALTER COLUMN context_schema_version TYPE integer USING ("
        "substring(context_schema_version from '^v?([0-9]+)$')::integer)"
    )
    bad = bind.execute(
        sa.text(
            "SELECT count(*) FROM record_machine_review WHERE prompt_version < 1 OR rubric_version < 1 "
            "OR context_schema_version < 1"
        )
    ).scalar()
    if bad:
        raise RuntimeError(f"{bad} record_machine_review row(s) would carry a version below 1.")
    op.alter_column("record_machine_review", "prompt_name", nullable=False)
    op.alter_column("record_machine_review", "rubric_name", nullable=False)
    op.alter_column("record_machine_review", "rubric_version", nullable=False)
    op.drop_column("record_machine_review", "rubric_versions_json")
    op.create_check_constraint(
        op.f("ck_record_machine_review_versions_positive"),
        "record_machine_review",
        "context_schema_version >= 1 AND prompt_version >= 1 AND rubric_version >= 1",
    )


def _upgrade_reproducibility(bind: sa.engine.Connection) -> None:
    unparsable = bind.execute(
        sa.text("SELECT count(*) FROM record_reproducibility_assessment WHERE rubric_version !~ '^v?[0-9]+$'")
    ).scalar()
    if unparsable:
        raise RuntimeError(f"{unparsable} reproducibility assessment(s) hold a rubric_version that is not vN.")
    for token in _REPRO_TOKENS:
        for column in ("context_json", "missing_json", "passed_json", "warnings_json"):
            op.execute(
                f"UPDATE record_reproducibility_assessment SET {column} = "
                f"replace({column}::text, '{token}_v1', '{token}')::jsonb "
                f"WHERE {column}::text LIKE '%{token}_v1%'"
            )
    op.execute(
        """
        UPDATE record_reproducibility_assessment SET context_json = jsonb_set(
            context_json, '{rubric,version}',
            to_jsonb(substring(context_json #>> '{rubric,version}' from '^v?([0-9]+)$')::integer))
        WHERE jsonb_typeof(context_json #> '{rubric,version}') = 'string'
        """
    )
    op.execute(
        "ALTER TABLE record_reproducibility_assessment ALTER COLUMN rubric_version TYPE integer USING ("
        "substring(rubric_version from '^v?([0-9]+)$')::integer)"
    )


def _upgrade_audit_events(bind: sa.engine.Connection) -> None:
    op.execute(
        """
        UPDATE submission_audit_event SET details_json =
            (details_json - 'schema_version')
            || jsonb_build_object('format', jsonb_build_object('name', 'machine_review', 'version', 1))
        WHERE jsonb_typeof(details_json) = 'object' AND details_json ->> 'schema_version' = 'machine_review_v2'
        """
    )


def _upgrade_releases(bind: sa.engine.Connection) -> None:
    """Unpublish every release (design F3): the format tag is inside a frozen checksum, so nothing is relabelled."""
    op.execute("DELETE FROM release_artifact")
    op.execute("DELETE FROM release_manifest")
    op.execute(
        "UPDATE dataset_release SET status = 'draft', published_at = NULL, withdrawn_at = NULL, "
        "withdrawn_reason = NULL WHERE status IN ('published', 'withdrawn')"
    )
    op.add_column("release_manifest", sa.Column("manifest_format_name", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("manifest_format_version", sa.Integer(), nullable=False))
    op.add_column("release_manifest", sa.Column("review_policy_name", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("review_policy_version_int", sa.Integer(), nullable=False))
    op.add_column("release_manifest", sa.Column("recovery_archive_format_name", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("recovery_archive_format_version", sa.Integer(), nullable=False))
    op.drop_column("release_manifest", "manifest_schema")
    op.drop_column("release_manifest", "review_policy_version")
    op.drop_column("release_manifest", "recovery_archive_schema")
    op.alter_column("release_manifest", "review_policy_version_int", new_column_name="review_policy_version")


def _upgrade_execution_environments(bind: sa.engine.Connection) -> None:
    op.add_column("execution_environment_manifest", sa.Column("schema_name", sa.String(64), nullable=True))
    op.execute("ALTER TABLE execution_environment_manifest ADD COLUMN schema_version_int integer")
    rows = bind.execute(
        sa.text("SELECT id, schema_version, canonical_json FROM execution_environment_manifest ORDER BY id")
    ).all()
    for row_id, schema_version, canonical in rows:
        if schema_version != _ENV_NAME_OLD:
            raise RuntimeError(
                f"execution_environment_manifest {row_id} has schema_version {schema_version!r}; this revision knows "
                f"only {_ENV_NAME_OLD!r}."
            )
        canonical = dict(canonical)
        canonical.pop("schema_version", None)
        canonical["schema_name"] = _ENV_NAME_NEW
        canonical["schema_version"] = 1
        bind.execute(
            sa.text(
                "UPDATE execution_environment_manifest SET schema_name = :n, schema_version_int = 1, "
                "canonical_json = CAST(:c AS jsonb), content_digest = :d WHERE id = :id"
            ),
            {"n": _ENV_NAME_NEW, "c": json.dumps(canonical), "d": _environment_digest(canonical), "id": row_id},
        )
    op.alter_column("execution_environment_manifest", "schema_name", nullable=False)
    op.alter_column("execution_environment_manifest", "schema_version_int", nullable=False)
    op.drop_column("execution_environment_manifest", "schema_version")
    op.alter_column("execution_environment_manifest", "schema_version_int", new_column_name="schema_version")


def _upgrade_parser_stamps(bind: sa.engine.Connection) -> None:
    for table, column, name_column in (
        ("calculation", "parameters_parser_version", "parameters_parser_name"),
        ("calculation_parameter", "parser_version", "parser_name"),
        ("calc_hessian", "parser_version", "parser_name"),
    ):
        op.add_column(table, sa.Column(name_column, sa.Text(), nullable=True))
        for old, new in _PARSER_OLD_TO_NEW.items():
            op.execute(
                sa.text(
                    f"UPDATE {table} SET {name_column} = :n, {column} = '1' WHERE {column} = :o"
                ).bindparams(n=new, o=old)
            )
        # A stamp that is none of TCKDB's own is a producer's: kept verbatim, named for what it is.
        op.execute(
            sa.text(
                f"UPDATE {table} SET {name_column} = :p WHERE {column} IS NOT NULL AND {name_column} IS NULL"
            ).bindparams(p=_PRODUCER)
        )


def _strip_rule_suffix_sql(column: str) -> str:
    expr = f"{column}::text"
    for rule in _RULE_IDS:
        expr = f"replace({expr}, '{rule}.v1', '{rule}')"
    expr = f"replace({expr}, '{_THERMOML_PARSER_OLD}', '1')"
    expr = f"replace({expr}, '{_THERMOML_MAPPING_OLD}', '1')"
    return f"({expr})::jsonb"


def _upgrade_external_sources(bind: sa.engine.Connection) -> None:
    op.add_column("external_source_record", sa.Column("mapping_name", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE external_source_record SET mapping_name = 'thermoml_cp_mapping', mapping_version = '1' "
            "WHERE mapping_version = :m"
        ).bindparams(m=_THERMOML_MAPPING_OLD)
    )
    op.execute(
        sa.text(
            "UPDATE external_source_record SET parser_name = 'thermoml_cp', parser_version = '1' "
            "WHERE parser_version = :p"
        ).bindparams(p=_THERMOML_PARSER_OLD)
    )
    op.execute(
        "UPDATE external_source_record SET mapping_name = 'thermoml_cp_mapping' WHERE mapping_name IS NULL"
    )
    op.execute(
        "UPDATE external_source_record SET mapping_report_json = "
        f"{_strip_rule_suffix_sql('mapping_report_json')} "
        "WHERE mapping_report_json IS NOT NULL AND mapping_report_json::text LIKE '%thermoml%'"
    )
    op.execute(
        "UPDATE external_source_record SET mapping_report_json = mapping_report_json || "
        "jsonb_build_object('parser_name', parser_name, 'mapping_name', mapping_name) "
        "WHERE jsonb_typeof(mapping_report_json) = 'object' AND mapping_report_json ? 'mapping_version'"
    )
    op.alter_column("external_source_record", "mapping_name", nullable=False)
    op.execute(
        "UPDATE external_source SET source_release = '1' "
        "WHERE source_name = 'TCKDB depositor ThermoML upload' AND source_release = 'v1'"
    )


def _upgrade_observations(bind: sa.engine.Connection) -> None:
    op.add_column("molecular_property_observation", sa.Column("external_source_parser_name", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE molecular_property_observation SET external_source_parser_name = 'cccbdb_experimental_species', "
            "external_source_parser_version = '1' WHERE external_source_parser_version = :p"
        ).bindparams(p=_CCCBDB_PARSER_OLD)
    )
    op.execute(
        "UPDATE molecular_property_observation SET raw_payload_json = "
        f"{_strip_rule_suffix_sql('raw_payload_json')} "
        "WHERE raw_payload_json IS NOT NULL AND raw_payload_json::text LIKE '%thermoml%'"
    )


def _upgrade_conformer_scheme(bind: sa.engine.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, name, scope::text FROM conformer_assignment_scheme "
            "WHERE name = 'torsion_basin' AND version = 'v1'"
        )
    ).all()
    for row_id, name, scope in rows:
        ref = _content_ref("cas", f"cas:name={name.strip().lower()};version=1;scope={scope}")
        bind.execute(
            sa.text("UPDATE conformer_assignment_scheme SET version = '1', public_ref = :r WHERE id = :id"),
            {"r": ref, "id": row_id},
        )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Reverse the split. Cannot restore the release manifests and artifacts the upgrade deleted (see the docstring)."""
    bind = op.get_bind()
    manifests = bind.execute(sa.text("SELECT count(*) FROM release_manifest")).scalar()
    if manifests:
        raise RuntimeError(
            "Refusing to downgrade c7d4a9e2b6f1: a release manifest exists. It was frozen after this revision, so its "
            "document carries the new format header and cannot be reproduced under the old tags. Export it first."
        )
    saved = _disable_guards(bind)
    _downgrade_conformer_scheme(bind)
    _downgrade_observations(bind)
    _downgrade_external_sources(bind)
    _downgrade_parser_stamps(bind)
    _downgrade_execution_environments(bind)
    _downgrade_releases(bind)
    _downgrade_audit_events(bind)
    _downgrade_reproducibility(bind)
    _downgrade_machine_review(bind)
    _enable_guards(bind, saved)


def _downgrade_conformer_scheme(bind: sa.engine.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, name, scope::text FROM conformer_assignment_scheme "
            "WHERE name = 'torsion_basin' AND version = '1'"
        )
    ).all()
    for row_id, name, scope in rows:
        ref = _content_ref("cas", f"cas:name={name.strip().lower()};version=v1;scope={scope}")
        bind.execute(
            sa.text("UPDATE conformer_assignment_scheme SET version = 'v1', public_ref = :r WHERE id = :id"),
            {"r": ref, "id": row_id},
        )


def _restore_rule_suffix_sql(column: str) -> str:
    expr = f"{column}::text"
    for rule in _RULE_IDS:
        expr = f"replace({expr}, '\"{rule}\"', '\"{rule}.v1\"')"
    return f"({expr})::jsonb"


def _downgrade_observations(bind: sa.engine.Connection) -> None:
    op.execute(
        "UPDATE molecular_property_observation SET raw_payload_json = "
        f"{_restore_rule_suffix_sql('raw_payload_json')} "
        "WHERE raw_payload_json IS NOT NULL AND raw_payload_json::text LIKE '%thermoml.mapping%'"
    )
    op.execute(
        sa.text(
            "UPDATE molecular_property_observation SET external_source_parser_version = :p "
            "WHERE external_source_parser_name = 'cccbdb_experimental_species'"
        ).bindparams(p=_CCCBDB_PARSER_OLD)
    )
    op.drop_column("molecular_property_observation", "external_source_parser_name")


def _downgrade_external_sources(bind: sa.engine.Connection) -> None:
    op.execute(
        sa.text(
            "UPDATE external_source_record SET mapping_report_json = jsonb_set(jsonb_set("
            "(mapping_report_json - 'parser_name' - 'mapping_name'), '{parser_version}', to_jsonb(CAST(:p AS text))), "
            "'{mapping_version}', to_jsonb(CAST(:m AS text))) "
            "WHERE jsonb_typeof(mapping_report_json) = 'object' AND mapping_report_json ? 'mapping_name'"
        ).bindparams(p=_THERMOML_PARSER_OLD, m=_THERMOML_MAPPING_OLD)
    )
    op.execute(
        "UPDATE external_source_record SET mapping_report_json = "
        f"{_restore_rule_suffix_sql('mapping_report_json')} "
        "WHERE mapping_report_json IS NOT NULL AND mapping_report_json::text LIKE '%thermoml.mapping%'"
    )
    op.execute(
        "UPDATE external_source SET source_release = 'v1' "
        "WHERE source_name = 'TCKDB depositor ThermoML upload' AND source_release = '1'"
    )
    op.execute(
        sa.text(
            "UPDATE external_source_record SET parser_name = 'thermoml', parser_version = :p "
            "WHERE parser_name = 'thermoml_cp'"
        ).bindparams(p=_THERMOML_PARSER_OLD)
    )
    op.execute(
        sa.text(
            "UPDATE external_source_record SET mapping_version = :m WHERE mapping_name = 'thermoml_cp_mapping'"
        ).bindparams(m=_THERMOML_MAPPING_OLD)
    )
    op.drop_column("external_source_record", "mapping_name")


def _downgrade_parser_stamps(bind: sa.engine.Connection) -> None:
    for table, column, name_column in (
        ("calculation", "parameters_parser_version", "parameters_parser_name"),
        ("calculation_parameter", "parser_version", "parser_name"),
        ("calc_hessian", "parser_version", "parser_name"),
    ):
        for new, old in _PARSER_NEW_TO_OLD.items():
            op.execute(
                sa.text(f"UPDATE {table} SET {column} = :o WHERE {name_column} = :n").bindparams(o=old, n=new)
            )
        op.drop_column(table, name_column)


def _downgrade_execution_environments(bind: sa.engine.Connection) -> None:
    op.execute("ALTER TABLE execution_environment_manifest ADD COLUMN schema_version_text varchar(32)")
    rows = bind.execute(sa.text("SELECT id, canonical_json FROM execution_environment_manifest ORDER BY id")).all()
    for row_id, canonical in rows:
        canonical = dict(canonical)
        canonical.pop("schema_name", None)
        canonical["schema_version"] = _ENV_NAME_OLD
        bind.execute(
            sa.text(
                "UPDATE execution_environment_manifest SET schema_version_text = :v, "
                "canonical_json = CAST(:c AS jsonb), content_digest = :d WHERE id = :id"
            ),
            {"v": _ENV_NAME_OLD, "c": json.dumps(canonical), "d": _environment_digest(canonical), "id": row_id},
        )
    op.alter_column("execution_environment_manifest", "schema_version_text", nullable=False)
    op.drop_column("execution_environment_manifest", "schema_version")
    op.drop_column("execution_environment_manifest", "schema_name")
    op.alter_column("execution_environment_manifest", "schema_version_text", new_column_name="schema_version")


def _downgrade_releases(bind: sa.engine.Connection) -> None:
    """The columns come back; the unpublished rows do not (the table is empty here: see :func:`downgrade`)."""
    op.add_column("release_manifest", sa.Column("manifest_schema", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("review_policy_version_text", sa.String(64), nullable=False))
    op.add_column("release_manifest", sa.Column("recovery_archive_schema", sa.String(64), nullable=False))
    op.drop_column("release_manifest", "manifest_format_name")
    op.drop_column("release_manifest", "manifest_format_version")
    op.drop_column("release_manifest", "review_policy_name")
    op.drop_column("release_manifest", "review_policy_version")
    op.drop_column("release_manifest", "recovery_archive_format_name")
    op.drop_column("release_manifest", "recovery_archive_format_version")
    op.alter_column("release_manifest", "review_policy_version_text", new_column_name="review_policy_version")


def _downgrade_audit_events(bind: sa.engine.Connection) -> None:
    op.execute(
        """
        UPDATE submission_audit_event SET details_json =
            (details_json - 'format') || jsonb_build_object('schema_version', 'machine_review_v2')
        WHERE jsonb_typeof(details_json) = 'object'
          AND details_json #>> '{format,name}' = 'machine_review' AND details_json #>> '{format,version}' = '1'
        """
    )


def _downgrade_reproducibility(bind: sa.engine.Connection) -> None:
    op.execute(
        "ALTER TABLE record_reproducibility_assessment ALTER COLUMN rubric_version TYPE varchar(64) USING "
        "('v' || rubric_version::text)"
    )
    op.execute(
        """
        UPDATE record_reproducibility_assessment SET context_json = jsonb_set(
            context_json, '{rubric,version}', to_jsonb('v' || (context_json #>> '{rubric,version}')))
        WHERE jsonb_typeof(context_json #> '{rubric,version}') = 'number'
        """
    )
    for token in _REPRO_TOKENS:
        for column in ("context_json", "missing_json", "passed_json", "warnings_json"):
            op.execute(
                f"UPDATE record_reproducibility_assessment SET {column} = "
                f"replace({column}::text, '\"{token}\"', '\"{token}_v1\"')::jsonb "
                f"WHERE {column}::text LIKE '%\"{token}\"%'"
            )


def _downgrade_machine_review(bind: sa.engine.Connection) -> None:
    op.drop_constraint(op.f("ck_record_machine_review_versions_positive"), "record_machine_review", type_="check")
    op.add_column("record_machine_review", sa.Column("rubric_versions_json", sa.dialects.postgresql.JSONB(), nullable=True))
    op.execute(
        "UPDATE record_machine_review SET rubric_versions_json = "
        "jsonb_build_object(rubric_name || '_v' || rubric_version::text, rubric_version::text)"
    )
    op.alter_column("record_machine_review", "rubric_versions_json", nullable=False)
    op.execute(
        "ALTER TABLE record_machine_review ALTER COLUMN context_schema_version TYPE varchar(32) USING "
        "('v' || context_schema_version::text)"
    )
    op.execute(
        "ALTER TABLE record_machine_review ALTER COLUMN prompt_version TYPE varchar(64) USING "
        "(CASE WHEN provider = '" + _SCIENTIFIC_CHECK_PROVIDER + "' AND prompt_name <> 'external_cp_comparison' "
        "THEN prompt_name ELSE prompt_name || '_v' || prompt_version::text END)"
    )
    op.execute(
        "UPDATE record_machine_review SET model = model || '_v1' "
        f"WHERE provider = '{_SCIENTIFIC_CHECK_PROVIDER}' AND model = 'external_cp_comparison'"
    )
    op.drop_column("record_machine_review", "prompt_name")
    op.drop_column("record_machine_review", "rubric_name")
    op.drop_column("record_machine_review", "rubric_version")
