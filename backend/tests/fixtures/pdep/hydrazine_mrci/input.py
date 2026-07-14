#!/usr/bin/env python
# encoding: utf-8

# Trimmed REAL excerpt of Michal Keslin's hydrazine Final_MRCI_PDep/input.py.
# Kept: N2H4 (well, hindered rotor, opticalIsomers=2), H2NN + H2 (a bimolecular
# channel), nitrogen (bath gas), TS1 (full ab-initio), reaction1 (N2H4 <=> H2 +
# H2NN via TS1), the network + pressureDependence blocks. This is a
# self-consistent mini-network exercising every parser path.

modelChemistry = CompositeLevelOfTheory(
            freq=LevelOfTheory(method="wb97xd", basis="def2tzvp", software="gaussian"),
            energy=LevelOfTheory(method="MRCI+Davidson", basis="aug-cc-pV(T+d)Z", software="molpro")
        )

useHinderedRotors = True

frequencyScaleFactor = 0.986

species('N2H4', 'Data/N2H4.py',
        structure = SMILES('NN'),
        collisionModel = TransportData(sigma=(3.62,'angstrom'), epsilon=(2866.18,'J/mol')),
        energyTransferModel = SingleExponentialDown(alpha0=(175,'cm^-1'), T0=(298,'K'), n=0.52),
)
species('H2NN', 'Data/NH2N.py',
        structure = SMILES('[N-]=[NH2+]'),
        collisionModel = TransportData(sigma=(3.47,'angstrom'), epsilon=(3222.33,'J/mol')),
        energyTransferModel = SingleExponentialDown(alpha0=(175,'cm^-1'), T0=(298,'K'), n=0.52),
)
species('H2', 'Data/H2.py',
        structure = SMILES('[H][H]'),
        collisionModel = TransportData(sigma=(2.92,'angstrom'), epsilon=(315.95,'J/mol')),
        energyTransferModel = SingleExponentialDown(alpha0=(175,'cm^-1'), T0=(298,'K'), n=0.52),
)
transitionState('TS1', 'Data/ts1.py')

species(
    label = 'nitrogen',
    reactive=False,
    structure = SMILES('N#N'),
    E0 = (-8.69489,'kJ/mol'),
    spinMultiplicity = 1,
    opticalIsomers = 1,
    collisionModel = TransportData(shapeIndex=1, epsilon=(322.846,'K'), sigma=(3.461,'angstroms'), dipoleMoment=(0,'C*m'),
    polarizability=(1.76,'angstroms^3'), rotrelaxcollnum=4.0, comment="""Jasper, ANL"""),
    energyTransferModel = SingleExponentialDown(alpha0=(175,'cm^-1'), T0=(298,'K'), n=0.52),
)

reaction(
    label = 'N2H4 <=> H2 + NH2N',
    reactants = ['N2H4'],
    products = ['H2', 'H2NN'],
    transitionState = 'TS1',
    tunneling = 'Eckart',
)

network(
    label = 'hydrazine',
    isomers = [
        'N2H4',
    ],
    reactants = [
        ('H2', 'H2NN'),
    ],
    bathGas = {
        'nitrogen': 1.0,
    }
)

pressureDependence(
    'hydrazine',
    Tmin=(300.0,'K'), Tmax=(2000.0,'K'), Tcount=8,
    Pmin=(0.01,'bar'), Pmax=(100.0,'bar'), Pcount=5,
    maximumGrainSize = (0.5,'kcal/mol'),
    minimumGrainCount = 200,
    method = 'modified strong collision',
    interpolationModel = ('chebyshev', 6, 4),
    activeJRotor = True,
)
