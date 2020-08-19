"""
TCKDB backend app schemas en_corr module
"""

from typing import Dict, List, Optional, Union

from pydantic import BaseModel, conint, constr, validator

from .common import is_valid_energy_unit, is_valid_element_symbol, is_valid_inchi, is_valid_smiles


class EnCorrBase(BaseModel):
    """
    An EnCorrBase class (shared properties)
    """
    level_id: conint(ge=0)
    supported_elements: List[str]
    energy_unit: constr(max_length=255)
    aec: Optional[Dict[str, float]] = None
    bac: Optional[Dict[str, float]] = None
    isodesmic_reactions: Optional[List[Dict[str, Union[list, float]]]] = None
    isodesmic_high_level_id: Optional[conint(ge=0)] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """EnCorr.reviewer_flags validator"""
        return value or dict()

    @validator('supported_elements', pre=True, always=True)
    def elements_exist(cls, value):
        """EnCorr.supported_elements validator"""
        for symbol in value:
            if not is_valid_element_symbol(symbol):
                raise ValueError(f'The symbol {symbol} does not seem to correspond to a known chemical element.\n'
                                 f'Got: {value}')
        return value

    @validator('energy_unit')
    def validate_energy_unit(cls, value):
        """EnCorr.energy_unit validator"""
        if not is_valid_energy_unit(value):
            raise ValueError(f'The energy unit "{value}" does not seem to be a valid energy unit.')
        return value

    @validator('aec')
    def validate_aec(cls, value, values):
        """EnCorr.aec validator"""
        for symbol in value.keys():
            if 'supported_elements' in values:
                if symbol not in values['supported_elements']:
                    raise ValueError(f'The supported_elements list is missing the symbol "{symbol}".\n'
                                     f'Got: {values["supported_elements"]}\n'
                                     f'and: {value}')
                if len(values['supported_elements']) != len(list(value.keys())):
                    raise ValueError(f'The supported_elements list length and the number '
                                     f'of entries in aec must be equal.\n'
                                     f'Got: {values["supported_elements"]} '
                                     f'(length {len(values["supported_elements"])})\n'
                                     f'and: {value}\n(number of elements: {len(list(value.keys()))})')
        return value

    @validator('bac')
    def validate_bac(cls, value, values):
        """EnCorr.bac validator"""
        bond_descriptors = ['-', '=', '#', '--', '&']
        for entry in value.keys():
            if ' ' in entry:
                raise ValueError(f'A bond representation cannot contain spaces. Got {entry} in\n{value}')
            bond_count = sum([entry.count(bond) for bond in bond_descriptors])
            if not bond_count:
                raise ValueError(f'Could not find a bond descriptor in {entry}. Recognized bond descriptors are '
                                 f'{bond_descriptors}. Got:\n{value}')
            if bond_count > 1:
                raise ValueError(f'Found {bond_count} bond descriptors in {entry} (expected to find only one). '
                                 f'Got:\n{value}')
            for bond_descriptor in bond_descriptors:
                if bond_descriptor in entry:
                    break
            symbols = entry.split(bond_descriptor)
            for symbol in symbols:
                if 'supported_elements' in values and symbol not in values['supported_elements']:
                    raise ValueError(f'The supported_elements list is missing the symbol "{symbol}".\n'
                                     f'Got: {values["supported_elements"]} and {entry} in\n'
                                     f'{value}')
        return value

    @validator('isodesmic_reactions', always=True)
    def validate_isodesmic_reactions(cls, value, values):
        """EnCorr.isodesmic_reactions validator"""
        if not value and 'aec' in values and 'bac' in values \
                and not all([attribute is not None for attribute in [values['aec'], values['bac']]]):
            raise ValueError(f'Either isodesmic reactions or aec and bac arguments must be specified.')
        if value is not None:
            if 'aec' in values and 'bac' in values \
                    and any([attribute is not None for attribute in [values['aec'], values['bac']]]):
                raise ValueError(f'When specifying isodesmic reactions, both aec and bac arguments must not be '
                                 f'specified.\nGot: {values["aec"]}\nand: {values["bac"]}')
            for isodesmic_reaction in value:
                for key, val in isodesmic_reaction.items():
                    if key in ['reactants', 'products']:
                        if not isinstance(val, list):
                            raise ValueError(f'The reactants and products in an isodesmic reaction must be lists, '
                                             f'got {val} which is a {type(val)} in:\n{isodesmic_reaction}')
                        for identifier in val:
                            if not is_valid_inchi(identifier) and not is_valid_smiles(identifier):
                                raise ValueError(f'Got an invalid species identifier {identifier} '
                                                 f'in {isodesmic_reaction}.')
                    elif key == 'stoichiometry':
                        if not isinstance(val, list):
                            raise ValueError(f'The stoichiometry argument of an isodesmic reaction must be a list, '
                                             f'got {val} which is a {type(val)} in:\n{isodesmic_reaction}')
                        for coefficient in val:
                            if not isinstance(coefficient, int):
                                try:
                                    value['stoichiometry'] = [int(v) for v in value['stoichiometry']]
                                except ValueError:
                                    raise ValueError(f'The stoichiometry coefficients must be integers, '
                                                     f'got {coefficient} which is a {type(coefficient)} in:'
                                                     f'\n{isodesmic_reaction}')
                                break
                    elif key == 'DHrxn298':
                        if not isinstance(val, float):
                            raise ValueError(f'The DHrxn298 argument of an isodesmic reaction must be a float, '
                                             f'got {val} which is a {type(val)} in:\n{isodesmic_reaction}')
                    else:
                        raise ValueError(f'Allowed keys in an isodesmic reaction are "reactants", "products", '
                                         f'"stoichiometry", and "DHrxn298". Got {key}.')
                if len(list(isodesmic_reaction.keys())) != 4:
                    raise ValueError(f'An isodesmic reaction entry has to include all three "reactants", "products", '
                                     f'"stoichiometry", and "DHrxn298" keys.\n'
                                     f'Got {isodesmic_reaction}\n'
                                     f'in: {value}')
        return value

    @validator('isodesmic_high_level_id', always=True)
    def validate_isodesmic_high_level_id(cls, value, values):
        """EnCorr.isodesmic_high_level_id validator"""
        if 'isodesmic_reactions' in values and values['isodesmic_reactions'] is not None and value is None:
            raise ValueError('The isodesmic high level argument must be given when specifying isodesmic_reactions.')
        if 'level_id' in values and value == values['level_id']:
            raise ValueError('The isodesmic high level must be different than the level of theory these '
                             'corrections apply for.')
        return value


