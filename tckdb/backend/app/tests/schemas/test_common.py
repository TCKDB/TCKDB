"""
TCKDB backend app tests schemas test_freq module
"""

import pytest
import tckdb.backend.app.conversions.converter as converter
import tckdb.backend.app.schemas.common as common
from tckdb.backend.app.schemas.common import Coordinates


ch4_coords = Coordinates(
    symbols=("C", "H", "H", "H", "H"),
    isotopes=(12, 1, 1, 1, 1),
    coords=(
        (0.0, 0.0, 0.0),
        (0.6300326, 0.6300326, 0.6300326),
        (-0.6300326, -0.6300326, 0.6300326),
        (-0.6300326, 0.6300326, -0.6300326),
        (0.6300326, -0.6300326, -0.6300326),
    ),
)

n3h5_xyz = {
    "symbols": ("N", "H", "H", "N", "H", "N", "H", "H"),
    "coords": (
        (-1.1997440839, -0.1610052059, 0.0274738287),
        (-1.4016624407, -0.6229695533, -0.848703408),
        (-1.8759e-06, 1.2861082773, 0.592607787),
        (8.52e-07, 0.5651072858, -0.1124621525),
        (-1.1294692206, -0.8709078271, 0.7537518889),
        (1.1997613019, -0.1609980472, 0.0274604887),
        (1.1294795781, -0.870899855, 0.7537444446),
        (1.4015274689, -0.6230592706, -0.8487058662),
    ),
}


def test_lowercase_dict():
    """Test attaining a dictionary with lowercase keys and values"""
    d_1 = {"D": 1}
    assert common.lowercase_dict(d_1) == {"d": 1}
    d_2 = {"D": "Z"}
    assert common.lowercase_dict(d_2) == {"d": "z"}
    d_3 = {5: "Z"}
    assert common.lowercase_dict(d_3) == {5: "z"}
    d_4 = {"D": {"A": "Z", "v": "n", "H": 54, "f": "L", 7: 0, 8: {"Q": 9}}}
    assert common.lowercase_dict(d_4) == {
        "d": {"a": "z", "v": "n", "h": 54, "f": "l", 7: 0, 8: {"q": 9}}
    }
    with pytest.raises(TypeError):
        common.lowercase_dict("D")


def test_is_valid_energy_unit():
    """Test whether an energy unit is valid"""
    assert common.is_valid_energy_unit("hartree")[0]
    assert common.is_valid_energy_unit("kJ / mol")[0]
    assert common.is_valid_energy_unit("kJ/mol")[0]
    assert common.is_valid_energy_unit("kcal")[0]
    assert common.is_valid_energy_unit("kcal/mol")[0]
    assert common.is_valid_energy_unit("eV")[0]
    assert not common.is_valid_energy_unit("km")[0]
    with pytest.raises(ValueError):
        common.is_valid_energy_unit("inch", raise_error=True)
    with pytest.raises(ValueError):
        common.is_valid_energy_unit("r3", raise_error=True)
    with pytest.raises(ValueError):
        common.is_valid_energy_unit(5.6, raise_error=True)


def test_is_valid_element_symbol():
    """Test whether an energy unit is valid"""
    assert common.is_valid_element_symbol("H")[0]
    assert common.is_valid_element_symbol("N")[0]
    assert common.is_valid_element_symbol("Cr")[0]
    assert common.is_valid_element_symbol("Cl")[0]
    assert common.is_valid_element_symbol("Ar")[0]
    assert common.is_valid_element_symbol("C")[0]
    assert common.is_valid_element_symbol("Zn")[0]
    assert not common.is_valid_element_symbol("M")[0]
    with pytest.raises(ValueError):
        common.is_valid_element_symbol("L", raise_error=True)
    with pytest.raises(ValueError):
        common.is_valid_element_symbol(8.7, raise_error=True)


def test_is_valid_inchi():
    """Test whether an InChI descriptor is valid"""
    assert common.is_valid_inchi("InChI=1S/CH4/h1H4")[0]
    assert common.is_valid_inchi("InChI=1S/C7H8O/c8-6-7-4-2-1-3-5-7/h1-5,8H,6H2")[0]
    assert common.is_valid_inchi(
        "InChI=1S/C19H37NO8/c1-11(27-17-9-16(24-5)14(22)10-26-17)8-15(12(2)21)28-19-18"
        "(23)13(20(3)4)6-7-25-19/h11-19,21-23H,6-10H2,1-5H3"
    )[0]
    assert common.is_valid_inchi("InChI=1S/CH3ClFNS/c2-1(5)4-3/h1,4-5H")[0]
    assert not common.is_valid_inchi("not_an_inchi")[0]
    assert not common.is_valid_inchi(15)[0]


