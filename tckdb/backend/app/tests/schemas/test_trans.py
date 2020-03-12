"""
TCKDB backend app tests schemas test_trans module
"""

import unittest

from pydantic import ValidationError

from tckdb.backend.app.schemas.trans import TransBase, TransModelEnum


class TestTransModelEnum(unittest.TestCase):
    """
    Contains unit tests for the TransModelEnum class
    """

    def test_trans_model_enum(self):
        """Test TransModelEnum"""
        TransModelEnum('Single Exponential Down')
        with self.assertRaises(ValueError):
            TransModelEnum('unsupported model')


class TestTransBaseSchema(unittest.TestCase):
    """
    Contains unit tests for the TransBase schema
    """

    def test_trans_schema(self):
        """Test creating an instance of Trans"""
        trans_1 = TransBase(model='Single Exponential Down',
                            parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)},
                            )
        self.assertEqual(trans_1.model, 'Single Exponential Down')
        self.assertEqual(trans_1.parameters, {'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)})

        with self.assertRaises(ValidationError):
            # wrong model
            TransBase(model='wrong', parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)})
        with self.assertRaises(ValidationError):
            # wrong parametrs type
            TransBase(model='Single Exponential Down', parameters=(175, 'cm^-1'))
        with self.assertRaises(ValidationError):
            # no alpha0
            TransBase(model='Single Exponential Down', parameters={'T0': (300, 'K'), 'n': (0.52,)})
        with self.assertRaises(ValidationError):
            # no T0
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), 'n': (0.52,)})
        with self.assertRaises(ValidationError):
            # no n
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K')})
        with self.assertRaises(ValidationError):
            # unsipported parameter key
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'),
                                                                   'n': (0.52,), 'unsupported': (175, 'cm^-1')})
        with self.assertRaises(ValidationError):
            # non tuple value
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'),
                                                                   'T0': (300, 'K'), 'n': 0.52})
        with self.assertRaises(ValidationError):
            # long tuple
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'),
                                                                   'T0': (300, 'K', 'J'), 'n': (0.52,)})
        with self.assertRaises(ValidationError):
            # non float first value
            TransBase(model='Single Exponential Down', parameters={'alpha0': ('175', 'cm^-1'),
                                                                   'T0': (300, 'K'), 'n': (0.52,)})
        with self.assertRaises(ValidationError):
            # non string second value
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 15),
                                                                   'T0': (300, 'K'), 'n': (0.52,)})
        with self.assertRaises(ValidationError):
            # units for n
            TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'),
                                                                   'T0': (300, 'K'), 'n': (0.52, 'units')})
