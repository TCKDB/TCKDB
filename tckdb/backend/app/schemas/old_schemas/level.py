"""
TCKDB backend app schemas level of theory module
"""

from typing import Dict, Optional

from pydantic import BaseModel, Field, validator

from tckdb.backend.app.schemas.temp_id import TempBase


class LevelBase(BaseModel):
    """
    A LevelBase class (shared properties)
    """
    method: str = Field(..., max_length=500, title='The computation method (e.g., CCSD(T) or B3LYP')
    basis: Optional[str] = Field(None, max_length=500, title='The computation basis set')
    auxiliary_basis: Optional[str] = Field(None, max_length=500, title='An auxiliary basis set')
    dispersion: Optional[str] = Field(None, max_length=500, title='The dispersion type used if the method is DFT '
                                                                  'and does not include built-in dispersion')
    grid: Optional[str] = Field(None, max_length=500, title='The DFT grid used, if applicable')
    level_arguments: Optional[str] = Field(None, max_length=500, title='Additional arguments provided to the ESS')
    solvent: Optional[str] = Field(None, max_length=100, title='The solvent used if a solvation correction was applied')
    solvation_method: Optional[str] = Field(None, max_length=500, title='The solvation method (e.g., SMD or COSMO-RS)')
    solvation_description: Optional[str] = Field(None, max_length=1000, title='Additional solvation scheme description')
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title='Reviewer flags')

    class Config:
        extra = "forbid"

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """Level.reviewer_flags validator"""
        return value or dict()

    @validator('method', always=True)
    def check_method(cls, value):
        """Level.method validator"""
        if '/' in value:
            raise ValueError(f"cannot have a slash symbol in 'method', got: {value}")
        return value.lower()

    @validator('basis', always=True)
    def check_basis(cls, value):
        """Level.basis validator"""
        if value is not None:
            if '/' in value:
                raise ValueError(f"cannot have a slash symbol in 'basis', got: {value}")
            return value.lower()

    @validator('auxiliary_basis', always=True)
    def check_auxiliary_basis(cls, value):
        """Level.auxiliary_basis validator"""
        return value.lower() if value is not None else None

    @validator('dispersion', always=True)
    def check_dispersion(cls, value):
        """Level.dispersion validator"""
        if value is not None:
            if '/' in value:
                raise ValueError(f"cannot have a slash symbol in 'dispersion', got: {value}")

    @validator('solvent', always=True)
    def check_solvent(cls, value):
        """Level.solvent validator"""
        if value is not None:
            if '/' in value:
                raise ValueError(f"cannot have a slash symbol in 'solvent', got: {value}")
            return value.lower()

    @validator('solvation_method', always=True)
    def check_solvation_method(cls, value, values):
        """Level.solvation_method validator"""
        if value is not None:
            if '/' in value:
                raise ValueError(f"cannot have a slash symbol in 'solvation_method', got: {value}")
            if values['solvent'] is None or not values['solvent']:
                raise ValueError(f"Must specify a solvent if a solvation method was specified.\n"
                                 f"Got {value} and {values['solvent']}")
            return value.lower()


class LevelCreate(LevelBase):
    """Create a Level item: Properties to receive on item creation"""
    method: str
    basis: Optional[str] = None
    auxiliary_basis: Optional[str] = None
    dispersion: Optional[str] = None
    grid: Optional[str] = None
    level_arguments: Optional[str] = None
    solvation_method: Optional[str] = None
    solvent: Optional[str] = None
    solvation_description: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class LevelCreateBatch(LevelCreate, TempBase):
    """Create a Level item: Properties to receive on item creation"""
    pass

class LevelUpdate(LevelBase):
    """Update a Level item: Properties to receive on item update"""
    method: str
    basis: Optional[str] = None
    auxiliary_basis: Optional[str] = None
    dispersion: Optional[str] = None
    grid: Optional[str] = None
    level_arguments: Optional[str] = None
    solvation_method: Optional[str] = None
    solvent: Optional[str] = None
    solvation_description: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class LevelInDBBase(LevelBase):
    """Properties shared by models stored in DB"""
    id: int
    method: str
    basis: Optional[str] = None
    auxiliary_basis: Optional[str] = None
    dispersion: Optional[str] = None
    grid: Optional[str] = None
    level_arguments: Optional[str] = None
    solvation_method: Optional[str] = None
    solvent: Optional[str] = None
    solvation_description: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class Level(LevelInDBBase):
    """Properties to return to client"""
    pass


class LevelInDB(LevelInDBBase):
    """Properties stored in DB"""
    pass
