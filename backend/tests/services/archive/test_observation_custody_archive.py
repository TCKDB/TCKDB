"""Archive coverage for source custody (Phase C-E1).

``external_source`` and ``external_source_record`` are new tables; a
forgotten entry in ``INCLUDED_TABLES``/``EXCLUDED_TABLES`` fails closed at
``validate_registry`` (exercised directly here, and also every time
``write_archive``/``restore_archive`` runs elsewhere), so the round trip
below is the second half of the story -- proof the registry entry actually
carries real rows through a restore, not just that the table is named.
"""

from __future__ import annotations

import io
from datetime import datetime

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.models.common import (
    ExternalSourceRecordKind,
    MolecularPropertyKind,
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    ScientificOriginKind,
)
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.services.archive import registry, restore_archive, write_archive
from app.services.archive.registry import ArchiveRegistryError, validate_registry
from tests.services.archive.test_archive import _empty_archive_tables


def test_validate_registry_includes_custody_tables():
    """Both new tables have an explicit archive decision."""
    validate_registry(Base.metadata)
    assert "external_source" in registry.INCLUDED_TABLES
    assert "external_source_record" in registry.INCLUDED_TABLES


def test_validate_registry_fails_closed_if_a_custody_table_is_forgotten(monkeypatch):
    """*Mutation*: forget one of the two tables in ``INCLUDED_TABLES`` --
    this reproduces that by removing it via monkeypatch and asserting the
    registry actually notices.
    """
    monkeypatch.setattr(
        registry,
        "INCLUDED_TABLES",
        registry.INCLUDED_TABLES - {"external_source_record"},
    )
    with pytest.raises(ArchiveRegistryError) as excinfo:
        validate_registry(Base.metadata)
    assert "external_source_record" in str(excinfo.value)


def test_round_trip_preserves_a_custody_row_and_an_observation(db_session):
    """A custody row plus the observation citing it survive a full
    write_archive -> empty -> restore_archive cycle intact.
    """
    source = ExternalSource(
        source_name="NIST ThermoML Archive",
        source_release="2024-06",
        source_database_doi="10.18434/T4D303",
        citation_text="NIST ThermoML Archive, 2024-06 release.",
    )
    db_session.add(source)
    db_session.flush()

    record = ExternalSourceRecord(
        external_source_id=source.id,
        record_kind=ExternalSourceRecordKind.thermoml_article,
        source_uri="https://trc.nist.gov/ThermoML/10.1016/j.jct.2020.01.001.xml",
        source_record_key="10.1016/j.jct.2020.01.001#PureOrMixtureData1/Property1/NumValue3",
        retrieved_at=datetime(2026, 9, 19, 12, 0, 0),
        http_status=200,
        content_sha256="c" * 64,
        content_length=8192,
        raw_uri="raw://thermoml/c" + "c" * 63,
        parser_name="thermoml",
        parser_version="1.0.0",
        mapping_version="mapping-v1",
        mapping_report_json={"unsupported": [], "rejected": []},
    )
    db_session.add(record)
    db_session.flush()

    observation = MolecularPropertyObservation(
        scientific_origin=ScientificOriginKind.experimental,
        property_kind=MolecularPropertyKind.heat_capacity_cp,
        scalar_value=29.1,
        scalar_unit="J/mol/K",
        temperature_k=298.15,
        pressure_bar=1.01325,
        state_basis=ObservedStateBasis.ideal_gas,
        scalar_uncertainty=0.5,
        uncertainty_kind=ObservedUncertaintyKind.expanded,
        uncertainty_coverage_factor=2.0,
        uncertainty_level_of_confidence_pct=95.0,
        uncertainty_assessor=ObservedUncertaintyAssessor.source_author,
        external_source_record_id=record.id,
    )
    db_session.add(observation)
    db_session.flush()
    observation_id = observation.id
    record_key = record.source_record_key
    source_name = source.source_name

    archive = io.BytesIO()
    write_archive(db_session, archive)
    frozen = archive.getvalue()

    _empty_archive_tables(db_session)
    restore_archive(db_session, io.BytesIO(frozen))

    restored_source = db_session.scalar(
        select(ExternalSource).where(ExternalSource.source_name == source_name)
    )
    assert restored_source is not None
    assert restored_source.source_release == "2024-06"

    restored_record = db_session.scalar(
        select(ExternalSourceRecord).where(
            ExternalSourceRecord.source_record_key == record_key
        )
    )
    assert restored_record is not None
    assert restored_record.content_sha256 == "c" * 64
    assert restored_record.mapping_report_json == {"unsupported": [], "rejected": []}
    assert restored_record.external_source_id == restored_source.id

    restored_observation = db_session.get(MolecularPropertyObservation, observation_id)
    assert restored_observation is not None
    assert restored_observation.property_kind == MolecularPropertyKind.heat_capacity_cp
    assert restored_observation.scalar_unit == "J/mol/K"
    assert restored_observation.state_basis == ObservedStateBasis.ideal_gas
    assert restored_observation.uncertainty_kind == ObservedUncertaintyKind.expanded
    assert restored_observation.uncertainty_coverage_factor == pytest.approx(2.0)
    assert restored_observation.external_source_record_id == restored_record.id

    assert archive.getvalue() == frozen
