# Trimmed REAL excerpt of Michal Keslin's hydrazine Final_MRCI_PDep/output.py.
# Kept: conformer statmech for N2H4 (with HinderedRotor), H2NN, H2, and TS1;
# plus one active pdepreaction Chebyshev block (H2 + H2NN <=> N2H4). A
# deliberately commented-out pdepreaction is kept to prove the parser skips it.

# Coordinates for N2H4 in Input Orientation (angstroms):
#   N   -0.6628   -0.1053   -0.2416
conformer(
    label = 'N2H4',
    E0 = (152.599, 'kJ/mol'),
    modes = [
        IdealGasTranslation(mass=(32.0375, 'amu')),
        NonlinearRotor(
            inertia = ([3.44885, 20.5046, 20.5203], 'amu*angstrom^2'),
            symmetry = 2,
        ),
        HarmonicOscillator(
            frequencies = ([808.45, 957.864, 1134.05, 1290.57, 1320.78, 1662.14, 1676.8, 3455.07, 3462.89, 3558.59, 3563.64], 'cm^-1'),
        ),
        HinderedRotor(
            inertia = (0.864714, 'amu*angstrom^2'),
            symmetry = 1,
            fourier = (
                [
                    [-0.162647, -13.0444, 0.633904, -0.18494, -0.563941],
                    [-8.63878, 1.65078, 3.54785, -0.755929, -0.386158],
                ],
                'kJ/mol',
            ),
            quantum = None,
            semiclassical = None,
        ),
    ],
    spin_multiplicity = 1,
    optical_isomers = 2,
)

# Coordinates for H2NN in Input Orientation (angstroms):
conformer(
    label = 'H2NN',
    E0 = (347.688, 'kJ/mol'),
    modes = [
        IdealGasTranslation(mass=(30.0218, 'amu')),
        NonlinearRotor(
            inertia = ([1.50692, 12.9409, 14.4478], 'amu*angstrom^2'),
            symmetry = 2,
        ),
        HarmonicOscillator(
            frequencies = ([997.747, 1311.96, 1579.08, 1710.4, 3034.33, 3040.95], 'cm^-1'),
        ),
    ],
    spin_multiplicity = 1,
    optical_isomers = 1,
)

# Coordinates for H2 in Input Orientation (angstroms):
conformer(
    label = 'H2',
    E0 = (
        -4.63721,
        'kJ/mol',
    ),
    modes = [
        IdealGasTranslation(mass=(2.01565, 'amu')),
        LinearRotor(inertia=(0.27598, 'amu*angstrom^2'), symmetry=2),
        HarmonicOscillator(frequencies=([4399.87], 'cm^-1')),
    ],
    spin_multiplicity = 1,
    optical_isomers = 1,
)

# Coordinates for TS1 in Input Orientation (angstroms):
conformer(
    label = 'TS1',
    E0 = (468.518, 'kJ/mol'),
    modes = [
        IdealGasTranslation(mass=(32.0375, 'amu')),
        NonlinearRotor(
            inertia = ([4.42746, 19.3729, 20.1294], 'amu*angstrom^2'),
            symmetry = 1,
        ),
        HarmonicOscillator(
            frequencies = ([222.15, 556.34, 910.501, 1009.88, 1192.98, 1271.86, 1349.33, 1675.76, 2897.65, 3430.92, 3532.53], 'cm^-1'),
        ),
    ],
    spin_multiplicity = 1,
    optical_isomers = 2,
    frequency = (
        -1406.6,
        'cm^-1',
    ),
)

# A commented-out pdepreaction that the parser MUST ignore:
#pdepreaction(
#    reactants = ['N2H4'],
#    products = ['H2', 'H2NN'],
#    kinetics = Chebyshev(
#        coeffs = [[1.0, 0.0, 0.0, 0.0]],
#        kunits = 's^-1',
#        Tmin = (300, 'K'), Tmax = (2000, 'K'),
#        Pmin = (0.01, 'bar'), Pmax = (100, 'bar'),
#    ),
#)

pdepreaction(
    reactants = ['H2', 'H2NN'],
    products = ['N2H4'],
    kinetics = Chebyshev(
        coeffs = [
            [-2.10678, 1.69152, 0.0388832, -0.0128202],
            [7.28692, 0.517624, -0.063172, 0.0163503],
            [0.476769, -0.295246, 0.0239678, -0.000208037],
            [-0.245158, 0.0852448, 0.0133574, -0.00866986],
            [-0.014641, 0.0265802, -0.0237032, 0.00734324],
            [0.0264297, -0.039152, 0.0107025, -0.000344881],
        ],
        kunits = 'cm^3/(mol*s)',
        Tmin = (300, 'K'),
        Tmax = (2000, 'K'),
        Pmin = (0.01, 'bar'),
        Pmax = (100, 'bar'),
    ),
)
