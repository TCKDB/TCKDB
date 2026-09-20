"""Tests for the ThermoML Cp(T) persistence service + CLI (Phase C-E3).

Uses the per-test transactional ``db_session`` fixture so every test rolls
back at teardown. DOI metadata lookup is always monkeypatched -- this
service must never hit the network during a test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    MoleculeKind,
    RightsBasisKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
    SubmissionSourceKind,
)
from app.db.models.external_source import ExternalSourceRecord
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.importers.thermoml.archive import ArticleBytes
from app.services.thermoml_cp_import import import_thermoml_cp_article

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "importers" / "thermoml" / "fixtures"

FLUOROETHANE_DOI = "10.1016/j.fluid.2016.07.034"
FLUOROETHANE_INCHIKEY = "UHCBBWUQDAVSMS-UHFFFAOYSA-N"

BENZENE_DOI = "10.1016/j.jct.2013.08.022"
BENZENE_INCHIKEY = "UHOVQNZJYSORNB-UHFFFAOYSA-N"

_LICENSE_ID = "CC0-1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _article_from_xml(xml_bytes: bytes, *, member_stub: str = "10.1016/fixture") -> ArticleBytes:
    return ArticleBytes(
        xml=xml_bytes,
        json_bytes=b"{}",
        xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        json_sha256=hashlib.sha256(b"{}").hexdigest(),
        member_paths=(f"{member_stub}.xml", f"{member_stub}.json"),
    )


def _fluoroethane_article() -> ArticleBytes:
    return _article_from_xml((FIXTURES / "cp_gas_single_component.xml").read_bytes())


_BENZENE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="http://www.iupac.org/namespaces/ThermoML"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xsi:schemaLocation="http://www.iupac.org/namespaces/ThermoML ../schema/ThermoML.xsd">
  <Version>
    <nVersionMajor>4</nVersionMajor>
    <nVersionMinor>0</nVersionMinor>
  </Version>
  <Citation>
    <sAuthor>Roe, S.</sAuthor>
    <sPubName>J. Chem. Thermodyn.</sPubName>
    <yrPubYr>2013</yrPubYr>
    <sTitle>Fixture: statistical-thermodynamics heat capacity of benzene (invented values)</sTitle>
    <sDOI>{doi}</sDOI>
  </Citation>
  <Compound>
    <nCompIndex>1</nCompIndex>
    <RegNum>
      <nCASRNum>71432</nCASRNum>
    </RegNum>
    <sStandardInChI>InChI=1S/C6H6/c1-2-4-6-5-3-1/h1-6H</sStandardInChI>
    <sStandardInChIKey>{inchikey}</sStandardInChIKey>
    <sCommonName>benzene</sCommonName>
    <sFormulaMolec>C6H6</sFormulaMolec>
    <sSmiles>c1ccccc1</sSmiles>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component>
      <nCompIndex>1</nCompIndex>
    </Component>
    <eExpPurpose>Principal objective of the work</eExpPurpose>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <sMethodName>{method_name}</sMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID>
        <ePropPhase>Ideal gas</ePropPhase>
      </PropPhaseID>
      <ePresentation>Direct value, X</ePresentation>
    </Property>
    <PhaseID>
      <ePhase>Ideal gas</ePhase>
    </PhaseID>
    <Constraint>
      <nConstraintNumber>1</nConstraintNumber>
      <ConstraintID>
        <ConstraintType>
          <ePressure>Pressure, kPa</ePressure>
        </ConstraintType>
      </ConstraintID>
      <nConstraintValue>100.0</nConstraintValue>
      <nConstrDigits>4</nConstrDigits>
    </Constraint>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID>
        <VariableType>
          <eTemperature>Temperature, K</eTemperature>
        </VariableType>
      </VariableID>
    </Variable>
    <NumValues>
      <VariableValue>
        <nVarNumber>1</nVarNumber>
        <nVarValue>298.15</nVarValue>
        <nVarDigits>5</nVarDigits>
      </VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>82.44</nPropValue>
        <nPropDigits>4</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""


def _benzene_article(*, method_name: str = "statistical thermodynamics") -> ArticleBytes:
    xml = _BENZENE_XML.format(
        doi=BENZENE_DOI, inchikey=BENZENE_INCHIKEY, method_name=method_name
    ).encode("utf-8")
    return _article_from_xml(xml, member_stub="10.1016/jct-fixture")


SCHEMA_INVALID_DOI = "10.1021/acs.jced.9xxxxxx"


def _schema_invalid_article() -> ArticleBytes:
    return _article_from_xml(
        (FIXTURES / "schema_invalid.xml").read_bytes(),
        member_stub="10.1021/schema-invalid-fixture",
    )


@pytest.fixture(autouse=True)
def _no_network_doi_lookup(monkeypatch):
    """This service must never hit the network for a citation lookup."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: None,
    )


