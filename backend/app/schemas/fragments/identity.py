from pydantic import Field, field_validator

from app.db.models.common import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.schemas.common import SchemaBase
from app.schemas.entities.species_entry import SpeciesEntryIdentityValidatorMixin
from app.schemas.utils import normalize_required_text


class SpeciesIdentityPayload(SchemaBase):
    """Reusable upload fragment for graph identity resolution.

    :param molecule_kind: High-level species kind. Current workflows assume molecules.
    :param smiles: Input graph identity SMILES string.
    :param charge: Expected formal charge for the uploaded identity.
    :param multiplicity: Expected spin multiplicity for the uploaded identity.
    """

    molecule_kind: MoleculeKind = MoleculeKind.molecule
    smiles: str = Field(min_length=1)
    charge: int
    multiplicity: int = Field(ge=1)

    @field_validator("smiles")
    @classmethod
    def normalize_smiles(cls, value: str) -> str:
        return normalize_required_text(value)


class SpeciesEntryIdentityPayload(
    SpeciesEntryIdentityValidatorMixin,
    SpeciesIdentityPayload,
):
    """Reusable upload fragment for resolved species-entry identity.

    :param species_entry_kind: Stationary-point kind for the resolved entry.
    :param unmapped_smiles: Optional display/search SMILES for the resolved entry.
    :param stereo_kind: Stereo classification for the resolved entry.
    :param stereo_label: Optional stereo label such as ``R`` or ``E``.
    :param electronic_state_kind: Electronic-state classification.
    :param electronic_state_label: Optional state label such as ``X`` or ``A``.
    :param term_symbol_raw: Optional raw uploaded term symbol.
    :param term_symbol: Optional canonicalized term symbol.
    :param isotopologue_label: Optional isotopologue label.
    """

    species_entry_kind: StationaryPointKind = StationaryPointKind.minimum
    unmapped_smiles: str | None = None

    stereo_kind: StereoKind = StereoKind.unspecified
    stereo_label: str | None = Field(default=None, max_length=64)

    electronic_state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground
    electronic_state_label: str | None = Field(default=None, max_length=8)

    term_symbol_raw: str | None = Field(default=None, max_length=64)
    term_symbol: str | None = Field(default=None, max_length=64)
    isotopologue_label: str | None = Field(default=None, max_length=64)
