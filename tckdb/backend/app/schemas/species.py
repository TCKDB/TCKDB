"""
TCKDB backend app schemas bot module
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from pydantic import BaseModel, confloat, conint, constr, validator

from arkane.statmech import is_linear
from rmgpy.molecule.adjlist import from_adjacency_list

import tckdb.backend.app.schemas.common as common
import tckdb.backend.app.conversions.converter as converter


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
    computation_type: TorsionComputationTypeEnum = TorsionComputationTypeEnum.continuous_constrained_optimization
    dimension: conint(gt=0) = 1
    constraints: Optional[Dict[Tuple[int, ...], float]] = None
    symmetry: Optional[conint(gt=0)] = None
    treatment: TorsionTreatmentEnum
    torsions: Union[List[List[int]], List[int]]
    top: List[int]
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


class SpeciesBase(BaseModel):
    """
    A SpeciesBase class (shared properties)
    """
    label: Optional[constr(max_length=255)] = None
    statmech_software: Optional[constr(max_length=150)] = None
    timestamp: Optional[confloat(gt=1.58E9)] = None  # 1.58E9 corresponds to 2020-01-25 19:53:20
    retracted: Optional[constr(max_length=255)] = None
    reviewed: Optional[bool] = None
    approved: Optional[bool] = None
    charge: conint(ge=-10, le=10)
    multiplicity: conint(ge=0, le=10)
    smiles: Optional[constr(max_length=5000)] = None
    inchi: Optional[constr(max_length=5000)] = None
    inchi_key: Optional[constr(max_length=27)] = None
    graph: Optional[constr(max_length=100000)] = None
    electronic_state: Optional[constr(max_length=150)] = 'X'
    coordinates: Dict[str, Union[Tuple[Tuple[float, float, float], ...],
                                 Tuple[conint(ge=1), ...], Tuple[constr(max_length=10), ...]]]
    fragments: Optional[List[List[conint(ge=1)]]] = None
    fragment_orientation: Optional[List[Dict[str, Union[float, List[float]]]]] = None
    external_symmetry: conint(ge=1)
    point_group: constr(max_length=6)
    chirality: Optional[Dict[Tuple[conint(ge=1), ...], constr(max_length=10)]] = None
    conformation_method: Optional[constr(max_length=500)] = None
    is_well: bool
    is_global_min: Optional[bool]
    global_min_geometry: Optional[Dict[str, Union[Tuple[Tuple[float, float, float], ...],
                                                  Tuple[conint(ge=1), ...], Tuple[constr(max_length=10), ...]]]] = None
    is_ts: bool = False
    irc_trajectories: Optional[List[List[Dict[str, Union[Tuple[Tuple[float, float, float], ...],
                                              Tuple[conint(ge=1), ...], Tuple[constr(max_length=10), ...]]]]]] = None
    electronic_energy: float
    E0: float
    active_space: Optional[Dict[str, int]] = None
    hessian: Optional[List[List[float]]] = None
    frequencies: Optional[List[float]] = None
    scaled_projected_frequencies: Optional[List[float]] = None  # check length after rotors/confs
    normal_displacement_modes: Optional[List[List[List[float]]]] = None
    freq_id: Optional[conint(ge=0)] = None
    rigid_rotor: constr(max_length=50)
    statmech_treatment: Optional[constr(max_length=50)] = None
    rotational_constants: Optional[List[float]] = None
    torsions: Optional[List[TorsionsBase]] = None
    conformers: Optional[List[Dict[str, Union[Tuple[Tuple[float, float, float], ...],
                                              Tuple[conint(ge=1), ...], Tuple[constr(max_length=10), ...],
                                              float]]]] = None
    H298: Optional[float] = None
    S298: Optional[confloat(gt=0)] = None
    Cp_values: Optional[List[confloat(gt=0)]] = None
    Cp_T_list: Optional[List[confloat(gt=0)]] = None
    heat_capacity_model: Optional[Dict[str, Union[float, Dict[str, Union[float, List[float]]], str]]] = None
    en_corr_id: Optional[conint(ge=0)] = None
    opt_path: Optional[constr(max_length=5000)] = None
    freq_path: Optional[constr(max_length=5000)] = None
    scan_paths: Optional[Dict[Tuple[Tuple[conint(ge=1), conint(ge=1), conint(ge=1), conint(ge=1)], ...],
                              constr(max_length=5000)]] = None
    irc_paths: Optional[List[constr(max_length=5000)]] = None
    sp_path: constr(max_length=5000)
    unconverged_jobs: Optional[List[Dict[str, str]]] = None
    extras: Optional[Dict[str, Any]] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        extra = "forbid"

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """Species.reviewer_flags validator"""
        return value or dict()

    @validator('timestamp', always=True)
    def assign_timestamp(cls, value):
        """Species.timestamp validator"""
        return datetime.timestamp(datetime.utcnow())

    @validator('retracted')
    def retracted_validator(cls, value, values):
        """Species.retracted validator"""
        label = f' (species label: "{values["label"]}")' if 'label' in values and values['label'] is not None else ''
        if value is not None:
            raise ValueError(f'The "retracted" argument is not a user input{label}.')
        return None

    @validator('reviewed', always=True)
    def reviewed_validator(cls, value, values):
        """Species.reviewed validator"""
        label = f' (species label: "{values["label"]}")' if 'label' in values and values['label'] is not None else ''
        if value is not None:
            raise ValueError(f'The "reviewed" argument is not a user input{label}.')
        return False

    @validator('approved', always=True)
    def approved_validator(cls, value, values):
        """Species.approved validator"""
        label = f' (species label: "{values["label"]}")' if 'label' in values and values['label'] is not None else ''
        if value is not None:
            raise ValueError(f'The "approved" argument is not a user input{label}.')
        return False

    @validator('smiles')
    def smiles_validator(cls, value, values):
        """Species.smiles validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        is_valid, err = common.is_valid_smiles(value)
        if not is_valid:
            raise ValueError(f'The SMILES "{value}"{label} is invalid. Reason:\n{err}')
        return value

    @validator('inchi')
    def inchi_validator(cls, value, values):
        """Species.inchi validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        is_valid, err = common.is_valid_inchi(value)
        if not is_valid:
            raise ValueError(f'The InChI "{value}"{label} is invalid. Reason:\n{err}')
        return value

    @validator('inchi_key')
    def inchi_key_validator(cls, value, values):
        """Species.inchi_key validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        is_valid, err = common.is_valid_inchi_key(value)
        if not is_valid:
            raise ValueError(f'The InChI Key "{value}"{label} is invalid. Reason:\n{err}')
        return value

    @validator('graph', always=True)
    def graph_validator(cls, value, values):
        """
        Species.graph validator
        Also used to populate SMILES, InChI, InChI Key, adjlist
        """
        label = f' (species label: "{values["label"]}")' if 'label' in values and values['label'] is not None else ''
        if value is not None:
            # adjlist was given, populate other attributes as needed
            if values['smiles'] is None or values['inchi'] is None:
                smiles, inchi = converter.smiles_and_inchi_from_adjlist(value)
                values['smiles'] = values['smiles'] or smiles
                values['inchi'] = values['inchi'] or inchi
        if values['inchi'] is not None:
            # InChI was given, populate other attributes as needed
            if 'smiles' not in values or not values['smiles']:
                values['smiles'] = converter.smiles_from_inchi(values['inchi'])
            value = value or converter.adjlist_from_smiles(values['smiles'])
        if 'smiles' in values and values['smiles'] is not None:
            # SMILES was given, populate other attributes as needed
            value = value or converter.adjlist_from_smiles(values['smiles'])
            values['inchi'] = values['inchi'] or converter.inchi_from_smiles(values['smiles'])
        # populate the InChI Key if not already set
        if values['inchi_key'] is not None and values['inchi'] is None:
            # InChI Key was given (and there's no InChI), populate other attributes as needed
            values['inchi'] = converter.inchi_from_inchi_key(values['inchi_key'])
            if values['inchi'] is not None:
                values['smiles'] = values['smiles'] or converter.smiles_from_inchi(values['inchi'])
                value = value or converter.adjlist_from_smiles(values['smiles'])
        values['inchi_key'] = values['inchi_key'] or converter.inchi_key_from_inchi(values['inchi'])
        if values is None or ('smiles' in values and values['smiles'] is None) \
                or ('inchi' in values and values['inchi'] is None):
            # couldn't populate adjlist, SMILES, nor InChI
            raise ValueError(f'A species descriptor (SMILES, InChI, or graph adjacency list) must be given{label}.')
        # adjlist validation
        if value is not None:
            is_valid, err = common.is_valid_adjlist(value)
            if not is_valid:
                raise ValueError(f'The RMG adjacency list{label} is invalid:\n{value}\nReason:\n{err}')
            multiplicity = from_adjacency_list(value, group=False, saturate_h=False)[1]
            if multiplicity != values['multiplicity']:
                if not abs(values['multiplicity'] - multiplicity) % 2 + abs(values['charge']):
                    # the difference is even, so it makes sense
                    adjlist_no_multiplicity = value.split("\n", 1)[1] if 'multiplicity' in value else value
                    value = f'multiplicity {values["multiplicity"]}\n{adjlist_no_multiplicity}'
                else:
                    raise ValueError(f'The given multiplicity {values["multiplicity"]} and the multiplicity of the '
                                     f'graph adjacency list mismatch{label}:\n{value}')
        return value

    @validator('coordinates')
    def coordinates_validator(cls, value, values):
        """Species.coordinates validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        converter.add_common_isotopes_to_coords(value)
        is_valid, err = common.is_valid_coordinates(value)
        if not is_valid:
            raise ValueError(f'The following coordinates dictionary{label} is invalid:\n{value}\nReason:\n{err}')
        return value

    @validator('fragments')
    def fragments_validator(cls, value, values):
        """Species.fragments validator"""
        label = f' of species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        atom_indices = list()
        for fragment in value:
            for index in fragment:
                is_valid, err = common.is_valid_atom_index(index=index,
                                                           coordinates=values['coordinates'] if 'coordinates' in values
                                                           else None,
                                                           existing_indices=atom_indices)
                if not is_valid:
                    raise ValueError(f'The atom index {index} in the fragments attribute{label} is invalid. '
                                     f'Got:\n{err}.')
                atom_indices.append(index)
        if 'coordinates' in values and len(values['coordinates']['symbols']) != len(atom_indices):
            raise ValueError(f'{len(values["coordinates"]["symbols"])} atoms were specified in the fragments {label}, '
                             f'while according to its coordinates it has {len(atom_indices)} atoms.')
        value = value if len(value) > 1 else None
        return value

    @validator('fragment_orientation', always=True)
    def fragment_orientation_validator(cls, value, values):
        """Species.fragment_orientation validator"""
        label = f' (species label "{values["label"]}")' if 'label' in values and values['label'] is not None else ''
        if value is None:
            if 'fragments' in values and values['fragments'] is not None:
                raise ValueError(f'Must specify fragment_orientation if fragments are specified{label}.')
        else:
            if 'fragments' in values:
                if values['fragments'] is None:
                    raise ValueError(f'The fragment_orientation argument{label} is unexpected if the fragments '
                                     f'argument is not specified.')
                if len(value) != len(values['fragments']) - 1:
                    raise ValueError(f'Expected {len(values["fragments"]) - 1} fragment orientation entries for a '
                                     f'species with {len(values["fragments"])} fragments, got {len(value)}.')
            valid_keys = ['cm', 'x', 'y', 'z']
            for entry in value:
                if len(list(entry.keys())) != 4:
                    raise ValueError(f'Expected the following keys in the fragment_orientation argument: "cm", "x", '
                                     f'"y", and "z". Got{label}: {list(entry.keys())}')
                for key, val in entry.items():
                    if key not in valid_keys:
                        raise ValueError(f'Got an unrecognized key "{key}" in the fragment_orientation '
                                         f'attribute{label}.')
                    if key == 'cm':
                        if not isinstance(val, list):
                            raise TypeError(f'The center of mass vector in the fragment_orientation attribute must be '
                                            f'a list type, got{label}: {type(val)}.')
                        if len(entry[key]) != 3:
                            raise ValueError(f'The center of mass vector in the fragment_orientation attribute{label} '
                                             f'has length {len(val)}, should have a length of 3.')
                    elif not isinstance(val, float):
                        raise TypeError(f'The "x", "y", and "z" in the fragment_orientation attribute must have '
                                        f'float type values, got{label}: {val} with type {type(val)} in\n{entry}.')
        return value

    @validator('point_group')
    def point_group_validator(cls, value, values):
        """Species.point_group validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        allowed_first_chars = ['C', 'D', 'I', 'O', 'S', 'T']
        allowed_last_chars = ['d', 'h', 'i', 's', 'v']
        inf = 'inf'
        if value[0] not in allowed_first_chars:
            raise ValueError(f'Invalid point group{label}: Expected it to *begin* with one of the following '
                             f'characters: {allowed_first_chars}.\nGot: "{value}".')
        if value[-1] not in allowed_last_chars and not value[-1].isdigit() and len(value) > 1:
            raise ValueError(f'Invalid point group{label}: Expected it to *end* with one of the following '
                             f'characters: {allowed_last_chars}.\nGot: "{value}".')
        for i, char in enumerate(value):
            if i != 0 and i != len(value) - 1:
                if not char.isdigit() and inf not in value:
                    raise ValueError(f'Invalid point group{label}: Expected it to contain only numbers or "inf" in '
                                     f'between the first and the last characters.\nGot: "{value}".')
        return value

    @validator('chirality')
    def chirality_validator(cls, value, values):
        """Species.chirality validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        chiral_atom_indices = list()
        allowed_values = ['R', 'S', 'NR', 'NS', 'E', 'Z']
        allowed_atoms = ['C', 'Si', 'Ge', 'Sn', 'Pb', 'N', 'P', 'As', 'Sb', 'Bi']
        for key, val in value.items():
            for index in key:
                is_valid, err = common.is_valid_atom_index(index=index,
                                                           coordinates=values['coordinates'] if 'coordinates' in values
                                                           else None,
                                                           existing_indices=chiral_atom_indices)
                if not is_valid:
                    raise ValueError(f'The atom index {index} in the fragments attribute{label} is invalid. '
                                     f'Got:\n{err}.')
                chiral_atom_indices.append(index)
                if 'coordinates' in values and values['coordinates']['symbols'][index - 1] not in allowed_atoms:
                    raise ValueError(f'A chiral site cannot include {values["coordinates"]["symbols"][index - 1]} '
                                     f'atoms. Got{label}:\n{value}')
            if val not in allowed_values:
                raise ValueError(f'The chirality notation is not recognized. Expected it to be in {allowed_values}, '
                                 f'got {val} in\n{value}')
            if len(key) == 1:
                if val not in ['R', 'S', 'NR', 'NS']:
                    raise ValueError(f'A chiral atom center must have one of the following notations: "R", "S", "NR", '
                                     f'or "NS", got {val} in {value}{label}.')
            elif len(key) == 2:
                if val not in ['E', 'Z']:
                    raise ValueError(f'A chiral center around a double bond must be noted by either "E" or "Z", '
                                     f'got {val} in {value}{label}.')
            else:
                raise ValueError(f'A chiral center must be noted by either a single atom index or two, got {len(key)} '
                                 f'in {value}{label}.')
            if val in ['NR', 'NS'] and 'coordinates' in values and values['coordinates']['symbols'][key[0] - 1] != 'N':
                raise ValueError(f'A chiral atom center{label} with an "NR" or "NS" notation but be a nitrogen atom.')
            elif val in ['R', 'S'] and 'coordinates' in values and values['coordinates']['symbols'][key[0] - 1] == 'N':
                raise ValueError(f'A chiral *nitrogen* atom center{label} with must be noted with "NR" or "NS".')
        return value

    @validator('conformation_method', always=True)
    def conformation_method_validator(cls, value, values):
        """Species.conformation_method validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if value is None and 'coordinates' in values and len(values['coordinates']['symbols']) >= 4:
            raise ValueError(f'Must provide a conformation method{label} when the species contains more than 4 atoms.')
        return value

    @validator('global_min_geometry')
    def global_min_geometry_validator(cls, value, values):
        """Species.global_min_geometry validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        converter.add_common_isotopes_to_coords(value)
        is_valid, err = common.is_valid_coordinates(value)
        if not is_valid:
            raise ValueError(f'The following global_min_geometry coordinates dictionary{label} is invalid:\n'
                             f'{value}\nReason:\n{err}')
        return value

    @validator('irc_trajectories', always=True)
    def irc_trajectories_validator(cls, value, values):
        """Species.irc_trajectories validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and values['is_ts']and value is None:
            raise ValueError(f'IRC trajectories must be given{label} if the species is a TS.')
        if 'is_ts' in values and not values['is_ts'] and value is not None:
            raise ValueError(f'IRC trajectories were given{label}, but the species is not defined as a TS.\n'
                             f'(Set the "is_ts" attribute to True if the species is meant to be a TS.)')
        if value is not None:
            for i, traj in enumerate(value):
                for j, frame in enumerate(traj):
                    converter.add_common_isotopes_to_coords(frame)
                    is_valid, err = common.is_valid_coordinates(frame)
                    if not is_valid:
                        raise ValueError(f'Frame {j} in IRC trajectory {i}{label} is invalid:\n'
                                         f'{frame}\nReason:\n{err}')
        return value

    @validator('active_space')
    def active_space_validator(cls, value, values):
        """Species.active_space validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        allowed_keys = ['electrons', 'orbitals']
        if any(key not in allowed_keys for key in value.keys()):
            raise ValueError(f'The active_space argument{label} has unrecognized keys.\n'
                             f'Allowed keys: {allowed_keys}, got: {[value.keys()]}.')
        if not all(key in [value.keys()] for key in allowed_keys):
            raise ValueError(f'Not all required keys of the active_space argument{label} were given.\n'
                             f'Required keys: {allowed_keys}, got: {[value.keys()]}.')
        return value

    @validator('hessian', always=True)
    def hessian_validator(cls, value, values):
        """Species.hessian validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        num_atoms = common.get_number_of_atoms(values)
        if num_atoms > 1:
            if value is None:
                raise ValueError(f'The Hessian was not given{label}. It must be given for polyatomic species.')
            if len(value) != num_atoms * 3:
                raise ValueError(f'The number of rows in the Hessian matrix ({len(value)}){label} is invalid, '
                                 f'expected {num_atoms * 3} rows for {num_atoms} atoms.')
            for i, row in enumerate(value):
                if len(row) < i + 1:
                    raise ValueError(f'Row {i} of the Hesian matrix{label} has only {len(row)} elements, '
                                     f'expected {i + 1} elements.')
        return value

    @validator('frequencies', always=True)
    def frequencies_validator(cls, value, values):
        """Species.frequencies validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if value is None and common.get_number_of_atoms(values) > 1:
            raise ValueError(f'Frequencies were not given{label}. Frequencies must be specified for polyatomic species.')
        if value is not None:
            if any(i == 0 for i in value):
                raise ValueError(f'A frequency cannot be zero, got {value}{label}.')
            if values['coordinates'] is not None and value is not None:
                linear = is_linear(coordinates=np.array(values['coordinates']['coords']))
                num_atoms = common.get_number_of_atoms(values)
                if num_atoms is not None:
                    expected_num_freqs = 3 * num_atoms - (6 - int(linear))  # 3N-6 for non linear, 3N-5 for linear
                    if len(value) != expected_num_freqs:
                        linear_txt = 'linear' if linear else 'non-linear'
                        raise ValueError(f'Expected {expected_num_freqs} frequencies for a {linear_txt} molecule, '
                                         f'got {len(value)} frequencies{label}.')
            if 'is_ts' in values and values['is_ts'] and all(freq > 0 for freq in value):
                raise ValueError(f'An imaginary frequency must be present for a TS species. '
                                 f'Got all real frequencies{label}.')
        return value

    @validator('scaled_projected_frequencies', always=True)
    def scaled_projected_frequencies_validator(cls, value, values):
        """Species.scaled_projected_frequencies validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if value is None and common.get_number_of_atoms(values) > 1:
            raise ValueError(f'Scaled projected frequencies were not given{label}.'
                             f'Must be specified for polyatomic species.')
        if value is not None:
            if any(i == 0 for i in value):
                raise ValueError(f'A frequency (scaled_projected_frequencies) cannot be zero, got {value}{label}.')
            if 'frequencies' in values:
                if len(value) > len(values['frequencies']):
                    raise ValueError(f"The scaled_projected_frequencies (length {len(value)}) cannot have more "
                                     f"entries that the frequencies (length {len(values['frequencies'])}{label}.")
                if value == values['frequencies']:
                    raise ValueError(f'The scaled_projected_frequencies are identical to the frequencies.\n'
                                     f'Did you forget to scale?')
        return value

    @validator('normal_displacement_modes', always=True)
    def normal_displacement_modes_validator(cls, value, values):
        """Species.normal_displacement_modes validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'frequencies' in values and values['frequencies'] is not None:
            if value is None:
                raise ValueError(f'Normal displacement modes were not given{label}.')
            if len(value) != len(values['frequencies']):
                raise ValueError(f"The number of normal displacement modes ({len(value)}) "
                                 f"differs from the number of frequencies ({len(values['frequencies'])}){label}.")
            num_atoms = common.get_number_of_atoms(values)
            if num_atoms is not None:
                for ndm in value:
                    if len(ndm) != num_atoms:
                        raise ValueError(f'The number of normal displacement modes per frequency must be equal '
                                         f'to the number of atoms ({num_atoms}), got {len(ndm)}.')
                    for displacement in ndm:
                        if len(displacement) != 3:
                            raise ValueError(f'Each displacement (per frequency per atom) must be a list of length 3, '
                                             f'got {len(displacement)}.')
        return value

    @validator('freq_id', always=True)
    def freq_id_validator(cls, value, values):
        """Species.freq_id validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'frequencies' in values and values['frequencies'] is not None and value is None:
            raise ValueError(f'freq_id was not given{label}.')
        return value

    @validator('rigid_rotor')
    def rigid_rotor_validator(cls, value, values):
        """Species.rigid_rotor validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        allowed_values = ['atom', 'linear', 'spherical top', 'symmetric top', 'asymmetric top']
        if value not in allowed_values:
            raise ValueError(f'The given rigid_rotor ({value}){label} is not recognized.\n'
                             f'Allowed values are {allowed_values}.')
        return value

    @validator('statmech_treatment', always=True)
    def statmech_treatment_validator(cls, value, values):
        """Species.statmech_treatment validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        allowed_values = ['RRHO', 'RRHO-1D', 'RRHO-1D-ND', 'RRHO-ND', 'RRHO-AD', 'RRAO']
        if value is None and common.get_number_of_atoms(values) > 2:
            raise ValueError(f'statmech_treatment was not given{label}. A statistical mechanics treatment '
                             f'(one of {allowed_values}) must be specified for polyatomic species.')
        if value is not None and value not in allowed_values:
            raise ValueError(f'The statmech_treatment {value} is not recognized.\n'
                             f'Allowed values are: {allowed_values}')
        return value

    @validator('rotational_constants', always=True)
    def rotational_constants_validator(cls, value, values):
        """Species.rotational_constants validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if value is None and common.get_number_of_atoms(values) > 1:
            raise ValueError(f'No rotational constants specified{label}.')
        if value is not None:
            if common.get_number_of_atoms(values) == 1:
                raise ValueError(f'Rotational constants were specified for a monoatomic species{label} ({value}).')
            if 'coordinates' in values and 'coords' in values['coordinates']:
                linear = is_linear(coordinates=np.array(values['coordinates']['coords']))
                if len(value) != 1 and linear:
                    raise ValueError(f'More than one rotational constant was specified for a linear species{label} '
                                     f'({value}).')
                if len(value) != 3 and not linear:
                    raise ValueError(f'The number of rotational constants for a non-linear species{label} must be 3.\n'
                                     f'Got {len(value)} rotational constants: {value}.')
        return value

    @validator('torsions')
    def torsions_validator(cls, value, values):
        """Species.torsions validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'scaled_projected_frequencies' in values and values['scaled_projected_frequencies'] \
                and 'frequencies' in values and values['frequencies'] \
                and len(values['frequencies']) - len(value) != len(values['scaled_projected_frequencies']):
            raise ValueError(f"Expected to have "
                             f"{len(values['frequencies']) - len(value)} "
                             f"scaled projected frequencies for {len(value)} torsions, but got "
                             f"{len(values['scaled_projected_frequencies'])}{label}.")
        return value

    @validator('conformers')
    def conformers_validator(cls, value, values):
        """Species.conformers validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'torsions' in values and values['torsions']:
            raise ValueError(f'Either torsions or conformers must be given, got both{label}.')
        for conformer in value:
            is_valid, err = common.is_valid_coordinates(conformer, allowed_keys=['energy', 'degeneracy'])
            if not is_valid:
                raise ValueError(f"Not all conformers{label} are valid. Reason:\n{err}\n"
                                 f"Got:\n{conformer}\nin:\n{value}.")
            if 'energy' not in conformer:
                raise ValueError(f'A conformer entry in the conformers argument{label} must have an "energy" key.')
            if not isinstance(conformer['energy'], float):
                raise ValueError(f"A conformer energy must be a float, got {conformer['energy']} which is a "
                                 f"{type(conformer['energy'])}{label}.")
            if 'degeneracy' not in conformer:
                conformer['degeneracy'] = 1
            if conformer['degeneracy'] % 1:
                # The degeneracy is converted to float, cannot check isinstance for int
                raise ValueError(f"A conformer degeneracy must be a float, got {conformer['degeneracy']} which is a "
                                 f"{type(conformer['degeneracy'])}{label}.")
        return value

    @validator('H298', always=True)
    def h298_validator(cls, value, values):
        """Species.H298 validator"""
        label = f' "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and not values['is_ts'] and value is None:
            raise ValueError(f'The "H298" argument must be given for non-TS species{label}.')
        return value

    @validator('S298', always=True)
    def s298_validator(cls, value, values):
        """Species.S298 validator"""
        label = f' "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and not values['is_ts'] and value is None:
            raise ValueError(f'The "S298" argument must be given for non-TS species{label}.')
        return value

    @validator('Cp_values', always=True)
    def cp_values_validator(cls, value, values):
        """Species.Cp_values validator"""
        label = f' "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and not values['is_ts'] and value is None:
            raise ValueError(f'The "Cp_values" argument must be given for non-TS species{label}.')
        return value

    @validator('Cp_T_list', always=True)
    def cp_t_list_validator(cls, value, values):
        """Species.Cp_T_list validator"""
        label = f' "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and not values['is_ts'] and value is None:
            raise ValueError(f'The "Cp_T_list" argument must be given for non-TS species{label}.')
        if 'Cp_values' in values and values['Cp_values'] and value is not None \
                and len(values['Cp_values']) != len(value):
            raise ValueError(f"The number of Cp values ({len(values['Cp_values'])}) "
                             f"must be equal to the number of Cp temperatures ({len(value)}).")
        return value

    @validator('en_corr_id', always=True)
    def en_corr_id_validator(cls, value, values):
        """Species.en_corr_id validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and not values['is_ts'] and value is None:
            raise ValueError(f'en_corr_id was not given{label}.')
        return value

    @validator('opt_path', always=True)
    def opt_path_validator(cls, value, values):
        """Species.opt_path validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if common.get_number_of_atoms(values) > 1 and value is None:
            raise ValueError(f'The opt_path was not given{label}.')
        return value

    @validator('freq_path', always=True)
    def freq_path_validator(cls, value, values):
        """Species.freq_path validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if common.get_number_of_atoms(values) > 1 and value is None:
            raise ValueError(f'The freq_path was not given{label}.')
        return value

    @validator('scan_paths', always=True)
    def scan_paths_validator(cls, value, values):
        """Species.scan_paths validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'torsions' in values and values['torsions']:
            if value is None:
                raise ValueError(f'The scan_paths was not given{label}.')
            else:
                for torsion in values['torsions']:
                    torsion_indices = tuple(tuple(indices) for indices in torsion.torsions)
                    match = False
                    for path_key in value.keys():
                        if path_key == torsion_indices:
                            match = True
                            break
                    if not match:
                        raise ValueError(f'Could not find a corresponding scan path '
                                         f'for the torsion {torsion_indices}{label}.')
        return value

    @validator('irc_paths', always=True)
    def irc_paths_validator(cls, value, values):
        """Species.irc_paths validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        if 'is_ts' in values and values['is_ts'] and value is None:
            raise ValueError(f'irc_paths was not given{label}.')
        if value is not None and len(value) not in [1, 2]:
            raise ValueError(f'The length of the IRC paths argument must be either 1 (for a forward+reverse IRC) or 2. '
                             f'Got: {len(value)}{label}.')
        return value

    @validator('unconverged_jobs')
    def unconverged_jobs_validator(cls, value, values):
        """Species.unconverged_jobs validator"""
        label = f' for species "{values["label"]}"' if 'label' in values and values['label'] is not None else ''
        allowed_keys = ['job type', 'issue', 'troubleshooting', 'comment', 'path']
        recognized_job_types = ['opt', 'freq', 'scan', 'irc', 'sp']
        for unconverged_job in value:
            if not any(key in allowed_keys for key in unconverged_job.keys()):
                raise ValueError(f'Got an unrecognized key in unconverged_jobs{label}.\n'
                                 f'Recognized keys are: {allowed_keys}\nGot: {list(unconverged_job.keys())}')
            if 'job type' not in unconverged_job:
                raise ValueError(f'A job type is required when reporting an unconverged job. Got None{label}.`')
            else:
                if unconverged_job['job type'] not in recognized_job_types:
                    raise ValueError(f"The unconverged job type {unconverged_job['job type']}{label} is invalid.\n"
                                     f"Recognized job types are {recognized_job_types}.")
            if 'path' not in unconverged_job:
                raise ValueError(f'A file path is required when reporting an unconverged job. Got None{label}.`')
        return value





