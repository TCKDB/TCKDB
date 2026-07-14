#!/usr/bin/env python3
# encoding: utf-8

bonds = {'H-N': 4, 'N-N': 1}

externalSymmetry = 2

spinMultiplicity = 1

opticalIsomers = 2

energy = Log('/home/michal/Dropbox/PersonalFolders/Michal_Keslin/Hydrazine/MRCI_davidson/Data/N2H4/sp.out')

geometry = Log('/home/michal/Dropbox/PersonalFolders/Michal_Keslin/Hydrazine/MRCI_davidson/Data/N2H4/freq.out')

frequencies = Log('/home/michal/Dropbox/PersonalFolders/Michal_Keslin/Hydrazine/MRCI_davidson/Data/N2H4/freq.out')



rotors = [HinderedRotor(scanLog=Log('/home/michal/Dropbox/PersonalFolders/Michal_Keslin/Hydrazine/MRCI_davidson/Data/N2H4/scan.out'), pivots=[1, 2], top=[1, 3, 4], symmetry=1, fit='fourier')]