def test_is_valid_inchi_key():
    """Test whether an InChI Key descriptor is valid"""
    assert common.is_valid_inchi_key("PXOLZRNDFGGLHD-UHFFFAOYSA-N")[
        0
    ]  # CCC(S)C(C)C(CF)CCl
    assert common.is_valid_inchi_key("YZCKVEUIGOORGS-UHFFFAOYSA-N")[0]  # [H]
    assert common.is_valid_inchi_key("HSMPSHPWCOOUJH-UHFFFAOYSA-N")[
        0
    ]  # N=C1[CH]C=CC=C1
    assert common.is_valid_inchi_key("IKOIEDCMCGZUMI-UHFFFAOYSA-N")[0]  # SC(O)OS
    assert common.is_valid_inchi_key("CYLNCQIARDQRRU-UHFFFAOYSA-N")[0]  # ClN(Cl)CO
    assert common.is_valid_inchi_key("XKRFYHLGVUSROY-UHFFFAOYSA-N")[0]  # [Ar]
    assert not common.is_valid_inchi_key("not_an_inchi_key")[0]
    assert not common.is_valid_inchi_key(15)[0]


def test_is_valid_smiles():
    """Test whether a SMILES descriptor is valid"""
    assert common.is_valid_smiles("C")[0]
    assert common.is_valid_smiles("CCC=CC(=O)O")[0]
    assert common.is_valid_smiles("CN(C(O[O])CCN1C2C=CC=CC=2CCC2C1=CC=CC=2)C")[0]
    assert common.is_valid_smiles("FNC(Cl)S")[0]
    assert not common.is_valid_smiles("not_a_smiles")[0]
    assert not common.is_valid_smiles(15)[0]


def test_is_valid_adjlist():
    """Test whether an adjacency list id valid"""
    assert common.is_valid_adjlist(
        """multiplicity 2
1 O u0 p2 c0 {2,S} {4,S}
2 O u1 p2 c0 {1,S}
3 N u0 p1 c0 {7,T}
4 C u0 p0 c0 {1,S} {5,S} {6,S} {7,S}
5 C u0 p0 c0 {4,S} {8,S} {9,S} {10,S}
6 C u0 p0 c0 {4,S} {11,S} {12,S} {13,S}
7 C u0 p0 c0 {3,T} {4,S}
8 H u0 p0 c0 {5,S}
9 H u0 p0 c0 {5,S}
10 H u0 p0 c0 {5,S}
11 H u0 p0 c0 {6,S}
12 H u0 p0 c0 {6,S}
13 H u0 p0 c0 {6,S}"""
    )[0]
    assert common.is_valid_adjlist(
        """multiplicity 2
1 H u1 p0 c0"""
    )[0]
    assert common.is_valid_adjlist(
        """1 O u0 p2 c0 {2,S} {3,S}
2 H u0 p0 c0 {1,S}
3 H u0 p0 c0 {1,S}"""
    )[0]
    assert common.is_valid_adjlist(
        """1  C u0 p0 c0 {2,S} {8,S} {9,S} {10,S}
2  C u0 p0 c0 {1,S} {3,S} {11,S} {12,S}
3  C u0 p0 c0 {2,S} {4,S} {13,S} {14,S}
4  C u0 p0 c0 {3,S} {5,D} {6,S}
5  O u0 p2 c0 {4,D}
6  O u0 p2 c0 {4,S} {7,S}
7  C u0 p0 c0 {6,S} {15,S} {16,S} {17,S}
8  H u0 p0 c0 {1,S}
9  H u0 p0 c0 {1,S}
10 H u0 p0 c0 {1,S}
11 H u0 p0 c0 {2,S}
12 H u0 p0 c0 {2,S}
13 H u0 p0 c0 {3,S}
14 H u0 p0 c0 {3,S}
15 H u0 p0 c0 {7,S}
16 H u0 p0 c0 {7,S}
17 H u0 p0 c0 {7,S}"""
    )[0]
    assert common.is_valid_adjlist(
        """1 F  u0 p3 c0 {2,S}
2 N  u0 p1 c0 {1,S} {3,S} {6,S}
3 C  u0 p0 c0 {2,S} {4,S} {5,S} {7,S}
4 Cl u0 p3 c0 {3,S}
5 S  u0 p2 c0 {3,S} {8,S}
6 H  u0 p0 c0 {2,S}
7 H  u0 p0 c0 {3,S}
8 H  u0 p0 c0 {5,S}"""
    )[0]
    assert not common.is_valid_adjlist("not_an_adjacency_list")[0]
    assert not common.is_valid_adjlist(10.1)[0]