@pytest.fixture
def curator(db_session) -> AppUser:
    user = AppUser(username="thermoml_curator_test", role=AppUserRole.curator)
    db_session.add(user)
    db_session.flush()
    return user


def _seed_species_entry(
    db_session,
    *,
    smiles: str,
    inchi_key: str,
    kind: StationaryPointKind = StationaryPointKind.minimum,
    state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground,
) -> int:
    species = Species(
        smiles=smiles,
        inchi_key=inchi_key,
        charge=0,
        multiplicity=1,
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
# Known-problem / dry-run vs commit
# ---------------------------------------------------------------------------


class TestDryRunVsCommit:
    def test_dry_run_inserts_nothing(self, db_session, curator):
        before = db_session.execute(select(MolecularPropertyObservation)).all()
        before_subs = db_session.execute(select(Submission)).all()

        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=False,
        )

        after = db_session.execute(select(MolecularPropertyObservation)).all()
        after_subs = db_session.execute(select(Submission)).all()
        assert len(after) == len(before)
        assert len(after_subs) == len(before_subs)
        assert result.would_insert_count == 3
        assert result.inserted_count == 0

    def test_commit_persists_rows_with_new_columns_populated(self, db_session, curator):
        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert result.inserted_count == 3
        rows = db_session.execute(
            select(MolecularPropertyObservation)
            .where(MolecularPropertyObservation.external_source_doi == FLUOROETHANE_DOI)
            .order_by(MolecularPropertyObservation.temperature_k)
        ).scalars().all()
        assert len(rows) == 3

        first = rows[0]
        assert first.pressure_bar == pytest.approx(1.01325)
        assert first.state_basis.value == "real_gas"
        assert first.uncertainty_kind.value == "combined_expanded"
        assert first.uncertainty_coverage_factor == 2.0
        assert first.uncertainty_level_of_confidence_pct == 95.0
        assert first.uncertainty_assessor.value == "source_evaluator"
        assert first.external_source_record_id == result.external_source_record_id
        assert first.property_kind.value == "heat_capacity_cp"
        assert first.scalar_unit == "J/mol/K"


# ---------------------------------------------------------------------------
# Schema-invalid document -- rejected before any parsing
# ---------------------------------------------------------------------------


