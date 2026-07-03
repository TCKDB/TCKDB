"""Backend shim-identity test: old ``app.schemas.*`` import paths must
resolve to the very same classes that ``tckdb_schemas`` exports.

If these break, the shim modules in ``app/schemas/`` have drifted from
their extracted twins — every existing backend / client caller that
imports from the old paths would still type-check but see different
objects, which causes silent isinstance / model_rebuild failures.
"""

from __future__ import annotations


def test_computed_species_upload_request_shim_identity() -> None:
    from tckdb_schemas.workflows.computed_species_upload import (
        ComputedSpeciesUploadRequest as ExtractedComputedSpeciesUploadRequest,
    )

    from app.schemas.workflows.computed_species_upload import (
        ComputedSpeciesUploadRequest,
    )

    assert ComputedSpeciesUploadRequest is ExtractedComputedSpeciesUploadRequest


def test_computed_reaction_upload_request_shim_identity() -> None:
    from tckdb_schemas.workflows.computed_reaction_upload import (
        ComputedReactionUploadRequest as ExtractedComputedReactionUploadRequest,
    )

    from app.schemas.workflows.computed_reaction_upload import (
        ComputedReactionUploadRequest,
    )

    assert ComputedReactionUploadRequest is ExtractedComputedReactionUploadRequest


def test_calculation_with_results_payload_shim_identity() -> None:
    from tckdb_schemas.fragments.calculation import (
        CalculationWithResultsPayload as ExtractedCalculationWithResultsPayload,
    )

    from app.schemas.fragments.calculation import CalculationWithResultsPayload

    assert (
        CalculationWithResultsPayload is ExtractedCalculationWithResultsPayload
    )


def test_geometry_payload_shim_identity() -> None:
    from tckdb_schemas.fragments.geometry import (
        GeometryPayload as ExtractedGeometryPayload,
    )

    from app.schemas.fragments.geometry import GeometryPayload

    assert GeometryPayload is ExtractedGeometryPayload


def test_artifact_in_shim_identity() -> None:
    from tckdb_schemas.fragments.artifact import ArtifactIn as ExtractedArtifactIn

    from app.schemas.fragments.artifact import ArtifactIn

    assert ArtifactIn is ExtractedArtifactIn


def test_literature_upload_request_shim_identity() -> None:
    from tckdb_schemas.literature import (
        LiteratureUploadRequest as ExtractedLiteratureUploadRequest,
    )

    from app.schemas.workflows.literature_upload import LiteratureUploadRequest

    assert LiteratureUploadRequest is ExtractedLiteratureUploadRequest


def test_applied_energy_correction_payload_shim_identity() -> None:
    from tckdb_schemas.energy_correction import (
        AppliedEnergyCorrectionUploadPayload as ExtractedAppliedEnergyCorrectionUploadPayload,
    )

    from app.schemas.workflows.energy_correction_upload import (
        AppliedEnergyCorrectionUploadPayload,
    )

    assert (
        AppliedEnergyCorrectionUploadPayload
        is ExtractedAppliedEnergyCorrectionUploadPayload
    )


def test_network_pdep_calculation_in_shim_identity() -> None:
    """Network-PDep hybrid shim re-exports the moved CalculationIn /
    GeometryIn / adapter from ``tckdb_schemas.shared.calculation_in``.
    """
    from tckdb_schemas.shared.calculation_in import (
        CalculationIn as ExtractedCalculationIn,
    )
    from tckdb_schemas.shared.calculation_in import (
        GeometryIn as ExtractedGeometryIn,
    )
    from tckdb_schemas.shared.calculation_in import (
        calculation_in_to_with_results_payload as extracted_adapter,
    )

    from app.schemas.workflows.network_pdep_upload import (
        CalculationIn,
        GeometryIn,
        calculation_in_to_with_results_payload,
    )

    assert CalculationIn is ExtractedCalculationIn
    assert GeometryIn is ExtractedGeometryIn
    assert calculation_in_to_with_results_payload is extracted_adapter


def test_statmech_torsion_coordinate_in_shim_identity() -> None:
    from tckdb_schemas.statmech_bits import (
        StatmechTorsionCoordinateIn as ExtractedStatmechTorsionCoordinateIn,
    )

    from app.schemas.workflows.statmech_upload import StatmechTorsionCoordinateIn

    assert StatmechTorsionCoordinateIn is ExtractedStatmechTorsionCoordinateIn


def test_species_entry_identity_validator_mixin_shim_identity() -> None:
    from tckdb_schemas.fragments.identity import (
        SpeciesEntryIdentityValidatorMixin as ExtractedSpeciesEntryIdentityValidatorMixin,
    )

    from app.schemas.entities.species_entry import SpeciesEntryIdentityValidatorMixin

    assert (
        SpeciesEntryIdentityValidatorMixin
        is ExtractedSpeciesEntryIdentityValidatorMixin
    )


def test_calculation_scan_result_create_shim_identity() -> None:
    from tckdb_schemas.fragments.scan import (
        CalculationScanResultCreate as ExtractedCalculationScanResultCreate,
    )

    from app.schemas.entities.calculation import CalculationScanResultCreate

    assert CalculationScanResultCreate is ExtractedCalculationScanResultCreate


def test_thermo_nasa_create_shim_identity() -> None:
    from tckdb_schemas.thermo import ThermoNASACreate as ExtractedThermoNASACreate

    from app.schemas.entities.thermo import ThermoNASACreate

    assert ThermoNASACreate is ExtractedThermoNASACreate
