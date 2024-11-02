from typing import Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    ValidationInfo,
)

from tckdb.backend.app.schemas.common import (
    is_valid_element_symbol,
    is_valid_energy_unit,
    is_valid_inchi,
    is_valid_smiles,
)
from tckdb.backend.app.schemas.connection_schema import ConnectionBase
from tckdb.backend.app.schemas.level import LevelCreate, LevelRead


class IsodesmicReactionEntry(BaseModel):
    """ """

    reactants: List[str]
    products: List[str]
    stoichiometry: List[int]
    DHrxn298: float
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @model_validator(mode="after")
    def check_required_fields(
        cls, model: "IsodesmicReactionEntry"
    ) -> "IsodesmicReactionEntry":
        if not all(
            [
                model.reactants,
                model.products,
                model.stoichiometry,
                model.DHrxn298 is not None,  # Ensures DHrxn298 is provided
            ]
        ):
            raise ValueError(
                'An isodesmic reaction entry must include all four "reactants", "products", "stoichiometry", and "DHrxn298" keys.'
            )
        return model

    @field_validator("stoichiometry", mode="before")
    @classmethod
    def validate_stoichiometry(cls, v):
        if not isinstance(v, list):
            raise TypeError("Stoichiometry must be a list of integers.")
        return v

    @field_validator("reactants", "products")
    def validate_species_identifiers(cls, v, field):
        for identifier in v:
            is_valid_inchi_, inchi_err = is_valid_inchi(identifier)
            is_valid_smiles_, smiles_err = is_valid_smiles(identifier)
            if not is_valid_inchi_ and not is_valid_smiles_:
                raise ValueError(
                    f'Invalid species identifier "{identifier}". Reason: {inchi_err or smiles_err}'
                )
        return v

    @field_validator("DHrxn298")
    @classmethod
    def validate_DHrxn298(cls, v):
        if not isinstance(v, float):
            raise TypeError("DHrxn298 must be a float.")
        return v


