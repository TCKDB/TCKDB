from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationInfo

from tckdb.backend.app.schemas.connection_schema import ConnectionBase


class LevelBase(BaseModel):
    """
    A LevelBase class (shared properties)
    """

    method: Optional[str] = Field(
        None, max_length=500, title="The computation method (e.g., CCSD(T) or B3LYP"
    )
    basis: Optional[str] = Field(
        None, max_length=500, title="The computation basis set"
    )
    auxiliary_basis: Optional[str] = Field(
        None, max_length=500, title="An auxiliary basis set"
    )
    dispersion: Optional[str] = Field(
        None,
        max_length=500,
        title="The dispersion type used if the method is DFT "
        "and does not include built-in dispersion",
    )
    grid: Optional[str] = Field(
        None, max_length=500, title="The DFT grid used, if applicable"
    )
    solvent: Optional[str] = Field(
        None,
        max_length=100,
        title="The solvent used if a solvation correction was applied",
    )
    solvation_method: Optional[str] = Field(
        None, max_length=500, title="The solvation method (e.g., SMD or COSMO-RS)"
    )
    solvation_description: Optional[str] = Field(
        None, max_length=1000, title="Additional solvation scheme description"
    )

    level_arguments: Optional[str] = Field(
        None, max_length=500, title="Additional arguments provided to the ESS"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("method")
    @classmethod
    def check_method(cls, v):
        if v is not None and "/" in v:
            raise ValueError(f"cannot have a slash symbol in 'method', got: {v}")
        return v.lower() if v is not None else None

    @field_validator("basis")
    @classmethod
    def check_basis(cls, v):
        if v is not None and "/" in v:
            raise ValueError(f"cannot have a slash symbol in 'basis', got: {v}")
        return v.lower() if v is not None else None

    @field_validator("auxiliary_basis")
    @classmethod
    def check_auxiliary_basis(cls, v):
        return v.lower() if v is not None else None

    @field_validator("dispersion")
    @classmethod
    def check_dispersion(cls, v):
        if v is not None and "/" in v:
            raise ValueError(f"cannot have a slash symbol in 'dispersion', got: {v}")
        return v.lower() if v is not None else None

    @field_validator("solvent")
    @classmethod
    def check_solvent(cls, v):
        if v is not None and "/" in v:
            raise ValueError(f"cannot have a slash symbol in 'solvent', got: {v}")
        return v.lower() if v is not None else None

    @field_validator("solvation_method")
    def check_solvation_method(cls, v, values: ValidationInfo):
        if v is not None:
            if "/" in v:
                raise ValueError(
                    f"cannot have a slash symbol in 'solvation_method', got: {v}"
                )
            if values["solvent"] is None or not values["solvent"]:
                raise ValueError(
                    f"Must specify a solvent if a solvation method was specified.\n"
                    f"Got {v} and {values['solvent']}"
                )
            return v.lower()


class LevelCreate(LevelBase):
    """Create a Level item: Properties to receive on item creation"""

    method: str = Field(
        ..., max_length=500, title="The computation method (e.g., CCSD(T) or B3LYP"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LevelCreateBatch(LevelCreate, ConnectionBase):
    """
    A LevelCreateBatch class (inherited from LevelCreate)
    """

    pass


class LevelUpdate(LevelBase):
    """
    A LevelUpdate class (inherited from LevelBase)
    """

    pass


class LevelRead(LevelBase):
    """
    A LevelRead class (inherited from LevelBase)
    """

    id: int
    model_config = ConfigDict(from_attributes=True)
