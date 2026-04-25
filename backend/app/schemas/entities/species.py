from pydantic import BaseModel, Field

from app.db.models.common import MoleculeKind, StereoKind
from app.schemas.common import SchemaBase, TimestampedReadSchema


class SpeciesBase(BaseModel):
    kind: MoleculeKind
    smiles: str = Field(description="Canonical SMILES string")
    inchi_key: str = Field(min_length=27, max_length=27)
    charge: int
    multiplicity: int = Field(ge=1)
    stereo_kind: StereoKind


class SpeciesCreate(SpeciesBase, SchemaBase):
    pass


class SpeciesUpdate(SchemaBase):
    kind: MoleculeKind | None = None
    smiles: str | None = None
    inchi_key: str | None = Field(default=None, min_length=27, max_length=27)
    charge: int | None = None
    multiplicity: int | None = Field(default=None, ge=1)
    stereo_kind: StereoKind | None = None


class SpeciesRead(SpeciesBase, TimestampedReadSchema):
    pass
