"""Backend drift test: mirrored wire enums must match backend DB enums.

The ``tckdb_schemas.enums`` mirror is independent of
``app.db.models.common`` in PR 1 (the boundary forbids backend imports
from the standalone package). The two sets are kept in lockstep with
this test — both ``.value`` sets and declaration order are compared so
serialization order can't drift either.
"""

from __future__ import annotations

from enum import Enum

import pytest
from tckdb_schemas import enums as wire_enums

from app.db.models import common as db_enums

# (backend_enum, wire_enum) pairs to compare. Mirrors the audited
# upload-facing enum closure documented in tckdb_schemas/enums.py.
ENUM_PAIRS: list[tuple[type[Enum], type[Enum]]] = [
    (db_enums.ActivationEnergyUnits, wire_enums.ActivationEnergyUnits),
    (db_enums.AppliedCorrectionComponentKind, wire_enums.AppliedCorrectionComponentKind),
    (db_enums.ArrheniusAUnits, wire_enums.ArrheniusAUnits),
    (db_enums.ArtifactKind, wire_enums.ArtifactKind),
    (db_enums.CalculationDependencyRole, wire_enums.CalculationDependencyRole),
    (db_enums.CalculationGeometryRole, wire_enums.CalculationGeometryRole),
    (db_enums.CalculationQuality, wire_enums.CalculationQuality),
    (db_enums.CalculationType, wire_enums.CalculationType),
    (db_enums.ConstraintKind, wire_enums.ConstraintKind),
    (db_enums.CoordinateUnit, wire_enums.CoordinateUnit),
    (db_enums.EnergyCorrectionApplicationRole, wire_enums.EnergyCorrectionApplicationRole),
    (db_enums.EnergyCorrectionSchemeKind, wire_enums.EnergyCorrectionSchemeKind),
    (db_enums.EnergyUnit, wire_enums.EnergyUnit),
    (db_enums.FrequencyScaleKind, wire_enums.FrequencyScaleKind),
    (db_enums.IRCDirection, wire_enums.IRCDirection),
    (db_enums.KineticsCalculationRole, wire_enums.KineticsCalculationRole),
    (db_enums.KineticsDirection, wire_enums.KineticsDirection),
    (db_enums.KineticsModelKind, wire_enums.KineticsModelKind),
    (db_enums.KineticsUncertaintyKind, wire_enums.KineticsUncertaintyKind),
    (db_enums.LiteratureKind, wire_enums.LiteratureKind),
    (db_enums.MeliusBacComponentKind, wire_enums.MeliusBacComponentKind),
    (db_enums.MoleculeKind, wire_enums.MoleculeKind),
    (db_enums.NetworkChannelKind, wire_enums.NetworkChannelKind),
    (db_enums.NetworkSolveCalculationRole, wire_enums.NetworkSolveCalculationRole),
    (db_enums.PathSearchMethod, wire_enums.PathSearchMethod),
    (db_enums.RigidRotorKind, wire_enums.RigidRotorKind),
    (db_enums.SCFStabilityStatus, wire_enums.SCFStabilityStatus),
    (db_enums.ScientificOriginKind, wire_enums.ScientificOriginKind),
    (db_enums.SpeciesEntryStateKind, wire_enums.SpeciesEntryStateKind),
    (db_enums.StatmechCalculationRole, wire_enums.StatmechCalculationRole),
    (db_enums.StatmechTreatmentKind, wire_enums.StatmechTreatmentKind),
    (db_enums.StationaryPointKind, wire_enums.StationaryPointKind),
    (db_enums.StereoKind, wire_enums.StereoKind),
    (db_enums.ThermoCalculationRole, wire_enums.ThermoCalculationRole),
    (db_enums.TorsionTreatmentKind, wire_enums.TorsionTreatmentKind),
]


@pytest.mark.parametrize("backend_enum,wire_enum", ENUM_PAIRS, ids=lambda e: e.__name__)
def test_wire_enum_values_match_backend(backend_enum, wire_enum) -> None:
    """Wire enum ``.value`` set must equal backend enum ``.value`` set."""
    backend_values = {m.value for m in backend_enum}
    wire_values = {m.value for m in wire_enum}
    assert wire_values == backend_values, (
        f"{wire_enum.__name__} drifted from {backend_enum.__name__}: "
        f"only-in-wire={wire_values - backend_values!r}, "
        f"only-in-backend={backend_values - wire_values!r}"
    )


@pytest.mark.parametrize("backend_enum,wire_enum", ENUM_PAIRS, ids=lambda e: e.__name__)
def test_wire_enum_declaration_order_matches_backend(backend_enum, wire_enum) -> None:
    """Declaration order must match so iteration / serialization order is stable."""
    assert [m.value for m in wire_enum] == [m.value for m in backend_enum], (
        f"{wire_enum.__name__} declaration order drifted from "
        f"{backend_enum.__name__}."
    )
