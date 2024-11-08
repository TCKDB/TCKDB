from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

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
from tckdb.backend.app.schemas.common import (
    Coordinates,
    get_number_of_atoms,
    is_valid_adjlist,
    is_valid_atom_index,
    is_valid_coordinates,
    is_valid_inchi,
    is_valid_inchi_key,
    is_valid_smiles,
)
from tckdb.backend.app.schemas.connection_schema import ConnectionBase


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


class NonPhysicalSpeciesBase(BaseModel):
    """
    A NonPhysicalSpeciesBase class (shared properties)
    """

    label: Optional[str] = Field(
        None, max_length=255, title="The label of the non-physical species"
    )
    smiles: Optional[str] = Field(
        None,
        max_length=255,
        title="The SMILES representation of the non-physical species",
    )
    inchi: Optional[str] = Field(
        None,
        max_length=255,
        title="The InChI representation of the non-physical species",
    )
    inchi_key: Optional[str] = Field(
        None, max_length=255, title="The InChI key of the non-physical species"
    )

    charge: Optional[int] = Field(
        None, ge=-10, le=10, title="The net charge of the non-physical species"
    )
    multiplicity: Optional[int] = Field(
        None, ge=1, title="The multiplicity of the non-physical species"
    )
    electronic_state: Optional[str] = Field(
        "X",
        title='The electronic state of the non-physical species. Default is "X", denoting ground state',
    )

    graph: Optional[str] = Field(
        None, title="The 2D connectivity graph in an RMG adjacency list format"
    )
    coordinates: Optional[Coordinates] = Field(
        None, title="The Cartesian coordinates of the non-physical species"
    )
    fragments: Optional[List[List[Annotated[int, Field(ge=1)]]]] = Field(
        None, title="The fragments of the non-physical species"
    )
    fragment_orientation: Optional[List[Dict[str, Union[float, List[float]]]]] = Field(
        None, title="The orientation of the fragments of the non-physical species"
    )
    chirality: Optional[
        Dict[
            Tuple[Annotated[int, Field(ge=1)], ...],
            Annotated[str, StringConstraints(max_length=10)],
        ]
    ] = Field(None, title="The chirality of the non-physical species")
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
        title="If this non-phsyical species does not represent the global minimum well, this argument must contain "
        "the coordinates of the global minimum energy conformer at the same opt level.",
    )

    # TS
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

    @model_validator(mode="before")
    def check_descriptor_presence(cls, v):
        smiles, inchi, graph = v.get("smiles"), v.get("inchi"), v.get("graph")
        if not (smiles or inchi or graph):
            raise ValueError(
                "At least one of 'smiles', 'inchi', or 'graph' must be provided."
            )
        return v

    @field_validator("smiles")
    @classmethod
    def smiles_validator(cls, v, values: ValidationInfo):
        """NonPhysicalSpecies.smiles validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        is_valid, err = is_valid_smiles(v)
        if not is_valid:
            raise ValueError(f'The SMILES "{v}"{label} is invalid. Reason:\n{err}')
        return v

    @field_validator("inchi")
    @classmethod
    def inchi_validator(cls, v, values: ValidationInfo):
        """NonPhysicalSpecies.inchi validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        is_valid, err = is_valid_inchi(v)
        if not is_valid:
            raise ValueError(f'The InChI "{v}"{label} is invalid. Reason:\n{err}')
        return v

    @field_validator("inchi_key")
    @classmethod
    def inchi_key_validator(cls, v, values: ValidationInfo):
        """NonPhysicalSpecies.inchi_key validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        is_valid, err = is_valid_inchi_key(v)
        if not is_valid:
            raise ValueError(f'The InChI Key "{v}"{label} is invalid. Reason:\n{err}')
        return v

    @field_validator("graph", mode="before")
    @classmethod
    def graph_validator(cls, value, values: ValidationInfo):
        """
        NonPhysicalSpecies.graph validator
        Also used to populate SMILES, InChI, InChI Key, adjlist
        """
        label = (
            f' (non-physical-species label: "{values.data["label"]}")'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if value is not None:
            # adjlist was given, populate other attributes as needed
            if values.data["smiles"] is None or values.data["inchi"] is None:
                smiles, inchi = converter.smiles_and_inchi_from_adjlist(value)
                values.data["smiles"] = values.data["smiles"] or smiles
                values.data["inchi"] = values.data["inchi"] or inchi
        if values["inchi"] is not None:
            # InChI was given, populate other attributes as needed
            if "smiles" not in values.data or not values.data["smiles"]:
                values.data["smiles"] = converter.smiles_from_inchi(values.data["inchi"])
            value = value or converter.adjlist_from_smiles(values.data["smiles"])
        if "smiles" in values.data and values.data["smiles"] is not None:
            # SMILES was given, populate other attributes as needed
            value = value or converter.adjlist_from_smiles(values["smiles"])
            values["inchi"] = values.data["inchi"] or converter.inchi_from_smiles(
                values.data["smiles"]
            )
        # populate the InChI Key if not already set
        if values.data["inchi_key"] is not None and values.data["inchi"] is None:
            # InChI Key was given (and there's no InChI), populate other attributes as needed
            values.data["inchi"] = converter.inchi_from_inchi_key(values.data["inchi_key"])
            if values.data["inchi"] is not None:
                values["smiles"] = values.data["smiles"] or converter.smiles_from_inchi(
                    values.data["inchi"]
                )
                value = value or converter.adjlist_from_smiles(values.data["smiles"])
        values.data["inchi_key"] = values.data["inchi_key"] or converter.inchi_key_from_inchi(
            values.data["inchi"]
        )
        if (
            values.data is None
            or ("smiles" in values.data and values.data["smiles"] is None)
            or ("inchi" in values.data and values.data["inchi"] is None)
        ):
            # couldn't populate adjlist, SMILES, nor InChI
            raise ValueError(
                f"A species descriptor (SMILES, InChI, or graph adjacency list) must be given{label}."
            )
        # adjlist validation
        if value is not None:
            is_valid, err = is_valid_adjlist(value)
            if not is_valid:
                raise ValueError(
                    f"The RMG adjacency list{label} is invalid:\n{value}\nReason:\n{err}"
                )
            multiplicity = converter.multiplicity_from_adjlist(value)
            if multiplicity != values.data["multiplicity"]:
                if not abs(values.data["multiplicity"] - multiplicity) % 2 + abs(
                    values.data["charge"]
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

    @field_validator("coordinates")
    @classmethod
    def coordinates_validator(cls, v, values: ValidationInfo):
        """NonPhysicalSpecies.coordinates validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        converter.add_common_isotopes_to_coords(v)
        is_valid, err = is_valid_coordinates(v)
        if not is_valid:
            raise ValueError(
                f"The following coordinates dictionary{label} is invalid:\n{v}\nReason:\n{err}"
            )
        return v

    @field_validator("fragments")
    @classmethod
    def fragments_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.fragments validator"""
        label = (
            f' of non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        atom_indices = list()
        for fragment in value:
            for index in fragment:
                is_valid, err = is_valid_atom_index(
                    index=index,
                    coordinates=(
                        values.data["coordinates"] if "coordinates" in values.data else None
                    ),
                    existing_indices=atom_indices,
                )
                if not is_valid:
                    raise ValueError(
                        f"The atom index {index} in the fragments attribute{label} is invalid. "
                        f"Got:\n{err}."
                    )
                atom_indices.append(index)
        if "coordinates" in values.data and len(values.data["coordinates"]["symbols"]) != len(
            atom_indices
        ):
            raise ValueError(
                f'{len(values["coordinates"]["symbols"])} atoms were specified in the fragments{label}, '
                f"while according to its coordinates it has {len(atom_indices)} atoms."
            )
        value = value if len(value) > 1 else None
        return value

    @field_validator("fragment_orientation", mode="before")
    @classmethod
    def fragment_orientation_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.fragment_orientation validator"""
        label = (
            f' (non-physical-species label "{values["label"]}")'
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

    @field_validator("chirality")
    @classmethod
    def chirality_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.chirality validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        chiral_atom_indices = list()
        allowed_values = ["R", "S", "NR", "NS", "E", "Z"]
        allowed_atoms = ["C", "Si", "Ge", "Sn", "Pb", "N", "P", "As", "Sb", "Bi"]
        for key, val in value.items():
            for index in key:
                is_valid, err = is_valid_atom_index(
                    index=index,
                    coordinates=(
                        values.data["coordinates"] if "coordinates" in values.data else None
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
                    and values.data["coordinates"]["symbols"][index - 1] not in allowed_atoms
                ):
                    raise ValueError(
                        f'A chiral site cannot include {values.data["coordinates"]["symbols"][index - 1]} '
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
                and values.data["coordinates"]["symbols"][key[0] - 1] != "N"
            ):
                raise ValueError(
                    f'A chiral atom center{label} with an "NR" or "NS" notation but be a nitrogen atom.'
                )
            elif (
                val in ["R", "S"]
                and "coordinates" in values.data
                and values["coordinates"]["symbols"][key[0] - 1] == "N"
            ):
                raise ValueError(
                    f'A chiral *nitrogen* atom center{label} with must be noted with "NR" or "NS".'
                )
        return value

    @field_validator("conformation_method", mode="before")
    @classmethod
    def conformation_method_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.conformation_method validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if (
            value is None
            and "coordinates" in values.data
            and len(values.data["coordinates"]["symbols"]) >= 4
        ):
            raise ValueError(
                f"Must provide a conformation method{label} when the species contains more than 4 atoms."
            )
        return value

    @field_validator("global_min_geometry")
    @classmethod
    def global_min_geometry_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.global_min_geometry validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        converter.add_common_isotopes_to_coords(value)
        is_valid, err = is_valid_coordinates(value)
        if not is_valid:
            raise ValueError(
                f"The following global_min_geometry coordinates dictionary{label} is invalid:\n"
                f"{value}\nReason:\n{err}"
            )
        return value

    @field_validator("irc_trajectories", mode="before")
    @classmethod
    def irc_trajectories_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.irc_trajectories validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "is_ts" in values.data and values.data["is_ts"] and value is None:
            raise ValueError(
                f"IRC trajectories must be given{label} if the species is a TS."
            )
        if value is not None:
            for i, traj in enumerate(value):
                for j, frame in enumerate(traj):
                    converter.add_common_isotopes_to_coords(frame)
                    is_valid, err = is_valid_coordinates(frame)
                    if not is_valid:
                        raise ValueError(
                            f"Frame {j} in IRC trajectory {i}{label} is invalid:\n"
                            f"{frame}\nReason:\n{err}"
                        )
        return value

    @field_validator("opt_path", mode="before")
    @classmethod
    def opt_path_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.opt_path validator"""
        label = (
            f' for non-physical-species "{values["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if get_number_of_atoms(values.data) > 1 and value is None:
            raise ValueError(f"The opt_path was not given{label}.")
        return value

    @field_validator("freq_path", mode="before")
    @classmethod
    def freq_path_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.freq_path validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if get_number_of_atoms(values.data) > 1 and value is None:
            raise ValueError(f"The freq_path was not given{label}.")
        return value

    @field_validator("irc_paths", mode="before")
    @classmethod
    def irc_paths_validator(cls, value, values: ValidationInfo):
        """NonPhysicalSpecies.irc_paths validator"""
        label = (
            f' for non-physical-species "{values.data["label"]}"'
            if "label" in values.data and values.data["label"] is not None
            else ""
        )
        if "irc_trajectories" in values.data and values.data["irc_trajectories"] and value is None:
            raise ValueError(f"The irc_paths argument was not given{label}.")
        if value is not None and len(value) not in [1, 2]:
            raise ValueError(
                f"The length of the IRC paths argument must be either 1 (for a forward+reverse IRC) or 2. "
                f"Got: {len(value)}{label}."
            )
        return value


class NonPhysicalSpeciesCreate(NonPhysicalSpeciesBase):
    """
    A NonPhysicalSpeciesCreate class (to be used to create non-physical species)
    """

    charge: int = Field(..., ge=-10, le=10, title="Net charge")
    multiplicity: int = Field(..., ge=1, le=10, title="Spin multiplicity")
    coordinates: Dict[
        str,
        Union[
            Tuple[Tuple[float, float, float], ...],
            Tuple[Annotated[int, Field(ge=1)], ...],
            Tuple[Annotated[str, StringConstraints(max_length=10)], ...],
        ],
    ] = Field(..., title="Cartesian coordinates")
    is_well: bool = Field(..., title="Is this species a well on the PES?")
    is_ts: bool = Field(False, title="Does this species represent a transition state?")
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class NonPhysicalSpeciesCreateBatch(NonPhysicalSpeciesCreate, ConnectionBase):
    """
    A NonPhysicalSpeciesCreateBatch class (to be used
    to create non-physical species with connection to a other objects
    """

    bot_connection_id: Optional[str] = Field(None, title="The id of the bot connection")
    lit_connection_id: Optional[str] = Field(
        None, title="The id of the literature connection"
    )
    encorr_connection_id: Optional[str] = Field(
        None, title="The id of the EnCorr connection"
    )
    freq_scale_connection_id: Optional[str] = Field(
        None, title="The id of the frequency scaling connection"
    )

    level_connections: Optional[LevelConnectionID] = Field(
        None, title="The id of the level connections"
    )
    ess_connections: Optional[ESSConnectionID] = Field(
        None, title="The id of the ESS connections"
    )


class NonPhysicalSpeciesUpdate(NonPhysicalSpeciesBase):
    """
    A NonPhysicalSpeciesUpdate class (to be used to update non-physical species)
    """

    pass


class NonPhysicalSpeciesRead(NonPhysicalSpeciesBase):
    """
    A NonPhysicalSpeciesRead class (to be used
    to return non-physical species)
    """

    id: int = Field(..., title="NonPhysicalSpecies ID")

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
