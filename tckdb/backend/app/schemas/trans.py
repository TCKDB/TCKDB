"""
TCKDB backend app schemas energy transfer (trans) module
"""

from enum import Enum
from typing import Dict, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationInfo


class TransModelEnum(str, Enum):
    """
    The supported Trans models
    """

    single_exponential_down = "Single Exponential Down"


class TransBase(BaseModel):
    """
    A TransBase class (shared properties)
    """

    model: TransModelEnum = Field(
        ...,
        title="The energy transfer model, "
        'currently only "Single Exponential Down" is supported',
    )
    parameters: Dict[str, Union[Tuple[Union[float], str], Union[float]]] = Field(
        ..., title="The energy transfer model parameters"
    )
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")

    @field_validator("reviewer_flags", mode="before")
    def check_reviewer_flags(cls, value):
        """Trans.reviewer_flags validator"""
        return value or dict()

    @field_validator("parameters", mode="before")
    def check_parameters(cls, value, values: ValidationInfo):
        """Trans.parameters validator"""
        if (
            "model" in info
            and values["model"] = = TransModelEnum.single_exponential_down
        ):
            if "alpha0" not in value:
                raise ValueError(
                    "The 'alpha0' parameter is required for a "
                    "Single Exponential Down energy transfer model"
                )
            if "T0" not in value:
                raise ValueError(
                    "The 'T0' parameter is required for a "
                    "Single Exponential Down energy transfer model"
                )
            if "n" not in value:
                raise ValueError(
                    "The 'n' parameter is required for a "
                    "Single Exponential Down energy transfer model"
                )
            for key, val in value.items():
                if key not in ["alpha0", "T0", "n"]:
                    raise ValueError(
                        "Got an unexpected key for the Single Exponential Down energy transfer model: "
                        f"'{key}'. Allowed keys are 'alpha0', 'T0', 'n'."
                    )
                if key == "n":
                    if not isinstance(val, (float, int)):
                        raise ValueError(
                            "The 'n' parameter of the Single Exponential Down energy transfer model must "
                            f"be dimensionless, got {val} in {value} which is a {type(val)}"
                        )
                else:
                    if not isinstance(val, tuple):
                        raise ValueError(
                            "The 'alpha0' and 'T0' parameters of the Single Exponential Down energy "
                            f"transfer model must be dimensionless, got {val} in {value} "
                            f"which is a {type(val)}"
                        )
        return value


class TransCreate(TransBase):
    """Create a Trans item: Properties to receive on item creation"""

    model: str
    parameters: Dict[str, Union[Tuple[Union[float, int], str], Union[float, int]]]
    reviewer_flags: Optional[Dict[str, str]] = None


class TransUpdate(TransBase):
    """Update a Trans item: Properties to receive on item update"""

    model: str
    parameters: Dict[str, Union[Tuple[Union[float, int], str], Union[float, int]]]
    reviewer_flags: Optional[Dict[str, str]] = None


class TransInDBBase(TransBase):
    """Properties shared by models stored in DB"""

    id: int
    model: str
    parameters: Dict[str, Union[Tuple[Union[float, int], str], Union[float, int]]]
    reviewer_flags: Optional[Dict[str, str]] = None
    model_config = ConfigDict(from_attributes=True)


class Trans(TransInDBBase):
    """Properties to return to client"""

    pass


class TransInDB(TransInDBBase):
    """Properties stored in DB"""

    pass