class EnCorrCreate(EnCorrBase):
    """Create an EnCorr item: Properties to receive on item creation"""
    level_id: int
    supported_elements: List[str]
    energy_unit: str
    aec: Optional[Dict[str, float]] = None
    bac: Optional[Dict[str, float]] = None
    isodesmic_reactions: Optional[List[Dict[str, Union[List[str], List[int], float]]]] = None
    isodesmic_high_level_id: Optional[int] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class EnCorrUpdate(EnCorrBase):
    """Update an EnCorr item: Properties to receive on item update"""
    level_id: int
    supported_elements: List[str]
    energy_unit: str
    aec: Optional[Dict[str, float]] = None
    bac: Optional[Dict[str, float]] = None
    isodesmic_reactions: Optional[List[Dict[str, Union[List[str], List[int], float]]]] = None
    isodesmic_high_level_id: Optional[int] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class EnCorrInDBBase(EnCorrBase):
    """Properties shared by models stored in DB"""
    level_id: int
    supported_elements: List[str]
    energy_unit: str
    aec: Optional[Dict[str, float]] = None
    bac: Optional[Dict[str, float]] = None
    isodesmic_reactions: Optional[List[Dict[str, Union[List[str], List[int], float]]]] = None
    isodesmic_high_level_id: Optional[int] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class EnCorr(EnCorrInDBBase):
    """Properties to return to client"""
    pass


class EnCorrInDB(EnCorrInDBBase):
    """Properties properties stored in DB"""
    pass
