"""Disposable-database contract for ``c7d4a9e2b6f1`` (versioning PR-4: split every fused name-and-version string).

The revision rewrites stored provenance in nine tables, three of them behind accepted-science immutability and three
more append-only or immutable, and does it with every guard trigger switched off. Four things have to be checkable on a
real database and not merely on an empty one:

1. **Every old shape comes out split.** The seed holds one row of every fused shape the code used to write (a reviewer
   review, a transition-state review, an external-Cp review, a consistency-runner review, a reproducibility assessment,
   a machine-review audit event, a published release, an execution-environment manifest, the five TCKDB parser stamps and
   a producer's own, a ThermoML source record, a CCCBDB observation, the seeded conformer scheme). Each is asserted by
   name and integer, so one shape the revision forgets leaves one fused string and fails here.
2. **The bypass is real, bounded and undone.** The seeded calculation is *approved*, so the freeze is genuinely on at the
   parent revision (asserted: the same ``UPDATE`` is refused there). The upgrade must still relabel its parser stamp, and
   afterwards **every user trigger the revision touched is enabled again**, by comparing the full trigger inventory before
   and after, and looking for any ``tgenabled = 'D'``.
3. **It aborts on an impossible state.** A ``record_machine_review`` whose ``rubric_versions_json`` holds two keys makes
   the upgrade fail, name the rows, and leave the database at the parent revision with its old columns intact.
4. **Downgrade round-trips.** upgrade -> downgrade restores the exact old strings the seed wrote (the one documented
   exception, the unpublished release manifest and artifact, does not come back), and upgrading again reproduces the split.
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


def _seed(conn) -> dict[str, int]:
    """One row of every old fused shape, written as raw SQL at the parent revision."""
    ids: dict[str, int] = {}
    user = conn.scalar(
        text("INSERT INTO app_user (username, role, is_active) VALUES ('split-curator', 'curator', true) RETURNING id")
    )
    ids["user"] = user

    def review(model, provider, prompt, rubrics, record_id, schema="v1"):
        return conn.scalar(
            text(
                "INSERT INTO record_machine_review (record_type, record_id, status, context_hash, "
                "context_schema_version, prompt_version, rubric_versions_json, model, provider, reviewed_at) "
                "VALUES ('thermo', :rid, 'machine_screened_pass', :h, :s, :p, CAST(:r AS jsonb), :m, :pr, now()) "
                "RETURNING id"
            ),
            {"rid": record_id, "h": "a" * 64, "s": schema, "p": prompt, "r": json.dumps(rubrics), "m": model, "pr": provider},
        )

    ids["mr_reviewer"] = review("fake_test/machine-review-v2", None, "machine_review_v1", {"computed_thermo_v2": "2"}, 1)
    ids["mr_ts"] = review(None, None, "machine_review_v1", {"computed_transition_state_v5": "5"}, 2)
    ids["mr_cp"] = review(
        "external_cp_comparison_v1", "tckdb.scientific_checks", "external_cp_comparison_v1", {"external_cp_comparison_v2": "2"}, 3
    )
    ids["mr_check"] = review(
        "thermo_consistency", "tckdb.scientific_checks", "thermo_consistency", {"thermo_consistency_v1": "1"}, 4
    )

    ids["repro"] = conn.scalar(
        text(
            "INSERT INTO record_reproducibility_assessment (record_type, record_id, grade, rubric_name, rubric_version, "
            "context_hash, context_json, missing_json, assessor_kind, assessed_at) "
            "VALUES ('thermo', 7, 'described', 'tckdb_reproducibility', 'v1', :h, CAST(:c AS jsonb), CAST(:m AS jsonb), "
            "'system', now()) RETURNING id"
        ),
        {
            "h": "b" * 64,
            "c": json.dumps({"rubric": {"name": "tckdb_reproducibility", "version": "v1"}, "note": "no_typed_result_contract_v1"}),
            "m": json.dumps(["selected_collection_requires_scientific_provenance_v1"]),
        },
    )

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

    species = conn.scalar(
        text(
            "INSERT INTO species (inchi_key, smiles, kind, charge, multiplicity, stereo_kind, public_ref) VALUES "
            "('AAAAAAAAAAAAAA-AAAAAAAAAA-N', '[H]', 'molecule', 0, 2, 'unspecified', 'spc_split') RETURNING id"
        )
    )
    entry = conn.scalar(text("INSERT INTO species_entry (species_id, public_ref) VALUES (:s, 'spe_split') RETURNING id"), {"s": species})
    geometry = conn.scalar(text("INSERT INTO geometry (geom_hash, natoms) VALUES (:h, 1) RETURNING id"), {"h": "e" * 64})

    def calc(stamp):
        return conn.scalar(
            text(
                "INSERT INTO calculation (type, species_entry_id, parameters_parser_version) VALUES ('opt', :e, :v) RETURNING id"
            ),
            {"e": entry, "v": stamp},
        )

    ids["calc_frozen"] = calc("gaussian_v1")
    ids["calc_orca"] = calc("orca_v1")
    ids["calc_psi4"] = calc("psi4-0.1.0")
    ids["calc_molpro"] = calc("molpro_v1")
    ids["calc_producer"] = calc("acme-reader 4.2")
    ids["calc_none"] = calc(None)
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
                    "transformed": ["pressure.kpa_to_bar.v1", "unit.j_mol_k.identity"],
                }
            ),
        },
    )
    ids["obs"] = conn.scalar(
        text(
            "INSERT INTO molecular_property_observation (property_kind, scientific_origin, scalar_value, scalar_unit, external_source_parser_version, "
            "raw_payload_json) VALUES ('dipole_moment', 'experimental', 1.0, 'D', 'cccbdb-experimental-species-parser/0.1.0', "
            "CAST(:j AS jsonb)) RETURNING id"
        ),
        {"j": json.dumps({"mapping": {"schema": "thermoml.mapping.v1", "rules": ["origin.prediction_type_map.v1"]}})},
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
        "scheme": "SELECT id, version, public_ref FROM conformer_assignment_scheme ORDER BY id",
    }
    return {name: [tuple(r) for r in conn.execute(text(q)).all()] for name, q in queries.items()}


def _triggers(conn) -> dict[str, str]:
    """Every user trigger on a touched table -> its enabled state."""
    rows = conn.execute(
        text(
            "SELECT c.relname || '.' || t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
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
        assert mr[ids["mr_reviewer"]] == ("fake_test/machine-review-v2", 1, "machine_review", 1, "computed_thermo", 2)
        assert mr[ids["mr_ts"]][2:] == ("machine_review", 1, "computed_transition_state", 5)
        assert mr[ids["mr_cp"]] == ("external_cp_comparison", 1, "external_cp_comparison", 1, "external_cp_comparison", 2)
        assert mr[ids["mr_check"]] == ("thermo_consistency", 1, "thermo_consistency", 1, "thermo_consistency", 1)
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
        assert repro[0] == 1
        assert repro[1] == {"rubric": {"name": "tckdb_reproducibility", "version": 1}, "note": "no_typed_result_contract"}
        assert repro[2] == ["selected_collection_requires_scientific_provenance"]

        marked = conn.scalar(text("SELECT details_json FROM submission_audit_event WHERE id = :i"), {"i": ids["audit_marked"]})
        assert marked == {"format": {"name": "machine_review", "version": 1}, "status": "machine_screened_pass"}
        assert conn.scalar(text("SELECT details_json FROM submission_audit_event WHERE id = :i"), {"i": ids["audit_plain"]}) == {
            "note": "no marker"
        }

        # Unpublished: manifest and artifact gone, the release is a draft again, the curated selection is kept.
        assert conn.scalar(text("SELECT count(*) FROM release_manifest")) == 0
        assert conn.scalar(text("SELECT count(*) FROM release_artifact")) == 0
        release = conn.execute(
            text("SELECT status::text, published_at, doi FROM dataset_release WHERE id = :i"), {"i": ids["release"]}
        ).one()
        assert (release[0], release[1], release[2]) == ("draft", None, "10.1/abc")
        assert conn.scalar(text("SELECT count(*) FROM release_selection WHERE id = :i"), {"i": ids["selection"]}) == 1

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
        assert stamps[ids["calc_orca"]] == ("orca_parameters", "1")
        assert stamps[ids["calc_psi4"]] == ("psi4_parameters", "1")
        assert stamps[ids["calc_molpro"]] == ("molpro_parameters", "1")
        assert stamps[ids["calc_producer"]] == ("producer", "acme-reader 4.2")
        assert stamps[ids["calc_none"]] == (None, None)
        assert conn.execute(text("SELECT parser_name, parser_version FROM calculation_parameter")).one() == ("gaussian_parameters", "1")
        assert conn.execute(text("SELECT parser_name, parser_version FROM calc_hessian")).one() == ("hessian", "1")

        esr = conn.execute(
            text("SELECT parser_name, parser_version, mapping_name, mapping_version, mapping_report_json FROM external_source_record")
        ).one()
        assert tuple(esr[:4]) == ("thermoml_cp", "1", "thermoml_cp_mapping", "1")
        assert esr[4]["schema"] == "thermoml.mapping" and esr[4]["transformed"] == ["pressure.kpa_to_bar", "unit.j_mol_k.identity"]
        assert esr[4]["parser_version"] == "1" and esr[4]["mapping_name"] == "thermoml_cp_mapping"
        assert conn.scalar(text("SELECT source_release FROM external_source")) == "1"
        obs = conn.execute(
            text("SELECT external_source_parser_name, external_source_parser_version, raw_payload_json FROM molecular_property_observation")
        ).one()
        assert tuple(obs[:2]) == ("cccbdb_experimental_species", "1")
        assert obs[2] == {"mapping": {"schema": "thermoml.mapping", "rules": ["origin.prediction_type_map"]}}

        scheme = conn.execute(text("SELECT version, public_ref FROM conformer_assignment_scheme")).one()
        assert scheme[0] == "1"
        from app.services.public_refs import make_content_ref

        assert scheme[1] == make_content_ref("cas", "cas:name=torsion_basin;version=1;scope=canonical")

        # No column of any touched table still holds a fused name+version string.
        for table, column in (
            ("record_machine_review", "prompt_name"),
            ("record_machine_review", "rubric_name"),
            ("release_manifest", "manifest_format_name"),
        ):
            assert not conn.scalar(text(f"SELECT count(*) FROM {table} WHERE {column} ~ '_v[0-9]+$'"))
        upgraded = _snapshot_new(conn)

    # Downgrade restores the old strings exactly (the unpublished manifest is the one documented exception).
    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        assert _triggers(conn) == before_triggers
        restored = _snapshot(conn)
    # ``orca_v1`` and ``orca_v2`` both became (orca_parameters, '1'), so the old schema gets ``orca_v2`` back: the one
    # documented loss of the downgrade.
    expected = {**old, "calc": [(i, "orca_v2" if v == "orca_v1" else v) for i, v in old["calc"]]}
    assert restored == expected

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
        "scheme": "SELECT id, version, public_ref FROM conformer_assignment_scheme ORDER BY id",
    }
    return {name: [tuple(r) for r in conn.execute(text(q)).all()] for name, q in queries.items()}


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
