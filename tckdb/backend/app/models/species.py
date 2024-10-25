"""
TCKDB backend app models species module
"""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from tckdb.backend.app.db.base_class import AuditMixin, Base
from tckdb.backend.app.models.associations import species_authors, species_reviewers
from tckdb.backend.app.models.common import MsgpackExt


class Species(Base, AuditMixin):
    """
    A class for representing a TCKDB Species item.

    Note:
        All atom indices in these arguments are 1-indexed unless explicitly stated otherwise.

    Note:
        Arguments use SI units as much as possible. Parameters must be provided in the requested units,
        necessary unit conversions must be done by the user prior to uploading data.
        The requested units are noted for each parameter where relevant.

    Example::

        Species(label='formaldehyde',
                statmech_software='Arkane (RMG) v3.0.0',
                smiles='C=O',
                charge=0,
                multiplicity=1,
                electronic_state='X',
                coordinates={'symbols': ('C', 'O', 'H', 'H'),
                             'isotopes': (12, 16, 1, 1),
                             'coords': ((-0.0122240982, 0.0001804054, -0.00162116),
                                        (1.2016481968, -0.0177341701, 0.1593624097),
                                        (-0.5971643978, 0.9327281670, 0.0424401022),
                                        (-0.5922597008, -0.9151744023, -0.2001813507))},
                external_symmetry=2,
                point_group='C2v',
                conformation_method='ARC',
                is_well=True,
                is_global_min=True,
                is_ts=False,
                electronic_energy=-325.6458956547,
                E0=123.54842,
                frequencies=[132.2, 548.5, 1032.5, 2015.22, 2018.12, 3005.22],
                scaled_projected_frequencies=[130.217, 540.2725, 1017.013,
                                              1984.992, 1987.848, 2960.142],
                normal_displacement_modes=[[0.125, 0.89, 0.35],
                                           [-0.25, 0.25, -0.89],
                                           [0.56, 0.98, -0.65],
                                           [0.022, -0.005, 0.5],
                                           [-0.98, -0.002, 0.65],
                                           [0.05, 0.0025, -0.722]],
                freq_id=11,
                rigid_rotor='asymmetric top',
                statmech_treatment='RRHO',
                rotational_constants=[1.25, 8.56, 9.55],
                H298=-109.4534,
                S298=218.237,
                Cp_values=[35.313, 39.037, 43.4299, 47.7813, 55.438, 61.463, 70.71],
                Cp_T_list=[300, 400, 500, 600, 800, 1000, 1500],
                heat_capacity_model={'model': 'NASA',
                                     'T min': 100,
                                     'T max': 5000,
                                     'coefficients': {'low': [4.13878818E+00, -4.69514383E-03,
                                                              2.25730249E-05, -2.09849937E-08,
                                                              6.36123283E-12, -1.43493283E+04,
                                                              3.23827482E+00],
                                                      'high': [2.36095410E+00, 7.66804276E-03,
                                                               -3.19770442E-06, 6.04724833E-10,
                                                               -4.27517878E-14, -1.42794809E+04,
                                                               1.04457152E+01],
                                                      'T int': 1041.96}},
                encorr_id=33,
                opt_path='path_opt',
                freq_path='path_freq',
                sp_path='path_sp')

    Attributes:
        id (int)
            The primary key (not a user input).

        label (Optional[str])
            A free user label for the species (maximum 255 characters).

        # provenance
        statmech_software (Optional[str])
            The statistical mechanics software and version used for the statistical mechanics and thermodynamic
            property computations. Should include the version. Example::

                'Arkane (RMG) v3.0.0'
        timestamp (float)
            The UTC timestamp of uploading the data to TCKDB (not a user input).
        retracted (str)
            A reason for retracting this object. Default: ``None`` (not a user input).

        # review
        reviewed (bool)
            Whether this entry was reviewed (not a user input).
        approved (bool)
            If reviewed, whether it was approved (not a user input).
        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (automatically determined, but users may add notes to flag
            items for the reviewer).

            Example::

                {'general': 'This species has a shallow (1.5 kJ/mol) well along the 4-8-7-10 torsional mode.'}

        # chemical identifiers
        smiles (Optional[str])
            The canonical SMILES descriptor with chirality information.

            Note:
                Either ``smiles``, ``inchi``, or ``graph`` must be specified.
        inchi (Optional[str])
            The InChI descriptor with an explicit H layer and chirality information.

            Note:
                Either ``smiles``, ``inchi``, or ``graph`` must be specified.
        inchi_key (Optional[str])
            The InChI key descriptor. Automatically assigned if not given by the user.
        charge (int)
            The net molecular charge.
        multiplicity (int)
            The spin multiplicity.
        electronic_state (Optional[str])
            The species electronic state at CIS level. Default: ``'X'`` (denoting ground state).

        # geometry and connectivity
        coordinates (Dict[str, Union[Tuple[Tuple[float, float, float], ...], Tuple[int, ...], Tuple[str, ...]]])
            Cartesian coordinates in standard orientation. Keys (values) are:

            * 'symbols' (Tuple[str])
                Chemical element symbols
            * 'isotopes' (Tuple[int])
                The respective isotopes
            * 'coords' (Tuple[Tuple[float]])
                The respective coordinates

            Example for methane::

                {'symbols': ('C', 'H', 'H', 'H', 'H'),
                 'isotopes': (12, 1, 1, 1, 1),
                 'coords': ((0.0, 0.0, 0.0),
                            (0.6300326, 0.6300326, 0.6300326),
                            (-0.6300326, -0.6300326, 0.6300326),
                            (-0.6300326, 0.6300326, -0.6300326),
                            (0.6300326, -0.6300326, -0.6300326))}
        graph (Optional[str])
            A 2D connectivity graph in an RMG adjacency list format. Note that this graph represents a single
            Lewis structure (resonance structure). These graphs can be generated using RMG's API or online at
            https://rmg.mit.edu/molecule_search.

            Note:
                Either ``smiles``, ``inchi``, or ``graph`` must be specified.

            Example for methane::

                multiplicity 1
                1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
                2 H u0 p0 c0 {1,S}
                3 H u0 p0 c0 {1,S}
                4 H u0 p0 c0 {1,S}
                5 H u0 p0 c0 {1,S}

        fragments (Optional[List[List[int]]])
            Fragments represented by this species, e.g., VdW wells. Entries are atom index lists of all atoms in a
            fragment. Default: ``None`` (denoting there's only one fragment).
        fragment_orientation (Optional[List[Dict[str, Union[float, List[float]]]]])
            Relative orientation of fragments, starting from the heaviest one. All fragments must be in standard
            Cartesian orientation prior to determining the following parameters. Entries are dictionaries with the
            following keys (values):

                * 'cm' (List[float])
                    A vector pointing from a fragment's center to the center of mass of the next fragment.

                    Units:
                        Angstrom
                * 'x' (float)
                    The X axis rotational angle in degrees.
                * 'y' (float)
                    The Y axis rotational angle in degrees.
                * 'z' (float)
                    The Z axis rotational angle in degrees.
        external_symmetry (int)
            The species external symmetry (excluding internal torsions)
        point_group (str)
            The symmetry point group (use "inf" for infinity, don't add spaces or underscores). Examples: ``'C1'``,
            ``'Cinfv'``, ``'C2h'``, ``'D4'``, ``'Dinfh'``, ``'S4'``, ``'T'``, ``'Th'``, ``'Td'``, ``'O'``, ``'Oh'``,
            ``'I'``, ``'Ih'``.
        chirality (Optional[Dict[Tuple[int], str]])
            The species' chiral centers, following the Cahn–Ingold–Prelog (CIP) notation (``'R'`` or ``'S'`` for atom
            centers, ``'E'`` or ``'Z'`` for double bond centers). Keys are tuples of atom indices of a chiral center,
            values are string representations of the respective chiral center.

            Note: Non-radical valance 3 nitrogen atoms are also considered chiral if connected to three different groups
            (considering the lone pair to always be a unique fourth group). In such cases, use a ``'NR'`` or ``'NS'``
            notation.

            Example:

                A double bond with chirality ``'E'`` between atoms 1-2, an ``'S'`` chiral center on atom 3,
                and an ``'S'`` chiral center on atom 6 which is a nitrogen are represented as::

                    {(1, 2): 'E', (3,): 'S', (6,): 'NS'})
        conformation_method (Optional[str])
            The method used to determine the lowest energy conformer. Required if the species has 4 or more atoms.
        is_well (bool)
            Whether this conformer represents a local well (at the opt level used).
        is_global_min (bool)
            If this conformer is a well, whether it is meant to represents the **global** minimum energy well.
        global_min_geometry (Optional[Dict[str, tuple]])
            If this species does not represent the global minimum well, this argument must contain the coordinates of
            the global minimum energy conformer at the same opt level.

        # TS
        is_ts (bool)
            Whether this species represents a transition state. Default: ``False``.
        irc_trajectories (Optional[List[List[Dict[str, tuple]]]])
            The two IRC trajectories. Each trajectory is a list of coordinates dictionaries.
            Required if the species is a transition state. Cannot be specified for non-TS species.

        # energy
        electronic_energy (float)
            The species single point electronic energy as calculated by the electronic structure software
            (without zero-point energy correction).

            Units:
                Hartree.
        E0 (float)
            The zero kelvin enthalpy (electronic energy + zero-point energy correction) after applying
            energy corrections.

            Units:
                kJ/mol
        active_space (Optional[Dict[str, int]])
            The active space used for a multireference calculation. Keys (values) are:

                * 'electrons' (int)
                    The number of electrons in the active space,
                * 'orbitals' (int)
                    The number of orbitals in the active space.

        # Hessian
        hessian (Optional[List[List[float]]])
            The computed Hessian matrix (lower triangle). Required for polyatomic species (with 2 or more atoms).

            Units:
                Hartree / Bohr :sup:`2`


        # vibrational modes
        frequencies (Optional[List[float]])
            Unscaled, unprojected frequencies. Complex roots are represented by a negative number.
            Required for polyatomic species (with 2 or more atoms).

            Units:
                cm :sup:`-1`
        scaled_projected_frequencies (Optional[List[float]])
            Scaled, projected frequencies (user input). If no rotors are used then the projection is unnecessary.
            Complex roots are represented by a negative number. Required for polyatomic species (with 2 or more atoms).

            Units:
                cm :sup:`-1`
        normal_displacement_modes (Optional[List[List[List[float]]]])
            The normal displacement modes. Required for polyatomic species (with 2 or more atoms).
            Entries of the first level list are normal displacement modes per frequency.
            Entries of the second level list are normal displacement modes per frequency per atom.
            Entries of the third level list are normal displacement modes per frequency per atom per axis (X, Y, Z).

            Units:
                Angstrom
        freq_id (Optional[int])
            The frequency scaling factor key for the :ref:`Freq table <freq_model>`.
            Required for polyatomic species (with 2 or more atoms).

            Note:
                This argument is facilitated by querying the :ref:`Freq table <freq_model>`.

        # rotational modes
        rigid_rotor (str)
            The rigid rotor treatment type.
            Allowed values: ``'atom'``, ``'linear'``, ``'spherical top'``, ``'symmetric top'``, or ``'asymmetric top'``.
        statmech_treatment (Optional[str])
            The statistical mechanics treatment of the species. Required for polyatomic species (with 3 or more atoms).

            Examples:

            * 'RRHO' (rigid rotor harmonic oscillator)
            * 'RRHO-1D' (rigid rotor harmonic oscillator with 1D torsions)
            * 'RRHO-1D-ND' (rigid rotor harmonic oscillator with mixed 1D torsions and ND torsions)
            * 'RRHO-ND' (rigid rotor harmonic oscillator with ND torsions)
            * 'RRHO-AD' (rigid rotor harmonic oscillator with ND torsions, D is the overall number of torsions, D > 1)
            * 'RRAO' (rigid rotor anharmonic oscillator)
        rotational_constants (Optional[List[float]])
            Rotational constants. ``None`` for monoatomic species.
            One entry for linear molecules, three entries for non-linear polyatomic molecules.
            Computed from the geometry if not provided by the user.

            Units:
                amu * Angstrom :sup:`2`

        # torsional modes
        torsions (Optional[List[Dict[str, Union[Dict[Tuple[int], float], int, List[int], List[List[int]], str]]]])
            The torsional modes. Entries are dictionaries with keys (values):

            * 'computation_type' (str, optional)
                The torsional mode computation type. Allowed values: ``'single point'``, ``'constrained optimization'``,
                or ``'continuous constrained optimization'``. Default: ``'continuous constrained optimization'``.
            * 'dimension' (int, optional)
                The torsional dimension treated by this mode. Default: 1.
            * 'constraints' (Dict[Tuple[int, ...], float], optional)
                Any constraints (bond distances, angles, and dihedral angles)
                used in the optimization other than the primary torsion modes. Keys (values) are:
                    * Tuple[int, ...] (float)
                        A key is a tuple of atom indices representing an internal geometrical
                        parameter (bond, angle, or dihedral angle). This parameter type is identified by the length
                        of the tuple key (two, three, and four atom indices represent bond, angle and dihedral
                        angle, respectively. Values are corresponding parameters in either degrees or Angstrom.
            * 'symmetry' (int)
                The internal rotation symmetry
            * 'treatment' (str)
                The torsion treatment method. Allowed values are ``'hindered rotor'``, ``'free rotor'``
                ``'rigid top'``, or ``'hindered rotor density of states'``.
            * 'torsions' (Union[List[List[int]], Iterable[int]])
                The atom indices describing the torsional mode. Entries are 4-length lists/tuples of atom
                indices, the number of entries is the torsion dimension.
            * 'top' (List[int])
                Atom indices of all atoms on one side of the internal rotor, starting from its center. For a 1D rotor
                ("R1-A-B-C-D-R2"), this means listing all atoms starting from one of the pivotal atoms ("C-D-R2").
                For an adjacent 2D rotor ("R1-A-B-C-D-E-R2"), this means starting from the middle atom which is a
                pivotal atom in both modes ("C-D-E-R2").
            * 'energies' (list)
                The scan energies. This is an ND array, axes order corresponds to the order
                in which the torsions are defined under ``torsions``.
                For a 1D torsion, this would be of type List[float].
                For a 2D torsion, this would be of type List[List[float]].
                For a 3D torsion, this would be of type List[List[List[float]]], and so on.

                Units:
                    kJ / mol
            * 'resolution' (Union[float, List[float]])
                The dihedral angle increment resolutions.

                Units:
                    degrees
            * 'trajectory' (list)
                Entries are Cartesian coordinates of respective points in the scan. This is an ND array.
                Axes order corresponds to the order in which the torsions are defined under ``torsions``.
                The structure of this argument is similar to the structure of the 'energies' argument.
            * 'invalidated' (str, optional)
                An invalidation reason, if this mode was invalidated. Useful for TSs where a torsion breaks the
                connectivity near the reactive site, and for high-barrier torsions not considered in the statmech
                treatment. Default: ``None``.

        # conformers
        conformers (Optional[List[Dict[str, Union[float, Tuple[Tuple[float]], Tuple[int], Tuple[str]]]]])
            The species conformers used for Boltzmann averaging of accessible wells. ``None`` if ``torsions`` are used,
            and vice versa. Entries are dictionaries containing the same keys as the ``coordinates`` attribute,
            with two additional keys: ``'energy'`` with the relative conformer energy in kJ/mol at the sp level,
            and ``'degeneracy'`` with the number of repetitions per conformer (defaults to 1).

        # thermochemical properties
        H298 (Optional[float])
            The standard (298.15 K, 1 bar) enthalpy change of formation. Required for non-TS species.

            Units:
                kJ/mol
        S298 (Optional[float])
            The standard (298.15 K, 1 bar) entropy change of formation. Required for non-TS species.

            Units:
                J / (mol * K)
        Cp_values (Optional[List[float]])
            The constant pressure heat capacity values. Required for non-TS species.

            Units:
                J / (mol * K).
        Cp_T_list (Optional[List[float]])
            The temperatures in K corresponding to the discrete Cp values. Required for non-TS species.
        heat_capacity_model (Optional[Dict[str, Union[float, Dict[str, Union[float, List[float]]], str]]])
            The heat capacity extrapolation model and coefficients.
            Required for non-TS species. Keys (values) are:

            * 'model' (str)
                The heat capacity model. For example: ``'NASA'``, ``'Wilhoit'``.
            * 'T min' (float)
                The minimum temperature range in K.
            * 'T max' (float)
                The maximum temperature range in K.
            * 'P min' (float)
                The minimum temperature range in K (irrelevant for NASA/Wilhoit).
            * 'P max' (float)
                The maximum temperature range in K (irrelevant for NASA/Wilhoit).
            * 'coefficients' (dict)
                The heat capacity coefficients. For a ``'NASA'`` model the keys (values) are:

                     * 'low' (List[float]): Low T range coefficients
                     * 'high' (List[float]): High T range coefficients
                     * 'T int' (float): Intermediate range temperature in K

                For a 'Wilhoit' model the keys (values) are:

                    * 'a0' (float): The Wilhoit a0 coefficient
                    * 'a1' (float): The Wilhoit a1 coefficient
                    * 'a2' (float): The Wilhoit a2 coefficient
                    * 'a3' (float): The Wilhoit a3 coefficient
                    * 'B' (float): The Wilhoit B coefficient
                    * 'H0' (float): The Wilhoit H0 coefficient
                    * 'S0' (float): The Wilhoit S0 coefficient

        # relationships - Many to One
        encorr_id (int)
            The energy correction key from the :ref:`EnCorr table <encorr_model>`.
        encorr (relationship)
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`EnCorr table <EnCorr_model>`,
            where the "reverse" side is a Many to One data model.
        literature_id (int)
            The literature reference from the :ref:`Literature table <literature_model>`.
        literature (relationship)
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Literature table <literature_model>`,
            where the "reverse" side is a Many to One data model.
        bot_id (int)
            The bot used to generate the object from the :ref:`Bot table <bot_model>`.
        bot (relationship)
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Bot table <bot_model>`,
            where the "reverse" side is a Many to One data model.

        opt_level (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Level table <level_model>`,
            where the "reverse" side is a Many to One data model.
        freq_level (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Level table <level_model>`,
            where the "reverse" side is a Many to One data model.
        scan_level (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Level table <level_model>`,
            where the "reverse" side is a Many to One data model.
        irc_level (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Level table <level_model>`,
            where the "reverse" side is a Many to One data model.
        sp_level (relationship)
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`Level table <level_model>`,
            where the "reverse" side is a Many to One data model. Required.

        opt_ess (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`ESS table <ess_model>`,
            where the "reverse" side is a Many to One data model.
        freq_ess (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`ESS table <ess_model>`,
            where the "reverse" side is a Many to One data model.
        scan_ess (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`ESS table <ess_model>`,
            where the "reverse" side is a Many to One data model.
        irc_ess (Optional[relationship])
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`ESS table <ess_model>`,
            where the "reverse" side is a Many to One data model.
        sp_ess (relationship)
            An attribute that establishes a bidirectional relationship in a One to Many data model
            with the :ref:`ESS table <ess_model>`,
            where the "reverse" side is a Many to One data model. Required.

        # relationships - Many to Many
        authors (relationship)
            An attribute that establishes a bidirectional relationship in a Many to Many data model
            with the :ref:`Person table <person_model>` representing authors of this object.
        reviewers (relationship)
            An attribute that establishes a bidirectional relationship in a Many to Many data model
            with the :ref:`Person table <person_model>` representing reviewers of this object.

        # paths
        opt_path (Optional[str])
            The path to the optimization output file. Required for polyatomic species (with 2 or more atoms).
        freq_path (Optional[str])
            The path to the frequencies calculation output file. Required for polyatomic species (with 2 or more atoms).
        scan_paths (Optional[Dict[Tuple[Tuple[int, int, int, int], ...], str]])
            Paths to the torsion scan calculation output files.
            Keys are tuples of tuples. The number of inner-level tuples corresponds to the torsion dimension. Entries of
            the inner-level tuple are torsion atom indices. Values are paths to the respective scan calculation log file.
        irc_paths (Optional[List[str]])
            Entries are paths to the IRC calculation output files. Required for transition states.
            Either a single path to a forward+reverse IRC, or two respective paths.
        sp_path (str)
            The path to the single point energy output file.

        # unconverged jobs
        unconverged_jobs (Optional[List[Dict[str, str]]])
            Any relevant unconverged jobs that were troubleshooted while calculating this Species.
            Entries are dictionaries, keys (values) are:

                * 'job type' (str): ``'opt'``, ``'freq'``, ``'scan'``, ``'irc'``, or ``'sp'``
                * 'issue' (str): The identified issue
                * 'troubleshooting' (str): Description of the troubleshooting method(s) that solved the issue
                * 'comment' (str): An optional comment explaining the troubleshooting approach
                * 'path' (str): The path to the relevant unconverged ESS log file

        # misc
        extras (Optional[Dict[str, Any]])
            Any additional information in the form of a dictionary.
    """

    id = Column(Integer, primary_key=True, index=True, nullable=False)

    label = Column(String(255), nullable=True)

    # provenance
    statmech_software = Column(String(150), nullable=True)
    timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    retracted = Column(String(255), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)

    # review
    reviewed = Column(Boolean, nullable=False, default=False)
    approved = Column(Boolean, nullable=True, default=None)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    # chemical identifiers
    smiles = Column(String(5000), nullable=False)
    inchi = Column(String(5000), nullable=False)
    inchi_key = Column(String(27), nullable=False)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    electronic_state = Column(String(150), nullable=False)

    # geometry and connectivity
    coordinates = Column(MsgpackExt, nullable=False)
    graph = Column(String(100000), nullable=True)
    fragments = Column(ARRAY(Integer), nullable=True)
    fragment_orientation = Column(MsgpackExt, nullable=True)
    external_symmetry = Column(Integer, nullable=False)
    point_group = Column(String(6), nullable=False)
    chirality = Column(MsgpackExt, nullable=True)
    conformation_method = Column(String(500), nullable=False)
    is_well = Column(Boolean, nullable=False)
    is_global_min = Column(Boolean, nullable=True)
    global_min_geometry = Column(MsgpackExt, nullable=True)

    # TS
    is_ts = Column(Boolean, nullable=False)
    irc_trajectories = Column(MsgpackExt, nullable=True)

    # energy
    electronic_energy = Column(Float, nullable=False)
    E0 = Column(Float, nullable=False)
    active_space = Column(MsgpackExt, nullable=True)

    # Hessian
    hessian = Column(MsgpackExt, nullable=True)

    # vibrational modes
    frequencies = Column(
        ARRAY(Float, as_tuple=False, dimensions=1, zero_indexes=True), nullable=True
    )
    scaled_projected_frequencies = Column(
        ARRAY(Float, as_tuple=False, dimensions=1, zero_indexes=True), nullable=False
    )
    normal_displacement_modes = Column(MsgpackExt, nullable=True)
    freq_scale_id = Column(
        Integer, ForeignKey("freqscale.id"), nullable=True, unique=False
    )

    # rotational modes
    rigid_rotor = Column(String(50), nullable=False)
    statmech_treatment = Column(String(50), nullable=True)
    rotational_constants = Column(MsgpackExt, nullable=True)

    # torsional modes
    torsions = Column(MsgpackExt, nullable=True)

    # conformers
    conformers = Column(MsgpackExt, nullable=True)

    # thermochemical properties
    H298 = Column(Float, nullable=False)
    S298 = Column(Float, nullable=False)
    Cp_values = Column(
        ARRAY(Float, as_tuple=False, dimensions=1, zero_indexes=True), nullable=False
    )
    Cp_T_list = Column(
        ARRAY(Float, as_tuple=False, dimensions=1, zero_indexes=True), nullable=False
    )
    heat_capacity_model = Column(MsgpackExt, nullable=True)  # Described as optional

    # relationships - Many (species) to One (other table)
    encorr_id = Column(Integer, ForeignKey("encorr.id"), nullable=True)
    encorr = relationship("EnCorr", back_populates="species")
    literature_id = Column(
        Integer, ForeignKey("literature.id", ondelete="SET NULL"), nullable=True
    )
    literature = relationship(
        "Literature", back_populates="species", foreign_keys=[literature_id]
    )
    bot_id = Column(
        Integer, ForeignKey("bot.id"), nullable=True, unique=False
    )  # Changed to nullable=False
    bot = relationship("Bot", back_populates="species", foreign_keys=[bot_id])

    opt_level_id = Column(Integer, ForeignKey("level.id"), nullable=True, unique=False)
    opt_level = relationship(
        "Level", backref="species_opt", foreign_keys=[opt_level_id]
    )
    freq_level_id = Column(Integer, ForeignKey("level.id"), nullable=True, unique=False)
    freq_level = relationship(
        "Level", backref="species_freq", foreign_keys=[freq_level_id]
    )
    scan_level_id = Column(Integer, ForeignKey("level.id"), nullable=True, unique=False)
    scan_level = relationship(
        "Level", backref="species_scan", foreign_keys=[scan_level_id]
    )
    irc_level_id = Column(Integer, ForeignKey("level.id"), nullable=True, unique=False)
    irc_level = relationship(
        "Level", backref="species_irc", foreign_keys=[irc_level_id]
    )
    sp_level_id = Column(Integer, ForeignKey("level.id"), nullable=False, unique=False)
    sp_level = relationship("Level", backref="species_sp", foreign_keys=[sp_level_id])

    opt_ess_id = Column(Integer, ForeignKey("ess.id"), nullable=True, unique=False)
    opt_ess = relationship("ESS", backref="species_opt", foreign_keys=[opt_ess_id])
    freq_ess_id = Column(Integer, ForeignKey("ess.id"), nullable=True, unique=False)
    freq_ess = relationship("ESS", backref="species_freq", foreign_keys=[freq_ess_id])
    scan_ess_id = Column(Integer, ForeignKey("ess.id"), nullable=True, unique=False)
    scan_ess = relationship("ESS", backref="species_scan", foreign_keys=[scan_ess_id])
    irc_ess_id = Column(Integer, ForeignKey("ess.id"), nullable=True, unique=False)
    irc_ess = relationship("ESS", backref="species_irc", foreign_keys=[irc_ess_id])
    sp_ess_id = Column(Integer, ForeignKey("ess.id"), nullable=False, unique=False)
    sp_ess = relationship("ESS", backref="species_sp", foreign_keys=[sp_ess_id])

    # relationships - Many to Many
    authors = relationship(
        "Person", secondary=species_authors, backref="authors_species"
    )
    reviewers = relationship(
        "Person", secondary=species_reviewers, backref="reviewers_species"
    )

    # paths
    opt_path = Column(String(5000), nullable=True)
    freq_path = Column(String(5000), nullable=True)
    scan_paths = Column(MsgpackExt, nullable=True)
    irc_paths = Column(MsgpackExt, nullable=True)
    sp_path = Column(String(5000), nullable=False)

    # unconverged jobs
    unconverged_jobs = Column(MsgpackExt, nullable=True)

    # misc
    extras = Column(MsgpackExt, nullable=True)

    def __str__(self) -> str:
        """
        A helper function for generating a user-friendly string representation of the object.
        """
        return species_as_str(
            class_name=self.__class__.__name__,
            id=self.id,
            label=self.label,
            smiles=self.smiles,
            inchi=self.inchi,
            inchi_key=self.inchi_key,
        )


def species_as_str(class_name, id, label, smiles, inchi, inchi_key):
    """
    A helper function for generating a user-friendly string representation of a Species or NonPhysicalSpecies object.
    """
    str_ = f"<{class_name}("
    str_ += f"id={id}"
    if label is not None and label:
        str_ += f", label={label}"
    if smiles is not None and smiles:
        str_ += f", smiles={smiles}"
    elif inchi is not None and inchi:
        str_ += f", inchi={inchi}"
    elif inchi_key is not None and inchi_key:
        str_ += f", inchi_key={inchi_key}"
    str_ += ")>"
    return str_
