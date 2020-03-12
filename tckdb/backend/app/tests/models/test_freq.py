"""
TCKDB backend app tests models test_author module
"""

import unittest

from tckdb.backend.app.models.freq import Freq


class TestFreqModel(unittest.TestCase):
    """
    Contains unit tests for the Author module
    """

    @classmethod
    def setUpClass(cls):
        """
        A method that is run before all unit tests in this class.
        """
        cls.maxDiff = None

    def test_author_model(self):
        """Test creating an instance of Author"""
        freq1 = Freq(level={'method': 'cbs-qb3'}, factor=0.99 * 1.014,
                     source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924')
        self.assertEqual(freq1.level, {'method': 'cbs-qb3'})
        self.assertEqual(freq1.factor, 0.99 * 1.014)
        self.assertEqual(freq1.source, 'J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, '
                                       'DOI: 10.1063/1.477924')
        self.assertEqual(repr(freq1),
                         "<Freq(id=None, "
                         "level={'method': 'cbs-qb3'}, "
                         "factor=1.00386, "
                         "source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, "
                         "DOI: 10.1063/1.477924')>")
        self.assertEqual(str(freq1),
                         "<Freq(level={'method': 'cbs-qb3'}, factor=1.00386, "
                         "source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, "
                         "DOI: 10.1063/1.477924')>")

        freq2 = Freq(level={'method': 'wB97xd', 'basis': 'def2TZVP', 'solvation': {'method': 'PCM', 'solvent': 'water'}},
                     factor=0.98, source="Calculated using the Truhlar method")
        self.assertEqual(str(freq2),
                         "<Freq(level={'method': 'wB97xd', 'basis': 'def2TZVP', 'solvation': "
                         "{'method': 'PCM', 'solvent': 'water'}}, factor=0.98, "
                         "source='Calculated using the Truhlar method')>")

        freq3 = Freq(level={'method': 'B3LYP', 'basis': '6-31G(d,p)', 'dispersion': 'gd3bj'},
                     factor=0.98, source="Calculated using the Truhlar method")
        self.assertEqual(str(freq3),
                         "<Freq(level={'method': 'B3LYP', 'basis': '6-31G(d,p)', 'dispersion': 'gd3bj'}, factor=0.98, "
                         "source='Calculated using the Truhlar method')>")

        freq4 = Freq(level={'method': 'DLPNO-CCSD(T)-F12', 'basis': 'cc-pVTZ-F12',
                            'auxiliary_basis': 'aug-cc-pVTZ/C cc-pVTZ-F12-CABS'},
                     factor=0.98, source="Calculated using the Truhlar method")
        self.assertEqual(str(freq4),
                         "<Freq(level={'method': 'DLPNO-CCSD(T)-F12', 'basis': 'cc-pVTZ-F12', "
                         "'auxiliary_basis': 'aug-cc-pVTZ/C cc-pVTZ-F12-CABS'}, factor=0.98, "
                         "source='Calculated using the Truhlar method')>")