class TestSchemaInvalidDocument:
    def test_schema_invalid_document_persists_nothing(self, db_session, curator):
        """A schema-invalid document is refused by E2's ``validate_bytes``
        before this service ever parses it. Nothing lands: no custody row,
        no submission, no observation. The result reports the rejection
        instead.

        Mutation: comment out the ``if not schema_report.valid:`` early
        return (letting the invalid document flow into ``parse_thermoml_
        document`` anyway) -- this goes red because a custody row and
        an observation would then appear for a document the schema gate
        was supposed to stop.
        """

        before_observations = db_session.execute(
            select(MolecularPropertyObservation)
        ).all()
        before_submissions = db_session.execute(select(Submission)).all()
        before_custody = db_session.execute(select(ExternalSourceRecord)).all()

        result = import_thermoml_cp_article(
            db_session,
            article=_schema_invalid_article(),
            doi=SCHEMA_INVALID_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )

        assert result.schema_valid is False
        assert result.payload_count == 0
        assert result.inserted_count == 0
        assert result.submission_id is None
        assert result.external_source_id is None
        assert result.external_source_record_id is None
        assert any("schema-invalid" in w for w in result.warnings)

        after_observations = db_session.execute(
            select(MolecularPropertyObservation)
        ).all()
        after_submissions = db_session.execute(select(Submission)).all()
        after_custody = db_session.execute(select(ExternalSourceRecord)).all()
        assert len(after_observations) == len(before_observations)
        assert len(after_submissions) == len(before_submissions)
        assert len(after_custody) == len(before_custody)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_is_all_duplicate_no_new_custody_or_submission(
        self, db_session, curator
    ):
        first = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert first.inserted_count == 3

        custody_count_before = len(
            db_session.execute(select(ExternalSourceRecord)).all()
        )
        submission_count_before = len(db_session.execute(select(Submission)).all())

        second = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )

        assert second.inserted_count == 0
        assert second.duplicate_count == 3
        assert all(d.action == "duplicate" for d in second.dispositions)

        custody_count_after = len(
            db_session.execute(select(ExternalSourceRecord)).all()
        )
        submission_count_after = len(db_session.execute(select(Submission)).all())
        assert custody_count_after == custody_count_before
        assert submission_count_after == submission_count_before

        rows = db_session.execute(
            select(MolecularPropertyObservation)
            .where(MolecularPropertyObservation.external_source_doi == FLUOROETHANE_DOI)
        ).scalars().all()
        assert len(rows) == 3

    def test_changed_mapping_version_appends_custody_row(
        self, db_session, curator, monkeypatch
    ):
        first = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        custody_before = len(db_session.execute(select(ExternalSourceRecord)).all())

        monkeypatch.setattr(
            "app.services.thermoml_cp_import.MAPPING_VERSION",
            "thermoml-cp-mapping/9.9.9-test",
        )
        second = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        custody_after = len(db_session.execute(select(ExternalSourceRecord)).all())
        assert custody_after == custody_before + 1
        assert second.external_source_record_created is True
        assert second.external_source_record_id != first.external_source_record_id


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


class TestIdentityResolution:
    def test_exact_inchikey_match_resolves(self, db_session, curator):
        entry_id = _seed_species_entry(
            db_session, smiles="CCF", inchi_key=FLUOROETHANE_INCHIKEY
        )
        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert result.resolved_identity_count == 3
        rows = db_session.execute(select(MolecularPropertyObservation)).scalars().all()
        assert all(r.species_entry_id == entry_id for r in rows)

    def test_ambiguous_inchikey_stays_unresolved_with_candidates_retained(
        self, db_session, curator
    ):
        species = Species(
            smiles="CCF", inchi_key=FLUOROETHANE_INCHIKEY,
            charge=0, multiplicity=1,
            kind=MoleculeKind.molecule, stereo_kind=StereoKind.achiral,
        )
        db_session.add(species)
        db_session.flush()
        for isotope_key in (None, "[2H]C[2H]F"):
            db_session.add(
                SpeciesEntry(
                    species_id=species.id,
                    unmapped_smiles="CCF",
                    isotope_key=isotope_key,
                )
            )
        db_session.flush()

        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert result.ambiguous_identity_count == 3
        rows = db_session.execute(select(MolecularPropertyObservation)).scalars().all()
        assert all(r.species_entry_id is None for r in rows)
        # Every candidate is retained in raw_payload_json, never picked.
        for row in rows:
            hint = row.raw_payload_json["identity_hint"]
            assert hint["inchikey"] == FLUOROETHANE_INCHIKEY

    def test_never_creates_a_species(self, db_session, curator):
        species_count_before = len(db_session.execute(select(Species)).all())
        import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        species_count_after = len(db_session.execute(select(Species)).all())
        assert species_count_after == species_count_before


# ---------------------------------------------------------------------------
# Submission + rights attestation
# ---------------------------------------------------------------------------


