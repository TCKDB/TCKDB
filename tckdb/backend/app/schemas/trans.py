"""
TCKDB backend app schemas trans module
"""

from enum import Enum
from typing import Dict

from pydantic import BaseModel, validator


class TransModelEnum(str, Enum):
    """
    The supported Trans models
    """
    single_exponential_down = 'Single Exponential Down'


class TransBase(BaseModel):
    """
    A TransBase class (shared properties)
    """
    model: TransModelEnum
    parameters: Dict[str, tuple]

    @validator('parameters', always=True)
    def check_journal(cls, value, values):
        """Literature.journal validator"""
        if 'model' in values and values['model'] == TransModelEnum.single_exponential_down:
            if 'alpha0' not in value:
                raise ValueError(f"The 'alpha0' parameter is required for a "
                                 f"Single Exponential Down energy transfer model")
            if 'T0' not in value:
                raise ValueError(f"The 'T0' parameter is required for a "
                                 f"Single Exponential Down energy transfer model")
            if 'n' not in value:
                raise ValueError(f"The 'n' parameter is required for a "
                                 f"Single Exponential Down energy transfer model")
            for key, val in value.items():
                if key not in ['alpha0', 'T0', 'n']:
                    raise ValueError(f"Got an unexpected key for the Single Exponential Down energy transfer model: "
                                     f"'{key}'. Allowed keys are 'alpha0', 'T0', 'n'.")
                if not isinstance(val, tuple):
                    raise TypeError(f"Parameter values must be tuples, got {type(val)} for '{key}'")
                if len(val) not in [1, 2]:
                    raise ValueError(f'Parameter values must be tuples of length 1 (if there are no units) or 2 '
                                     f'(if there are units). Got {val} which is of length {len(val)}.')
                if not isinstance(val[0], (int, float)):
                    raise TypeError(f'The first entry in a parameter value must be a float or an integer, '
                                    f'got {val[0]} in {value}, which is a {type(val[0])}')
                if len(val) == 2 and not isinstance(val[1], str):
                    raise TypeError(f'The second entry in a parameter value, if given, must be a string, '
                                    f'got {val[1]} in {value}, which is a {type(val[1])}')
                if key == 'n' and len(val) == 2:
                    raise ValueError(f"THe parameter 'n' of the Single Exponential Down energy transfer model must "
                                     f"be dimentionless, got {val} in {value}")
        return value


class TransCreate(TransBase):
    """Create a Trans item: Properties to receive on item creation"""
    model: str
    parameters: dict


class TransUpdate(TransBase):
    """Update a Trans item: Properties to receive on item update"""
    model: str
    parameters: dict


class TransInDBBase(TransBase):
    """Properties shared by models stored in DB"""
    model: str
    parameters: dict

    class Config:
        orm_mode = True


class Trans(TransInDBBase):
    """Properties to return to client"""
    pass


class TransInDB(TransInDBBase):
    """Properties properties stored in DB"""
    pass
