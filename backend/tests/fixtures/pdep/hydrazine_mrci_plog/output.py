# Trimmed REAL-shaped excerpt of a SECOND Arkane run of Michal Keslin's
# hydrazine network, fit as PLOG (interpolationModel = ('pdeparrhenius',))
# instead of Chebyshev. Same network / same channels as the sibling
# hydrazine_mrci fixture; only the k(T,P) parameterization differs.
#
# Kept: one active pdepreaction / PDepArrhenius block for the same channel the
# Chebyshev fixture carries (H2 + H2NN <=> N2H4), plus one commented-out block
# the parser MUST ignore.

# A commented-out pdepreaction that the parser MUST ignore:
#pdepreaction(
#    reactants = ['N2H4'],
#    products = ['H2', 'H2NN'],
#    kinetics = PDepArrhenius(
#        pressures = ([0.01, 100], 'bar'),
#        arrhenius = [
#            Arrhenius(A=(1.0, 's^-1'), n=0.0, Ea=(0.0, 'kJ/mol'), T0=(1,'K')),
#            Arrhenius(A=(1.0, 's^-1'), n=0.0, Ea=(0.0, 'kJ/mol'), T0=(1,'K')),
#        ],
#    ),
#)

pdepreaction(
    reactants = ['H2', 'H2NN'],
    products = ['N2H4'],
    kinetics = PDepArrhenius(
        pressures = ([0.01, 0.1, 1, 10, 100], 'bar'),
        arrhenius = [
            Arrhenius(
                A = (1.23456e+12, 'cm^3/(mol*s)'),
                n = 0.42,
                Ea = (35.1, 'kJ/mol'),
                T0 = (1, 'K'),
                Tmin = (300, 'K'),
                Tmax = (2000, 'K'),
                comment = 'Fitted to 8 data points; dA = *|/ 1.2, dn = +|- 0.01, dEa = +|- 0.1 kJ/mol',
            ),
            Arrhenius(
                A = (2.34567e+12, 'cm^3/(mol*s)'),
                n = 0.51,
                Ea = (36.2, 'kJ/mol'),
                T0 = (1, 'K'),
                Tmin = (300, 'K'),
                Tmax = (2000, 'K'),
                comment = 'Fitted to 8 data points',
            ),
            Arrhenius(
                A = (3.45678e+12, 'cm^3/(mol*s)'),
                n = 0.60,
                Ea = (37.3, 'kJ/mol'),
                T0 = (1, 'K'),
                Tmin = (300, 'K'),
                Tmax = (2000, 'K'),
                comment = 'Fitted to 8 data points',
            ),
            Arrhenius(
                A = (4.56789e+12, 'cm^3/(mol*s)'),
                n = 0.69,
                Ea = (38.4, 'kJ/mol'),
                T0 = (1, 'K'),
                Tmin = (300, 'K'),
                Tmax = (2000, 'K'),
                comment = 'Fitted to 8 data points',
            ),
            Arrhenius(
                A = (5.67891e+12, 'cm^3/(mol*s)'),
                n = 0.78,
                Ea = (39.5, 'kJ/mol'),
                T0 = (1, 'K'),
                Tmin = (300, 'K'),
                Tmax = (2000, 'K'),
                comment = 'Fitted to 8 data points',
            ),
        ],
    ),
)
