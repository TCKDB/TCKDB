"""
TCKDB backend app tests schemas test_LJ module
"""

import unittest

from pydantic import ValidationError

from tckdb.backend.app.schemas.LJ import LJBase


class TestLJBaseSchema(unittest.TestCase):
    """
    Contains unit tests for the LJBase schema
    """

    def test_LJ_schema(self):
        """Test creating an instance of LJ"""
        LJ_1 = LJBase(sigma=(4.467, 'angstroms'),
                      epsilon=(387.557, 'K'),
                      )
        self.assertEqual(LJ_1.sigma, (4.467, 'angstroms'))
        self.assertEqual(LJ_1.epsilon, (387.557, 'K'))

        LJ_2 = LJBase(sigma=('4.467', 'angstroms'),
                      epsilon=('387.557', 'K'),
                      )
        self.assertEqual(LJ_2.sigma, (4.467, 'angstroms'))
        self.assertEqual(LJ_2.epsilon, (387.557, 'K'))

        with self.assertRaises(ValidationError):
            # wrong length
            LJBase(sigma=(4.467, 'angstroms'), epsilon=(387.557, 'K', 'LJ'))
        with self.assertRaises(ValidationError):
            # not a float
            LJBase(sigma=([4, 6, 7], 'angstroms'), epsilon=(387.557, 'K'))
        with self.assertRaises(ValidationError):
            # not a string
            LJBase(sigma=(4.467, [4, 6, 7]), epsilon=(387.557, 'K'))
        with self.assertRaises(ValidationError):
            # missing sigma
            LJBase(epsilon=(387.557, 'K'))
        with self.assertRaises(ValidationError):
            # missing epsilon
            LJBase(sigma=(4.467, 'angstroms'))