def test_colliding_atoms():
    """Test correct determination of atom collisions in coordinates"""
    xyz_no_0 = """C	0.0000000	0.0000000	0.6505570"""  # monoatomic
    xyz_no_1 = """C      -0.84339557   -0.03079260   -0.13110478
N       0.53015060    0.44534713   -0.25006000
O       1.33245258   -0.55134720    0.44204567
H      -1.12632103   -0.17824612    0.91628291
H      -1.52529493    0.70480833   -0.56787044
H      -0.97406455   -0.97317212   -0.67214713
H       0.64789210    1.26863944    0.34677470
H       1.98414750   -0.79355889   -0.24492049"""  # no colliding atoms
    xyz_no_2 = """C      0.0 0.0 0.0
H       0.0 0.0 1.09"""  # no colliding atoms
    xyz_no_3 = """N      -0.29070308    0.26322835    0.48770927
N       0.29070351   -0.26323281   -0.48771096
N      -2.61741263    1.38275080    2.63428181
N       2.61742270   -1.38276006   -2.63427425
C      -1.77086206    0.18100754    0.43957605
C       1.77086254   -0.18101028   -0.43957552
C      -2.22486176   -1.28143567    0.45202312
C      -2.30707039    0.92407663   -0.78734681
C       2.30707074   -0.92407071    0.78735246
C       2.22485929    1.28143406   -0.45203080
C      -2.23868798    0.85547218    1.67084736
C       2.23869247   -0.85548109   -1.67084185
H      -1.90398693   -1.81060764   -0.45229645
H      -3.31681639   -1.35858536    0.51240600
H      -1.80714051   -1.81980551    1.31137107
H      -3.40300863    0.95379538   -0.78701415
H      -1.98806037    0.44494681   -1.71978670
H      -1.94802915    1.96005927   -0.81269573
H       1.98805486   -0.44493850    1.71978893
H       1.94803425   -1.96005464    0.81270509
H       3.40300902   -0.95378386    0.78702431
H       1.90398036    1.81061002    0.45228426
H       3.31681405    1.35858667   -0.51241516
H       1.80713611    1.81979843   -1.31138136"""  # check that N=N and C#N do not collide

    assert not common.check_colliding_atoms(converter.str_to_xyz(xyz_no_0))
    assert not common.check_colliding_atoms(converter.str_to_xyz(xyz_no_1))
    assert not common.check_colliding_atoms(converter.str_to_xyz(xyz_no_2))
    assert not common.check_colliding_atoms(converter.str_to_xyz(xyz_no_3))

    xyz_0 = """C      0.0 0.0 0.0
H       0.0 0.0 0.5"""  # colliding atoms
    xyz_1 = """C      -0.84339557   -0.03079260   -0.13110478
N       0.53015060    0.44534713   -0.25006000
O       1.33245258   -0.55134720    0.44204567
H      -1.12632103   -0.17824612    0.91628291
H      -1.52529493    0.70480833   -0.56787044
H      -0.97406455   -0.97317212   -0.67214713
H       1.33245258   -0.55134720    0.48204567
H       1.98414750   -0.79355889   -0.24492049"""  # colliding atoms
    xyz_2 = """ N                 -0.29070308    0.26322835    0.48770927
 N                  0.29070351   -0.26323281   -0.48771096
 N                 -2.48318439    1.19587180    2.29281971
 N                  2.61742270   -1.38276006   -2.63427425
 C                 -1.77086206    0.18100754    0.43957605
 C                  1.77086254   -0.18101028   -0.43957552
 C                 -2.22486176   -1.28143567    0.45202312
 C                 -2.30707039    0.92407663   -0.78734681
 C                  2.30707074   -0.92407071    0.78735246
 C                  2.22485929    1.28143406   -0.45203080
 C                 -2.23868798    0.85547218    1.67084736
 C                  2.23869247   -0.85548109   -1.67084185
 H                 -1.90398693   -1.81060764   -0.45229645
 H                 -3.31681639   -1.35858536    0.51240600
 H                 -1.80714051   -1.81980551    1.31137107
 H                 -3.40300863    0.95379538   -0.78701415
 H                 -1.98806037    0.44494681   -1.71978670
 H                 -1.94802915    1.96005927   -0.81269573
 H                  1.98805486   -0.44493850    1.71978893
 H                  1.94803425   -1.96005464    0.81270509
 H                  3.40300902   -0.95378386    0.78702431
 H                  1.90398036    1.81061002    0.45228426
 H                  3.31681405    1.35858667   -0.51241516
 H                  1.80713611    1.81979843   -1.31138136"""  # check that C-N collide
    xyz_3 = """ N                 -0.29070308    0.26322835    0.48770927
 N                  0.29070351   -0.26323281   -0.48771096
 N                 -2.61741263    1.38275080    2.63428181
 N                  2.61742270   -1.38276006   -2.63427425
 C                 -1.77086206    0.18100754    0.43957605
 C                  1.77086254   -0.18101028   -0.43957552
 C                 -2.22486176   -1.28143567    0.45202312
 C                 -2.30707039    0.92407663   -0.78734681
 C                  2.30707074   -0.92407071    0.78735246
 C                  2.22485929    1.28143406   -0.45203080
 C                 -2.23868798    0.85547218    1.67084736
 C                  2.23869247   -0.85548109   -1.67084185
 H                 -1.90398693   -1.81060764   -0.45229645
 H                 -2.77266137   -1.32013927    0.48231533
 H                 -1.80714051   -1.81980551    1.31137107
 H                 -3.40300863    0.95379538   -0.78701415
 H                 -1.98806037    0.44494681   -1.71978670
 H                 -1.94802915    1.96005927   -0.81269573
 H                  1.98805486   -0.44493850    1.71978893
 H                  1.94803425   -1.96005464    0.81270509
 H                  3.40300902   -0.95378386    0.78702431
 H                  1.90398036    1.81061002    0.45228426
 H                  3.31681405    1.35858667   -0.51241516
 H                  1.80713611    1.81979843   -1.31138136"""  # check that C-H collide

    assert common.check_colliding_atoms(converter.str_to_xyz(xyz_0))
    assert common.check_colliding_atoms(converter.str_to_xyz(xyz_1))
    assert common.check_colliding_atoms(converter.str_to_xyz(xyz_2))
    assert common.check_colliding_atoms(converter.str_to_xyz(xyz_3))


