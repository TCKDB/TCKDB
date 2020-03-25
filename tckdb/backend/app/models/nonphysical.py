"""
TCKDB backend app models nonphysical module
"""

from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class NonPhysicalSpecies(Base):
    """
    A class for representing a TCKDB NonPhysicalSpecies item

    Attributes:
        id (int): The primary key.
        label (str): A free user label for the species.
        provenance (dict): Author and bot information. Keys (values) are:
                           - 'authors' (list): Entries are Author IDs,
                           - 'bot' (int): A Bot ID,
                           - 'timestamp' (str): The timestamp of uploading the data to TCKDB (automatically assigned).
        review (dict): Information related to the review process. Keys (values) are:
                       - 'reviewer' (int): An Author ID,
                       - 'reviewed' (bool): Whether this entry was reviewed,
                       - 'approved' (bool): If this entry was reviewed, whether it was approved.
        retracted (str): A reason for retracting this object (``None`` if not retracted).
        extras (dict): Any additional information in the form of a Python dictionary.
        identifiers (dict): Chemical identifiers. Keys (values) are:
                            - 'smiles' (str): The SMILES descriptor with chirality information,
                            - 'inchi' (str): The InChI descriptor with the H layer and chirality,
                            - 'inchi key' (str): The InChI key descriptor.
        charge (int): The net molecular charge.
        multiplicity (int): The spin multiplicity.
        coordinates (dict): Cartesian coordinates in standard orientation. Keys (values) are:
                            - 'symbols' (list[str]): The chemical element symbols,
                            - 'isotopes' (list[int]): The respective isotopes,
                            - 'coords' (list[list[float]]): The respective coordinates.
        graphs (list): A list of 2D graphs in an RMG adjacency list format.
                       Each graph represents a localized Lewis structure, while collectively the graphs represent all
                       significant (representative) resonance structures of the species.
        fragments (list): Fragments represented by this species, e.g., VdW wells. ``None`` if there's only one fragment.
                          Entries are lists of 1-indexed atom indices of all atoms in a fragment.
        fragment_orientation (list): Relative orientation of fragments starting from the heaviest one.
                                     Both fragments must be in standard Cartesian orientation.
                                     Entries are dicts with keys (values):
                                     - 'cm' (list[float]),
                                     - 'x' (float),
                                     - 'y' (float),
                                     - 'z' (float).
        conformation_info (dict): Information relating to the conformer.
                                  Keys (values) are:
                                  - 'method' (str): The conformer search method,
                                  - 'is_well' (bool): Whether this conformer represents a local well at the opt level,
                                  - 'is_global_min' (bool): Whether this conformer intends to represents the global minimum,
                                  - 'shift' (dict): The bond distances, angles, and dihedral angles which were modified
                                                    relative to the global minimum conformer at the opt level of theory.
                                                    Keys (values) are:
                                                    - list (Tuple(float, str)): A key is a list of 1-indexed atom
                                                                                indices representing a geometry
                                                                                parameter (bond, angle, or dihedral
                                                                                angle). The parameter type is
                                                                                identified by the key list length.
                                                                                Values are corresponding parameters and
                                                                                units.
        levels (dict): The levels of theory. Keys (values) are:
                       - 'opt' (dict): The optimization level of theory.
                       - 'freq' (dict): The frequencies calculation level of theory.
                       - 'scan' (dict): The torsion scan calculation level of theory.
                       - 'irc' (dict): The IRC calculation level of theory.
                       - 'sp' (dict): The single point energy calculation level of theory.
                       Each value is a dictionary with the following keys (values):
                       -  'method' (str): The method used for the computation (e.g., 'B3LYP').
                       -  'basis' (str): The basis set used for the computation (e.g., '6-311+G(d,p)').
                       -  'dispersion' (str): The DFT dispersion type used for the computation, if relevant
                                              (e.g., 'gd3bj').
                       -  'aux_basis' (str): The auxiliary basis set(s) used for the computation, if relevant
                                             (e.g., 'aug-cc-pvtz/c cc-pvtz-f12-cabs').
                       -  'solvation' (dict): The solvation method and solvent used, if relevant.
                                              Keys (values) are:
                                              - 'method': The solvation method used (e.g., 'SMD', 'PCM')
                                              - 'solvent': The solvent properties used (e.g., 'water')
        ess (dict): The electronic structure software (ESS) used for the different computations. Keys (values) are:
                    - 'opt' (dict): The electronic structure software used for the optimization.
                    - 'freq' (dict): The electronic structure software used for the frequencies calculation.
                    - 'scan' (dict): The electronic structure software used for the torsion scan calculation.
                    - 'irc' (dict): The electronic structure software used for the IRC calculation.
                    - 'sp' (dict): The electronic structure software used for the single point energy calculation.
                    Each value is a dictionary with the following keys (values):
                    - 'software' (str): An electronic structure software name.
                    - 'version' (str): An electronic structure software version.
                    - 'revision' (str): An electronic structure software revision.
        files (dict): The output files of the electronic structure computations. Keys (values) are:
                      - 'opt' (str): The path to the optimization output file.
                      - 'freq' (str): The path to the frequencies calculation output file.
                      - 'scan' (dict): Paths to the torsion scan calculation output files. Keys (values) are:
                                       - Tuple[Tuple[int]] (str): Keys are tuples of tuples. The number of inner-level
                                                                  tuples corresponds to the torsion dimension. Entries
                                                                  of the inner-level tuple are 1-indexed torsion atom
                                                                  indices. Values are paths to the scan calculation
                                                                  output file.
                      - 'irc' (list): A length two list. Entries are paths to the IRC calculation output files.
                      - 'sp' (str): The path to the single point energy output file.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    label = Column(String(255), nullable=True)
    provenance = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    review = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    retracted = Column(String(255), nullable=True)
    extras = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    identifiers = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    coordinates = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    graphs = Column(ARRAY(item_type=str, as_tuple=False, dimensions=1, zero_indexes=True), nullable=True)
    fragments = Column(ARRAY(item_type=int, as_tuple=False, dimensions=2, zero_indexes=True), nullable=True)
    fragment_orientation = Column(ARRAY(item_type=dict, as_tuple=False, dimensions=1, zero_indexes=True), nullable=True)
    conformation_info = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    levels = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    ess = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    files = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    reviewer_flags = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)


    def __str__(self) -> str:
        str_ = f"<{self.__class__.__name__}("
        str_ += f"id={self.id}, "
        if self.label is not None and self.label:
            str_ += f"label={self.label}, "
        if self.identifiers is not None and self.identifiers:

            str_ += f"identifiers={self.identifiers}, "
        str_ += ")>"
        return str_
