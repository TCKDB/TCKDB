"""Shared statmech upload fragments.

Every reference out of these fragments is a **local string key**, never a
database row id (DR-0029 Requirement 1, and the "no FK IDs in upload
schemas" rule). A depositor can name a calculation they declared in the
same request; they cannot know its primary key, and a contract that asks
for one is only usable by something that has already queried the
database.

Three components live here, and each is the single home for its concept
across every upload path that carries statmech:

``StatmechTorsionCoordinateIn``
    The atom quartet defining one torsional coordinate. Shared by the
    conformer, standalone-statmech and bundle paths, and reused as the
    field home for the backend's ``*Read`` schema.

``StatmechSourceCalcIn``
    A statmech → calculation link, by local calculation key and
    scientific role. ``StatmechSourceCalcInBundle`` is a backwards
    compatible alias re-exported from the bundle modules.

``StatmechTorsionIn``
    One torsional mode, with its principal rotor scan addressed by local
    key. Shared by the conformer and standalone-statmech paths; the
    bundle keeps ``StatmechTorsionInBundle`` because it deliberately
    allows ``coordinates`` to be omitted.

The full ``StatmechUploadRequest`` (and its inline-calculation container)
stay backend-side because they orchestrate service-layer resolution and
ownership checks. The id-bearing ``*Base`` / ``*Read`` / ``*Update``
schemas stay backend-side too, in ``app.schemas.entities.statmech`` —
they describe persisted rows, which is not what a wire contract is for.
"""

from typing import Self

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import StatmechCalculationRole, TorsionTreatmentKind


class StatmechTorsionCoordinateIn(SchemaBase):
    """Atom indices for one torsional coordinate.

    :param coordinate_index: One-based coordinate number within the rotor.
    :param atom1_index: First atom index.
    :param atom2_index: Second atom index.
    :param atom3_index: Third atom index.
    :param atom4_index: Fourth atom index.
    """

    coordinate_index: int = Field(ge=1)
    atom1_index: int = Field(ge=1)
    atom2_index: int = Field(ge=1)
    atom3_index: int = Field(ge=1)
    atom4_index: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_distinct_atoms(self) -> Self:
        atoms = {
            self.atom1_index,
            self.atom2_index,
            self.atom3_index,
            self.atom4_index,
        }
        if len(atoms) != 4:
            raise ValueError("Torsion coordinate atom indices must be distinct.")
        return self


class StatmechSourceCalcIn(SchemaBase):
    """Statmech → calculation link, by local calculation key.

    Only a key is accepted (DR-0029 Requirement 1). The key resolves
    against whichever calc-key namespace the enclosing request defines —
    the bundle's global namespace, the standalone statmech upload's
    inline ``calculations`` list, or the keys a conformer upload put on
    its own primary/additional calculations — and the resolved row is
    attached as a ``statmech_source_calculation`` row with this role.

    :param calculation_key: Local key of a calculation declared in the
        same request.
    :param role: Scientific role the calculation plays for this statmech.
    """

    calculation_key: str = Field(min_length=1)
    role: StatmechCalculationRole


class StatmechTorsionIn(SchemaBase):
    """One statmech torsion, with its rotor scan addressed by local key.

    :param torsion_index: One-based torsion number within the statmech record.
    :param symmetry_number: Optional torsional symmetry number.
    :param treatment_kind: Optional torsion treatment kind.
    :param dimension: Number of coupled torsional coordinates in this rotor.
    :param top_description: Optional description of the rotating top.
    :param invalidated_reason: Optional reason why the torsion was invalidated.
    :param note: Optional free-text note.
    :param source_scan_calculation_key: Optional local key of the
        calculation that produced this rotor's scan. Resolves against the
        same calc-key namespace as ``StatmechSourceCalcIn``.
    :param coordinates: Ordered torsional coordinate definitions. The
        number of coordinates must equal ``dimension``, and
        ``coordinate_index`` values must run contiguously from
        ``1..dimension``.
    """

    torsion_index: int = Field(ge=1)
    symmetry_number: int | None = Field(default=None, ge=1)
    treatment_kind: TorsionTreatmentKind | None = None

    dimension: int = Field(default=1, ge=1)
    top_description: str | None = None
    invalidated_reason: str | None = None
    note: str | None = None

    source_scan_calculation_key: str | None = Field(default=None, min_length=1)

    coordinates: list[StatmechTorsionCoordinateIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if len(self.coordinates) != self.dimension:
            raise ValueError("Number of torsion coordinates must equal dimension.")

        coordinate_indices = [
            coordinate.coordinate_index for coordinate in self.coordinates
        ]
        expected_indices = list(range(1, self.dimension + 1))
        if sorted(coordinate_indices) != expected_indices:
            raise ValueError(
                "Torsion coordinate_index values must run contiguously from 1..dimension."
            )
        return self


#: Historical name for :class:`StatmechSourceCalcIn`, kept importable so
#: bundle-facing code and docs that spell it this way keep working. It is
#: the same class object, so there is one OpenAPI component, not two.
StatmechSourceCalcInBundle = StatmechSourceCalcIn