class EnCorrBase(BaseModel):
    """
    An EnCorrBase class (shared properties)
    """

    supported_elements: Optional[List[str]] = Field(
        None, title="The supported elements for the EnCorr calculation"
    )
    energy_unit: Optional[str] = Field(
        None, title="The energy unit for the EnCorr calculation"
    )
    aec: Optional[Dict[str, float]] = Field(
        None,
        title="Atom energy corrections dictionary "
        "(including spin-orbital corrections)",
    )
    bac: Optional[Dict[str, float]] = Field(
        None, title="Bond additivity energy corrections dictionary"
    )

    isodesmic_reactions: Optional[List[IsodesmicReactionEntry]] = Field(
        None, title="Isodesmic reactions for the EnCorr calculation"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("supported_elements")
    @classmethod
    def elements_check(cls, v):
        for element in v:
            is_valid, err = is_valid_element_symbol(element)
            if not is_valid:
                raise ValueError(f'Invalid element symbol "{element}". Reason: {err}')
        return v

    @field_validator("energy_unit")
    @classmethod
    def energy_unit_check(cls, v):
        is_valid, err = is_valid_energy_unit(v)
        if not is_valid:
            raise ValueError(f'Invalid energy unit "{v}". Reason: {err}')
        return v

    @field_validator("aec")
    def validate_aec(cls, value, values: ValidationInfo):
        """EnCorr.aec validator"""
        for symbol in value.keys():
            if "supported_elements" in values.data:
                if symbol not in values["supported_elements"]:
                    raise ValueError(
                        f'The supported_elements list is missing the symbol "{symbol}".\n'
                        f'Got: {values["supported_elements"]}\n'
                        f"and: {value}"
                    )
                if len(values["supported_elements"]) != len(list(value.keys())):
                    raise ValueError(
                        f"The supported_elements list length and the number "
                        f"of entries in aec must be equal.\n"
                        f'Got: {values["supported_elements"]} '
                        f'(length {len(values["supported_elements"])})\n'
                        f"and: {value}\n(number of elements: {len(list(value.keys()))})"
                    )
        return value

    @field_validator("bac")
    def validate_bac(cls, value, values: ValidationInfo):
        """EnCorr.bac validator"""
        bond_descriptors = ["-", "=", "#", "--", "&"]
        for entry in value.keys():
            if " " in entry:
                raise ValueError(
                    f"A bond representation cannot contain spaces. Got {entry} in\n{value}"
                )
            bond_count = sum([entry.count(bond) for bond in bond_descriptors])
            if not bond_count:
                raise ValueError(
                    f"Could not find a bond descriptor in {entry}. Recognized bond descriptors are "
                    f"{bond_descriptors}. Got:\n{value}"
                )
            if bond_count > 1:
                raise ValueError(
                    f"Found {bond_count} bond descriptors in {entry} (expected to find only one). "
                    f"Got:\n{value}"
                )
            for bond_descriptor in bond_descriptors:
                if bond_descriptor in entry:
                    break
            symbols = entry.split(bond_descriptor)
            for symbol in symbols:
                if (
                    "supported_elements" in values.data
                    and symbol not in values["supported_elements"]
                ):
                    raise ValueError(
                        f'The supported_elements list is missing the symbol "{symbol}".\n'
                        f'Got: {values["supported_elements"]} and {entry} in\n'
                        f"{value}"
                    )
        return value

    @field_validator("isodesmic_reactions", mode="before")
    def validate_isodesmic_reactions(cls, value, values: ValidationInfo):
        """EnCorr.isodesmic_reactions validator"""
        if (
            not value
            and "aec" in values.data
            and "bac" in values.data
            and not all(
                [attribute is not None for attribute in [values["aec"], values["bac"]]]
            )
        ):
            raise ValueError(
                "Either isodesmic reactions or aec and bac arguments must be specified."
            )
        if value is not None:
            if (
                "aec" in values.data
                and "bac" in values.data
                and any(
                    [
                        attribute is not None
                        for attribute in [values.data["aec"], values.data["bac"]]
                    ]
                )
            ):
                raise ValueError(
                    f"When specifying isodesmic reactions, both aec and bac arguments must not be "
                    f'specified.\nGot: {values.data["aec"]}\nand: {values.data["bac"]}'
                )
            for isodesmic_reaction in value:
                print("************ISODESMIC REACTION************")
                print(isodesmic_reaction)
                print("************ISODESMIC REACTION************")
                reactants = isodesmic_reaction["reactants"]
                products = isodesmic_reaction["products"]
                stoichiometry = isodesmic_reaction["stoichiometry"]
                DHrxn298 = isodesmic_reaction["DHrxn298"]

                if reactants and products:
                    if not isinstance(reactants, list) or not isinstance(
                        products, list
                    ):
                        raise ValueError(
                            f"The reactants and products in an isodesmic reaction must be lists. "
                            f"Got {reactants} and {products} in:\n{isodesmic_reaction}"
                        )
                        # Check if the reactants and products are valid species identifiers
                    for identifier in reactants + products:
                        is_valid_inchi_, inchi_err = is_valid_inchi(identifier)
                        is_valid_smiles_, smiles_err = is_valid_smiles(identifier)
                        if not is_valid_inchi_ and not is_valid_smiles_:
                            raise ValueError(
                                f"Got an invalid species identifier {identifier} "
                                f"in {isodesmic_reaction}. Reason: {inchi_err or smiles_err}"
                            )
                if stoichiometry:
                    if not isinstance(stoichiometry, list):
                        raise ValueError(
                            f"The stoichiometry argument of an isodesmic reaction must be a list, "
                            f"got {stoichiometry} which is a {type(stoichiometry)} in:\n{isodesmic_reaction}"
                        )
                    for coefficient in stoichiometry:
                        if not isinstance(coefficient, int):
                            try:
                                isodesmic_reaction.stoichiometry = [
                                    int(v) for v in isodesmic_reaction.stoichiometry
                                ]
                            except ValueError as e:
                                raise ValueError(
                                    f"The stoichiometry coefficients must be integers, "
                                    f"got {coefficient} which is a {type(coefficient)} in:"
                                    f"\n{isodesmic_reaction}"
                                ) from e
                if DHrxn298:
                    if not isinstance(DHrxn298, float):
                        raise ValueError(
                            f"The DHrxn298 argument of an isodesmic reaction must be a float, "
                            f"got {DHrxn298} which is a {type(DHrxn298)} in:\n{isodesmic_reaction}"
                        )

                if not all([reactants, products, stoichiometry, DHrxn298]):
                    raise ValueError(
                        f'An isodesmic reaction entry has to include all four "reactants", "products", '
                        f'"stoichiometry", and "DHrxn298" keys.\n'
                        f"Got {isodesmic_reaction}\n"
                        f"in: {value}"
                    )
        return value


class EnCorrCreate(EnCorrBase):
    """
    An EnCorrCreate class (inherited from EnCorrBase)

    Allows for the creation of Primary Level and Isodesmic Level without requiring the connection ID.

    """

    supported_elements: List[str] = Field(
        ..., title="The chemical elements supported by this energy correction object"
    )
    energy_unit: str = Field(
        ..., max_length=255, title="The energy units the corrections are given in"
    )

    primary_level: LevelCreate = Field(
        ..., title="The primary level of theory for the energy correction"
    )
    isodesmic_high_level: Optional[LevelCreate] = Field(
        None, title="The high level of theory for the isodesmic reactions"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("isodesmic_high_level", mode="before")
    def validate_isodesmic_high_level(cls, value, values: ValidationInfo):
        """Ensure that isodesmic_high_level is provided if isodesmic_reactions are specified."""
        if values["isodesmic_reactions"] is not None and value is None:
            raise ValueError(
                "The isodesmic_high_level must be provided when isodesmic_reactions are specified."
            )
        if value is not None and "primary_level" in values.data:
            # Assuming Level uniqueness is based on method, basis, etc., prevent primary and isodesmic levels from being the same
            primary_level = values["primary_level"]
            if (
                primary_level.method == value.method
                and primary_level.basis == value.basis
                and primary_level.auxiliary_basis == value.auxiliary_basis
                and primary_level.level_arguments == value.level_arguments
                and primary_level.solvation_description == value.solvation_description
            ):
                raise ValueError(
                    "The isodesmic_high_level must be different than the primary_level of theory."
                )
        return value


class EnCorrCreateBatch(EnCorrBase, ConnectionBase):
    """
    An EnCorrCreateBatch class (inherited from EnCorrCreate)
    """

    supported_elements: List[str] = Field(
        ..., title="The chemical elements supported by this energy correction object"
    )
    energy_unit: str = Field(
        ..., max_length=255, title="The energy units the corrections are given in"
    )

    # Connection ID
    primary_level_connection_id: Optional[str] = Field(
        None, title="The primary level connection ID for internal referencing"
    )
    isodesmic_level_connection_id: Optional[str] = Field(
        None, title="The isodesmic level connection ID for internal referencing"
    )

    @model_validator(mode="after")
    def check_connections(cls, v):
        primary = v.primary_level_connection_id
        isodesmic = v.isodesmic_level_connection_id
        if primary is not None and isodesmic is not None and primary == isodesmic:
            raise ValueError(
                "Primary and isodesmic level connection IDs must be different. Ensure the Level of Theory is different."
            )
        return v

    @field_validator("isodesmic_level_connection_id")
    def validate_isodesmic_level(cls, v, values: ValidationInfo):
        if values.data["isodesmic_reactions"] is not None and v is None:
            raise ValueError(
                "Isodesmic level connection ID must be provided if isodesmic reactions are specified."
            )


class EnCorrUpdate(EnCorrBase):
    """
    An EnCorrUpdate class (inherited from EnCorrBase)
    """

    pass


class EnCorrRead(EnCorrBase):
    """
    An EnCorrRead class (inherited from EnCorrBase)
    """

    id: int
    primary_level: LevelRead
    isodesmic_high_level: Optional[LevelRead] = None

    reviewer_flags: Optional[Dict[str, str]] = None
    model_config = ConfigDict(from_attributes=True)
