"""
TCKDB backend app tests models test_LJ module
"""

import unittest

from tckdb.backend.app.models.LJ import LJ


class TestLJModel(unittest.TestCase):
    """
    Contains unit tests for the LJ module
    """

    def test_LJ_model(self):
        """Test creating an instance of LJ"""
        LJ_1 = LJ(sigma=(4.467, 'angstroms'), epsilon=(387.557, 'K'))
        self.assertEqual(LJ_1.sigma, (4.467, 'angstroms'))
        self.assertEqual(LJ_1.epsilon, (387.557, 'K'))
        self.assertEqual(str(LJ_1), "<LJ(id=None, sigma=(4.467, 'angstroms'), epsilon=(387.557, 'K'))>")
