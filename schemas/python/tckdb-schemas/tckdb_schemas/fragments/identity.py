"""Identity upload fragments and the identity-text validator mixin.

Combines:

* ``SpeciesEntryIdentityValidatorMixin`` (from the backend
  ``app.schemas.entities.species_entry`` module) — the shared
  identity-text normalizer also reused by the backend's read/write
  species-entry schemas.
* ``SpeciesIdentityPayload`` and ``SpeciesEntryIdentityPayload`` (from
  the backend ``app.schemas.fragments.identity`` module) — the
  upload-facing identity fragments embedded in every computed-species
  and computed-reaction bundle.

Backend-only read/CRUD species-entry schemas remain in the backend
package and continue to import the mixin from this module via the shim.
"""

from typing import Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text

_IDENTITY_TEXT_FIELDS = (
    "unmapped_smiles",
    "stereo_label",
    "electronic_state_label",
    "term_symbol_raw",
    "term_symbol",
    "isotopologue_label",
)


class SpeciesEntryIdentityValidatorMixin:
    @model_validator(mode="after")
    def normalize_identity_text_fields(self) -> Self:
        """Normalize optional identity text fields without imposing stricter semantics yet."""

        for field_name in _IDENTITY_TEXT_FIELDS:
            setattr(
                self,
                field_name,
                normalize_optional_text(getattr(self, field_name, None)),
            )

        return self


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
