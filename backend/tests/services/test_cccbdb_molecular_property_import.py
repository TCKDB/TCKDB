"""Tests for the CCCBDB molecular-property import service + CLI.

Uses the per-test transactional ``db_session`` fixture so every test
rolls back at teardown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.common import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import Species, SpeciesEntry
from app.importers.cccbdb.payload_io import (
    filter_payloads_by_property_kind,
    load_payloads,
)
from app.services.cccbdb_molecular_property_import import (
    import_cccbdb_molecular_property_payloads,
)

WATER_INCHIKEY = "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
ETHANOL_INCHIKEY = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(
    *,
    property_kind: str = "atomization_energy",
    property_label: str | None = "atomization_energy_0k",
    scalar_value: float = 917.8,
    scalar_unit: str = "kJ/mol",
    scalar_uncertainty: float | None = 0.1,
    record_key: str = "h2o",
    inchikey: str | None = WATER_INCHIKEY,
    formula: str | None = "H2O",
    name: str | None = "Water",
    cas_number: str | None = None,
    reference_label: str | None = None,
    external_source_url: str = "https://cccbdb.nist.gov/ea1x.asp",
    content_sha256: str = "0" * 64,
    raw_payload_extra: dict | None = None,
) -> dict:
    """Return a payload dict that validates through
    ``MolecularPropertyObservationCreate``."""

    identity_hint: dict = {}
    if formula:
        identity_hint["formula"] = formula
    if name:
        identity_hint["name"] = name
    if inchikey:
        identity_hint["inchikey"] = inchikey
    if cas_number:
        identity_hint["cas_number"] = cas_number

    raw_payload: dict = {
        "target_kind": "atomization_energy",
        "row_formula": formula,
        "row_name": name,
        "raw_row": {},
    }
    if identity_hint:
        raw_payload["identity_hint"] = identity_hint
    if raw_payload_extra:
        raw_payload.update(raw_payload_extra)

    return {
        "species_entry_id": None,
        "scientific_origin": "experimental",
        "property_kind": property_kind,
        "property_label": property_label,
        "scalar_value": scalar_value,
        "scalar_unit": scalar_unit,
        "scalar_uncertainty": scalar_uncertainty,
        "external_source_name": "CCCBDB",
        "external_source_release": "22",
        "external_source_url": external_source_url,
        "external_source_record_key": record_key,
        "external_source_page_kind": "experimental_form_result",
        "external_source_content_sha256": content_sha256,
        "external_source_parser_version": "cccbdb-experimental-species-parser/0.1.0",
        "reference_label": reference_label,
        "raw_payload_json": raw_payload,
    }


def _seed_species_entry(
    db_session,
    *,
    smiles: str,
    inchi_key: str,
    multiplicity: int = 1,
    charge: int = 0,
    kind: StationaryPointKind = StationaryPointKind.minimum,
    state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground,
) -> int:
    """Insert one Species + SpeciesEntry pair and return the entry id."""

    species = Species(
        smiles=smiles,
        inchi_key=inchi_key,
        charge=charge,
        multiplicity=multiplicity,
        kind=MoleculeKind.molecule,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(species)
    db_session.flush()
    entry = SpeciesEntry(
        species_id=species.id,
        unmapped_smiles=smiles,
        kind=kind,
        electronic_state_kind=state_kind,
    )
    db_session.add(entry)
    db_session.flush()
    return entry.id


# ---------------------------------------------------------------------------
# Payload loading
# ---------------------------------------------------------------------------


class TestPayloadLoading:
    def _write_target_file(
        self, path: Path, property_kind: str, payloads: list[dict]
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"property_kind": property_kind, "payloads": payloads}
            )
        )

    def test_loads_flat_lane(self, tmp_path):
        flat = tmp_path / "flat"
        self._write_target_file(
            flat / "atomization_energy.json",
            "atomization_energy",
            [_make_payload()],
        )
        (flat / "summary.json").write_text("{}")
        loaded = load_payloads(flat_payload_dir=flat)
        assert len(loaded) == 1
        assert loaded[0].lane == "flat_property_table"
        assert loaded[0].target_kind == "atomization_energy"

    def test_loads_form_lane(self, tmp_path):
        form = tmp_path / "form"
        self._write_target_file(
            form / "atomization_energy.json",
            "atomization_energy",
            [_make_payload()],
        )
        loaded = load_payloads(form_payload_dir=form)
        assert len(loaded) == 1
        assert loaded[0].lane == "form_result"

    def test_summary_json_is_excluded(self, tmp_path):
        flat = tmp_path / "flat"
        flat.mkdir()
        (flat / "summary.json").write_text('{"foo": "bar"}')
        loaded = load_payloads(flat_payload_dir=flat)
        assert loaded == []

    def test_filter_by_property_kind(self, tmp_path):
        flat = tmp_path / "flat"
        self._write_target_file(
            flat / "ae.json",
            "atomization_energy",
            [_make_payload(property_kind="atomization_energy")],
        )
        self._write_target_file(
            flat / "dipole.json",
            "dipole_moment",
            [_make_payload(property_kind="dipole_moment", inchikey=None)],
        )
        loaded = load_payloads(flat_payload_dir=flat)
        filtered = filter_payloads_by_property_kind(
            loaded, ["atomization_energy"]
        )
        assert len(filtered) == 1
        assert filtered[0].payload["property_kind"] == "atomization_energy"


# ---------------------------------------------------------------------------
# Service: dry-run vs commit
# ---------------------------------------------------------------------------


class TestDryRunVsCommit:
    def test_dry_run_inserts_no_rows(self, db_session):
        payloads = [_make_payload()]
        before = db_session.execute(
            select(MolecularPropertyObservation)
        ).all()
        result = import_cccbdb_molecular_property_payloads(
            db_session, payloads, commit=False, resolve_identity=False
        )
        after = db_session.execute(
            select(MolecularPropertyObservation)
        ).all()
        assert len(after) == len(before)
        assert result.would_insert_count == 1
        assert result.inserted_count == 0

    def test_commit_persists_rows(self, db_session):
        payloads = [_make_payload()]
        result = import_cccbdb_molecular_property_payloads(
            db_session, payloads, commit=True, resolve_identity=False
        )
        rows = db_session.execute(
            select(MolecularPropertyObservation)
            .where(MolecularPropertyObservation.external_source_record_key == "h2o")
        ).scalars().all()
        assert len(rows) == 1
        assert result.inserted_count == 1
        assert result.would_insert_count == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_import_is_idempotent(self, db_session):
        payloads = [_make_payload()]
        first = import_cccbdb_molecular_property_payloads(
            db_session, payloads, commit=True, resolve_identity=False
        )
        second = import_cccbdb_molecular_property_payloads(
            db_session, payloads, commit=True, resolve_identity=False
        )
        rows = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        assert len(rows) == 1, (
            f"expected 1 row after two imports, got {len(rows)}"
        )
        assert first.inserted_count == 1
        assert second.inserted_count == 0
        assert second.duplicate_count == 1


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


class TestIdentityResolution:
    def test_exact_inchikey_match_resolves(self, db_session):
        entry_id = _seed_species_entry(
            db_session, smiles="O", inchi_key=WATER_INCHIKEY
        )
        result = import_cccbdb_molecular_property_payloads(
            db_session, [_make_payload()], commit=True
        )
        assert result.resolved_identity_count == 1
        row = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalar_one()
        assert row.species_entry_id == entry_id

    def test_no_inchikey_remains_unresolved_but_insertable(self, db_session):
        payload = _make_payload(inchikey=None)
        result = import_cccbdb_molecular_property_payloads(
            db_session, [payload], commit=True
        )
        assert result.unresolved_identity_count == 1
        assert result.inserted_count == 1
        row = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalar_one()
        assert row.species_entry_id is None

    def test_inchikey_not_in_db_is_not_found(self, db_session):
        payload = _make_payload(inchikey="NOTASPECIESINCHIKEY-AA-N")
        result = import_cccbdb_molecular_property_payloads(
            db_session, [payload], commit=True
        )
        assert result.not_found_identity_count == 1
        assert result.inserted_count == 1
        row = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalar_one()
        assert row.species_entry_id is None

    def test_multiple_compatible_entries_is_ambiguous(self, db_session):
        species = Species(
            smiles="O", inchi_key=WATER_INCHIKEY,
            charge=0, multiplicity=1,
            kind=MoleculeKind.molecule,
            stereo_kind=StereoKind.achiral,
        )
        db_session.add(species)
        db_session.flush()
        for label in ("entryA", "entryB"):
            db_session.add(
                SpeciesEntry(
                    species_id=species.id,
                    unmapped_smiles="O",
                    isotopologue_label=label,
                )
            )
        db_session.flush()
        result = import_cccbdb_molecular_property_payloads(
            db_session, [_make_payload()], commit=True
        )
        assert result.ambiguous_identity_count == 1
        row = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalar_one()
        assert row.species_entry_id is None

    def test_formula_only_does_not_auto_resolve(self, db_session):
        payload = _make_payload(
            inchikey=None, name=None, cas_number=None, formula="H2O"
        )
        result = import_cccbdb_molecular_property_payloads(
            db_session, [payload], commit=True
        )
        assert result.unresolved_identity_count == 1
        assert result.resolved_identity_count == 0
        # Warning surface mentions the proposal-only formula.
        assert any(
            "formula" in w
            for w in result.dispositions[0].warnings
        )

    def test_formula_plus_name_does_not_auto_resolve(self, db_session):
        payload = _make_payload(
            inchikey=None, cas_number=None, formula="H2O", name="Water"
        )
        result = import_cccbdb_molecular_property_payloads(
            db_session, [payload], commit=True
        )
        assert result.resolved_identity_count == 0
        assert result.unresolved_identity_count == 1

    def test_cas_only_does_not_auto_resolve(self, db_session):
        payload = _make_payload(
            inchikey=None, cas_number="7732-18-5", name=None, formula="H2O"
        )
        result = import_cccbdb_molecular_property_payloads(
            db_session, [payload], commit=True
        )
        assert result.resolved_identity_count == 0
        assert result.unresolved_identity_count == 1
        # Warning surface mentions the CAS proposal-only path.
        assert any(
            "CAS" in w
            for w in result.dispositions[0].warnings
        )

    def test_no_resolve_identity_flag_skips_lookup(self, db_session):
        _seed_species_entry(
            db_session, smiles="O", inchi_key=WATER_INCHIKEY
        )
        result = import_cccbdb_molecular_property_payloads(
            db_session,
            [_make_payload()],
            commit=True,
            resolve_identity=False,
        )
        # Even with a matching species in the DB, the flag skips lookup.
        assert result.resolved_identity_count == 0
        assert result.unresolved_identity_count == 1
        row = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalar_one()
        assert row.species_entry_id is None


# ---------------------------------------------------------------------------
# Invalid payloads
# ---------------------------------------------------------------------------


class TestInvalidPayloads:
    def test_invalid_payload_warns_and_continues(self, db_session):
        good = _make_payload()
        bad = {
            # Missing required scientific_origin / property_kind.
            "scalar_value": 100.0,
            "scalar_unit": "kJ/mol",
        }
        result = import_cccbdb_molecular_property_payloads(
            db_session, [good, bad], commit=True, resolve_identity=False
        )
        assert result.invalid_payload_count == 1
        assert result.valid_payload_count == 1
        assert result.inserted_count == 1
        actions = [d.action for d in result.dispositions]
        assert "invalid" in actions
        assert "inserted" in actions

    def test_fail_on_invalid_raises(self, db_session):
        from pydantic import ValidationError

        bad = {"scalar_value": 100.0, "scalar_unit": "kJ/mol"}
        with pytest.raises(ValidationError):
            import_cccbdb_molecular_property_payloads(
                db_session, [bad],
                commit=False, resolve_identity=False,
                fail_on_invalid=True,
            )


# ---------------------------------------------------------------------------
# Result/disposition shape
# ---------------------------------------------------------------------------


class TestDispositionShape:
    def test_counts_add_up(self, db_session):
        _seed_species_entry(
            db_session, smiles="O", inchi_key=WATER_INCHIKEY
        )
        payloads = [
            _make_payload(record_key="resolve_me"),  # resolves
            _make_payload(record_key="no_inchi", inchikey=None),
            _make_payload(record_key="not_found",
                          inchikey="NOMATCHINCHIKEY-AB-N"),
            {"scalar_value": 1.0, "scalar_unit": "kJ/mol"},  # invalid
        ]
        result = import_cccbdb_molecular_property_payloads(
            db_session, payloads, commit=True
        )
        assert result.payload_count == 4
        assert (
            result.valid_payload_count + result.invalid_payload_count
            == result.payload_count
        )
        assert result.resolved_identity_count == 1
        assert result.unresolved_identity_count == 1
        assert result.not_found_identity_count == 1
        assert result.invalid_payload_count == 1
        # Three valid payloads → three rows inserted.
        rows = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        assert len(rows) == 3

    def test_disposition_to_json_keys(self, db_session):
        result = import_cccbdb_molecular_property_payloads(
            db_session, [_make_payload(inchikey=None)],
            commit=False, resolve_identity=True,
        )
        d = result.dispositions[0].to_json()
        assert {
            "property_kind",
            "property_label",
            "external_source_record_key",
            "identity_status",
            "species_entry_id",
            "action",
            "warnings",
        } <= set(d.keys())


# ---------------------------------------------------------------------------
# Structural / no-DB-bleed invariants
# ---------------------------------------------------------------------------


def test_service_does_not_import_parsers_or_fetchers():
    """The DB service must NOT pull in CCCBDB parsers / snapshot /
    form_resolver modules. Those are upstream-only; co-loading them
    here would couple the workflow layer to parser internals."""

    from app.services import cccbdb_molecular_property_import as svc

    forbidden = (
        "parse_experimental_property_table_page",
        "parse_form_result_page",
        "discover_form",
        "run_form_resolver_queue",
        "run_snapshot",
        "HttpFetcher",
    )
    for name in forbidden:
        assert name not in svc.__dict__, (
            f"service leaked upstream symbol {name!r}"
        )


# ---------------------------------------------------------------------------
# CLI (subset — the CLI just composes load + service; tests exercise both)
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_rejects_when_no_dirs_given(self, tmp_path):
        from scripts.cccbdb_import_molecular_property_payloads import main

        rc = main([])
        assert rc == 2

    def test_cli_rejects_missing_directory(self, tmp_path):
        from scripts.cccbdb_import_molecular_property_payloads import main

        rc = main(
            [
                "--flat-payload-dir", str(tmp_path / "nope"),
            ]
        )
        assert rc == 2
