from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
    ValidationInfo,
)
from typing_extensions import Annotated

import tckdb.backend.app.conversions.converter as converter
import tckdb.backend.app.schemas.common as common
from tckdb.backend.app.conversions import adjlist_conversion_process

# from tckdb.backend.app.conversions.converter import is_linear
# from rmgpy.molecule.adjlist import from_adjacency_list
from tckdb.backend.app.conversions.adjlist_conversion_process import (
    multiplicity_from_adjlist,
)
from tckdb.backend.app.conversions.converter import is_linear
from tckdb.backend.app.schemas.common import Coordinates
from tckdb.backend.app.schemas.connection_schema import ConnectionBase
from tckdb.backend.app.schemas.torsion import TorsionsBase


class ESSConnectionID(BaseModel):
    irc: Optional[str] = Field(
        None, title="The connection ID of the IRC object for internal referencing"
    )
    opt: Optional[str] = Field(
        None, title="The connection ID of the opt object for internal referencing"
    )
    scan: Optional[str] = Field(
        None, title="The connection ID of the scan object for internal referencing"
    )
    sp: Optional[str] = Field(
        None, title="The connection ID of the sp object for internal referencing"
    )
    freq: Optional[str] = Field(
        None, title="The connection ID of the freq object for internal referencing"
    )


class LevelConnectionID(BaseModel):
    irc: Optional[str] = Field(
        None, title="The connection ID of the IRC object for internal referencing"
    )
    opt: Optional[str] = Field(
        None, title="The connection ID of the opt object for internal referencing"
    )
    scan: Optional[str] = Field(
        None, title="The connection ID of the scan object for internal referencing"
    )
    sp: Optional[str] = Field(
        None, title="The connection ID of the sp object for internal referencing"
    )
    freq: Optional[str] = Field(
        None, title="The connection ID of the freq object for internal referencing"
    )


