"""
TCKDB backend app models non-physical species (np_species) module
"""

from typing import List

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.associations import np_species_authors, np_species_reviewers
from tckdb.backend.app.models.common import MsgpackExt
from tckdb.backend.app.models.species import species_as_str


class NonPhysicalSpecies(Base):
    """
    A class for representing a TCKDB NonPhysicalSpecies item.

    Note:
        All atom indices in these arguments are 1-indexed unless explicitly stated otherwise.

    Attributes:
        id (int)
            The primary key (not a user input).

        label (Optional[str])
            A free user label for the species (maximum 255 characters).

        # provenance
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

        # relationships - Many to One
        literature_id (int)
            The literature reference from the :ref:`Literature table <Literature_model>`.
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
        sp_level (Optional[relationship])
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
        sp_ess (Optional[relationship])
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
        sp_path (Optional[str])
            The path to the single point energy output file.
            Unlike the ``Species`` object, here ``sp_path`` is optional.

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
        extras (Dict[str, Any])
            Any additional information in the form of a dictionary
            Unlike the ``Species`` object, here ``extras`` is required and should at least contain a reason
            for declaring this species non-physical.
    """

    id = Column(Integer, primary_key=True, index=True, nullable=False)

    label = Column(String(255), nullable=True)

    # provenance
    timestamp = Column(Float, nullable=False)
    retracted = Column(String(255), nullable=True)

    # review
    reviewed = Column(Boolean, nullable=False)
    approved = Column(Boolean, nullable=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    # chemical identifiers
    smiles = Column(String(5000), nullable=False)
    inchi = Column(String(5000), nullable=False)
    inchi_key = Column(String(5000), nullable=False)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    electronic_state = Column(String(150), nullable=False)

    # geometry and connectivity
    coordinates = Column(MsgpackExt, nullable=False)
    graph = Column(String(100000), nullable=True)
    fragments = Column(ARRAY(item_type=int, as_tuple=False, zero_indexes=True), nullable=True)
    fragment_orientation = Column(MsgpackExt, nullable=True)
    chirality = Column(MsgpackExt, nullable=True)
    conformation_method = Column(String(500), nullable=False)
    is_well = Column(Boolean, nullable=False)
    is_global_min = Column(Boolean, nullable=False)
    global_min_geometry = Column(MsgpackExt, nullable=True)

    # TS
    is_ts = Column(Boolean, nullable=False)
    irc_trajectories = Column(MsgpackExt, nullable=True)

    # relationships - Many to One
    literature_id = Column(Integer, ForeignKey('literature.id'), nullable=True, unique=False)
    literature = relationship('Literature', back_populates='np_species')
    bot_id = Column(Integer, ForeignKey('bot.id'), nullable=True, unique=False)
    bot = relationship('Bot', back_populates='np_species')

    opt_level_id = Column(Integer, ForeignKey('level.id'), nullable=True, unique=False)
    opt_level = relationship('Level', backref="np_species_opt", foreign_keys=[opt_level_id])
    freq_level_id = Column(Integer, ForeignKey('level.id'), nullable=True, unique=False)
    freq_level = relationship('Level', backref='np_species_freq', foreign_keys=[freq_level_id])
    scan_level_id = Column(Integer, ForeignKey('level.id'), nullable=True, unique=False)
    scan_level = relationship('Level', backref='np_species_scan', foreign_keys=[scan_level_id])
    irc_level_id = Column(Integer, ForeignKey('level.id'), nullable=True, unique=False)
    irc_level = relationship('Level', backref='np_species_irc', foreign_keys=[irc_level_id])
    sp_level_id = Column(Integer, ForeignKey('level.id'), nullable=False, unique=False)
    sp_level = relationship('Level', backref='np_species_sp', foreign_keys=[sp_level_id])

    opt_ess_id = Column(Integer, ForeignKey('ess.id'), nullable=True, unique=False)
    opt_ess = relationship('ESS', backref='np_species_opt', foreign_keys=[opt_ess_id])
    freq_ess_id = Column(Integer, ForeignKey('ess.id'), nullable=True, unique=False)
    freq_ess = relationship('ESS', backref='np_species_freq', foreign_keys=[freq_ess_id])
    scan_ess_id = Column(Integer, ForeignKey('ess.id'), nullable=True, unique=False)
    scan_ess = relationship('ESS', backref='np_species_scan', foreign_keys=[scan_ess_id])
    irc_ess_id = Column(Integer, ForeignKey('ess.id'), nullable=True, unique=False)
    irc_ess = relationship('ESS', backref='np_species_irc', foreign_keys=[irc_ess_id])
    sp_ess_id = Column(Integer, ForeignKey('ess.id'), nullable=False, unique=False)
    sp_ess = relationship('ESS', backref='np_species_sp', foreign_keys=[sp_ess_id])

    # relationships - Many to Many
    authors = relationship('Person', secondary=np_species_authors, backref='authors_np_species')
    reviewers = relationship('Person', secondary=np_species_reviewers, backref='reviewers_np_species')

    # paths
    opt_path = Column(String(5000), nullable=True)
    freq_path = Column(String(5000), nullable=True)
    scan_paths = Column(MsgpackExt, nullable=True)
    irc_paths = Column(ARRAY(item_type=List[str], as_tuple=False, zero_indexes=True), nullable=True)
    sp_path = Column(String(5000), nullable=False)

    # unconverged jobs
    unconverged_jobs = Column(MsgpackExt, nullable=True)

    # misc
    extras = Column(MsgpackExt, nullable=True)

    def __str__(self) -> str:
        """
        A helper function for generating a user-friendly string representation of the object.
        """
        return species_as_str(class_name=self.__class__.__name__,
                              id=self.id,
                              label=self.label,
                              smiles=self.smiles,
                              inchi=self.inchi,
                              inchi_key=self.inchi_key)
