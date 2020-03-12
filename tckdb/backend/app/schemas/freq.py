"""
TCKDB backend app schemas freq module
"""

from typing import Dict, Union

from pydantic import BaseModel, confloat, constr, validator

from tckdb.backend.app.schemas.common import lowercase_dict

class FreqBase(BaseModel):
    """
    A FreqBase class (shared properties)
    """
    level: Dict[str, Union[str, dict]]
    factor: confloat(gt=0, lt=2)
    source: constr(max_length=1600)

    @validator('level')
    def check_level(cls, value):
        """Freq.level validator"""
        value = lowercase_dict(value)
        if 'method' not in value:
            raise ValueError("A 'method' for a level of theory must be provided")
        if 'solvation' in value:
            if not isinstance(value['solvation'], dict):
                raise TypeError(f"The value for the 'solvation' part of the level of theory must be a dictionary, "
                                f"got {type(value['solvation'])}")
            for key in value['solvation'].keys():
                if key not in ['method', 'solvent']:
                    raise ValueError(f"Got an unrecognized key in the 'solvation' part of the level of theory: "
                                     f"'{key}'. Allowed keys are 'method' and 'solvent'.")
            if 'method' not in value['solvation'] or 'solvent' not in value['solvation']:
                raise ValueError(f"If 'solvation' is specified in a level of theory, then both 'method' (e.g., 'PCM') "
                                 f"and 'solvent' (e.g., 'water') must be specified as well. Got: {value['solvation']}")
        for key in value.keys():
            if key not in ['method', 'basis', 'dispersion', 'auxiliary_basis', 'solvation']:
                raise ValueError(f"Got an unrecognized key in level of theory: '{key}'. Allowed keys are 'method', "
                                 f"'basis', 'dispersion', 'auxiliary_basis', 'solvation'.")
        return value


class FreqCreate(FreqBase):
    """Create a Freq item: Properties to receive on item creation"""
    type: str
    authors: str
    title: str
    year: int
    journal: str = None
    publisher: str = None
    volume: int = None
    issue: int = None
    page_start: int = None
    page_end: int = None
    editors: str = None
    edition: str = None
    chapter_title: str = None
    publication_place: str = None
    doi: str = None
    isbn: str = None
    url: str


class FreqUpdate(FreqBase):
    """Update a Freq item: Properties to receive on item update"""
    authors: str
    title: str
    year: int
    journal: str
    publisher: str
    volume: int
    issue: int
    page_start: int
    page_end: int
    editors: str
    edition: str
    chapter_title: str
    publication_place: str
    doi: str
    isbn: str
    url: str


class FreqInDBBase(FreqBase):
    """Properties shared by models stored in DB"""
    id: int
    authors: str
    title: str
    year: int
    journal: str
    publisher: str
    volume: int
    issue: int
    page_start: int
    page_end: int
    editors: str
    edition: str
    chapter_title: str
    publication_place: str
    doi: str
    isbn: str
    url: str

    class Config:
        orm_mode = True


class Freq(FreqInDBBase):
    """Properties to return to client"""
    pass


class FreqInDB(FreqInDBBase):
    """Properties properties stored in DB"""
    pass