class SpeciesBase(BaseModel):

    label: Optional[str] = Field(None, max_length=255, title="Species label")
    statmech_software: Optional[str] = Field(
        None,
        max_length=150,
        title="The software used to compute the species statmech data",
    )
    smiles: Optional[str] = Field(None, max_length=5000, title="SMILES")
    inchi: Optional[str] = Field(None, max_length=5000, title="InChI")
    inchi_key: Optional[str] = Field(None, max_length=27, title="InChI key")

    charge: Optional[int] = Field(
        None, ge=-10, le=10, title="The net charge of the species"
    )
    multiplicity: Optional[int] = Field(
        None, ge=1, le=10, title="The spin multiplicity of the species"
    )
    electronic_state: Optional[str] = Field(
        "X",
        max_length=150,
        title='Electronic state. Default is "X", denoting ground state',
    )

    graph: Optional[str] = Field(None, max_length=100000, title="Adjacency list graph")
    coordinates: Optional[Coordinates] = Field(None, title="Cartesian coordinates")
    fragments: Optional[List[List[Annotated[int, Field(ge=1)]]]] = Field(
        None, title="Fragments"
    )
    fragment_orientation: Optional[List[Dict[str, Union[float, List[float]]]]] = Field(
        None, title="Fragment orientation"
    )
    external_symmetry: Optional[int] = Field(None, ge=1, title="External symmetry")
    point_group: Optional[str] = Field(None, max_length=6, title="Point group")
    chirality: Optional[
        Dict[
            Tuple[Annotated[int, Field(ge=1)], ...],
            Annotated[str, StringConstraints(max_length=10)],
        ]
    ] = Field(None, title="Chirality")
    conformation_method: Optional[Annotated[str, StringConstraints(max_length=500)]] = (
        Field(None, title="Conformarion method")
    )
    is_well: Optional[bool] = Field(None, title="Is this species a well on the PES?")
    is_global_min: Optional[bool] = Field(
        None,
        title="If this conformer is a well, whether it is meant to represents "
        "the **global** minimum energy well",
    )
    global_min_geometry: Optional[
        Dict[
            str,
            Union[
                Tuple[Tuple[float, float, float], ...],
                Tuple[Annotated[int, Field(ge=1)], ...],
                Tuple[Annotated[str, StringConstraints(max_length=10)], ...],
            ],
        ]
    ] = Field(
        None,
        title="If this species does not represent the global minimum well, this argument must contain "
        "the coordinates of the global minimum energy conformer at the same opt level.",
    )
    is_ts: Optional[bool] = Field(
        False, title="Does this species represent a transition state?"
    )
    irc_trajectories: Optional[
        List[
            List[
                Dict[
                    str,
                    Union[
                        Tuple[Tuple[float, float, float], ...],
                        Tuple[Annotated[int, Field(ge=1)], ...],
                        Tuple[Annotated[str, StringConstraints(max_length=10)], ...],
                    ],
                ]
            ]
        ]
    ] = Field(None, title="IRC trajectories (for TS species)")
    electronic_energy: Optional[float] = Field(
        None, title="Electronic energy in Hartree"
    )
    E0: Optional[float] = Field(None, title="E0 (zero-point energy) in kJ/mol")
    active_space: Optional[Dict[str, int]] = Field(
        None, title="The active space (number of electrons and orbitals)"
    )
    hessian: Optional[List[List[float]]] = Field(None, title="Hessian matrix")
    frequencies: Optional[List[float]] = Field(None, title="Calculated frequencies")
    scaled_projected_frequencies: Optional[List[float]] = Field(
        None, title="Scaled and projected frequencies"
    )  # check length after rotors/confs
    normal_displacement_modes: Optional[List[List[List[float]]]] = Field(
        None, title="Normal displacement modes"
    )
    rigid_rotor: Optional[str] = Field(
        None,
        max_length=50,
        title='The rigid rotor treatment type. Allowed values: "atom", '
        '"linear", "spherical top", "symmetric top", or "asymmetric top".',
    )
    statmech_treatment: Optional[str] = Field(
        None, max_length=50, title="The statistical mechanics treatment"
    )
    rotational_constants: Optional[List[float]] = Field(
        None, title="Rotational constants"
    )
    torsions: Optional[List[TorsionsBase]] = Field(None, title="Torsions")
    conformers: Optional[
        List[
            Dict[
                str,
                Union[
                    Tuple[Tuple[float, float, float], ...],
                    Tuple[Annotated[int, Field(ge=1)], ...],
                    Tuple[Annotated[str, StringConstraints(max_length=10)], ...],
                    float,
                ],
            ]
        ]
    ] = Field(None, title="Conformers")
    H298: Optional[float] = Field(None, title="Standard enthalpy of formation")
    S298: Optional[float] = Field(None, gt=0, title="Standard entropy of formation")
    Cp_values: Optional[List[Annotated[float, Field(gt=0)]]] = Field(
        None, title="Constant pressure heat capacity values"
    )
    Cp_T_list: Optional[List[Annotated[float, Field(gt=0)]]] = Field(
        None, title="Constant pressure heat capacity temperature list"
    )
    heat_capacity_model: Optional[
        Dict[str, Union[float, Dict[str, Union[float, List[float]]], str]]
    ] = Field(None, title="Heat capacity model")

    # Paths
    # Storage requires consideration
    opt_path: Optional[str] = Field(None, title="The path to the opt output file")
    freq_path: Optional[str] = Field(None, title="The path to the freq output file")
    scan_paths: Optional[
        Dict[
            Tuple[
                Tuple[
                    Annotated[int, Field(ge=1)],
                    Annotated[int, Field(ge=1)],
                    Annotated[int, Field(ge=1)],
                    Annotated[int, Field(ge=1)],
                ],
                ...,
            ],
            Annotated[str, StringConstraints(max_length=5000)],
        ]
    ] = Field(None, title="Paths to scan log files")
    irc_paths: Optional[List[Annotated[str, StringConstraints(max_length=5000)]]] = (
        Field(None, title="Paths to IRC log files")
    )
    sp_path: Optional[str] = Field(
        None, max_length=5000, title="Path to single-point energy log file"
    )

    extras: Optional[Dict[str, Any]] = Field(None, title="Extras")
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("fragments")
    def fragments_validator(cls, value, values: ValidationInfo):
        """Species.fragments validator"""
        if value is None:
            return value  # Allow None if the field is optional

        label = (
            f' of species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        atom_indices = list()
        for fragment in value:
            for index in fragment:
                is_valid, err = common.is_valid_atom_index(
                    index=index,
                    coordinates=values.data["coordinates"],
                    existing_indices=atom_indices,
                )
                if not is_valid:
                    raise ValueError(
                        f"The atom index {index} in the fragments attribute{label} is invalid. "
                        f"Got:\n{err}."
                    )
                atom_indices.append(index)
        if (
            "coordinates" in values.data
            and values.data["coordinates"]
            and len(values.data["coordinates"].symbols) != len(atom_indices)
        ):
            raise ValueError(
                f'{len(values.data["coordinates"].symbols)} atoms were specified in the fragments{label}, '
                f"while according to its coordinates it has {len(atom_indices)} atoms."
            )
        return value if len(value) > 1 else None

    @field_validator("fragment_orientation", mode="before")
    def fragment_orientation_validator(cls, value, values: ValidationInfo):
        """Species.fragment_orientation validator"""
        label = (
            f' (species label "{values.data["label"]}")'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if value is None:
            if "fragments" in values.data and values.data["fragments"] is not None:
                raise ValueError(
                    f"Must specify fragment_orientation if fragments are specified{label}."
                )
        else:
            if "fragments" in values.data:
                if values.data["fragments"] is None:
                    raise ValueError(
                        f"The fragment_orientation argument{label} is unexpected if the fragments "
                        f"argument is not specified."
                    )
                if len(value) != len(values.data["fragments"]) - 1:
                    raise ValueError(
                        f'Expected {len(values.data["fragments"]) - 1} fragment orientation entries for a '
                        f'species with {len(values.data["fragments"])} fragments, got {len(value)}.'
                    )
            valid_keys = ["cm", "x", "y", "z"]
            for entry in value:
                if len(list(entry.keys())) != 4:
                    raise ValueError(
                        f'Expected the following keys in the fragment_orientation argument: "cm", "x", '
                        f'"y", and "z". Got{label}: {list(entry.keys())}'
                    )
                for key, val in entry.items():
                    if key not in valid_keys:
                        raise ValueError(
                            f'Got an unrecognized key "{key}" in the fragment_orientation '
                            f"attribute{label}."
                        )
                    if key == "cm":
                        if not isinstance(val, list):
                            raise TypeError(
                                f"The center of mass vector in the fragment_orientation attribute must be "
                                f"a list type, got{label}: {type(val)}."
                            )
                        if len(entry[key]) != 3:
                            raise ValueError(
                                f"The center of mass vector in the fragment_orientation attribute{label} "
                                f"has length {len(val)}, should have a length of 3."
                            )
                    elif not isinstance(val, float):
                        raise TypeError(
                            f'The "x", "y", and "z" in the fragment_orientation attribute must have '
                            f"float type values, got{label}: {val} with type {type(val)} in\n{entry}."
                        )
        return value

    @field_validator("point_group")
    def point_group_validator(cls, value, values: ValidationInfo):
        """Species.point_group validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        allowed_first_chars = ["C", "D", "I", "O", "S", "T"]
        allowed_last_chars = ["d", "h", "i", "s", "v"]
        inf = "inf"
        if value[0] not in allowed_first_chars:
            raise ValueError(
                f"Invalid point group{label}: Expected it to *begin* with one of the following "
                f'characters: {allowed_first_chars}.\nGot: "{value}".'
            )
        if (
            value[-1] not in allowed_last_chars
            and not value[-1].isdigit()
            and len(value) > 1
        ):
            raise ValueError(
                f"Invalid point group{label}: Expected it to *end* with one of the following "
                f'characters: {allowed_last_chars}.\nGot: "{value}".'
            )
        for i, char in enumerate(value):
            if i != 0 and i != len(value) - 1:
                if not char.isdigit() and inf not in value:
                    raise ValueError(
                        f'Invalid point group{label}: Expected it to contain only numbers or "inf" in '
                        f'between the first and the last characters.\nGot: "{value}".'
                    )
        return value

    @field_validator("chirality")
    def chirality_validator(cls, value, values: ValidationInfo):
        """Species.chirality validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        chiral_atom_indices = list()
        allowed_values = ["R", "S", "NR", "NS", "E", "Z"]
        allowed_atoms = ["C", "Si", "Ge", "Sn", "Pb", "N", "P", "As", "Sb", "Bi"]
        if value is None:
            return value
        for key, val in value.items():
            for index in key:
                is_valid, err = common.is_valid_atom_index(
                    index=index,
                    coordinates=(
                        values.data["coordinates"]
                        if "coordinates" in values.data
                        else None
                    ),
                    existing_indices=chiral_atom_indices,
                )
                if not is_valid:
                    raise ValueError(
                        f"The atom index {index} in the fragments attribute{label} is invalid. "
                        f"Got:\n{err}."
                    )
                chiral_atom_indices.append(index)
                if (
                    "coordinates" in values.data
                    and values.data["coordinates"].symbols[index - 1]
                    not in allowed_atoms
                ):
                    raise ValueError(
                        f'A chiral site cannot include {values.data["coordinates"].symbols[index - 1]} '
                        f"atoms. Got{label}:\n{value}"
                    )
            if val not in allowed_values:
                raise ValueError(
                    f"The chirality notation is not recognized. Expected it to be in {allowed_values}, "
                    f"got {val} in\n{value}"
                )
            if len(key) == 1:
                if val not in ["R", "S", "NR", "NS"]:
                    raise ValueError(
                        f'A chiral atom center must have one of the following notations: "R", "S", "NR", '
                        f'or "NS", got {val} in {value}{label}.'
                    )
            elif len(key) == 2:
                if val not in ["E", "Z"]:
                    raise ValueError(
                        f'A chiral center around a double bond must be noted by either "E" or "Z", '
                        f"got {val} in {value}{label}."
                    )
            else:
                raise ValueError(
                    f"A chiral center must be noted by either a single atom index or two, got {len(key)} "
                    f"in {value}{label}."
                )
            if (
                val in ["NR", "NS"]
                and "coordinates" in values.data
                and values.data["coordinates"].symbols[key[0] - 1] != "N"
            ):
                raise ValueError(
                    f'A chiral atom center{label} with an "NR" or "NS" notation but be a nitrogen atom.'
                )
            elif (
                val in ["R", "S"]
                and "coordinates" in values.data
                and values.data["coordinates"].symbols[key[0] - 1] == "N"
            ):
                raise ValueError(
                    f'A chiral *nitrogen* atom center{label} with must be noted with "NR" or "NS".'
                )
        return value

    @field_validator("conformation_method", mode="before")
    def conformation_method_validator(cls, value, values: ValidationInfo):
        """Species.conformation_method validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if (
            value is None
            and "coordinates" in values.data
            and len(values.data["coordinates"].symbols) >= 4
        ):
            raise ValueError(
                f"Must provide a conformation method{label} when the species contains more than 4 atoms."
            )
        return value

    @field_validator("global_min_geometry")
    def global_min_geometry_validator(cls, value, values: ValidationInfo):
        """Species.global_min_geometry validator"""
        if value is None:
            return value
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        converter.add_common_isotopes_to_coords(value)
        is_valid, err = common.is_valid_coordinates(value)
        if not is_valid:
            raise ValueError(
                f"The following global_min_geometry coordinates dictionary{label} is invalid:\n"
                f"{value}\nReason:\n{err}"
            )
        return value

    @field_validator("irc_trajectories", mode="before")
    def irc_trajectories_validator(cls, value, values: ValidationInfo):
        """Species.irc_trajectories validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and values.data["is_ts"] and value is None:
            raise ValueError(
                f"IRC trajectories must be given{label} if the species is a TS."
            )
        if "is_ts" in values.data and not values.data["is_ts"] and value is not None:
            raise ValueError(
                f"IRC trajectories were given{label}, but the species is not defined as a TS.\n"
                f'(Set the "is_ts" attribute to True if the species is meant to be a TS.)'
            )
        if value is not None:
            for i, traj in enumerate(value):
                for j, frame in enumerate(traj):
                    converter.add_common_isotopes_to_coords(frame)
                    is_valid, err = common.is_valid_coordinates(frame)
                    if not is_valid:
                        raise ValueError(
                            f"Frame {j} in IRC trajectory {i}{label} is invalid:\n"
                            f"{frame}\nReason:\n{err}"
                        )
        return value

    @field_validator("active_space")
    def active_space_validator(cls, value, values: ValidationInfo):
        """Species.active_space validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        allowed_keys = ["electrons", "orbitals"]
        if value is None:
            return value
        if any(key not in allowed_keys for key in value.keys()):
            raise ValueError(
                f"The active_space argument{label} has unrecognized keys.\n"
                f"Allowed keys: {allowed_keys}, got: {[value.keys()]}."
            )
        if not all(key in [value.keys()] for key in allowed_keys):
            raise ValueError(
                f"Not all required keys of the active_space argument{label} were given.\n"
                f"Required keys: {allowed_keys}, got: {[value.keys()]}."
            )
        return value

    @field_validator("hessian", mode="before")
    def hessian_validator(cls, value, values: ValidationInfo):
        """Species.hessian validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        num_atoms = common.get_number_of_atoms(values.data)
        if num_atoms > 1:
            if value is None:
                raise ValueError(
                    f"The Hessian was not given{label}. It must be given for polyatomic species."
                )
            if len(value) != num_atoms * 3:
                raise ValueError(
                    f"The number of rows in the Hessian matrix ({len(value)}){label} is invalid, "
                    f"expected {num_atoms * 3} rows for {num_atoms} atoms."
                )
            for i, row in enumerate(value):
                if len(row) < i + 1:
                    raise ValueError(
                        f"Row {i} of the Hesian matrix{label} has only {len(row)} elements, "
                        f"expected {i + 1} elements."
                    )
        return value

    @field_validator("scaled_projected_frequencies", mode="before")
    def scaled_projected_frequencies_validator(cls, value, values: ValidationInfo):
        """Species.scaled_projected_frequencies validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if value is None and common.get_number_of_atoms(values.data) > 1:
            raise ValueError(
                f"Scaled projected frequencies were not given{label}."
                f"Must be specified for polyatomic species."
            )
        if value is not None:
            if any(i == 0 for i in value):
                raise ValueError(
                    f"A frequency (scaled_projected_frequencies) cannot be zero, got {value}{label}."
                )
            if "frequencies" in values.data:
                if len(value) > len(values.data["frequencies"]):
                    raise ValueError(
                        f"The scaled_projected_frequencies (length {len(value)}) cannot have more "
                        f"entries that the frequencies (length {len(values.data['frequencies'])}{label}."
                    )
                if value == values.data["frequencies"]:
                    raise ValueError(
                        "The scaled_projected_frequencies are identical to the frequencies.\n"
                        f"Did you forget to scale?"
                    )
        return value

    @field_validator("normal_displacement_modes", mode="before")
    def normal_displacement_modes_validator(cls, value, values: ValidationInfo):
        """Species.normal_displacement_modes validator"""
        # The normal displacement modes. Required for polyatomic species (with 2 or more atoms).
        if value is None and common.get_number_of_atoms(values.data) < 2:
            return value
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "frequencies" in values.data and values.data["frequencies"] is not None:
            if value is None:
                raise ValueError(f"Normal displacement modes were not given{label}.")
            if len(value) != len(values.data["frequencies"]):
                raise ValueError(
                    f"The number of normal displacement modes ({len(value)}) "
                    f"differs from the number of frequencies ({len(values.data['frequencies'])}){label}."
                )
            num_atoms = common.get_number_of_atoms(values.data)
            if num_atoms is not None:
                for ndm in value:
                    if len(ndm) != num_atoms:
                        raise ValueError(
                            f"The number of normal displacement modes per frequency must be equal "
                            f"to the number of atoms ({num_atoms}), got {len(ndm)}."
                        )
                    for displacement in ndm:
                        if len(displacement) != 3:
                            raise ValueError(
                                f"Each displacement (per frequency per atom) must be a list of length 3, "
                                f"got {len(displacement)}."
                            )
        return value

    @field_validator("frequencies")
    def frequencies_validator(cls, value, values: ValidationInfo):
        """Species.frequencies validator"""
        label = f' for species "{values.data["label"]}"' if values.data["label"] else ""

        if value is None and common.get_number_of_atoms(values.data) > 1:
            raise ValueError(
                f"Frequencies were not given{label}. Frequencies must be specified for polyatomic species."
            )

        if value is not None:
            if any(i == 0 for i in value):
                raise ValueError(f"A frequency cannot be zero, got {value}{label}.")

            coordinates = values.data.get("coordinates")
            if coordinates is not None:
                linear = is_linear(coordinates=np.array(coordinates.coords))
                num_atoms = common.get_number_of_atoms(values.data)
                if num_atoms is not None:
                    expected_num_freqs = 3 * num_atoms - (
                        6 - int(linear)
                    )  # 3N-6 for non-linear, 3N-5 for linear
                    if len(value) != expected_num_freqs:
                        linear_txt = "linear" if linear else "non-linear"
                        raise ValueError(
                            f"Expected {expected_num_freqs} frequencies for a {linear_txt} molecule, "
                            f"got {len(value)} frequencies{label}."
                        )

            if values.data["is_ts"] and all(freq > 0 for freq in value):
                raise ValueError(
                    f"An imaginary frequency must be present for a TS species. "
                    f"Got all real frequencies{label}."
                )

        return value

    @model_validator(mode="before")
    @classmethod
    def handle_descriptors(cls, values: dict):
        """
        Handle the interdependent fields: smiles, inchi, and graph.
        Populate missing fields based on the provided descriptor.
        """
        smiles = values.get("smiles")
        inchi = values.get("inchi")
        graph = values.get("graph")
        inchi_key = values.get("inchi_key")

        if graph:
            # If graph is provided, derive smiles and inchi if not provided
            if not smiles or not inchi:
                smiles, inchi = (
                    adjlist_conversion_process.smiles_and_inchi_from_adjlist(graph)
                )
                values["smiles"] = smiles or values.data["smiles"]
                values["inchi"] = inchi or values.data["inchi"]
        elif inchi:
            # If InChI is provided, derive smiles and graph if not provided
            if not smiles:
                smiles = converter.smiles_from_inchi(inchi)
                values["smiles"] = smiles
            if not graph:
                graph = converter.adjlist_from_smiles(smiles)
                values["graph"] = graph
        elif smiles:
            # If SMILES is provided, derive inchi and graph if not provided
            if not inchi:
                inchi = converter.inchi_from_smiles(smiles)
                values["inchi"] = inchi
            if not graph:
                graph = converter.adjlist_from_smiles(smiles)
                values["graph"] = graph
        elif inchi_key:
            # If InChI Key is provided without InChI, derive InChI, smiles, and graph
            if not inchi:
                inchi = converter.inchi_from_inchi_key(inchi_key)
                values["inchi"] = inchi
            if inchi:
                if not smiles:
                    smiles = converter.smiles_from_inchi(inchi)
                    values["smiles"] = smiles
                if not graph:
                    graph = converter.adjlist_from_smiles(smiles)
                    values["graph"] = graph

        if not inchi_key and inchi:
            inchi_key = converter.inchi_key_from_inchi(inchi)
            values["inchi_key"] = inchi_key

        # Ensure that at least one descriptor is present
        if not (values["smiles"] or values["inchi"] or values["graph"]):
            raise ValueError(
                "A species descriptor (SMILES, InChI, or graph adjacency list) must be given."
            )

        return values

    @field_validator("graph")
    def validate_graph(cls, value, values: ValidationInfo):
        """
        Validate the adjacency list graph.
        """
        label = (
            f' (species label: "{values.data["label"]}")'
            if values.data["label"]
            else ""
        )
        if value:
            is_valid, err = common.is_valid_adjlist(value)
            if not is_valid:
                raise ValueError(
                    f"The RMG adjacency list{label} is invalid:\n{value}\nReason:\n{err}"
                )
            multiplicity = multiplicity_from_adjlist(value)
            print("**************")
            print(multiplicity)
            print("**************")
            if multiplicity != values.data.get("multiplicity"):
                if not (
                    abs(values.data.get("multiplicity") - multiplicity) % 2
                    + abs(values.data.get("charge", 0))
                ):
                    # the difference is even, so it makes sense
                    adjlist_no_multiplicity = (
                        value.split("\n", 1)[1] if "multiplicity" in value else value
                    )
                    value = f'multiplicity {values.data["multiplicity"]}\n{adjlist_no_multiplicity}'
                else:
                    raise ValueError(
                        f'The given multiplicity {values.data["multiplicity"]} and the multiplicity of the '
                        f"graph adjacency list mismatch{label}:\n{value}"
                    )
        return value

    @field_validator("rigid_rotor")
    def rigid_rotor_validator(cls, value, values: ValidationInfo):
        """Species.rigid_rotor validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        allowed_values = [
            "atom",
            "linear",
            "spherical top",
            "symmetric top",
            "asymmetric top",
        ]
        if value not in allowed_values:
            raise ValueError(
                f"The given rigid_rotor ({value}){label} is not recognized.\n"
                f"Allowed values are {allowed_values}."
            )
        return value

    @field_validator("statmech_treatment", mode="before")
    def statmech_treatment_validator(cls, value, values: ValidationInfo):
        """Species.statmech_treatment validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        allowed_values = ["RRHO", "RRHO-1D", "RRHO-1D-ND", "RRHO-ND", "RRHO-AD", "RRAO"]
        if value is None and common.get_number_of_atoms(values.data) > 2:
            raise ValueError(
                f"statmech_treatment was not given{label}. A statistical mechanics treatment "
                f"(one of {allowed_values}) must be specified for polyatomic species."
            )
        if value is not None and value not in allowed_values:
            raise ValueError(
                f"The statmech_treatment {value} is not recognized.\n"
                f"Allowed values are: {allowed_values}"
            )
        return value

    @field_validator("rotational_constants", mode="before")
    def rotational_constants_validator(cls, value, values: ValidationInfo):
        """Species.rotational_constants validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if value is None and common.get_number_of_atoms(values.data) > 1:
            raise ValueError(f"No rotational constants specified{label}.")
        if value is not None:
            if common.get_number_of_atoms(values.data) == 1:
                raise ValueError(
                    f"Rotational constants were specified for a monoatomic species{label} ({value})."
                )
            if "coordinates" in values.data and "coords" in values.data["coordinates"]:
                linear = is_linear(
                    coordinates=np.array(values.data["coordinates"]["coords"])
                )
                if len(value) != 1 and linear:
                    raise ValueError(
                        f"More than one rotational constant was specified for a linear species{label} "
                        f"({value})."
                    )
                if len(value) != 3 and not linear:
                    raise ValueError(
                        f"The number of rotational constants for a non-linear species{label} must be 3.\n"
                        f"Got {len(value)} rotational constants: {value}."
                    )
        return value

    @field_validator("conformers")
    def conformers_validator(cls, value, values: ValidationInfo):
        """Species.conformers validator"""
        if value is None:
            return value
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "torsions" in values.data and values.data["torsions"]:
            raise ValueError(
                f"Either torsions or conformers must be given, got both{label}."
            )
        for conformer in value:
            is_valid, err = common.is_valid_coordinates(
                conformer, allowed_keys=["energy", "degeneracy"]
            )
            if not is_valid:
                raise ValueError(
                    f"Not all conformers{label} are valid. Reason:\n{err}\n"
                    f"Got:\n{conformer}\nin:\n{value}."
                )
            if "energy" not in conformer:
                raise ValueError(
                    f'A conformer entry in the conformers argument{label} must have an "energy" key.'
                )
            if not isinstance(conformer["energy"], float):
                raise ValueError(
                    f"A conformer energy must be a float, got {conformer['energy']} which is a "
                    f"{type(conformer['energy'])}{label}."
                )
            if "degeneracy" not in conformer:
                conformer["degeneracy"] = 1
            if conformer["degeneracy"] % 1:
                # The degeneracy is converted to float, cannot check isinstance for int
                raise ValueError(
                    f"A conformer degeneracy must be a float, got {conformer['degeneracy']} which is a "
                    f"{type(conformer['degeneracy'])}{label}."
                )
        return value

    @field_validator("H298", mode="before")
    def h298_validator(cls, value, values: ValidationInfo):
        """Species.H298 validator"""
        label = (
            f' "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and not values.data["is_ts"] and value is None:
            raise ValueError(
                f'The "H298" argument must be given for non-TS species{label}.'
            )
        return value

    @field_validator("S298", mode="before")
    def s298_validator(cls, value, values: ValidationInfo):
        """Species.S298 validator"""
        label = (
            f' "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and not values.data["is_ts"] and value is None:
            raise ValueError(
                f'The "S298" argument must be given for non-TS species{label}.'
            )
        return value

    @field_validator("Cp_values", mode="before")
    def cp_values_validator(cls, value, values: ValidationInfo):
        """Species.Cp_values validator"""
        label = (
            f' "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and not values.data["is_ts"] and value is None:
            raise ValueError(
                f'The "Cp_values" argument must be given for non-TS species{label}.'
            )
        return value

    @field_validator("Cp_T_list", mode="before")
    def cp_t_list_validator(cls, value, values: ValidationInfo):
        """Species.Cp_T_list validator"""
        label = (
            f' "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and not values.data["is_ts"] and value is None:
            raise ValueError(
                f'The "Cp_T_list" argument must be given for non-TS species{label}.'
            )
        if (
            "Cp_values" in values.data
            and values.data["Cp_values"]
            and value is not None
            and len(values.data["Cp_values"]) != len(value)
        ):
            raise ValueError(
                f"The number of Cp values ({len(values.data['Cp_values'])}) "
                f"must be equal to the number of Cp temperatures ({len(value)})."
            )
        return value

    @field_validator("opt_path", mode="before")
    def opt_path_validator(cls, value, values: ValidationInfo):
        """Species.opt_path validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if common.get_number_of_atoms(values.data) > 1 and value is None:
            raise ValueError(f"The opt_path was not given{label}.")
        return value

    @field_validator("freq_path", mode="before")
    def freq_path_validator(cls, value, values: ValidationInfo):
        """Species.freq_path validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if common.get_number_of_atoms(values.data) > 1 and value is None:
            raise ValueError(f"The freq_path was not given{label}.")
        return value

    @field_validator("scan_paths", mode="before")
    def scan_paths_validator(cls, value, values: ValidationInfo):
        """Species.scan_paths validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "torsions" in values.data and values.data["torsions"]:
            if value is None:
                raise ValueError(f"The scan_paths was not given{label}.")
            else:
                for torsion in values.data["torsions"]:
                    torsion_indices = tuple(
                        tuple(indices) for indices in torsion.torsions
                    )
                    match = False
                    for path_key in value.keys():
                        if path_key == torsion_indices:
                            match = True
                            break
                    if not match:
                        raise ValueError(
                            f"Could not find a corresponding scan path "
                            f"for the torsion {torsion_indices}{label}."
                        )
        return value

    @field_validator("irc_paths", mode="before")
    def irc_paths_validator(cls, value, values: ValidationInfo):
        """Species.irc_paths validator"""
        label = (
            f' for species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and values.data["is_ts"] and value is None:
            raise ValueError(f"The irc_paths argument was not given{label}.")
        if value is not None and len(value) not in [1, 2]:
            raise ValueError(
                f"The length of the IRC paths argument must be either 1 (for a forward+reverse IRC) or 2. "
                f"Got: {len(value)}{label}."
            )
        return value


class SpeciesCreate(SpeciesBase):

    # Required Fields
    charge: int = Field(..., ge=-10, le=10, title="The net charge of the species")
    multiplicity: int = Field(
        ..., ge=1, le=10, title="The spin multiplicity of the species"
    )
    external_symmetry: int = Field(..., ge=1, title="External symmetry")
    point_group: str = Field(..., max_length=6, title="Point group")
    electronic_energy: float = Field(..., title="Electronic energy in Hartree")
    E0: float = Field(..., title="E0 (zero-point energy) in kJ/mol")
    is_well: bool = Field(..., title="Is this species a well on the PES?")

    sp_path: str = Field(
        ..., max_length=5000, title="Path to single-point energy log file"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    # @model_validator
    # def check_descriptor_presence(cls, info: ValidationInfo):
    #     smiles, inchi, graph = values.data.get('smiles'), values.data.get('inchi'), values.data.get('graph')
    #     if not (smiles or inchi or graph):
    #         raise ValueError("At least one of 'smiles', 'inchi', or 'graph' must be provided.")
    #     return values


class SpeciesCreateBatch(SpeciesBase, ConnectionBase):

    bot_connection_id: Optional[str] = Field(
        None, title="The connection ID of the bot object for internal referencing"
    )
    literature_connection_id: Optional[str] = Field(
        None,
        title="The connection ID of the literature object for internal referencing",
    )
    encorr_connection_id: Optional[str] = Field(
        None,
        title="The connection ID of the enthalpy correction object for internal referencing",
    )
    freq_scale_connection_id: Optional[str] = Field(
        None,
        title="The connection ID of the frequency scaling object for internal referencing",
    )

    level_connections: Optional[LevelConnectionID] = Field(
        None, title="The connection IDs of the level objects for internal referencing"
    )
    ess_connections: Optional[ESSConnectionID] = Field(
        None, title="The connection IDs of the ESS objects for internal referencing"
    )


class SpeciesUpdate(SpeciesBase):
    pass


class SpeciesRead(SpeciesBase):

    id: int = Field(..., title="Species ID")

    # Non-User Input
    reviewed: bool = Field(False, title="Is this species reviewed?")
    approved: bool = Field(None, title="Is this species approved?")
    retraction: str = Field(None, title="Retraction reason")
    reviewer_flags: Dict[str, str] = Field(None, title="Reviewer flags")
    timestamp: datetime = Field(..., title="Timestamp")

    # Freq Scale, Encorr, Lit, Bot and ESS Level IDs
    # TODO: Discuss how to show the readout of these other tables
    freq_scale_id: Optional[int] = Field(None, title="The frequency scaling ID")

    encorr_id: Optional[int] = Field(None, title="The enthalpy correction ID")
    literature_id: Optional[int] = Field(None, title="The literature ID")
    bot_id: Optional[int] = Field(None, title="The bot ID")

    opt_level_id: Optional[int] = Field(None, title="The opt level ID")
    freq_level_id: Optional[int] = Field(None, title="The freq level ID")
    scan_level_id: Optional[int] = Field(None, title="The scan level ID")
    irc_level_id: Optional[int] = Field(None, title="The IRC level ID")
    sp_level_id: Optional[int] = Field(None, title="The single point level ID")

    opt_ess_id: Optional[int] = Field(None, title="The opt ESS ID")
    freq_ess_id: Optional[int] = Field(None, title="The freq ESS ID")
    scan_ess_id: Optional[int] = Field(None, title="The scan ESS ID")
    irc_ess_id: Optional[int] = Field(None, title="The IRC ESS ID")
    sp_ess_id: Optional[int] = Field(None, title="The single point ESS ID")
    model_config = ConfigDict(from_attributes=True)


class SpeciesUpdate(SpeciesBase):
    pass


class SpeciesUpdateBatch(SpeciesBase, ConnectionBase):
    pass


class SpeciesInDBBase(SpeciesBase):
    id: int = Field(..., title="Species ID")

    freq_scale_id: Optional[int] = Field(None, title="The frequency scaling ID")

    encorr_id: Optional[int] = Field(None, title="The enthalpy correction ID")
    literature_id: Optional[int] = Field(None, title="The literature ID")
    bot_id: Optional[int] = Field(None, title="The bot ID")

    opt_level_id: Optional[int] = Field(None, title="The opt level ID")
    freq_level_id: Optional[int] = Field(None, title="The freq level ID")
    scan_level_id: Optional[int] = Field(None, title="The scan level ID")
    irc_level_id: Optional[int] = Field(None, title="The IRC level ID")
    sp_level_id: Optional[int] = Field(None, title="The single point level ID")

    opt_ess_id: Optional[int] = Field(None, title="The opt ESS ID")
    freq_ess_id: Optional[int] = Field(None, title="The freq ESS ID")
    scan_ess_id: Optional[int] = Field(None, title="The scan ESS ID")
    irc_ess_id: Optional[int] = Field(None, title="The IRC ESS ID")
    sp_ess_id: Optional[int] = Field(None, title="The single point ESS ID")
    model_config = ConfigDict(from_attributes=True)
