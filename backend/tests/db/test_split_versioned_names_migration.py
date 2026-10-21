"""Disposable-database contract for ``c7d4a9e2b6f1`` (versioning PR-4: split every fused name-and-version string).

The revision rewrites stored provenance in fourteen tables, three of them behind accepted-science immutability and
three more append-only or immutable, and does it with every guard trigger switched off. What has to be checkable on a
real database and not merely on an empty one:

1. **Every old shape comes out split.** The seed holds one row of every fused shape the code used to write (a reviewer
   review, a transition-state review, an external-Cp review, a consistency-runner review, the fake test provider's
   review, a published and a withdrawn release, an execution-environment manifest, the TCKDB parser stamps and a
   producer's own, a ThermoML source record, CCCBDB and producer-stamped observations, conformer schemes). Each is
   asserted by name and integer, so one shape the revision forgets leaves one fused string and fails here.
2. **The bypass is real, bounded and undone.** The seeded calculation is *approved*, so the freeze is genuinely on at
   the parent revision. Afterwards every trigger the revision disabled is back in the exact mode it had (ENABLE,
   ENABLE ALWAYS or ENABLE REPLICA), and a trigger that was already disabled before the upgrade is left as it was.
3. **It aborts on what it cannot split cleanly.** A review stamped with two rubrics, a fused name the revision does not
   know, a look-alike parser stamp, a source record that is not ThermoML, a conformer scheme with a free-text version:
   each makes the upgrade fail, name the rows, and leave the database at the parent revision untouched.
4. **Downgrade round-trips exactly.** upgrade -> downgrade restores the exact old strings the seed wrote, including
   ``orca_v1`` (kept distinct from ``orca_v2`` as version ``'0'``), ``custom-prompt`` and ``runner_x_v3``. The one
   documented exception is the unpublished release manifests and artifacts, which do not come back.
5. **Things that must not move do not.** ``token_v10`` is not ``token_v1``, a withdrawn release stays withdrawn with its
   reason, and ``raw_payload_json`` (verbatim upstream data) is never rewritten.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("c7d4a9e2b6f1")

_TOUCHED = (
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

_SC = "tckdb.scientific_checks"


class _Harness:
    """Create a throwaway database and drive alembic against it."""

    def __init__(self, name_prefix: str):
        from conftest import _database_url, _db_env, scratch_database_name

        self.db_name = scratch_database_name(name_prefix)
        self.env = _db_env(self.db_name)
        self._database_url = _database_url
        self._admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        self._admin_conn = self._admin.connect()
        self._admin_conn.execute(text(f'CREATE DATABASE "{self.db_name}"'))
        self.engine = None
        self.root = Path(__file__).resolve().parents[2]

    def run(self, direction: str, revision: str, *, check: bool = True):
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
            cwd=self.root,
            env=self.env,
            check=check,
            capture_output=True,
            text=True,
        )
        self.engine = create_engine(self._database_url(self.db_name), pool_pre_ping=True)
        return completed

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        try:
            self._admin_conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
                {"name": self.db_name},
            )
            self._admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{self.db_name}"'))
        finally:
            self._admin_conn.close()
            self._admin.dispose()


@pytest.fixture
def harness():
    created = _Harness("split_versioned_names")
    yield created
    created.close()


def _env_digest(canonical: dict) -> str:
    """The digest the application computes (a frozen copy, so the test does not import what it tests)."""
    import hashlib

    return "sha256:" + hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_OLD_ENV = {
    "schema_version": "tckdb.execution-environment.v1",
    "runtime": {"runtime_kind": "described", "description": "site install", "modules": [{"name": "gaussian", "version": "16"}]},
    "software_release": {"name": "Gaussian", "version": "16"},
    "workflow_tool_release": None,
    "executable": {"locator": "file:///opt/g16/g16", "digest": None},
    "closure": [],
}


def _user(conn) -> int:
    return conn.scalar(
        text("INSERT INTO app_user (username, role, is_active) VALUES ('split-curator', 'curator', true) RETURNING id")
    )


def _review(conn, *, model, provider, prompt, rubrics, record_id, schema="v1"):
    return conn.scalar(
        text(
            "INSERT INTO record_machine_review (record_type, record_id, status, context_hash, "
            "context_schema_version, prompt_version, rubric_versions_json, model, provider, reviewed_at) "
            "VALUES ('thermo', :rid, 'machine_screened_pass', :h, :s, :p, CAST(:r AS jsonb), :m, :pr, now()) "
            "RETURNING id"
        ),
        {"rid": record_id, "h": "a" * 64, "s": schema, "p": prompt, "r": json.dumps(rubrics), "m": model, "pr": provider},
    )


def _entry(conn) -> int:
    species = conn.scalar(
        text(
            "INSERT INTO species (inchi_key, smiles, kind, charge, multiplicity, stereo_kind, public_ref) VALUES "
            "('AAAAAAAAAAAAAA-AAAAAAAAAA-N', '[H]', 'molecule', 0, 2, 'unspecified', 'spc_split') RETURNING id"
        )
    )
    return conn.scalar(text("INSERT INTO species_entry (species_id, public_ref) VALUES (:s, 'spe_split') RETURNING id"), {"s": species})


def _calc(conn, entry, stamp) -> int:
    return conn.scalar(
        text("INSERT INTO calculation (type, species_entry_id, parameters_parser_version) VALUES ('opt', :e, :v) RETURNING id"),
        {"e": entry, "v": stamp},
    )


def _old_manifest(conn, release, policy, *, suffix: str) -> int:
    return conn.scalar(
        text(
            "INSERT INTO release_manifest (dataset_release_id, manifest_schema, alembic_revision, backend_version, "
            "schemas_package_version, review_policy_version, curation_policy_id, recovery_archive_schema, release_public_ref, "
            "release_tag, release_title, release_contact, release_status_at_publication, data_license, code_license, "
            "citation_text, curation_policy_ref, curation_policy_name, curation_policy_version, curation_policy_description, "
            "contract_json, document_json, content_sha256, selected_record_count, candidate_record_count, public_ref) VALUES "
            "(:r, 'tckdb.dataset_release.v2', 'rev', 'b', 's', 'record_review.v1', :p, 'tckdb.archive.v1', :ref, "
            "'2026.10.0', 't', 'x@y', 'published', 'CC-BY-4.0', 'MIT', 'c', 'cpol_split', 'p', '1', 'd', '{}'::jsonb, "
            "'{\"schema\": \"tckdb.dataset_release.v2\"}'::jsonb, :sha, 1, 1, :mref) RETURNING id"
        ),
        {"r": release, "p": policy, "sha": suffix * 64, "ref": f"drel_{suffix}", "mref": f"rman_{suffix}"},
    )


def _seed(conn) -> dict[str, int]:
    """One row of every old fused shape, written as raw SQL at the parent revision."""
    ids: dict[str, int] = {}
    user = _user(conn)
    ids["user"] = user

    ids["mr_reviewer"] = _review(
        conn, model="fake_test/machine-review", provider=None, prompt="machine_review_v1", rubrics={"computed_thermo_v2": "2"}, record_id=1
    )
    ids["mr_ts"] = _review(
        conn, model=None, provider=None, prompt="machine_review_v1", rubrics={"computed_transition_state_v5": "5"}, record_id=2
    )
    ids["mr_cp"] = _review(
        conn, model="external_cp_comparison_v1", provider=_SC, prompt="external_cp_comparison_v1",
        rubrics={"external_cp_comparison_v2": "2"}, record_id=3,
    )
    ids["mr_check"] = _review(
        conn, model="thermo_consistency", provider=_SC, prompt="thermo_consistency", rubrics={"thermo_consistency_v1": "1"}, record_id=4
    )
    # The test-only fake provider: its model string used to end -v2.
    ids["mr_fake"] = _review(
        conn, model="fake_test/machine-review-v2", provider="FakeMachineReviewProvider", prompt="machine_review_v1",
        rubrics={"computed_thermo_v2": "2"}, record_id=5,
    )
    # A runner prompt that never carried a version: stored at 0 (an old stamp), given back as written.
    ids["mr_custom"] = _review(
        conn, model="custom-prompt", provider=_SC, prompt="custom-prompt", rubrics={"thermo_consistency_v1": "1"}, record_id=6
    )
    # An OLDER version of a rubric whose latest was 2: stored at 0, and given back as exactly version 1 (the only older one).
    ids["mr_older_thermo"] = _review(
        conn, model="fake_test/machine-review", provider=None, prompt="machine_review_v1", rubrics={"computed_thermo_v1": "1"}, record_id=10
    )

    def repro(record_id, note):
        return conn.scalar(
            text(
                "INSERT INTO record_reproducibility_assessment (record_type, record_id, grade, rubric_name, rubric_version, "
                "context_hash, context_json, missing_json, assessor_kind, assessed_at) "
                "VALUES ('thermo', :rid, 'described', 'tckdb_reproducibility', 'v1', :h, CAST(:c AS jsonb), CAST(:m AS jsonb), "
                "'system', now()) RETURNING id"
            ),
            {
                "rid": record_id,
                "h": f"{record_id:x}" * 64 if record_id < 16 else "b" * 64,
                "c": json.dumps({"rubric": {"name": "tckdb_reproducibility", "version": "v1"}, "note": note}),
                "m": json.dumps(["selected_collection_requires_scientific_provenance_v1"]),
            },
        )

    ids["repro"] = repro(7, "no_typed_result_contract_v1")
    # ``token_v10`` is a different token from ``token_v1``: the rewrite must not touch it.
    ids["repro_v10"] = repro(9, "no_typed_result_contract_v10")

    submission = conn.scalar(
        text("INSERT INTO submission (created_by, submission_kind) VALUES (:u, 'computed_species') RETURNING id"), {"u": user}
    )
    ids["audit_marked"] = conn.scalar(
        text(
            "INSERT INTO submission_audit_event (submission_id, event_kind, actor_kind, details_json) VALUES "
            "(:s, 'llm_precheck_recorded', 'llm', CAST(:d AS jsonb)) RETURNING id"
        ),
        {"s": submission, "d": json.dumps({"schema_version": "machine_review_v2", "status": "machine_screened_pass"})},
    )
    ids["audit_plain"] = conn.scalar(
        text(
            "INSERT INTO submission_audit_event (submission_id, event_kind, actor_kind, details_json) VALUES "
            "(:s, 'submission_created', 'system', CAST(:d AS jsonb)) RETURNING id"
        ),
        {"s": submission, "d": json.dumps({"note": "no marker"})},
    )

    policy = conn.scalar(
        text(
            "INSERT INTO curation_policy (name, version, description, public_ref) VALUES ('p', '1', 'd', 'cpol_split') RETURNING id"
        )
    )
    release = conn.scalar(
        text(
            "INSERT INTO dataset_release (tag, title, status, curation_policy_id, data_license, code_license, citation_text, "
            "contact, published_at, public_ref, doi) VALUES ('2026.10.0', 't', 'published', :p, 'CC-BY-4.0', 'MIT', 'c', 'x@y', "
            "now(), 'drel_split', '10.1/abc') RETURNING id"
        ),
        {"p": policy},
    )
    ids["release"] = release
    ids["selection"] = conn.scalar(
        text(
            "INSERT INTO release_selection (dataset_release_id, curation_policy_id, record_type, record_id, subject_type, "
            "subject_id, rationale, selected_by, public_ref) VALUES (:r, :p, 'thermo', 1, 'species_entry', 1, 'why', :u, 'rsel_split') "
            "RETURNING id"
        ),
        {"r": release, "p": policy, "u": user},
    )
    manifest = conn.scalar(
        text(
            "INSERT INTO release_manifest (dataset_release_id, manifest_schema, alembic_revision, backend_version, "
            "schemas_package_version, review_policy_version, curation_policy_id, recovery_archive_schema, release_public_ref, "
            "release_tag, release_title, release_contact, release_status_at_publication, data_license, code_license, "
            "citation_text, curation_policy_ref, curation_policy_name, curation_policy_version, curation_policy_description, "
            "contract_json, document_json, content_sha256, selected_record_count, candidate_record_count, public_ref) VALUES "
            "(:r, 'tckdb.dataset_release.v2', 'rev', 'b', 's', 'record_review.v1', :p, 'tckdb.archive.v1', 'drel_split', "
            "'2026.10.0', 't', 'x@y', 'published', 'CC-BY-4.0', 'MIT', 'c', 'cpol_split', 'p', '1', 'd', '{}'::jsonb, "
            "'{\"schema\": \"tckdb.dataset_release.v2\"}'::jsonb, :sha, 1, 1, 'rman_split') RETURNING id"
        ),
        {"r": release, "p": policy, "sha": "c" * 64},
    )
    ids["manifest"] = manifest
    conn.execute(
        text(
            "INSERT INTO release_artifact (release_manifest_id, kind, path, media_type, content, sha256, byte_count, record_count) "
            "VALUES (:m, 'selected_records', 'selected_records.ndjson', 'application/x-ndjson', '\\x7b7d'::bytea, :sha, 2, 0)"
        ),
        {"m": manifest, "sha": "d" * 64},
    )
    # A withdrawn release: its curator-recorded withdrawal must survive the unpublish of its manifest.
    ids["withdrawn"] = conn.scalar(
        text(
            "INSERT INTO dataset_release (tag, title, status, curation_policy_id, data_license, code_license, citation_text, "
            "contact, published_at, withdrawn_at, withdrawn_reason, public_ref, doi) VALUES ('2026.09.0', 't', 'withdrawn', :p, "
            "'CC-BY-4.0', 'MIT', 'c', 'x@y', '2026-09-01 00:00:00', '2026-09-15 12:00:00', 'a mislabelled set', 'drel_wd', '10.1/wd') "
            "RETURNING id"
        ),
        {"p": policy},
    )
    _old_manifest(conn, ids["withdrawn"], policy, suffix="e")

    software = conn.scalar(text("INSERT INTO software (name) VALUES ('gaussian') RETURNING id"))
    release_row = conn.scalar(text("INSERT INTO software_release (software_id) VALUES (:s) RETURNING id"), {"s": software})
    ids["env"] = conn.scalar(
        text(
            "INSERT INTO execution_environment_manifest (schema_version, content_digest, runtime_kind, runtime_locator, "
            "executable_locator, closure_json, canonical_json, software_release_id) VALUES "
            "('tckdb.execution-environment.v1', :d, 'described', 'module://gaussian/16', 'file:///opt/g16/g16', '[]'::jsonb, "
            "CAST(:c AS jsonb), :sr) RETURNING id"
        ),
        {"d": _env_digest(_OLD_ENV), "c": json.dumps(_OLD_ENV), "sr": release_row},
    )

    entry = _entry(conn)
    geometry = conn.scalar(text("INSERT INTO geometry (geom_hash, natoms) VALUES (:h, 1) RETURNING id"), {"h": "e" * 64})

    ids["calc_frozen"] = _calc(conn, entry, "gaussian_v1")
    ids["calc_orca1"] = _calc(conn, entry, "orca_v1")
    ids["calc_orca2"] = _calc(conn, entry, "orca_v2")
    ids["calc_psi4"] = _calc(conn, entry, "psi4-0.1.0")
    ids["calc_molpro"] = _calc(conn, entry, "molpro_v1")
    ids["calc_producer"] = _calc(conn, entry, "acme-reader 4.2")
    ids["calc_none"] = _calc(conn, entry, None)
    # Ordinary free-text producer stamps that happen to start with a TCKDB parser family. They were legal on main, one of
    # them sits on an APPROVED (frozen) calculation, and the operator cannot edit that row: the upgrade must carry them
    # through as producer stamps, never refuse them.
    ids["calc_gaussian16"] = _calc(conn, entry, "Gaussian 16")
    ids["calc_orca504"] = _calc(conn, entry, "orca 5.0.4")
    ids["calc_psi4_191"] = _calc(conn, entry, "psi4-1.9.1")
    ids["calc_gaussian16_compact"] = _calc(conn, entry, "gaussian16")
    ids["calc_molpro2024"] = _calc(conn, entry, "molpro2024.1")
    ids["param"] = conn.scalar(
        text(
            "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, parser_version) VALUES "
            "(:c, 'k', 'v', 'parser', 'gaussian_v1') RETURNING id"
        ),
        {"c": ids["calc_frozen"]},
    )
    ids["hessian"] = conn.scalar(
        text(
            "INSERT INTO calc_hessian (calculation_id, geometry_id, natoms, lower_triangle_hartree_bohr2, source, parser_version) "
            "VALUES (:c, :g, 1, ARRAY[0,0,0,0,0,0]::float8[], 'parsed_log', 'hessian_v1') RETURNING calculation_id"
        ),
        {"c": ids["calc_frozen"], "g": geometry},
    )
    # The freeze: an approved calculation is immutable, which is what makes the bypass necessary.
    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
            "VALUES ('calculation', :c, 'approved', :u, now(), now())"
        ),
        {"c": ids["calc_frozen"], "u": user},
    )

    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
            "VALUES ('calculation', :c, 'approved', :u, now(), now())"
        ),
        {"c": ids["calc_gaussian16"], "u": user},
    )

    source = conn.scalar(
        text(
            "INSERT INTO external_source (source_name, source_release) VALUES ('TCKDB depositor ThermoML upload', 'v1') RETURNING id"
        )
    )
    ids["esr"] = conn.scalar(
        text(
            "INSERT INTO external_source_record (external_source_id, record_kind, source_uri, source_record_key, retrieved_at, "
            "content_sha256, content_length, raw_uri, parser_name, parser_version, mapping_version, mapping_report_json) VALUES "
            "(:s, 'thermoml_article', 'u', 'k', now(), :h, 1, 'r', 'thermoml', 'thermoml-cp-parser/0.1.0', "
            "'thermoml-cp-mapping/0.1.0', CAST(:j AS jsonb)) RETURNING id"
        ),
        {
            "s": source,
            "h": "f" * 64,
            "j": json.dumps(
                {
                    "schema": "thermoml.mapping.v1",
                    "parser_version": "thermoml-cp-parser/0.1.0",
                    "mapping_version": "thermoml-cp-mapping/0.1.0",
                    # ``.v10`` is not ``.v1``: it must come through untouched.
                    "transformed": ["pressure.kpa_to_bar.v1", "pressure.kpa_to_bar.v10", "unit.j_mol_k.identity"],
                }
            ),
        },
    )

    def observation(stamp, payload, value=1.0):
        return conn.scalar(
            text(
                "INSERT INTO molecular_property_observation (property_kind, scientific_origin, scalar_value, scalar_unit, "
                "external_source_parser_version, raw_payload_json) VALUES ('dipole_moment', 'experimental', :x, 'D', :v, "
                "CAST(:j AS jsonb)) RETURNING id"
            ),
            {"v": stamp, "j": json.dumps(payload), "x": value},
        )

    # ``raw_payload_json`` is verbatim upstream data: it is never rewritten, whatever it happens to contain.
    ids["obs"] = observation(
        "cccbdb-experimental-species-parser/0.1.0",
        {"mapping": {"schema": "thermoml.mapping.v1", "rules": ["origin.prediction_type_map.v1"]}},
    )
    ids["obs_producer"] = observation("acme-obs 1.0", {"note": "producer"}, 2.0)
    ids["obs_cccbdb_scrape"] = observation("CCCBDB scrape 2024", {"note": "a depositor's own CCCBDB scrape"}, 3.0)

    # The seeded ``torsion_basin`` scheme (``v1``) is already there; add a numeric-text one.
    ids["scheme_extra"] = conn.scalar(
        text("INSERT INTO conformer_assignment_scheme (name, version, scope, public_ref) VALUES ('extra', '2', 'canonical', 'cas_extra') RETURNING id")
    )
    return ids


def _snapshot(conn) -> dict[str, list]:
    """The old-shape columns the seed wrote, in a stable order."""
    queries = {
        "machine_review": "SELECT id, model, provider, context_schema_version, prompt_version, rubric_versions_json "
        "FROM record_machine_review ORDER BY id",
        "repro": "SELECT id, rubric_version, context_json, missing_json FROM record_reproducibility_assessment ORDER BY id",
        "audit": "SELECT id, details_json FROM submission_audit_event ORDER BY id",
        "env": "SELECT id, schema_version, content_digest, canonical_json FROM execution_environment_manifest ORDER BY id",
        "calc": "SELECT id, parameters_parser_version FROM calculation ORDER BY id",
        "param": "SELECT id, parser_version FROM calculation_parameter ORDER BY id",
        "hessian": "SELECT calculation_id, parser_version FROM calc_hessian ORDER BY calculation_id",
        "esr": "SELECT id, parser_name, parser_version, mapping_version, mapping_report_json FROM external_source_record ORDER BY id",
        "source": "SELECT id, source_release FROM external_source ORDER BY id",
        "obs": "SELECT id, external_source_parser_version, raw_payload_json FROM molecular_property_observation ORDER BY id",
        "scheme": "SELECT id, version::text, public_ref FROM conformer_assignment_scheme ORDER BY id",
    }
    return {name: [tuple(r) for r in conn.execute(text(q)).all()] for name, q in queries.items()}


def _triggers(conn) -> dict[str, str]:
    """Every user trigger on a touched table -> its enabled state."""
    rows = conn.execute(
        text(
            "SELECT c.relname || '.' || t.tgname, t.tgenabled::text FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relname = ANY(:tables) ORDER BY 1"
        ),
        {"tables": list(_TOUCHED)},
    ).all()
    return {str(row[0]): str(row[1]) for row in rows}


def test_the_freeze_is_really_on_at_the_parent_revision(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    with pytest.raises(Exception, match="immutable|accepted|frozen|approved"), harness.engine.begin() as conn:
        conn.execute(
            text("UPDATE calculation SET parameters_parser_version = 'tampered' WHERE id = :c"), {"c": ids["calc_frozen"]}
        )


def test_upgrade_splits_every_fused_shape_and_downgrade_round_trips(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before_triggers = _triggers(conn)
        old = _snapshot(conn)
    assert before_triggers and all(state == "O" for state in before_triggers.values())

    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        after_triggers = _triggers(conn)
        # Every guard the revision switched off is on again, and it is the same set of triggers.
        assert after_triggers == before_triggers
        assert "D" not in after_triggers.values()

        mr = {
            row[0]: tuple(row[1:])
            for row in conn.execute(
                text(
                    "SELECT id, model, context_schema_version, prompt_name, prompt_version, rubric_name, rubric_version "
                    "FROM record_machine_review"
                )
            )
        }
        # A rubric's latest old version is 1; the prompt is always 0 (its wording changed; a runner's stamp had no version).
        assert mr[ids["mr_reviewer"]] == ("fake_test/machine-review", 1, "machine_review", 0, "computed_thermo", 1)
        assert mr[ids["mr_ts"]][2:] == ("machine_review", 0, "computed_transition_state", 1)
        assert mr[ids["mr_cp"]] == ("external_cp_comparison", 1, "external_cp_comparison", 0, "external_cp_comparison", 1)
        assert mr[ids["mr_check"]] == ("thermo_consistency", 1, "thermo_consistency", 0, "thermo_consistency", 1)
        # Any OLDER rubric version is 0.
        assert mr[ids["mr_older_thermo"]][2:] == ("machine_review", 0, "computed_thermo", 0)
        # The fake provider's model string no longer ends -v2.
        assert mr[ids["mr_fake"]][0] == "fake_test/machine-review"
        # An unversioned runner prompt is stored at version 0 like every other pre-reset prompt.
        assert mr[ids["mr_custom"]][2:4] == ("custom-prompt", 0)
        assert "rubric_versions_json" not in {
            c[0]
            for c in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'record_machine_review'")
            )
        }

        repro = conn.execute(
            text("SELECT rubric_version, context_json, missing_json FROM record_reproducibility_assessment WHERE id = :i"),
            {"i": ids["repro"]},
        ).one()
        assert repro[0] == 0  # a pre-reset assessment: never current
        assert repro[1] == {"rubric": {"name": "tckdb_reproducibility", "version": 0}, "note": "no_typed_result_contract"}
        assert repro[2] == ["selected_collection_requires_scientific_provenance"]
        # ``..._v10`` is not ``..._v1``: its note is untouched (its missing token, a real ``_v1``, is split).
        v10 = conn.scalar(
            text("SELECT context_json ->> 'note' FROM record_reproducibility_assessment WHERE id = :i"), {"i": ids["repro_v10"]}
        )
        assert v10 == "no_typed_result_contract_v10"

        marked = conn.scalar(text("SELECT details_json FROM submission_audit_event WHERE id = :i"), {"i": ids["audit_marked"]})
        assert marked == {"format": {"name": "machine_review", "version": 1}, "status": "machine_screened_pass"}
        assert conn.scalar(text("SELECT details_json FROM submission_audit_event WHERE id = :i"), {"i": ids["audit_plain"]}) == {
            "note": "no marker"
        }

        # Unpublished: manifest and artifact gone, the published release is a draft again, the curated selection is kept.
        assert conn.scalar(text("SELECT count(*) FROM release_manifest")) == 0
        assert conn.scalar(text("SELECT count(*) FROM release_artifact")) == 0
        release = conn.execute(
            text("SELECT status::text, published_at, doi FROM dataset_release WHERE id = :i"), {"i": ids["release"]}
        ).one()
        assert (release[0], release[1], release[2]) == ("draft", None, "10.1/abc")
        assert conn.scalar(text("SELECT count(*) FROM release_selection WHERE id = :i"), {"i": ids["selection"]}) == 1
        # A withdrawn release stays withdrawn, with who-and-why intact; only its manifest is gone.
        withdrawn = conn.execute(
            text(
                "SELECT status::text, published_at, withdrawn_at, withdrawn_reason, doi FROM dataset_release WHERE id = :i"
            ),
            {"i": ids["withdrawn"]},
        ).one()
        assert withdrawn[0] == "withdrawn"
        assert str(withdrawn[1]).startswith("2026-09-01") and str(withdrawn[2]).startswith("2026-09-15")
        assert (withdrawn[3], withdrawn[4]) == ("a mislabelled set", "10.1/wd")

        env = conn.execute(
            text("SELECT schema_name, schema_version, content_digest, canonical_json FROM execution_environment_manifest")
        ).one()
        assert (env[0], env[1]) == ("tckdb.execution-environment", 1)
        assert env[3]["schema_name"] == "tckdb.execution-environment" and env[3]["schema_version"] == 1
        assert env[2] == _env_digest(env[3]) and env[2] != _env_digest(_OLD_ENV)

        stamps = {
            row[0]: (row[1], row[2])
            for row in conn.execute(text("SELECT id, parameters_parser_name, parameters_parser_version FROM calculation"))
        }
        assert stamps[ids["calc_frozen"]] == ("gaussian_parameters", "1")
        # ``orca_v1`` and ``orca_v2`` are different parsers: they stay different rows of provenance.
        assert stamps[ids["calc_orca1"]] == ("orca_parameters", "0")
        assert stamps[ids["calc_orca2"]] == ("orca_parameters", "1")
        assert stamps[ids["calc_psi4"]] == ("psi4_parameters", "1")
        assert stamps[ids["calc_molpro"]] == ("molpro_parameters", "1")
        assert stamps[ids["calc_producer"]] == ("producer", "acme-reader 4.2")
        for key, text_ in (
            ("calc_gaussian16", "Gaussian 16"),
            ("calc_orca504", "orca 5.0.4"),
            ("calc_psi4_191", "psi4-1.9.1"),
            ("calc_gaussian16_compact", "gaussian16"),
            ("calc_molpro2024", "molpro2024.1"),
        ):
            assert stamps[ids[key]] == ("producer", text_), key
        assert stamps[ids["calc_none"]] == (None, None)
        assert conn.execute(text("SELECT parser_name, parser_version FROM calculation_parameter")).one() == ("gaussian_parameters", "1")
        assert conn.execute(text("SELECT parser_name, parser_version FROM calc_hessian")).one() == ("hessian", "1")

        esr = conn.execute(
            text("SELECT parser_name, parser_version, mapping_name, mapping_version, mapping_report_json FROM external_source_record")
        ).one()
        assert tuple(esr[:4]) == ("thermoml_cp", "1", "thermoml_cp_mapping", "1")
        assert esr[4]["schema"] == "thermoml.mapping"
        assert esr[4]["transformed"] == ["pressure.kpa_to_bar", "pressure.kpa_to_bar.v10", "unit.j_mol_k.identity"]
        assert esr[4]["parser_version"] == "1" and esr[4]["mapping_name"] == "thermoml_cp_mapping"
        assert conn.scalar(text("SELECT source_release FROM external_source")) == "1"
        obs = {
            row[0]: tuple(row[1:])
            for row in conn.execute(
                text(
                    "SELECT id, external_source_parser_name, external_source_parser_version, raw_payload_json "
                    "FROM molecular_property_observation"
                )
            )
        }
        assert obs[ids["obs"]][:2] == ("cccbdb_experimental_species", "1")
        assert obs[ids["obs_producer"]][:2] == ("producer", "acme-obs 1.0")
        assert obs[ids["obs_cccbdb_scrape"]][:2] == ("producer", "CCCBDB scrape 2024")
        # Verbatim upstream data: byte for byte what was deposited.
        assert obs[ids["obs"]][2] == {"mapping": {"schema": "thermoml.mapping.v1", "rules": ["origin.prediction_type_map.v1"]}}

        schemes = {
            row[0]: (row[1], row[2])
            for row in conn.execute(text("SELECT name, version, public_ref FROM conformer_assignment_scheme"))
        }
        from app.services.public_refs import make_content_ref

        assert schemes["torsion_basin"] == (1, make_content_ref("cas", "cas:name=torsion_basin;version=1;scope=canonical"))
        assert schemes["extra"][0] == 2
        assert conn.scalar(
            text(
                "SELECT data_type FROM information_schema.columns WHERE table_name = 'conformer_assignment_scheme' "
                "AND column_name = 'version'"
            )
        ) == "integer"

        # No new name column holds a fused name+version string.
        for table, column in (
            ("record_machine_review", "prompt_name"),
            ("record_machine_review", "rubric_name"),
            ("release_manifest", "manifest_format_name"),
            ("calculation", "parameters_parser_name"),
            ("external_source_record", "mapping_name"),
        ):
            assert not conn.scalar(text(f"SELECT count(*) FROM {table} WHERE {column} ~* '[_.@-]v[0-9]+$'"))

        # Every integer version the revision introduced is held at >= 1 by a CHECK.
        constraints = {
            row[0]
            for row in conn.execute(text("SELECT conname FROM pg_constraint WHERE contype = 'c'"))
        }
        assert {
            "ck_release_manifest_versions_positive",
            "ck_execution_environment_manifest_schema_version_positive",
            "ck_record_reproducibility_assessment_rubric_version_nonneg",
            "ck_conformer_assignment_scheme_version_positive",
            "ck_record_machine_review_versions_in_range",
        } <= constraints
        # And the identity of a source record now includes the parser and mapping names.
        identity = conn.scalar(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'uq_external_source_record_identity'")
        )
        assert "parser_name" in identity and "mapping_name" in identity
        upgraded = _snapshot_new(conn)

    # Downgrade restores the old strings exactly (the unpublished manifest is the one documented exception).
    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        assert _triggers(conn) == before_triggers
        restored = _snapshot(conn)
    assert restored == old

    # And upgrading again reproduces the split.
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        assert _snapshot_new(conn) == upgraded
        assert _triggers(conn) == before_triggers


def _snapshot_new(conn) -> dict[str, list]:
    queries = {
        "mr": "SELECT id, model, context_schema_version, prompt_name, prompt_version, rubric_name, rubric_version "
        "FROM record_machine_review ORDER BY id",
        "repro": "SELECT id, rubric_version, context_json, missing_json FROM record_reproducibility_assessment ORDER BY id",
        "env": "SELECT id, schema_name, schema_version, content_digest FROM execution_environment_manifest ORDER BY id",
        "calc": "SELECT id, parameters_parser_name, parameters_parser_version FROM calculation ORDER BY id",
        "esr": "SELECT id, parser_name, parser_version, mapping_name, mapping_version FROM external_source_record ORDER BY id",
        "obs": "SELECT id, external_source_parser_name, external_source_parser_version FROM molecular_property_observation ORDER BY id",
        "scheme": "SELECT id, version, public_ref FROM conformer_assignment_scheme ORDER BY id",
    }
    return {name: [tuple(r) for r in conn.execute(text(q)).all()] for name, q in queries.items()}


def test_trigger_modes_are_restored_exactly_and_a_preexisting_disabled_one_is_left_alone(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
        names = sorted(_triggers(conn))
        by_table = {}
        for key in names:
            table, trigger = key.split(".", 1)
            by_table.setdefault(table, []).append(trigger)
        # One of each mode the revision has to put back: ENABLE ALWAYS, ENABLE REPLICA, and one that was OFF all along.
        always_table = "record_reproducibility_assessment"
        replica_table = "release_manifest"
        off_table = "calc_hessian"
        always_name, replica_name, off_name = by_table[always_table][0], by_table[replica_table][0], by_table[off_table][0]
        conn.execute(text(f'ALTER TABLE "{always_table}" ENABLE ALWAYS TRIGGER "{always_name}"'))
        conn.execute(text(f'ALTER TABLE "{replica_table}" ENABLE REPLICA TRIGGER "{replica_name}"'))
        conn.execute(text(f'ALTER TABLE "{off_table}" DISABLE TRIGGER "{off_name}"'))
        before = _triggers(conn)
    assert before[f"{always_table}.{always_name}"] == "A"
    assert before[f"{replica_table}.{replica_name}"] == "R"
    assert before[f"{off_table}.{off_name}"] == "D"

    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        # Same mode for every trigger: A is not flattened to O, and the one that was off stays off (the revision
        # did not disable it, so it does not claim to have to re-enable it, and does not fail).
        assert _triggers(conn) == before

    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        assert _triggers(conn) == before


# ---------------------------------------------------------------------------
# Aborts: what the revision cannot split cleanly, it refuses to touch
# ---------------------------------------------------------------------------


def _abort_prompt_fused(conn):
    _review(conn, model=None, provider=None, prompt="machine_review.v2", rubrics={"computed_thermo_v2": "2"}, record_id=90)


def _abort_prompt_runner_versioned(conn):
    # A deterministic runner's ``X_vN`` (other than the Cp runner's v1) cannot be given back exactly: refused.
    _review(conn, model="m", provider=_SC, prompt="runner_x_v3", rubrics={"thermo_consistency_v1": "1"}, record_id=90)


def _abort_machine_review_v2(conn):
    # Main only ever wrote machine_review_v1; the downgrade would give back v1, so v2 is refused.
    _review(conn, model=None, provider=None, prompt="machine_review_v2", rubrics={"computed_thermo_v2": "2"}, record_id=90)


def _abort_prompt_ambiguous_runner(conn):
    # ``X_v1`` on a deterministic runner cannot be told apart from an unversioned ``X`` on the way back.
    _review(conn, model="m", provider=_SC, prompt="runner_x_v1", rubrics={"runner_x_v1": "1"}, record_id=90)


def _abort_prompt_name_fused_inside(conn):
    # ``foo.v2_v1`` splits as name ``foo.v2`` + 1: the name still ends in a version segment, so it cannot be written.
    _review(conn, model=None, provider=None, prompt="foo.v2_v1", rubrics={"computed_thermo_v2": "2"}, record_id=90)


def _abort_rubric_fused(conn):
    _review(conn, model=None, provider=None, prompt="machine_review_v1", rubrics={"computed_thermo.v2": "2"}, record_id=90)


def _abort_rubric_value_disagrees(conn):
    _review(conn, model=None, provider=None, prompt="machine_review_v1", rubrics={"computed_thermo_v2": "3"}, record_id=90)


def _abort_context_schema(conn):
    _review(conn, model=None, provider=None, prompt="machine_review_v1", rubrics={"computed_thermo_v2": "2"}, record_id=90, schema="1")


def _abort_repro_rubric_version(conn):
    conn.execute(
        text(
            "INSERT INTO record_reproducibility_assessment (record_type, record_id, grade, rubric_name, rubric_version, "
            "context_hash, context_json, missing_json, assessor_kind, assessed_at) VALUES ('thermo', 90, 'described', "
            "'tckdb_reproducibility', '1', :h, '{}'::jsonb, '[]'::jsonb, 'system', now())"
        ),
        {"h": "b" * 64},
    )


def _abort_env_schema(conn):
    software = conn.scalar(text("INSERT INTO software (name) VALUES ('gaussian') RETURNING id"))
    release_row = conn.scalar(text("INSERT INTO software_release (software_id) VALUES (:s) RETURNING id"), {"s": software})
    conn.execute(
        text(
            "INSERT INTO execution_environment_manifest (schema_version, content_digest, runtime_kind, runtime_locator, "
            "executable_locator, closure_json, canonical_json, software_release_id) VALUES "
            "('tckdb.execution-environment.v2', :d, 'described', 'module://gaussian/16', 'file:///opt/g16/g16', '[]'::jsonb, "
            "CAST(:c AS jsonb), :sr)"
        ),
        {"d": _env_digest(_OLD_ENV), "c": json.dumps(_OLD_ENV), "sr": release_row},
    )


def _abort_calc_lookalike(stamp):
    def seed(conn):
        _calc(conn, _entry(conn), stamp)

    return seed


def _abort_parameter_lookalike(conn):
    calc = _calc(conn, _entry(conn), None)
    conn.execute(
        text(
            "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, parser_version) VALUES "
            "(:c, 'k', 'v', 'parser', 'molpro_v9')"
        ),
        {"c": calc},
    )


def _abort_observation_lookalike(conn):
    conn.execute(
        text(
            "INSERT INTO molecular_property_observation (property_kind, scientific_origin, scalar_value, scalar_unit, "
            "external_source_parser_version) VALUES ('dipole_moment', 'experimental', 1.0, 'D', "
            "'cccbdb-experimental-species-parser/0.2.0')"
        )
    )


def _abort_source_record_not_thermoml(conn):
    source = conn.scalar(text("INSERT INTO external_source (source_name, source_release) VALUES ('Some Other Source', '1') RETURNING id"))
    conn.execute(
        text(
            "INSERT INTO external_source_record (external_source_id, record_kind, source_uri, source_record_key, retrieved_at, "
            "content_sha256, content_length, raw_uri, parser_name, parser_version, mapping_version) VALUES "
            "(:s, 'thermoml_article', 'u', 'k', now(), :h, 1, 'r', 'other_parser', 'other-parser/2.0', 'other-mapping/2.0')"
        ),
        {"s": source, "h": "f" * 64},
    )


def _abort_scheme(conn):
    conn.execute(
        text("INSERT INTO conformer_assignment_scheme (name, version, scope, public_ref) VALUES ('canon', 'v1', 'canonical', 'cas_canon')")
    )


_ABORTS = {
    "prompt_fused": (_abort_prompt_fused, "prompt_version"),
    "prompt_runner_versioned": (_abort_prompt_runner_versioned, "prompt_version"),
    "prompt_machine_review_v2": (_abort_machine_review_v2, "prompt_version"),
    "prompt_ambiguous_runner": (_abort_prompt_ambiguous_runner, "prompt_version"),
    # Refused up front (the pre-check) and, were that check ever weakened, still refused by the post-rewrite check.
    "prompt_name_fused_inside": (_abort_prompt_name_fused_inside, ("prompt_version", "fused name+version")),
    "rubric_fused": (_abort_rubric_fused, "rubric"),
    "rubric_value_disagrees": (_abort_rubric_value_disagrees, "rubric"),
    "context_schema": (_abort_context_schema, "context_schema_version"),
    "repro_rubric_version": (_abort_repro_rubric_version, "record_reproducibility_assessment"),
    "env_schema": (_abort_env_schema, "execution_environment_manifest"),
    "calc_lookalike_lower": (_abort_calc_lookalike("gaussian_v2"), "parser stamp"),
    "calc_lookalike_upper": (_abort_calc_lookalike("GAUSSIAN_V1"), "parser stamp"),
    "calc_lookalike_orca": (_abort_calc_lookalike("orca_v3"), "parser stamp"),
    "parameter_lookalike": (_abort_parameter_lookalike, "parser stamp"),
    "observation_lookalike": (_abort_observation_lookalike, "parser stamp"),
    "source_record_not_thermoml": (_abort_source_record_not_thermoml, "external_source_record"),
    "scheme_unrecognised": (_abort_scheme, "conformer_assignment_scheme"),
}


@pytest.mark.parametrize("case", sorted(_ABORTS))
def test_upgrade_aborts_on_a_shape_it_cannot_split_cleanly(harness, case) -> None:
    seed, fragment = _ABORTS[case]
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seed(conn)
        before = _triggers(conn)
    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    output = completed.stderr + completed.stdout
    assert completed.returncode != 0, output
    fragments = (fragment,) if isinstance(fragment, str) else fragment
    assert any(f in output for f in fragments), output
    with harness.engine.begin() as conn:
        # Nothing was committed: still at the parent revision, and not one guard is left in a different state.
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.parent
        assert _triggers(conn) == before


def test_upgrade_aborts_on_a_review_stamped_with_two_rubrics(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
        conn.execute(
            text(
                "INSERT INTO record_machine_review (record_type, record_id, status, context_hash, context_schema_version, "
                "prompt_version, rubric_versions_json, reviewed_at) VALUES ('thermo', 99, 'machine_screened_pass', :h, 'v1', "
                "'machine_review_v1', CAST(:r AS jsonb), now())"
            ),
            {"h": "a" * 64, "r": json.dumps({"computed_thermo_v2": "2", "computed_kinetics_v2": "2"})},
        )
    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode != 0
    assert "not exactly one key" in (completed.stderr + completed.stdout)
    with harness.engine.begin() as conn:
        # Nothing was committed: still at the parent revision, old column intact, no guard left off.
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.parent
        assert conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'record_machine_review' "
                "AND column_name = 'rubric_versions_json'"
            )
        ) == 1
        assert "D" not in _triggers(conn).values()


def test_downgrade_refuses_a_stamp_the_old_schema_cannot_spell(harness) -> None:
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        entry = _entry(conn)
        conn.execute(
            text(
                "INSERT INTO calculation (type, species_entry_id, parameters_parser_name, parameters_parser_version) "
                "VALUES ('opt', :e, 'gaussian_parameters', '2')"
            ),
            {"e": entry},
        )
    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert completed.returncode != 0
    assert "gaussian_parameters" in (completed.stderr + completed.stdout)
    with harness.engine.begin() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision


def test_downgrade_refuses_while_a_release_manifest_exists(harness) -> None:
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        user = conn.scalar(text("INSERT INTO app_user (username, role, is_active) VALUES ('c', 'curator', true) RETURNING id"))
        policy = conn.scalar(
            text("INSERT INTO curation_policy (name, version, description, public_ref) VALUES ('p', '1', 'd', 'cpol_x') RETURNING id")
        )
        release = conn.scalar(
            text(
                "INSERT INTO dataset_release (tag, title, status, curation_policy_id, data_license, code_license, citation_text, "
                "contact, published_at, public_ref) VALUES ('t', 't', 'published', :p, 'x', 'y', 'c', 'x', now(), 'drel_x') RETURNING id"
            ),
            {"p": policy},
        )
        conn.execute(
            text(
                "INSERT INTO release_manifest (dataset_release_id, manifest_format_name, manifest_format_version, alembic_revision, "
                "backend_version, schemas_package_version, review_policy_name, review_policy_version, curation_policy_id, "
                "recovery_archive_format_name, recovery_archive_format_version, release_public_ref, release_tag, release_title, "
                "release_contact, release_status_at_publication, data_license, code_license, citation_text, curation_policy_ref, "
                "curation_policy_name, curation_policy_version, curation_policy_description, contract_json, document_json, "
                "content_sha256, selected_record_count, candidate_record_count, public_ref) VALUES (:r, 'tckdb.dataset_release', 1, "
                "'rev', 'b', 's', 'record_review', 1, :p, 'tckdb.archive', 1, 'drel_x', 't', 't', 'x', 'published', 'x', 'y', 'c', "
                "'cpol_x', 'p', '1', 'd', '{}'::jsonb, '{}'::jsonb, :sha, 1, 1, 'rman_x')"
            ),
            {"r": release, "p": policy, "sha": "c" * 64},
        )
        del user
    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert completed.returncode != 0
    assert "release manifest exists" in (completed.stderr + completed.stdout)


@pytest.mark.parametrize(
    ("rubric", "rubric_version", "prompt", "prompt_version", "fragment"),
    [
        # Several older versions collapsed into 0 (latest 5, older 1-4): not one specific old version, so refused.
        ("computed_transition_state", 0, "machine_review", 1, "computed_transition_state"),
        # A rubric main no longer had, at 0: nothing to give back.
        ("retired_rubric", 0, "machine_review", 1, "retired_rubric"),
        # An LLM prompt other than machine_review at 0: its old spelling is unknown.
        ("computed_thermo", 1, "custom-prompt", 0, "custom-prompt"),
    ],
)
def test_downgrade_refuses_what_a_zero_cannot_give_back(harness, rubric, rubric_version, prompt, prompt_version, fragment) -> None:
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO record_machine_review (record_type, record_id, status, context_hash, context_schema_version, "
                "prompt_name, prompt_version, rubric_name, rubric_version, reviewed_at) VALUES ('thermo', 1, "
                "'machine_screened_pass', :h, 1, :pn, :pv, :rn, :rv, now())"
            ),
            {"h": "a" * 64, "pn": prompt, "pv": prompt_version, "rn": rubric, "rv": rubric_version},
        )
    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    output = completed.stderr + completed.stdout
    assert completed.returncode != 0, output
    assert fragment in output, output
    with harness.engine.begin() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision


def _post_reset_review(conn, *, prompt_version=1, rubric_version=1, context_schema=1):
    conn.execute(
        text(
            "INSERT INTO record_machine_review (record_type, record_id, status, context_hash, context_schema_version, "
            "prompt_name, prompt_version, rubric_name, rubric_version, reviewed_at) VALUES ('thermo', 1, "
            "'machine_screened_pass', :h, :cs, 'machine_review', :pv, 'computed_thermo', :rv, now())"
        ),
        {"h": "a" * 64, "cs": context_schema, "pv": prompt_version, "rv": rubric_version},
    )


def _post_reset_review_rubric_2(conn):
    _post_reset_review(conn, rubric_version=2)


def _post_reset_review_prompt_2(conn):
    _post_reset_review(conn, prompt_version=2)


def _post_reset_review_context_2(conn):
    _post_reset_review(conn, context_schema=2)


def _post_reset_assessment_2(conn):
    conn.execute(
        text(
            "INSERT INTO record_reproducibility_assessment (record_type, record_id, grade, rubric_name, rubric_version, "
            "context_hash, context_json, missing_json, assessor_kind, assessed_at) VALUES ('thermo', 1, 'described', "
            "'tckdb_reproducibility', 2, :h, '{}'::jsonb, '[]'::jsonb, 'system', now())"
        ),
        {"h": "b" * 64},
    )


def _post_reset_environment_2(conn):
    software = conn.scalar(text("INSERT INTO software (name) VALUES ('gaussian') RETURNING id"))
    release_row = conn.scalar(text("INSERT INTO software_release (software_id) VALUES (:s) RETURNING id"), {"s": software})
    conn.execute(
        text(
            "INSERT INTO execution_environment_manifest (schema_name, schema_version, content_digest, runtime_kind, "
            "runtime_locator, executable_locator, closure_json, canonical_json, software_release_id) VALUES "
            "('tckdb.execution-environment', 2, :d, 'described', 'module://gaussian/16', 'file:///opt/g16/g16', '[]'::jsonb, "
            "'{}'::jsonb, :sr)"
        ),
        {"d": "sha256:" + "d" * 64, "sr": release_row},
    )


@pytest.mark.parametrize(
    ("seed", "fragment"),
    [
        (_post_reset_review_rubric_2, "rubric_version"),
        (_post_reset_review_prompt_2, "prompt_version"),
        (_post_reset_review_context_2, "context_schema_version"),
        (_post_reset_assessment_2, "record_reproducibility_assessment"),
        (_post_reset_environment_2, "execution_environment_manifest"),
    ],
    ids=["rubric", "prompt", "context_schema", "assessment_rubric", "environment_schema"],
)
def test_downgrade_refuses_a_post_reset_version_the_old_numbering_would_misread(harness, seed, fragment) -> None:
    """A version of 2 or more only exists after the reset. Written back as ``<name>_v2`` it would name a different
    rubric in the old numbering, so the downgrade refuses it, naming the kind of row, instead of mislabelling it."""
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        seed(conn)
    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    output = completed.stderr + completed.stdout
    assert completed.returncode != 0, output
    assert fragment in output and "version 2 or above" in output, output
    with harness.engine.begin() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision
