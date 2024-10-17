"""
TCKDB backend app tests models test_species module
"""

from datetime import datetime

import rdkit

from tckdb.backend.app.models.ess import ESS
from tckdb.backend.app.models.level import Level
from tckdb.backend.app.models.species import Species


timestamp = datetime.timestamp(datetime.utcnow())
formaldehyde_xyz = {'symbols': ('C', 'O', 'H', 'H'),
                    'isotopes': (12, 16, 1, 1),
                    'coords': ((-0.0122240982, 0.0001804054, -0.00162116),
                               (1.2016481968, -0.0177341701, 0.1593624097),
                               (-0.5971643978, 0.9327281670, 0.0424401022),
                               (-0.5922597008, -0.9151744023, -0.2001813507))}
formaldehyde_adj = """1 C u0 p0 c0 {2,D} {3,S} {4,S}
2 O u0 p2 c0 {1,D}
3 H u0 p0 c0 {1,S}
4 H u0 p0 c0 {1,S}"""

species_1 = Species(label='formaldehyde',
                    statmech_software='Arkane',
                    timestamp=timestamp,
                    reviewed=False,
                    approved=False,
                    smiles='C=O',
                    inchi='InChI=1S/CH2O/c1-2/h1H2',
                    inchi_key=rdkit.Chem.inchi.InchiToInchiKey('InChI=1S/CH2O/c1-2/h1H2'),
                    charge=0,
                    multiplicity=1,
                    electronic_state='X',
                    coordinates=formaldehyde_xyz,
                    graph=formaldehyde_adj,
                    external_symmetry=2,
                    point_group='C2v',
                    conformation_method='CCCBDB',
                    is_well=True,
                    is_global_min=True,
                    is_ts=False,
                    electronic_energy=-325.6458956547,
                    E0=123.54842,
                    active_space={'electrons': 6, 'orbitals': 12},
                    hessian=[[1, 2, 3],
                             [2, 5, 4],
                             [3, 4, 9]],
                    frequencies=[132.2, 548.5, 1032.5, 2015.22, 2018.12, 3005.22],
                    scaled_projected_frequencies=[130.217, 540.2725, 1017.013, 1984.992, 1987.848, 2960.142],
                    normal_displacement_modes=[[0.125, 0.89, 0.35],
                                               [-0.25, 0.25, -0.89],
                                               [0.56, 0.98, -0.65],
                                               [0.022, -0.005, 0.5],
                                               [-0.98, -0.002, 0.65],
                                               [0.05, 0.0025, -0.722]],
                    #freq_id=11,
                    rigid_rotor='asymmetric top',
                    statmech_treatment='RRHO',
                    rotational_constants=[1.25, 8.56, 9.55],
                    conformers=[{1: 3}, {2: 4}],
                    H298=-109.4534,
                    S298=218.237,
                    Cp_values=[35.313, 39.037, 43.4299, 47.7813, 55.438, 61.463, 70.71],
                    Cp_T_list=[300, 400, 500, 600, 800, 1000, 1500],
                    heat_capacity_model={'model': 'NASA',
                                         'T min': 100,
                                         'T max': 5000,
                                         'coefficients': {'low': [4.13878818E+00,
                                                                  -4.69514383E-03,
                                                                  2.25730249E-05,
                                                                  -2.09849937E-08,
                                                                  6.36123283E-12,
                                                                  -1.43493283E+04,
                                                                  3.23827482E+00],
                                                          'high': [2.36095410E+00,
                                                                   7.66804276E-03,
                                                                   -3.19770442E-06,
                                                                   6.04724833E-10,
                                                                   -4.27517878E-14,
                                                                   -1.42794809E+04,
                                                                   1.04457152E+01],
                                                          'T int': 1041.96}},
                    encorr_id=33,
                    opt_path='path_opt',
                    freq_path='path_freq',
                    sp_path='path_sp',
                    extras={'tst': 'testing extras'},
                    )


