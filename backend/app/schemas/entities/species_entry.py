"""Backend hybrid module — identity validator mixin lives in
``tckdb_schemas.fragments.identity``; backend-only ORM-read/CRUD shapes
for ``species_entry`` remain here.
"""

from pydantic import BaseModel, Field
from tckdb_schemas.fragments.identity import (
    _IDENTITY_TEXT_FIELDS,
    SpeciesEntryIdentityValidatorMixin,
)

from app.db.models.common import (
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.common import SchemaBase, TimestampedCreatedByReadSchema


class SpeciesEntryBase(SpeciesEntryIdentityValidatorMixin, BaseModel):
    species_id: int
    kind: StationaryPointKind = StationaryPointKind.minimum

    unmapped_smiles: str | None = None

    stereo_label: str | None = Field(default=None, max_length=64)

    electronic_state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground
    electronic_state_label: str | None = Field(default=None, max_length=8)

    term_symbol_raw: str | None = Field(default=None, max_length=64)
    term_symbol: str | None = Field(default=None, max_length=64)


class SpeciesEntryCreate(SpeciesEntryBase, SchemaBase):
    """Create shape for a species entry.

    ``isotope_key`` is deliberately absent: it is a *derived* identity key
    computed by :func:`app.chemistry.species.canonical_isotope_key` from the
    atom-resolved isotope labels in the uploaded SMILES. Derived keys are
    never accepted from a client.
    """


class SpeciesEntryUpdate(SpeciesEntryIdentityValidatorMixin, SchemaBase):
    species_id: int | None = None
    kind: StationaryPointKind | None = None

    unmapped_smiles: str | None = None

    stereo_label: str | None = Field(default=None, max_length=64)

    electronic_state_kind: SpeciesEntryStateKind | None = None
    electronic_state_label: str | None = Field(default=None, max_length=8)

    term_symbol_raw: str | None = Field(default=None, max_length=64)
    term_symbol: str | None = Field(default=None, max_length=64)


class SpeciesEntryConformerSummaryRead(BaseModel):
    """Compact conformer summary embedded in species-entry reads.

    Counts are computed by the route, not read from an ORM attribute, so this
    schema is populated explicitly rather than via `model_validate(entry)`.
    """

    conformer_group_count: int
    conformer_observation_count: int


class SpeciesEntryRead(SpeciesEntryBase, TimestampedCreatedByReadSchema):
    """Read shape for a species entry.

    ``isotope_key`` is exposed on reads (clients need to see which
    isotopologue/isotopomer an entry is) but never on writes. It is the
    canonical SMILES of the isotope-labelled molecule, or ``None`` when
    every atom is at its most abundant natural isotope.
    """

    isotope_key: str | None = None
    conformer_summary: SpeciesEntryConformerSummaryRead | None = None
