"""Tests for the ThermoML Cp(T) persistence service + CLI (Phase C-E3,
C-E6).

Uses the per-test transactional ``db_session`` fixture so every test rolls
back at teardown. DOI metadata lookup is always monkeypatched -- this
service must never hit the network during a test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from tckdb_schemas.rights import DepositRights

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
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.db.models.literature import Literature
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.importers.thermoml import TERMS_TEXT
from app.importers.thermoml.archive import ArticleBytes
from app.services.rights import standing_attestation
from app.services.thermoml_cp_import import (
    UPLOAD_SOURCE_NAME,
    UPLOAD_SOURCE_RELEASE,
    ThermoMLDoiConflictError,
    import_thermoml_cp_article,
    import_thermoml_cp_upload,
)

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


def _fluoroethane_article_with_nonce(nonce: str) -> ArticleBytes:
    """Same fixture, with a unique XML comment spliced in right after the
    declaration so the content -- and its SHA-256 -- is unique to one test
    run. Used only where a test needs a content-addressed object-store key
    nobody else (this file's own other tests, or a concurrent pytest run
    against the same shared local MinIO) could plausibly also write, so a
    "does this key exist" check is not a false-positive risk.
    """
    xml = (FIXTURES / "cp_gas_single_component.xml").read_text(encoding="utf-8")
    header, _, rest = xml.partition("\n")
    xml = f"{header}\n<!-- test-nonce: {nonce} -->\n{rest}"
    return _article_from_xml(xml.encode("utf-8"))


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

    def test_dry_run_does_not_write_to_object_store(self, db_session, curator):
        """A dry run (``commit=False``) must never upload the article bytes
        to S3/MinIO -- only ``commit=True`` may. ``_raw_uri_for`` computes
        the would-be content-addressed key locally instead.

        Mutation: in ``_raw_uri_for``, make the ``if not commit:`` guard
        unconditional again (always calling ``store_artifact`` regardless
        of ``commit``) -- goes red because ``head_artifact_object`` then
        finds the object the dry run was never supposed to write.
        """
        import uuid

        from app.services.artifact_storage import (
            S3_BUCKET,
            _get_s3_client,
            content_addressed_key,
            delete_artifact_object,
            head_artifact_object,
        )

        # Precondition, stated rather than implied: ``head_artifact_object``
        # returns ``None`` for "absent" AND for "unreachable", so without a
        # live object store the assertions below would be vacuously green.
        # Fail loudly instead of passing having verified nothing.
        try:
            _get_s3_client().head_bucket(Bucket=S3_BUCKET)
        except Exception as exc:  # any failure at all means "no store"
            pytest.fail(f"object store unreachable; this test needs MinIO: {exc!r}")

        article = _fluoroethane_article_with_nonce(uuid.uuid4().hex)
        sha256 = article.xml_sha256
        assert head_artifact_object(sha256) is None, (
            "unique nonce content already had an object in the local store "
            "-- this should be statistically impossible; investigate before "
            "trusting this test"
        )

        try:
            result = import_thermoml_cp_article(
                db_session,
                article=article,
                doi=FLUOROETHANE_DOI,
                actor=curator,
                license_id=_LICENSE_ID,
                commit=False,
            )

            assert head_artifact_object(sha256) is None, (
                "dry run wrote to the object store"
            )

            # The dry run rolls back the whole transaction (including the
            # custody row), so the would-be URI is only recoverable from
            # the result's own warnings, not by re-fetching the row.
            would_store_warnings = [
                w for w in result.warnings if "would_store" in w and "dry run" in w
            ]
            assert len(would_store_warnings) == 1
            assert content_addressed_key(sha256) in would_store_warnings[0]
        finally:
            # Defensive: only fires if the guard regressed and the object
            # was actually written, so a red run of this test doesn't
            # litter the shared local MinIO for the next one. This is a
            # deliberate exception to ``delete_artifact_object``'s "only
            # ``hold_artifact_object`` may call this" contract: the key is
            # nonce-unique, no row references it, and the transaction has
            # been rolled back, so nothing can be orphaned by the delete.
            delete_artifact_object(sha256)

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


_IDEMPOTENCY_TABLES: tuple[tuple[str, type], ...] = (
    ("molecular_property_observation", MolecularPropertyObservation),
    ("external_source_record", ExternalSourceRecord),
    ("external_source", ExternalSource),
    ("submission", Submission),
    ("submission_rights_attestation", SubmissionRightsAttestation),
    ("submission_record_link", SubmissionRecordLink),
    ("literature", Literature),
    ("species", Species),
    ("app_user", AppUser),
)


def _table_counts(db_session) -> dict[str, int]:
    return {
        name: len(db_session.execute(select(model)).all())
        for name, model in _IDEMPOTENCY_TABLES
    }


class TestIdempotency:
    def test_second_run_is_all_duplicate_no_new_custody_or_submission(
        self, db_session, curator
    ):
        """A second run of an already-fully-ingested article touches none of
        the nine tables a run can write to.

        Mutation table (one mutation per table this reproves is unchanged
        on a second run -- see the module docstring's dedupe-key
        pre-check design):

        - ``molecular_property_observation``, ``submission``,
          ``submission_rights_attestation``, ``submission_record_link``:
          make ``_existing_dedupe_id`` always ``return None`` (as if the
          pre-check were broken). The authoritative
          ``ON CONFLICT DO NOTHING`` still stops a duplicate row, but the
          now-wrong ``would_insert_count`` opens a *second* submission (and
          its attestations) with nothing to link -- goes red on
          ``submission``/``submission_rights_attestation`` (and, had
          anything actually inserted, ``submission_record_link`` too).
        - ``external_source_record``: in ``_get_or_create_custody``, drop
          the ``if existing is not None: return existing, False, False``
          early return so it always inserts. Goes red on
          ``external_source_record`` doubling.
        - ``external_source``: in ``_get_or_create_external_source``, drop
          its analogous early return. Goes red on ``external_source``
          doubling.
        - ``literature``: in ``app.services.literature_resolution.
          resolve_or_create_literature``, skip the existing-DOI lookup so
          it always creates. Goes red on ``literature`` doubling -- this
          service calls it unconditionally on every run, dedupe-key
          pre-check or not.
        - ``species``, ``app_user``: this service never writes either
          table on any path (see ``test_never_creates_a_species`` above);
          included here as a standing regression tripwire rather than a
          distinct mutation, since there is no code path in this service
          that could touch them.
        """
        first = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        assert first.inserted_count == 3

        before = _table_counts(db_session)

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

        after = _table_counts(db_session)
        assert after == before, {
            name: (before[name], after[name])
            for name in before
            if before[name] != after[name]
        }

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

    def test_standing_attestation_is_source_terms_superseding_depositor_agreement(
        self, db_session, curator
    ):
        """Two attestations are recorded per submission (module docstring,
        'Two attestations, one standing'): ``open_upload_submission``'s own
        ``depositor_agreement`` row, then an explicit ``source_terms`` row
        that supersedes it and becomes standing.

        Three independently load-bearing facts, each with its own mutation:

        1. ``standing_attestation()`` -- the same reader the release layer
           uses -- returns the ``source_terms`` row, not the
           ``depositor_agreement`` one.
           Mutation: comment out the explicit ``record_attestation(...,
           basis=RightsBasisKind.source_terms, ...)`` call in
           ``_open_submission_with_source_terms_attestation`` -- goes red
           because the standing row is then ``depositor_agreement``.
        2. The standing row's ``source_terms`` text equals ``TERMS_TEXT``
           verbatim (not just "contains ThermoML" as the older assertion
           checked).
           Mutation: pass ``source_terms=TERMS_TEXT[:-1]`` instead of
           ``TERMS_TEXT`` to that same call -- goes red on the equality
           check.
        3. The ``depositor_agreement`` row is retained (never deleted) and
           the ``source_terms`` row's ``supersedes_attestation_id`` points
           at it.
           Mutation: pass ``rights=None`` to ``open_upload_submission`` in
           ``_open_submission_with_source_terms_attestation`` -- no
           ``depositor_agreement`` row is ever created, so it goes red on
           the "exactly one depositor_agreement row" assertion.
        """
        result = import_thermoml_cp_article(
            db_session,
            article=_fluoroethane_article(),
            doi=FLUOROETHANE_DOI,
            actor=curator,
            license_id=_LICENSE_ID,
            commit=True,
        )
        submission = db_session.get(Submission, result.submission_id)

        attestations = db_session.execute(
            select(SubmissionRightsAttestation)
            .where(SubmissionRightsAttestation.submission_id == submission.id)
            .order_by(SubmissionRightsAttestation.id)
        ).scalars().all()

        depositor_rows = [
            a for a in attestations if a.basis == RightsBasisKind.depositor_agreement
        ]
        source_terms_rows = [
            a for a in attestations if a.basis == RightsBasisKind.source_terms
        ]
        assert len(depositor_rows) == 1
        assert len(source_terms_rows) == 1
        depositor_row = depositor_rows[0]
        source_terms_row = source_terms_rows[0]

        # (3) depositor_agreement retained; source_terms names it as the one
        # it supersedes.
        assert source_terms_row.supersedes_attestation_id == depositor_row.id

        # (2) verbatim text, not a substring match.
        assert source_terms_row.source_terms == TERMS_TEXT

        # (1) the standing-attestation reader (what the release layer
        # actually asks) names the source_terms row.
        standing = standing_attestation(db_session, submission_id=submission.id)
        assert standing is not None
        assert standing.id == source_terms_row.id
        assert standing.basis == RightsBasisKind.source_terms

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
# Upload path (Phase C-E6): anyone may ingest a ThermoML file, not only the
# NIST archive. ``import_thermoml_cp_upload`` shares the validate -> parse
# -> map -> persist core with ``import_thermoml_cp_article`` above but
# differs in custody (depositor-upload channel, not NIST) and rights
# (the caller's own ``DepositRights``, not a ``source_terms`` attestation).
# ---------------------------------------------------------------------------


_UPLOAD_LICENSE_ID = "CC0-1.0"


def _upload_rights(*, source_terms: str | None = None) -> DepositRights:
    return DepositRights(
        license=_UPLOAD_LICENSE_ID,
        depositor_attests_right_to_license=True,
        source_terms=source_terms,
    )


@pytest.fixture
def depositor(db_session) -> AppUser:
    """A plain ``user``-role account -- the upload path (unlike the
    archive path's ``source_terms`` attestation) needs no elevated role;
    it is a standard upload."""
    user = AppUser(username="thermoml_depositor_test", role=AppUserRole.user)
    db_session.add(user)
    db_session.flush()
    return user


class TestUploadPath:
    def test_upload_persists_rows_with_depositor_custody_not_nist(
        self, db_session, depositor
    ):
        """The custody row for an uploaded document must name the
        depositor-upload channel, never NIST.

        Mutation: pass ``name=SOURCE_NAME, release=SOURCE_RELEASE`` (the
        NIST constants) instead of ``UPLOAD_SOURCE_NAME``/
        ``UPLOAD_SOURCE_RELEASE`` in ``import_thermoml_cp_upload``'s
        ``external_source_kwargs`` -- this assertion goes red because the
        row's ``source_name`` would then read "NIST TRC ThermoML
        Archive".
        """
        result = import_thermoml_cp_upload(
            db_session,
            article=_benzene_article(),
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=True,
        )
        assert result.inserted_count == 1
        source = db_session.get(ExternalSource, result.external_source_id)
        assert source.source_name == UPLOAD_SOURCE_NAME
        assert source.source_release == UPLOAD_SOURCE_RELEASE
        assert source.source_name != "NIST TRC ThermoML Archive"

        custody = db_session.get(
            ExternalSourceRecord, result.external_source_record_id
        )
        # No bulk-archive container to cite -- honest absence, not the
        # archive's SHA-256.
        assert custody.container_digest is None
        assert custody.source_uri == "tckdb:depositor-upload"

    def test_upload_dry_run_writes_nothing_to_db_or_object_store(
        self, db_session, depositor
    ):
        before_rows = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        before_sources = db_session.execute(select(ExternalSource)).scalars().all()

        result = import_thermoml_cp_upload(
            db_session,
            article=_benzene_article(),
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=False,
        )
        assert result.would_insert_count == 1
        assert result.inserted_count == 0
        assert any(
            "object store not written" in w for w in result.warnings
        )

        after_rows = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        after_sources = db_session.execute(select(ExternalSource)).scalars().all()
        assert after_rows == before_rows
        assert after_sources == before_sources

    def test_doi_taken_from_file_sdoi_when_not_supplied(self, db_session, depositor):
        result = import_thermoml_cp_upload(
            db_session,
            article=_benzene_article(),
            doi=None,
            actor=depositor,
            rights=_upload_rights(),
            commit=True,
        )
        assert result.doi == BENZENE_DOI
        row = db_session.execute(
            select(MolecularPropertyObservation).where(
                MolecularPropertyObservation.external_source_doi == BENZENE_DOI
            )
        ).scalar_one()
        assert row is not None

    def test_doi_conflict_is_refused_before_anything_is_written(
        self, db_session, depositor
    ):
        before = db_session.execute(select(ExternalSource)).scalars().all()
        with pytest.raises(ThermoMLDoiConflictError):
            import_thermoml_cp_upload(
                db_session,
                article=_benzene_article(),  # sDOI = BENZENE_DOI
                doi="10.1000/not-the-same-doi",
                actor=depositor,
                rights=_upload_rights(),
                commit=True,
            )
        after = db_session.execute(select(ExternalSource)).scalars().all()
        assert after == before
        rows = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        assert rows == []

    def test_second_upload_of_identical_bytes_is_all_duplicate_no_second_submission(
        self, db_session, depositor
    ):
        article = _benzene_article()
        first = import_thermoml_cp_upload(
            db_session,
            article=article,
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=True,
        )
        assert first.inserted_count == 1
        assert first.submission_id is not None

        second = import_thermoml_cp_upload(
            db_session,
            article=article,
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=True,
        )
        assert second.inserted_count == 0
        assert second.duplicate_count == 1
        # No new submission opened for an all-duplicate run.
        assert second.submission_id is None
        assert second.submission_ref is None

    def test_upload_rights_is_depositor_agreement_only_no_source_terms(
        self, db_session, depositor
    ):
        """Unlike the archive path, an uploaded file's submission stands
        on the depositor's own ``DepositRights`` recorded as an ordinary
        ``depositor_agreement`` -- never a fabricated ``source_terms`` row
        (there is no third-party source these bytes were taken under).

        Mutation: call ``_open_submission_with_source_terms_attestation``
        (the archive path's helper) instead of
        ``_open_upload_submission_for_depositor`` inside
        ``import_thermoml_cp_upload`` -- this goes red because a
        ``source_terms`` row would then exist.
        """
        result = import_thermoml_cp_upload(
            db_session,
            article=_benzene_article(),
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=True,
        )
        submission = db_session.get(Submission, result.submission_id)
        assert submission.source_kind == SubmissionSourceKind.api

        attestations = db_session.execute(
            select(SubmissionRightsAttestation).where(
                SubmissionRightsAttestation.submission_id == submission.id
            )
        ).scalars().all()
        assert len(attestations) == 1
        assert attestations[0].basis == RightsBasisKind.depositor_agreement

        standing = standing_attestation(db_session, submission_id=submission.id)
        assert standing is not None
        assert standing.basis == RightsBasisKind.depositor_agreement
        assert standing.license_id == _UPLOAD_LICENSE_ID

    def test_observation_ref_is_none_on_dry_run(self, db_session, depositor):
        # A dry run's internal ``session.rollback()`` would also discard an
        # actor row created earlier in the same transaction if it had not
        # yet been committed -- one call per test/actor combination, like
        # every other dry-run test in this module.
        dry = import_thermoml_cp_upload(
            db_session,
            article=_benzene_article(),
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=False,
        )
        assert dry.dispositions[0].action == "would_insert"
        assert dry.dispositions[0].observation_ref is None

    def test_observation_ref_is_set_on_commit(self, db_session, depositor):
        committed = import_thermoml_cp_upload(
            db_session,
            article=_benzene_article(),
            doi=BENZENE_DOI,
            actor=depositor,
            rights=_upload_rights(),
            commit=True,
        )
        assert committed.dispositions[0].action == "inserted"
        assert committed.dispositions[0].observation_ref is not None
        assert committed.dispositions[0].observation_ref.startswith("mpo_")


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

    def test_cli_file_and_archive_mutually_exclusive_exits_2(self, tmp_path):
        """``--file``/``--archive`` are an argparse
        ``mutually_exclusive_group(required=True)`` -- argparse itself
        refuses this, before any of this module's own code runs.

        Mutation: change the group back to two independent optional
        arguments (drop the mutually-exclusive group) -- this goes red
        because argparse would then accept both flags together.
        """
        from scripts.thermoml_cp_import import main

        archive_path = tmp_path / "a.tgz"
        file_path = tmp_path / "f.xml"
        file_path.write_bytes(b"<DataReport/>")
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--archive", str(archive_path),
                    "--file", str(file_path),
                    "--license", _LICENSE_ID,
                ]
            )
        assert exc_info.value.code == 2

    def test_cli_neither_archive_nor_file_exits_2(self):
        from scripts.thermoml_cp_import import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--license", _LICENSE_ID])
        assert exc_info.value.code == 2

    def test_cli_archive_without_doi_exits_2(self, tmp_path):
        from scripts import thermoml_cp_import as cli

        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                [
                    "--archive", str(tmp_path / "a.tgz"),
                    "--license", _LICENSE_ID,
                ]
            )
        assert exc_info.value.code == 2

    def test_cli_file_missing_returns_2(self, tmp_path):
        from scripts import thermoml_cp_import as cli

        rc = cli.main(
            [
                "--file", str(tmp_path / "does-not-exist.xml"),
                "--license", _LICENSE_ID,
            ]
        )
        assert rc == 2

    def test_cli_file_mode_dry_run_persists_nothing(
        self, tmp_path, db_engine, monkeypatch
    ):
        """``--file`` runs the real ``import_thermoml_cp_upload`` pipeline
        against a real DB connection in dry-run mode (default, no
        ``--commit``), which rolls back internally -- proving the CLI
        wires the upload path end to end, not just that argparse accepts
        the flag.

        Mutation: have the ``--file`` branch call
        ``import_thermoml_cp_article`` (the archive function, which
        requires a ``str`` doi and would TypeError on ``doi=None``, or
        silently use the wrong custody path) instead of
        ``import_thermoml_cp_upload`` -- this goes red (non-zero exit or
        wrong behaviour).
        """
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from scripts import thermoml_cp_import as cli

        fixture_path = tmp_path / "benzene.xml"
        fixture_path.write_text(
            _BENZENE_XML.format(
                doi=BENZENE_DOI,
                inchikey=BENZENE_INCHIKEY,
                method_name="statistical thermodynamics",
            ),
            encoding="utf-8",
        )

        url = db_engine.url
        monkeypatch.setenv("DB_USER", url.username or "tckdb")
        monkeypatch.setenv("DB_PASSWORD", url.password or "tckdb")
        monkeypatch.setenv("DB_HOST", url.host or "127.0.0.1")
        monkeypatch.setenv("DB_PORT", str(url.port or 5432))
        monkeypatch.setenv("DB_NAME", url.database)
        monkeypatch.setattr(
            "app.services.literature_resolution.fetch_doi_metadata",
            lambda doi: None,
        )

        rc = cli.main(
            [
                "--file", str(fixture_path),
                "--license", _LICENSE_ID,
            ]
        )
        assert rc == 0

        with Session(db_engine) as session:
            rows = session.execute(
                select(MolecularPropertyObservation).where(
                    MolecularPropertyObservation.external_source_doi == BENZENE_DOI
                )
            ).scalars().all()
            assert rows == []