def test_species_model():
    """Test creating an instance of Species"""
    assert species_1.label == 'formaldehyde'
    assert species_1.statmech_software == 'Arkane'
    assert species_1.timestamp == timestamp
    assert species_1.retracted is None
    assert species_1.reviewed is False
    assert species_1.approved is False
    assert species_1.reviewer_flags is None
    assert species_1.smiles == 'C=O'
    assert species_1.inchi == 'InChI=1S/CH2O/c1-2/h1H2'
    assert species_1.inchi_key == 'WSFSSNUMVMOOMR-UHFFFAOYSA-N'
    assert species_1.charge == 0
    assert species_1.multiplicity == 1
    assert species_1.electronic_state == 'X'
    assert species_1.coordinates == formaldehyde_xyz
    assert species_1.graph == formaldehyde_adj
    assert species_1.fragments is None
    assert species_1.fragment_orientation is None
    assert species_1.external_symmetry == 2
    assert species_1.point_group == 'C2v'
    assert species_1.chirality is None
    assert species_1.conformation_method == 'CCCBDB'
    assert species_1.is_well is True
    assert species_1.is_global_min is True
    assert species_1.global_min_geometry is None
    assert species_1.is_ts is False
    assert species_1.irc_trajectories is None
    assert species_1.electronic_energy == -325.6458956547
    assert species_1.E0 == 123.54842
    assert species_1.active_space == {'electrons': 6, 'orbitals': 12}
    assert species_1.hessian == [[1, 2, 3],
                                 [2, 5, 4],
                                 [3, 4, 9]]
    assert species_1.frequencies == [132.2, 548.5, 1032.5, 2015.22, 2018.12, 3005.22]
    assert species_1.scaled_projected_frequencies == [130.217, 540.2725, 1017.013, 1984.992, 1987.848, 2960.142]
    assert species_1.normal_displacement_modes == [[0.125, 0.89, 0.35],
                                                   [-0.25, 0.25, -0.89],
                                                   [0.56, 0.98, -0.65],
                                                   [0.022, -0.005, 0.5],
                                                   [-0.98, -0.002, 0.65],
                                                   [0.05, 0.0025, -0.722]]
    #assert species_1.freq_id == 11
    assert species_1.rigid_rotor == 'asymmetric top'
    assert species_1.statmech_treatment == 'RRHO'
    assert species_1.rotational_constants == [1.25, 8.56, 9.55]
    assert species_1.torsions is None
    assert species_1.conformers == [{1: 3}, {2: 4}]
    assert species_1.H298 == -109.4534
    assert species_1.S298 == 218.237
    assert species_1.Cp_values == [35.313, 39.037, 43.4299, 47.7813, 55.438, 61.463, 70.71]
    assert species_1.Cp_T_list == [300, 400, 500, 600, 800, 1000, 1500]
    assert species_1.heat_capacity_model == {'model': 'NASA',
                                             'T min': 100,
                                             'T max': 5000,
                                             'coefficients': {'low': [4.13878818E+00,
                                                                      -4.69514383E-03,
                                                                      2.25730249E-05,
                                                                      -2.09849937E-08,
                                                                      6.36123283E-12,
                                                                      -1.43493283E+04,
                                                                      3.23827482E+00],
                                                              'high': [2.36095410E+00,
                                                                       7.66804276E-03,
                                                                       -3.19770442E-06,
                                                                       6.04724833E-10,
                                                                       -4.27517878E-14,
                                                                       -1.42794809E+04,
                                                                       1.04457152E+01],
                                                              'T int': 1041.96}}
    assert species_1.encorr_id == 33
    assert species_1.opt_path == 'path_opt'
    assert species_1.freq_path == 'path_freq'
    assert species_1.scan_paths is None
    assert species_1.irc_paths is None
    assert species_1.sp_path == 'path_sp'
    assert species_1.unconverged_jobs is None
    assert species_1.extras == {'tst': 'testing extras'}
    assert str(species_1) == '<Species(id=None, label=formaldehyde, smiles=C=O)>'


def test_species_relationships():
    """Test Species relationships: Level and ESS"""
    level_1 = Level(method='cbs-qb3')
    level_2 = Level(method='wB97xd', basis='def2TZVP', solvation_method='PCM', solvent='water', grid='UltraFine')
    species_1.sp_level = level_1
    species_1.opt_level = level_2
    assert species_1.sp_level.method == 'cbs-qb3'
    assert str(species_1.opt_level) == 'wB97xd/def2TZVP UltraFine solvation: PCM in water'
    assert species_1.opt_level.method == 'wB97xd'
    assert species_1.freq_level is None

    ess_1 = ESS(name='Psi4', version='1.1', url='http://www.psicode.org/')
    ess_2 = ESS(name='Gaussian', version='16', revision='C.01', url='https://gaussian.com/')
    species_1.sp_ess = ess_2
    species_1.opt_ess = ess_1
    assert species_1.sp_ess.name == 'Gaussian'
    assert str(species_1.sp_ess) == 'Gaussian 16'
    assert species_1.opt_ess.name == 'Psi4'
    assert species_1.freq_ess is None
