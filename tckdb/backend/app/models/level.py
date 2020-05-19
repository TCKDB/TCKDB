"""
TCKDB backend app models level module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class Level(Base):
    """
    A class for representing a TCKDB Level item

    Describing a level of theory

    Examples::

        Level(method='cbs-qb3')

        Level(method='B3LYP',
              basis='6-31G(d,p)',
              dispersion='gd3bj')

        Level(method='wB97xd',
              basis='def2TZVP',
              solvation_method='PCM',
              solvent='water',
              grid='UltraFine')

        Level(method='DLPNO-CCSD(T)-F12',
              basis='cc-pVTZ-F12',
              auxiliary_basis='aug-cc-pVTZ/C cc-pVTZ-F12-CABS',
              level_arguments='tight-PNO',
              solvation_description='APFD/6-311+G(2d,p) SMD water '
                                    'e_elect = e_DLPNO + sp_e_APFD_sol - sp_e_APFD_gas')

    Attributes:
        id (int)
            The primary key (not a user input)
        method (str)
            The method part of the level of theory
        basis (str)
            The basis set part of the level of theory
        auxiliary_basis (str)
            The auxiliary basis set part of the level of theory
        dispersion (str)
            The dispersion part of the level of theory, where relevant and if not included in the method
        grid (str)
            A description of the DFT grid, if applicable
        level_arguments (str)
            Additional arguments for defining a level, e.g., 'normal-PNO'
        solvation_method (str)
            The solvation method used, e.g., 'SMD'
        solvent (str)
            The considered solvent, e.g., 'water'
        solvation_description (str)
            An optional description of the solvation method used if not standard
        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (not a user input)
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    method = Column(String(500), nullable=False)
    basis = Column(String(500), nullable=True)
    auxiliary_basis = Column(String(500), nullable=True)
    dispersion = Column(String(500), nullable=True)
    grid = Column(String(500), nullable=True)
    level_arguments = Column(String(500), nullable=True)
    solvation_method = Column(String(500), nullable=True)
    solvent = Column(String(500), nullable=True)
    solvation_description = Column(String(1000), nullable=True)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"id={self.id}, "
        str_ += f"method='{self.method}'"
        if self.basis is not None:
            str_ += f", basis='{self.basis}'"
        if self.auxiliary_basis is not None:
            str_ += f", auxiliary_basis='{self.auxiliary_basis}'"
        if self.dispersion is not None:
            str_ += f", dispersion='{self.dispersion}'"
        if self.grid is not None:
            str_ += f", grid='{self.grid}'"
        if self.level_arguments is not None:
            str_ += f", level_arguments='{self.level_arguments}'"
        if self.solvation_method is not None:
            str_ += f", solvation_method={self.solvation_method}"
            str_ += f", solvent={self.solvent}"
        if self.solvation_description is not None:
            str_ += f", solvation_description={self.solvation_description}"
        str_ += f")>"
        return str_

    def __str__(self) -> str:
        """
        A user-friendly string representation of the object.
        """
        str_ = f"{self.method}"
        if self.basis is not None:
            str_ += f"/{self.basis}"
        if self.auxiliary_basis is not None:
            str_ += f"/{self.auxiliary_basis}"
        if self.dispersion is not None:
            str_ += f" {self.dispersion}"
        if self.grid is not None:
            str_ += f" {self.grid}"
        if self.level_arguments is not None:
            str_ += f" {self.level_arguments}"
        if self.solvation_method is not None:
            str_ += f" solvation: {self.solvation_method} in {self.solvent}"
        if self.solvation_description is not None:
            str_ += f" {self.solvation_description}"
        return str_