class NonPhysicalSpeciesBase(BaseModel):
    """
    A NonPhysicalSpeciesBase class (shared properties)
    """
    label: Optional[constr(max_length=255)] = None
    timestamp: confloat(gt=1.58E9)  # 1.58E9 corresponds to 2020-01-25 19:53:20
    retracted: Optional[constr(max_length=255)] = None
    reviewed: bool
    approved: bool
    smiles: Optional[constr(max_length=5000)] = None
    inchi: Optional[constr(max_length=5000)] = None
    inchi_key: Optional[constr(max_length=5000)] = None
    charge: conint(ge=-10, le=10)
    multiplicity: conint(ge=0, le=10)
    electronic_state: Optional[constr(max_length=150)] = 'X'
    coordinates: Dict[str, Union[Tuple[Tuple[float]], Tuple[conint(ge=1)], Tuple[constr(max_length=10)]]]
    graph: Optional[constr(max_length=100000)] = None
    fragments: Optional[List[List[conint(ge=1)]]] = None
    fragment_orientation: Optional[List[Dict[str, Union[float, List[float]]]]] = None
    conformation_method: Optional[constr(max_length=500)] = None
    is_well: bool
    is_global_min: bool
    global_min_geometry: Optional[Dict[str, Union[Tuple[Tuple[float]],
                                                  Tuple[conint(ge=1)], Tuple[constr(max_length=10)]]]] = None
    is_ts: bool
    irc_trajectories: Optional[List[List[Dict[str, Union[Tuple[Tuple[float]],
                                                         Tuple[conint(ge=1)], Tuple[constr(max_length=10)]]]]]] = None
    opt_path: Optional[constr(max_length=5000)] = None
    freq_path: Optional[constr(max_length=5000)] = None
    scan_paths: Optional[Dict[Tuple[Tuple[conint(ge=1)]], constr(max_length=5000)]] = None
    irc_paths: Optional[List[constr(max_length=5000)]] = None
    sp_path: Optional[constr(max_length=5000)] = None
    unconverged_jobs: Optional[List[Dict[str, str]]] = None
    extras: Dict[str, Any]
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        extra = "forbid"

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """NonPhysicalSpecies.reviewer_flags validator"""
        return value or dict()