class TestSubmissionAndRights:
    def test_submission_opened_bulk_import_with_one_source_terms_attestation(
        self, db_session, curator
    ):
        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        submission = db_session.get(Submission, result.submission_id)
        assert submission is not None
        assert submission.source_kind == SubmissionSourceKind.bulk_import

        attestations = db_session.execute(
            select(SubmissionRightsAttestation).where(
                SubmissionRightsAttestation.submission_id == submission.id
            )
        ).scalars().all()
        source_terms_rows = [
            a for a in attestations if a.basis == RightsBasisKind.source_terms
        ]
        assert len(source_terms_rows) == 1
        assert "ThermoML" in source_terms_rows[0].source_terms

        links = db_session.execute(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission.id
            )
        ).scalars().all()
        assert len(links) == 3

    def test_literature_resolved_by_doi(self, db_session, curator):
        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert result.literature_id is not None
        from app.db.models.literature import Literature

        lit = db_session.get(Literature, result.literature_id)
        assert lit.doi == FLUOROETHANE_DOI.lower()


# ---------------------------------------------------------------------------
# sMethodName origin allowlist (benzene pilot)
# ---------------------------------------------------------------------------


class TestSMethodNameAllowlist:
    def test_statistical_thermodynamics_maps_to_computed(self, db_session, curator):
        result = import_thermoml_cp_article(
            db_session,
            article=_benzene_article(method_name="statistical thermodynamics"),
            doi=BENZENE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert result.inserted_count == 1
        row = db_session.execute(
            select(MolecularPropertyObservation).where(
                MolecularPropertyObservation.external_source_doi == BENZENE_DOI
            )
        ).scalar_one()
        assert row.scientific_origin.value == "computed"

    def test_unknown_smethodname_string_is_rejected(self, db_session, curator):
        result = import_thermoml_cp_article(
            db_session,
            article=_benzene_article(method_name="a method nobody allowlisted"),
            doi=BENZENE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert result.inserted_count == 0
        rows = db_session.execute(
            select(MolecularPropertyObservation).where(
                MolecularPropertyObservation.external_source_doi == BENZENE_DOI
            )
        ).scalars().all()
        assert rows == []


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def test_service_does_not_import_upload_workflows():
    """This service composes lower-level primitives (rights, submission,
    literature, identity) directly and must not pull in a per-family upload
    workflow module."""

    from app.services import thermoml_cp_import as svc

    forbidden = ("persist_thermo_upload", "persist_statmech_upload")
    for name in forbidden:
        assert name not in svc.__dict__, f"service leaked upload-workflow symbol {name!r}"


# ---------------------------------------------------------------------------
# CLI (argument validation + failure paths only -- a real run needs a live
# DB and is exercised by the service tests above; the CLI is a thin wrapper)
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_missing_required_args_exits_2(self):
        from scripts.thermoml_cp_import import main

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_cli_archive_fetch_failure_returns_2(self, tmp_path, monkeypatch):
        from scripts import thermoml_cp_import as cli

        def _boom(url, dest):
            raise RuntimeError("network unavailable in test")

        monkeypatch.setattr(cli, "fetch_archive", _boom)
        rc = cli.main(
            [
                "--archive", str(tmp_path / "missing.tgz"),
                "--doi", FLUOROETHANE_DOI,
                "--license", _LICENSE_ID,
            ]
        )
        assert rc == 2

    def test_cli_article_not_found_returns_2(self, tmp_path, monkeypatch):
        from scripts import thermoml_cp_import as cli

        archive_path = tmp_path / "present.tgz"
        archive_path.write_bytes(b"not a real tgz, never opened by the mock below")

        def _not_found(tgz_path, doi):
            raise RuntimeError("no article found")

        monkeypatch.setattr(cli, "select_article", _not_found)
        rc = cli.main(
            [
                "--archive", str(archive_path),
                "--doi", FLUOROETHANE_DOI,
                "--license", _LICENSE_ID,
            ]
        )
        assert rc == 2
