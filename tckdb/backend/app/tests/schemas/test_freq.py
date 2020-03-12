"""
TCKDB backend app tests schemas test_freq module
"""

import unittest

from pydantic import ValidationError

from tckdb.backend.app.schemas.freq import FreqBase


class TestFreqBaseSchema(unittest.TestCase):
    """
    Contains unit tests for the FreqBase schema
    """

    def test_freq_schema(self):
        """Test creating an instance of Literature"""
        freq_1 = FreqBase(level={'method': 'CBS-QB3'},
                          factor=0.99 * 1.014,
                          source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, '
                                 'DOI: 10.1063/1.477924'
                          )
        self.assertEqual(freq_1.level, {'method': 'cbs-qb3'})

        freq_2 = FreqBase(level={'method': 'wB97xd', 'basis': 'def2TZVP',
                                 'solVation': {'Method': 'PCM', 'solvent': 'Water'}},
                          factor=0.97,
                          source='Calculated using the Truhlar method'
                          )
        self.assertEqual(freq_2.level, {'method': 'wb97xd', 'basis': 'def2tzvp',
                                        'solvation': {'method': 'pcm', 'solvent': 'water'}})
        self.assertEqual(freq_2.factor, 0.97)
        self.assertEqual(freq_2.source, 'Calculated using the Truhlar method')

        with self.assertRaises(ValidationError):
            # no level
            FreqBase(factor=0.95, source='Calculated using the Truhlar method')
        with self.assertRaises(ValidationError):
            # no factor
            FreqBase(level={'method': 'cbs-qb3'}, source='Calculated using the Truhlar method')
        with self.assertRaises(ValidationError):
            # no source
            FreqBase(level={'method': 'cbs-qb3'}, factor=0.95)
        with self.assertRaises(ValidationError):
            # no method in level
            FreqBase(level={'basis': 'def2TZVP'}, factor=0.95)
        with self.assertRaises(ValidationError):
            # wrong solvation type
            FreqBase(level={'method': 'b3lyp', 'basis': 'def2TZVP', 'solvation': 'pcm'}, factor=0.95)
        with self.assertRaises(ValidationError):
            # no method in solvation
            FreqBase(level={'method': 'b3lyp', 'basis': 'def2TZVP', 'solvation': {'solvent': 'water'}}, factor=0.95)
        with self.assertRaises(ValidationError):
            # no solvent in solvation
            FreqBase(level={'method': 'b3lyp', 'basis': 'def2TZVP', 'solvation': {'method': 'pcm'}}, factor=0.95)
        with self.assertRaises(ValidationError):
            # illegal key in solvation
            FreqBase(level={'method': 'b3lyp', 'basis': 'def2TZVP', 'solvation':
                {'method': 'pcm', 'solvent': 'water', 'illegal key': 'value'}}, factor=0.95)
        with self.assertRaises(ValidationError):
            # illegal key in level
            FreqBase(level={'method': 'b3lyp', 'basis': 'def2TZVP', 'illegal key': 'value'}, factor=0.95)