def test_is_valid_coordinates():
    """Test whether a coordinates dictionary is valid"""
    assert common.is_valid_coordinates(ch4_coords)[0]
    assert not common.is_valid_coordinates(n3h5_xyz)[0]
    converter.add_common_isotopes_to_coords(n3h5_xyz)
    assert common.is_valid_coordinates(n3h5_xyz)[0]


def test_is_valid_atom_index():
    """Test whether an atom index is valid"""
    assert common.is_valid_atom_index(index=1)[0]
    assert common.is_valid_atom_index(index=100)[0]
    assert common.is_valid_atom_index(index=100, existing_indices=[1, 8, 3, 15])[0]
    assert common.is_valid_atom_index(index=5, coordinates=ch4_coords)[0]
    assert not common.is_valid_atom_index(index=0)[0]
    assert not common.is_valid_atom_index(index=6, coordinates=ch4_coords)[0]
    assert not common.is_valid_atom_index(index=3, existing_indices=[1, 8, 3, 15])[0]


def test_get_number_of_atoms():
    """Test determining the number of atoms in a coordinates dictionary"""
    assert common.get_number_of_atoms(None) is None
    assert common.get_number_of_atoms(ch4_coords) == 5
    assert common.get_number_of_atoms(n3h5_xyz) == 8
    ch4_coords_in_dict = {"coordinates": ch4_coords}
    assert common.get_number_of_atoms(ch4_coords_in_dict) == 5