class SpeciesCreate(SpeciesBase):
    """Create a Species item: Properties to receive on item creation"""
    reviewer_flags: Optional[Dict[str, str]] = None


class SpeciesUpdate(SpeciesBase):
    """Update a Species item: Properties to receive on item update"""
    reviewer_flags: Optional[Dict[str, str]] = None


class SpeciesInDBBase(SpeciesBase):
    """Properties shared by models stored in DB"""
    id: int
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class Species(SpeciesInDBBase):
    """Properties to return to client"""
    pass


class SpeciesInDB(SpeciesInDBBase):
    """Properties stored in DB"""
    pass


class NonPhysicalSpeciesCreate(NonPhysicalSpeciesBase):
    """Create a NonPhysicalSpecies item: Properties to receive on item creation"""
    reviewer_flags: Optional[Dict[str, str]] = None


class NonPhysicalSpeciesUpdate(NonPhysicalSpeciesBase):
    """Update a NonPhysicalSpecies item: Properties to receive on item update"""
    reviewer_flags: Optional[Dict[str, str]] = None


class NonPhysicalSpeciesInDBBase(NonPhysicalSpeciesBase):
    """Properties shared by models stored in DB"""
    id: int
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class NonPhysicalSpecies(NonPhysicalSpeciesInDBBase):
    """Properties to return to client"""
    pass


class NonPhysicalSpeciesInDB(NonPhysicalSpeciesInDBBase):
    """Properties stored in DB"""
    pass
