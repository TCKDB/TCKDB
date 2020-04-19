"""
TCKDB backend app schemas species module
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, conint, constr, validator


class Coordinates(BaseModel):
    """
    A Coordinates model for representing Cartesian coordinates
    """
    symbols: Tuple[constr(max_length=2)]
    isotopes: Tuple[conint(lt=500)]
    coords: Tuple[Tuple[float, float ,float]]


class FragmentOrientation(BaseModel):
    """
    A FragmentOrientation model for representing fragment orientation
    """
    cm: List[float, float, float]
    x: float
    y: float
    z: float


class SpeciesBase(BaseModel):
    """
    A SpeciesBase class (shared properties)
    """
    label: constr(max_length=255) = None
    provenance: Dict[str, Union[List[int], int, str]]
    review: Dict[str, Union[Union[int, None], bool]] = {'reviewer': None,
                                                        'reviewed': False,
                                                        'approved': False}
    literature: conint(ge=0) = None
    retracted: constr(max_length=255) = None
    extras: dict = None
    identifiers: Dict[str, str]
    charge: conint(ge=-10, le=10)
    multiplicity: conint(ge=1)
    coordinates: Coordinates
    graphs: Optional[List[str]] = None
    fragments: Optional[List[List[conint(ge=1)]]] = None
    fragment_orientation: Optional[List[FragmentOrientation]] = None
"""
        fragment_orientation (list): Relative orientation of fragments starting from the heaviest one.
                                     Both fragments must be in standard Cartesian orientation.
                                     Entries are dicts with keys (values):
                                     - 'cm' (list[float]),
                                     - 'x' (float),
                                     - 'y' (float),
                                     - 'z' (float).
"""

    # attribute Sandbox:
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










    @validator('name')
    def name_must_contain_space(cls, value):
        """Author.name validator"""
        if ' ' not in value:
            raise ValueError('provide a full name')
        return value.title()

    @validator('email')
    def validate_email(cls, value):
        """Author.email validator"""
        if '@' not in value:
            raise ValueError('email must contain a "@"')
        if value.count('@') > 1:
            raise ValueError('email must contain only one "@"')
        if '.' not in value.split('@')[1]:
            raise ValueError('email invalid (expected a "." after the "@" sign)')
        if ' ' in value:
            raise ValueError('email invalid (no spaces allowed)')
        return value


class AuthorCreate(AuthorBase):
    """Create an Author item: Properties to receive on item creation"""
    name: str
    email: str
    affiliation: str


class AuthorUpdate(AuthorBase):
    """Update an Author item: Properties to receive on item update"""
    name: str
    email: str
    affiliation: str


class AuthorInDBBase(AuthorBase):
    """Properties shared by models stored in DB"""
    id: int
    name: str
    email: int
    affiliation: int

    class Config:
        orm_mode = True


class Author(AuthorInDBBase):
    """Properties to return to client"""
    pass


class AuthorInDB(AuthorInDBBase):
    """Properties properties stored in DB"""
    pass
