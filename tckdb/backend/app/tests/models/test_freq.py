"""
TCKDB backend app tests models test_author module
"""

from tckdb.backend.app.models.freq import Freq


def test_author_model():
    """Test creating an instance of Author"""
    freq1 = Freq(level={'method': 'cbs-qb3'}, factor=0.99 * 1.014,
                 source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924')
    assert freq1.level == {'method': 'cbs-qb3'}
    assert freq1.factor == 0.99 * 1.014
    assert freq1.source == 'J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924'
    assert repr(freq1) == "<Freq(id=None, " \
                          "level={'method': 'cbs-qb3'}, " \
                          "factor=1.00386, " \
                          "source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, " \
                          "DOI: 10.1063/1.477924')>"
    assert str(freq1) == "<Freq(level={'method': 'cbs-qb3'}, factor=1.00386, " \
                         "source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, " \
                         "DOI: 10.1063/1.477924')>"

    freq2 = Freq(level={'method': 'wB97xd', 'basis': 'def2TZVP', 'solvation': {'method': 'PCM', 'solvent': 'water'}},
                 factor=0.98, source="Calculated using the Truhlar method")
    assert str(freq2) == "<Freq(level={'method': 'wB97xd', 'basis': 'def2TZVP', 'solvation': " \
                         "{'method': 'PCM', 'solvent': 'water'}}, factor=0.98, " \
                         "source='Calculated using the Truhlar method')>"

    freq3 = Freq(level={'method': 'B3LYP', 'basis': '6-31G(d,p)', 'dispersion': 'gd3bj'},
                 factor=0.98, source="Calculated using the Truhlar method")
    assert str(freq3) == "<Freq(level={'method': 'B3LYP', 'basis': '6-31G(d,p)', 'dispersion': 'gd3bj'}, factor=0.98," \
                         " source='Calculated using the Truhlar method')>"

    freq4 = Freq(level={'method': 'DLPNO-CCSD(T)-F12', 'basis': 'cc-pVTZ-F12',
                        'auxiliary_basis': 'aug-cc-pVTZ/C cc-pVTZ-F12-CABS'},
                 factor=0.98, source="Calculated using the Truhlar method")
    assert str(freq4) == "<Freq(level={'method': 'DLPNO-CCSD(T)-F12', 'basis': 'cc-pVTZ-F12', " \
                         "'auxiliary_basis': 'aug-cc-pVTZ/C cc-pVTZ-F12-CABS'}, factor=0.98, " \
                         "source='Calculated using the Truhlar method')>"
