"""
TCKDB backend app models freq module
"""

from sqlalchemy import Column, Float, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class Freq(Base):
    """
    A class for representing a TCKDB Freq item (frequency scaling factor)

    Attributes:
        id (int): The primary key.
        level (dict): The level of theory.
                      Allowed keys are 'method', 'basis', 'dispersion', 'auxiliary_basis', 'solvation'.
                      Values are respective strings except 'solvation' where the value is a dictionary
                      with 'method' and 'solvent' as keys.
                      Note: All string will be converted to a lowercase format.
                      Examples:
                          level_1 = {'method': 'wB97xd', 'basis': 'def2TZVP'}
                          level_1 = {'method': 'wB97xd', 'basis': 'def2TZVP',
                                     'solvation': {'method': 'PCM', 'solvent': 'water'}}
                          level_2 = {'method': 'B3LYP', 'basis': '6-31G(d,p)', 'dispersion': 'gd3bj'}
                          level_3 = {'method': 'CBS-QB3'}
                          level_4 = {'method': 'DLPNO-CCSD(T)-F12', 'basis': 'cc-pVTZ-F12',
                                    'auxiliary_basis': 'aug-cc-pVTZ/C cc-pVTZ-F12-CABS'}
        factor (float): The frequency scaling factor.
        source (str): The source for the determine frequency scaling factor.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    level = Column(MutableDict.as_mutable(JSONEncodedDict), unique=True, nullable=False)
    factor = Column(Float(), nullable=False)
    source = Column(String(255), nullable=False)
    reviewer_flags = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"level={self.level}, " \
               f"factor={self.factor}, " \
               f"source='{self.source}'" \
               f")>"

    def __str__(self) -> str:
        level_str = "{"
        level_str += f"'method': '{self.level['method']}'"
        if 'basis' in self.level:
            level_str += f", 'basis': '{self.level['basis']}'"
        if 'dispersion' in self.level:
            level_str += f", 'dispersion': '{self.level['dispersion']}'"
        if 'auxiliary_basis' in self.level:
            level_str += f", 'auxiliary_basis': '{self.level['auxiliary_basis']}'"
        if 'solvation' in self.level:
            solvation_str = "{"
            solvation_str += f"'method': '{self.level['solvation']['method']}', " \
                             f"'solvent': '{self.level['solvation']['solvent']}'"
            solvation_str += "}"
            level_str += f", 'solvation': {solvation_str}"
        level_str += "}"
        freq_str = f"<Freq(level={level_str}, factor={self.factor}, source='{self.source}')>"
        return freq_str
