"""
TCKDB backend app models species module
"""

from typing import List, Tuple, Union

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class Species(Base):
    """
    A class for representing a TCKDB Species item

    Attributes:
        id (int): The primary key.
        label (str): A free user label for the species.
        provenance (dict): Author and bot information. Keys (values) are:
                           - 'authors' (list): Entries are Author IDs,
                           - 'bot' (int): A Bot ID,
                           - 'timestamp' (str): The timestamp of uploading the data to TCKDB (automatically assigned).
        review (dict): Information related to the review process. Keys (values) are:
                       - 'reviewer' (Union[int, None]): An Author ID,
                       - 'reviewed' (bool): Whether this entry was reviewed,
                       - 'approved' (bool): If this entry was reviewed, whether it was approved.
        literature (int): A Literature id.
        retracted (str): A reason for retracting this object (``None`` if not retracted).
        extras (dict): Any additional information in the form of a Python dictionary.
        identifiers (dict): Chemical identifiers. Keys (values) are:
                            - 'smiles' (str): The SMILES descriptor with chirality information,
                            - 'inchi' (str): The InChI descriptor with the H layer and chirality,
                            - 'inchi key' (str): The InChI key descriptor.
        charge (int): The net molecular charge.
        multiplicity (int): The spin multiplicity.
        coordinates (dict): Cartesian coordinates in standard orientation. Keys (values) are:
                            - 'symbols' (Tuple[str]): The chemical element symbols,
                            - 'isotopes' (Tuple[int]): The respective isotopes,
                            - 'coords' (Tuple[Tuple[float]]): The respective coordinates.
        graphs (list): A list of 2D graphs in an RMG adjacency list format.
                       Each graph represents a localized Lewis structure, while collectively the graphs represent all
                       significant (representative) resonance structures of the species.
        fragments (list): Fragments represented by this species, e.g., VdW wells. ``None`` if there's only one fragment.
                          Entries are lists of 1-indexed atom indices of all atoms in a fragment.
        fragment_orientation (list): Relative orientation of fragments starting from the heaviest one.
                                     Both fragments must be in standard Cartesian orientation.
                                     Entries are dicts with keys (values):
                                     - 'cm' (List[float, float, float]),
                                     - 'x' (float),
                                     - 'y' (float),
                                     - 'z' (float).
                                     Where 'cm' is a vector pointing from the center of mass of the previous fragment
                                     to the center of mass of the present fragment, 'x', 'y', and 'z', are the angle
                                     formed between the X, Y, and Z axes of the previous fragment and the X, Y, Z axes
                                     of the present fragment, respectively.
        external_symmetry (int): The species external symmetry (excluding internal rotations).
        chirality (dict): The species chiral centers.
                          Keys (values) are: 'centers' (list[tuple[int]]), 'types' (list[str]).
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
        is_ts (bool): Whether this species represents a transition state.
        irc_trajectories (list): The two IRC trajectories. Each trajectory is a list of coordinates dictionaries.
        electronic_energy (Tuple[float, str]): The species single point electronic energy (without zero-point energy
                                               correction). The first entry is the value, the second is the units.
        E0 (Tuple[float, str]): The zero kelvin enthalpy (electronic energy + zero-point energy correction).
                                The first entry is the value, the second is the units.
        electronic_state (str): The species electronic state. # Todo - consult
        active_space (dict): The active space used for a multireference calculation. Keys (values) are:
                             - 'electrons' (int): The number of electrons in the active space,
                             - 'orbitals' (int): The number of orbitals in the active space.
        hessian (list): The computed Hessian matrix. Units: # todo
        frequencies (List[float]): Unscaled, unprojected frequencies (computed from the Hessian, not a user input).
                                   Complex roots are represented by a negative number. Units: cm^-1.
        scaled_projected_frequencies (List[float]): Scaled, projected frequencies (user input).
                                                    Complex roots are represented by a negative number. Units: cm^-1.
        normal_displacement_modes (List[List[float]]): The normal displacement modes (computed from the Hessian,
                                                       not a user input). Units: # todo ?
        rigid_rotor (str): The rigid rotor treatment type.
                           Allowed values: 'atom', 'linear', 'spherical top', 'symmetric top', or 'asymmetric top'.
        treatment (str): The statistical mechanics treatment of the species. Allowed values: 'RRHO', or 'RRAO'.  # todo - consult
        rotational_constants (List[Tuple[float, str]]): Rotational constants (computed from the geometry, not a user
                                                        input). The first entry of each tuple is the value,
                                                        the second is the units.
        torsions (List[dict]): The torsional modes. Entries are dictionaries with keys (values):
                               - 'computation type' (str): The torsional mode computation type. Allowed values:
                                                           'single point', 'constrained optimization',
                                                           or 'continuous constrained optimization',
                               - 'dimensions' (int): The torsional dimensions treated in this mode,
                               - 'constraints' (dict): Any constraints (bond distances, angles, and dihedral angles) used
                                                       in the optimization other than the primary torsion modes.
                                                       Keys (values) are:
                                                       - list (Tuple(float, str)): A key is a list of 1-indexed atom
                                                                                   indices representing a geometry
                                                                                   parameter (bond, angle, or dihedral
                                                                                   angle). The parameter type is
                                                                                   identified by the key list length.
                                                                                   Values are corresponding parameters
                                                                                   and units.
                               - 'symmetry' (int): Internal rotation symmetry.
                               - 'treatment' (str): The torsion treatment method. Allowed values are: 'hindered rotor',
                                                    'free rotor', 'rigid top', or 'hindered rotor density of states'.
                               - 'torsion' (List[list]): The (1-indexed) atom indices describing the mode. Entries are
                                                         4-length lists of atom indices, the number of entries must be
                                                         equal to the torsion dimension.
                               - 'top' (list): The (1-indexed) atom indices of all atoms on one side of the rotor,
                                               including (only) one pivotal atom.  # todo - ND?
                               - 'energies' (list): The scan energies, an ND array.
                               - 'energy_units' (str): The energy units.
                               - 'dihedrals' (list): The scan dihedral angles in degrees, an ND array.
                               - 'resolution' (List[float]): The dihedral angle increment resolutions, and ND array.
                               - 'trajectory' (List[dict]): Entries are Cartesian coordinates of respective points on
                                                            the scan.
                               - 'invalidated' (bool): Whether this mode was invalidated (``False`` by default).
                                                       Useful for TSs where a torsion breaks the connectivity near the
                                                       reactive sites.
        conformers (List(dict)): The species conformers used for accounting for all accessible wells. ``None`` if
                                 'torsions' are used, and vice versa. Entries are dictionaries with the same keys as
                                 the 'coordinates' attribute.
        H298 (Tuple[float, str]): The standard (298.15 K, 1 bar) enthalpy change of formation.
                                  The first entry is the value, the second is the units.
        S298 (Tuple[float, str]): The standard (298.15 K, 1 bar) entropy change of formation.
                                  The first entry is the value, the second is the units.
        Cp (dict): The discrete constant pressure heat capacity. Keys (values) are:
                   - 'Cp_0' (float): The zero Kelvin Cp.
                   - 'Cp_inf' (float): The high temperature Cp.
                   - 'values' (List[float]): The constant pressure heat capacity values.
                   - 'units' (str): The Cp units.
                   - 'temperatures' (List[float]): The temperatures in K corresponding to the discrete values.
        heat_capacity_model (dict): The Heat capacity extrapolation model and coefficients. Keys (values) are:
                                    - 'model' (str): The heat capacity model. For example: 'NASA', 'Wilhoit'.
                                    - 'T min' (float): The minimum temperature range in K.
                                    - 'T max' (float): The maximum temperature range in K.
                                    - 'P min' (float): The minimum temperature range in K (irrelevant for NASA/Wilhoit).
                                    - 'P max' (float): The maximum temperature range in K (irrelevant for NASA/Wilhoit).
                                    - 'coefficients' (dict): The heat capacity coefficients.
                                                             For a 'NASA' model the keys (values) are:
                                                             - 'coefficients_low' (List[float]): Low T range coeffs.
                                                             - 'coefficients_high' (List[float]): High T range coeffs.
                                                             - 'T min' (float): Minimum range temperature in K.
                                                             - 'T int' (float): Intermediate range temperature in K.
                                                             - 'T max' (float): Maximum range temperature in K.
                                                             For a 'Wilhoit' model the keys (values) are:
                                                             - 'a0' (float): The Wilhoit a0 coefficient.
                                                             - 'a1' (float): The Wilhoit a1 coefficient.
                                                             - 'a2' (float): The Wilhoit a2 coefficient.
                                                             - 'a3' (float): The Wilhoit a3 coefficient.
                                                             - 'B' (float): The Wilhoit B coefficient.
                                                             - 'H0' (float): The Wilhoit H0 coefficient.
                                                             - 'S0' (float): The Wilhoit S0 coefficient.
                                                             - 'T min' (float): Minimum range temperature in K.
                                                             - 'T max' (float): Maximum range temperature in K.
        energy_corrections (dict): Energy corrections used to compute the thermodynamic properties. Keys (values) are:
                                   - 'AEC' (int): An AEC (atom energy correction) ID.
                                   - 'BAC' (int): A BAC (bond additivity energy correction) ID.
                                   - 'SOC' (int): A SOC (spin-orbit interaction energy correction) ID.
                                   - 'isodesmic reactions' (list): The isodesmic reactions used for the energy
                                                                   correction. If specified, 'AEC', 'BAC', and 'SOC'
                                                                   must be ``None``, and vice versa.
                                                                   Entries are dictionaries representing reactions.
                                                                   Each reaction dict has 'reactants' and 'products'
                                                                   keys, values are species dicts. Each species dict
                                                                   has 'identifier' (SMILES/InChI) and 'H298' in kJ/mol
                                                                   at the ``isodesmic_high_level``.
                                   - 'isodesmic_high_level' (dict): The high level of theory used for all other species.
                                                                    See the ``.levels`` attribute for the keys and
                                                                    values allowed when specifying a level of theory.
        statmech_software (str): The statistical mechanics software and version used for the statmech and thermodynamic
                                 properties computation.
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
        unconverged_jobs (list): Any unconverged jobs that were troubleshooted while calculating this Species.
                                 Entries are dictionaries, keys (values) are:
                                 - 'job_type' (str): 'opt', 'freq', 'scan', 'irc', or 'sp'.
                                 - 'issue' (str): The identified issue.
                                 - 'troubleshooting' (str): A description of the troubleshooting method(s) that solved
                                                            the issue.
                                 - 'comment' (str): A comment.
                                 - 'file' (str): The path to the relevant unconverged output file.
        reviewer_flags (dict): Backend flags to assist the review process.


        Todo:
            - replace MutableDict.as_mutable(JSONEncodedDict) with QCEl msgpacket_dumps, e.g. as done in QCFrac storage_sockets models sql_models.py
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    label = Column(String(255), nullable=True)
    provenance = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    review = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    literature = Column(Integer, ForeignKey('literature.id'), nullable=True)
    retracted = Column(String(255), nullable=True)
    extras = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    identifiers = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    coordinates = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    graphs = Column(ARRAY(item_type=str, as_tuple=False, dimensions=1, zero_indexes=True), nullable=True)
    fragments = Column(ARRAY(item_type=int, as_tuple=False, dimensions=2, zero_indexes=True), nullable=True)
    fragment_orientation = Column(ARRAY(item_type=dict, as_tuple=False, dimensions=1, zero_indexes=True), nullable=True)
    external_symmetry = Column(Integer, nullable=True)
    chirality = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    conformation_info = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    is_ts = Column(Boolean, nullable=False)
    irc_trajectories = Column(ARRAY(item_type=MutableDict.as_mutable(JSONEncodedDict),
                                    as_tuple=False, dimensions=2, zero_indexes=True), nullable=True)
    electronic_energy = Column(ARRAY(item_type=Union[float, str], as_tuple=True, dimensions=1, zero_indexes=True),
                               nullable=False)
    E0 = Column(ARRAY(item_type=Union[float, str], as_tuple=True, dimensions=1, zero_indexes=True), nullable=False)
    electronic_state = Column(String(255), nullable=True)
    active_space = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    hessian = Column(ARRAY(item_type=float, as_tuple=False, zero_indexes=True), nullable=False)
    frequencies = Column(ARRAY(item_type=float, as_tuple=False, zero_indexes=True), nullable=False)
    scaled_projected_frequencies = Column(ARRAY(item_type=float, as_tuple=False, zero_indexes=True), nullable=False)
    normal_displacement_modes = Column(ARRAY(item_type=List[float], as_tuple=False, zero_indexes=True), nullable=False)
    rigid_rotor = Column(String(25), nullable=False)
    treatment = Column(String(10), nullable=False)
    rotational_constants = Column(ARRAY(item_type=Tuple[float, str], as_tuple=True, dimensions=1, zero_indexes=True),
                                  nullable=False)
    torsions = Column(ARRAY(item_type=MutableDict.as_mutable(JSONEncodedDict),
                            as_tuple=False, zero_indexes=True), nullable=True)
    conformers = Column(ARRAY(item_type=MutableDict.as_mutable(JSONEncodedDict),
                              as_tuple=False, zero_indexes=True), nullable=True)
    H298 = Column(ARRAY(item_type=Union[float, str], as_tuple=True, dimensions=1, zero_indexes=True), nullable=False)
    S298 = Column(ARRAY(item_type=Union[float, str], as_tuple=True, dimensions=1, zero_indexes=True), nullable=False)
    Cp = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    heat_capacity_model = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    energy_corrections = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    statmech_software = Column(String(150), nullable=True)
    levels = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    ess = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    files = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)
    unconverged_jobs = Column(ARRAY(item_type=MutableDict.as_mutable(JSONEncodedDict),
                                    as_tuple=False, zero_indexes=True), nullable=True)
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
