from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from arkane.statmech import is_linear
from pydantic import BaseModel, Field, confloat, conint, constr, validator, root_validator
from rmgpy.molecule.adjlist import from_adjacency_list

import tckdb.backend.app.conversions.converter as converter
from tckdb.backend.app.schemas.bot import BotCreate
import tckdb.backend.app.schemas.common as common

import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class TorsionComputationTypeEnum(str, Enum):
    """
    The supported torsion computation types
    """
    single_point = 'single point'
    constrained_optimization = 'constrained optimization'
    continuous_constrained_optimization = 'continuous constrained optimization'


class TorsionTreatmentEnum(str, Enum):
    """
    The supported torsion treatment types
    """
    hindered_rotor = 'hindered rotor'
    free_rotor = 'free rotor'
    rigid_top = 'rigid top'
    hindered_rotor_density_of_states = 'hindered rotor density of states'


class TorsionsBase(BaseModel):
    """
    A class for validating SpeciesBase.torsions arguments
    """
    computation_type: TorsionComputationTypeEnum = Field(TorsionComputationTypeEnum.continuous_constrained_optimization,
                                                         title="The computation type used for torsion scans, either "
                                                               "'single point', 'constrained optimization', "
                                                               "or 'continuous constrained optimization' (default)")
    dimension: int = Field(1, ge=1, title='The scan dimension')
    constraints: Optional[Dict[Tuple[int, ...], float]] = \
        Field(None, title='Any non-trivial constraints (i.e., other than the scanned mode) used during optimization')
    symmetry: Optional[int] = Field(None, gt=0, title='The internal symmetry number of the scanned mode')
    treatment: TorsionTreatmentEnum = Field(..., title="The torsion treatment, either 'hindered rotor', 'free rotor', "
                                                       "'rigid top', or 'hindered rotor density of states'")
    torsions: Union[List[List[int]], List[int]] = Field(..., title='The torsions list described by this mode')
    top: List[int] = Field(..., title='The lost of atoms at one of the tops')
    energies: list
    resolution: Union[float, List[float]]
    trajectory: list
    invalidated: Optional[str] = None

    class Config:
        extra = "forbid"

    @validator('constraints')
    def constraints_validator(cls, value, values):
        """TorsionsBase.constraints validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        for key in value.keys():
            if len(key) not in [2, 3, 4]:
                raise ValueError(f'A constraint key length must be between 2 to 4, got {key} of length '
                                 f'{len(key)}{label} in\n{value}')
            if any(index == 0 for index in key):
                raise ValueError(f'Atom indices in the constrains must be 1-indexed, got{label} {key} in\n{value}')
        return value

    @validator('symmetry', always=True)
    def symmetry_validator(cls, value, values):
        """TorsionsBase.symmetry validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'dimension' in values and values['dimension'] == 1 and value is None:
            raise ValueError(f'The "symmetry" key is required for a torsion dictionary{label}.\nGot: {values}')
        return value

    @validator('torsions')
    def torsions_validator(cls, value, values):
        """TorsionsBase.torsions validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if not isinstance(value[0], list):
            # correct to a List[List[float]] form
            value = [value]
        for atom_indices in value:
            if len(atom_indices) != 4:
                raise ValueError(f'Atom indices in "torsions" must be of length 4, got{label} {atom_indices}'
                                 f'in\n{values}')
            if any(index == 0 for index in atom_indices):
                raise ValueError(f'Torsion atom indices must be 1-indexed, got{label} {atom_indices} in\n{values}')
        if 'dimension' in values and values['dimension'] and len(value) != values['dimension']:
            raise ValueError(f"Got a {len(value)}D torsion for a declared dimension of "
                             f"{values['dimension']}{label}:\n{value}")
        return value

    @validator('top')
    def top_validator(cls, value, values):
        """TorsionsBase.top validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if any(index == 0 for index in value):
            raise ValueError(f'Top atom indices must be 1-indexed, got{label} {value} in\n{values}')
        return value

    @validator('energies')
    def energies_validator(cls, value, values):
        """TorsionsBase.energies validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'dimension' in values and values['dimension']:
            energies_dimension = 0
            entry = value
            while not isinstance(entry, float):
                if isinstance(entry, (list, tuple)):
                    entry = entry[0]
                    energies_dimension += 1
                elif not isinstance(entry, float):
                    raise ValueError(f"Lowest level energy entries in a torsion must be floats, "
                                     f"got {entry}{label} which is a {type(entry)} in\n{value}")
            if energies_dimension != values['dimension']:
                raise ValueError(f"Got a {energies_dimension}D energies attribute for a declared dimension "
                                 f"of {values['dimension']}{label}:\n{value}")
        return value

    @validator('resolution')
    def resolution_validator(cls, value, values):
        """TorsionsBase.resolution validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if not isinstance(value, list):
            value = [value]
        for resolution in value:
            if 360 % resolution:
                raise ValueError(f"The scan resolution {resolution} in {value}{label} is invalid. "
                                 f"It has to be a divisor of 360.")
        return value

    @validator('trajectory')
    def trajectory_validator(cls, value, values):
        """TorsionsBase.trajectory validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        trajectory_dimension = 0
        entry = value
        while not isinstance(entry, dict):
            if isinstance(entry, (list, tuple)):
                entry = entry[0]
                trajectory_dimension += 1
            elif isinstance(entry, dict):
                is_valid, err = common.is_valid_coordinates(entry)
                if not is_valid:
                    raise ValueError(f"Not all coordinates in the torsion trajectory{label} are valid."
                                     f"Reason:\n{err}\nGot:\n{entry}.")
            else:
                raise ValueError(f"Lowest level trajectory entries in a torsion must be coordinates "
                                 f"dictionaries, got {entry}{label} which is a {type(entry)}.")
        if 'dimension' in values and values['dimension'] and trajectory_dimension != values['dimension']:
            raise ValueError(f"Got a {trajectory_dimension}D trajectory attribute for a declared dimension "
                             f"of {values['dimension']}{label}:\n{value}")
        return value
