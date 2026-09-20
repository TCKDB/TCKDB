"""The manuscript-number generators and their rendering rules.

Determinism is the property under test. A generator that stamps a
wall-clock value, iterates a set, or leaks a database id produces different
bytes on a second run or on a restored database, and the deposit's byte
comparison exists to refuse exactly that. These tests land each of those
faults on the renderer or the generators and check that it is caught.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

import pytest

from app.db.models.common import CalculationType, SubmissionRecordType
from app.services.deposit.expected_outputs import (
    normalize,
    render_json,
    render_markdown,
    write_expected_outputs,
)
from scripts.paper import generators
from scripts.paper.registry import GENERATORS
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_thermo_source_calculation,
    make_calculation,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_workflow_tool_release,
)


class _Kind(Enum):
    a = "alpha"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_normalize_renders_decimal_datetime_and_enum_deterministically():
    out = normalize(
        {
            "decimal": Decimal("1.2500"),
            "when": datetime(2026, 7, 1, 12, 30, 0),
            "day": date(2026, 7, 1),
            "kind": _Kind.a,
            "nested": [{"x": Decimal("3")}],
        }
    )
    assert out == {
        "decimal": "1.2500",
        "when": "2026-07-01T12:30:00.000000",
        "day": "2026-07-01",
        "kind": "alpha",
        "nested": [{"x": "3"}],
    }


def test_normalize_refuses_an_object_it_cannot_render():
    with pytest.raises(TypeError, match=r"\$\.rows\[0\]\.obj: object is not renderable"):
        normalize({"rows": [{"obj": object()}]})


def test_render_json_is_canonical_and_newline_terminated():
    first = render_json({"b": 1, "a": [1, 2]})
    second = render_json({"a": [1, 2], "b": 1})
    assert first == second == b'{"a":[1,2],"b":1}\n'


def test_render_markdown_tabulates_lists_of_rows_and_escapes_pipes():
    text = render_markdown("demo", {"rows": [{"k": "a|b", "n": 1}], "total": 1, "meta": {"x": True}}).decode()
    assert text.startswith("# demo\n")
    assert "| k | n |" in text
    assert "a\\|b" in text
    assert "- total: 1" in text
    assert "| x | true |" in text


def test_write_expected_outputs_refuses_an_empty_registry(db_session, tmp_path):
    with pytest.raises(ValueError, match="no generators registered"):
        write_expected_outputs(db_session, tmp_path, {})


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_covers_every_data_claim_and_is_callable():
    expected = {
        "corpus_counts",
        "mechanism_roundtrip_counts",
        "selected_thermo_by_species",
        "candidate_lineage",
        "transition_state_evidence",
        "mechanism_fixture_provenance",
        "experimental_cp_comparison",
        "thermoml_source_provenance",
    }
    assert expected <= set(GENERATORS)
    for name, generator in GENERATORS.items():
        assert callable(generator), name


def test_every_generator_is_byte_stable_across_two_runs(db_session, tmp_path):
    first = write_expected_outputs(db_session, tmp_path / "one", GENERATORS)
    second = write_expected_outputs(db_session, tmp_path / "two", GENERATORS)
    assert first == second
    assert len(first) == 2 * len(GENERATORS)
    for name in first:
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()


def test_no_generator_output_carries_a_database_id_key(db_session, tmp_path):
    write_expected_outputs(db_session, tmp_path, GENERATORS)
    documents = sorted(tmp_path.glob("*.json"))
    assert documents, "the scan must have files to scan"
    assert len(documents) == len(GENERATORS)
    for path in documents:
        document = json.loads(path.read_text())

        def _keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from _keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from _keys(item)

        offenders = sorted({k for k in _keys(document) if k == "id" or k.endswith("_id")})
        assert not offenders, f"{path.name} leaks database ids: {offenders}"


# ---------------------------------------------------------------------------
# individual generators
# ---------------------------------------------------------------------------


def test_mechanism_roundtrip_counts_match_the_fixture_and_the_manuscript_line():
    counts = generators.mechanism_roundtrip_counts(session=None)
    assert counts["species"] == 21
    assert counts["rate_expressions"] == 64
    assert counts["distinct_reactions"] == 61
    assert counts["forms"]["chebyshev"] == 9
    assert counts["forms"]["troe"] == 5
    assert counts["forms"]["troe_with_third_body_efficiencies"] == 5
    assert counts["forms"]["duplicate"] == 6
    assert counts["nasa7_thermo_entries"] == 21
    assert counts["transport_entries"] == 21


def test_mechanism_fixture_provenance_digests_the_committed_files():
    provenance = generators.mechanism_fixture_provenance(session=None)
    assert [f["path"] for f in provenance["files"]] == ["chem.inp", "species_dictionary.txt", "tran.dat", "PROVENANCE.md"]
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 for f in provenance["files"])
    assert "Energy & Fuels" in provenance["emulated_publication"]
    assert provenance["cantera_version"] is None or provenance["cantera_version"][0].isdigit()


def test_corpus_counts_reflect_seeded_rows(db_session):
    before = generators.corpus_counts(db_session)
    species = make_species(db_session, smiles="CCCC")
    entry = make_species_entry(db_session, species=species)
    make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    after = generators.corpus_counts(db_session)
    assert after["species"] == before["species"] + 1
    assert after["species_entries"] == before["species_entries"] + 1
    assert after["calculations"] == before["calculations"] + 2
    assert after["calculations_by_type"]["sp"] == before["calculations_by_type"]["sp"] + 1
    assert after["calculations_by_type"]["freq"] == before["calculations_by_type"]["freq"] + 1
    assert set(after["calculations_by_type"]) == {kind.value for kind in CalculationType}


def test_candidate_lineage_lists_submissions_commits_and_artifact_digests(db_session):
    species = make_species(db_session, smiles="CCO")
    entry = make_species_entry(db_session, species=species)
    tool = make_workflow_tool_release(db_session, name="arc", version="9", git_commit="b" * 40)
    first = make_thermo_scalar(db_session, species_entry=entry, workflow_tool_release_id=tool.id)
    second = make_thermo_scalar(db_session, species_entry=entry)
    calculation = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    attach_artifact(db_session, calculation=calculation, sha256="c" * 64)
    attach_thermo_source_calculation(db_session, thermo=second, calculation=calculation)
    db_session.flush()

    lineage = generators.candidate_lineage(db_session)
    row = next(r for r in lineage["lineage"] if r["species_entry_ref"] == entry.public_ref)
    assert row["candidate_count"] == 2
    assert [c["thermo_ref"] for c in row["candidates"]] == sorted([first.public_ref, second.public_ref])
    by_ref = {c["thermo_ref"]: c for c in row["candidates"]}
    assert by_ref[first.public_ref]["workflow_tool_release_commits"] == ["b" * 40]
    assert by_ref[second.public_ref]["artifact_digests"] == ["c" * 64]
    assert by_ref[second.public_ref]["source_calculation_refs"] == [calculation.public_ref]
    assert by_ref[second.public_ref]["review_state"] == "not_reviewed"
    assert row["distinct_artifact_digests"] == 1


def test_selected_thermo_is_empty_when_no_release_is_published(db_session):
    out = generators.selected_thermo_by_species(db_session)
    assert out["selections"] == len(out["selected_thermo"])
    assert SubmissionRecordType.thermo.value == "thermo"


# ---------------------------------------------------------------------------
# Phase C-E4 generators
# ---------------------------------------------------------------------------


def test_experimental_cp_comparison_reports_every_finding_field_with_no_ids(db_session):
    from app.db.models.common import (
        MolecularPropertyKind,
        ObservedStateBasis,
        ScientificOriginKind,
    )
    from app.db.models.molecular_property_observation import MolecularPropertyObservation
    from app.services.external_comparison.cp import run_and_record
    from tests.services.scientific_read._factories import attach_thermo_nasa

    species = make_species(db_session, smiles="c1ccccc1O")
    entry = make_species_entry(db_session, species=species)
    thermo = make_thermo_scalar(
        db_session, species_entry=entry, scientific_origin=ScientificOriginKind.computed
    )
    attach_thermo_nasa(db_session, thermo=thermo)
    obs = MolecularPropertyObservation(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.experimental,
        property_kind=MolecularPropertyKind.heat_capacity_cp,
        scalar_value=95.0,
        scalar_unit="J/mol/K",
        temperature_k=298.15,
        state_basis=ObservedStateBasis.ideal_gas,
    )
    db_session.add(obs)
    db_session.flush()

    run_and_record(db_session, thermo.id)
    db_session.flush()

    out = generators.experimental_cp_comparison(db_session)
    row = next(c for c in out["comparisons"] if c["thermo_ref"] == thermo.public_ref)
    assert row["species_ref"] == species.public_ref
    assert row["species_entry_ref"] == entry.public_ref
    assert row["finding_count"] == 1
    (finding,) = row["findings"]
    assert finding["temperature_k"] == pytest.approx(298.15)
    assert finding["cp_observed_j_mol_k"] == pytest.approx(95.0)
    assert finding["representation"] == "nasa7"
    assert finding["comparability"] == "comparable"
    assert "observation_ref" in finding

    # Byte-stable and id-free, same as every other generator.
    first = render_json(out)
    second = render_json(generators.experimental_cp_comparison(db_session))
    assert first == second

    def _keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from _keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from _keys(item)

    offenders = {k for k in _keys(json.loads(first)) if k == "id" or k.endswith("_id")}
    assert not offenders, offenders


def test_thermoml_source_provenance_lists_custody_rows_with_counts(db_session):
    from datetime import datetime as _dt

    from app.db.models.common import ExternalSourceRecordKind
    from app.db.models.external_source import ExternalSource, ExternalSourceRecord

    source = ExternalSource(source_name="NIST ThermoML Archive", source_release="paper-generator-test")
    db_session.add(source)
    db_session.flush()
    record = ExternalSourceRecord(
        external_source_id=source.id,
        record_kind=ExternalSourceRecordKind.thermoml_article,
        source_uri="https://trc.nist.gov/ThermoML/paper_generator_test.xml",
        source_record_key="10.1016/j.jct.2013.08.022#P1/V1",
        retrieved_at=_dt(2026, 9, 1, 12, 0, 0),
        content_sha256="b" * 64,
        content_length=42,
        raw_uri="artifacts/paper-generator-test",
        parser_name="thermoml_cp_parser",
        parser_version="1.0.0",
        mapping_version="1.0.0",
        mapping_report_json={"values_mapped": [1, 2, 3], "rejected": []},
    )
    db_session.add(record)
    db_session.flush()

    out = generators.thermoml_source_provenance(db_session)
    row = next(r for r in out["thermoml_source_records"] if r["record_key"] == record.source_record_key)
    assert row["source_name"] == "NIST ThermoML Archive"
    assert row["content_sha256"] == "b" * 64
    assert row["parser_version"] == "1.0.0"
    assert row["mapping_report_counts"] == {"values_mapped": 3, "rejected": 0}

    first = render_json(out)
    second = render_json(generators.thermoml_source_provenance(db_session))
    assert first == second
