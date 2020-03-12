"""
TCKDB backend app tests models test_trans module
"""

import unittest

from tckdb.backend.app.models.trans import Trans


class TestTransModel(unittest.TestCase):
    """
    Contains unit tests for the Trans module
    """

    def test_trans_model(self):
        """Test creating an instance of Trans"""
        trans_1 = Trans(model='Single Exponential Down',
                        parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)})
        self.assertEqual(trans_1.model, 'Single Exponential Down')
        self.assertEqual(trans_1.parameters, {'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)})
