"""
TCKDB backend app tests schemas test_trans module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.trans import TransBase, TransModelEnum


def test_trans_model_enum():
    """Test TransModelEnum"""
    TransModelEnum('Single Exponential Down')
    with pytest.raises(ValueError):
        TransModelEnum('unsupported model')


def test_trans_schema():
    """Test creating an instance of Trans"""
    trans_1 = TransBase(model='Single Exponential Down',
                        parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)},
                        )
    assert trans_1.model == 'Single Exponential Down'
    assert trans_1.parameters == {'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)}

    with pytest.raises(ValidationError):
        # wrong model
        TransBase(model='wrong', parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)})
    with pytest.raises(ValidationError):
        # wrong parametrs type
        TransBase(model='Single Exponential Down', parameters=(175, 'cm^-1'))
    with pytest.raises(ValidationError):
        # no alpha0
        TransBase(model='Single Exponential Down', parameters={'T0': (300, 'K'), 'n': (0.52,)})
    with pytest.raises(ValidationError):
        # no T0
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), 'n': (0.52,)})
    with pytest.raises(ValidationError):
        # no n
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K')})
    with pytest.raises(ValidationError):
        # unsipported parameter key
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'),
                                                               'n': (0.52,), 'unsupported': (175, 'cm^-1')})
    with pytest.raises(ValidationError):
        # non tuple value
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'),
                                                               'T0': (300, 'K'), 'n': 0.52})
    with pytest.raises(ValidationError):
        # long tuple
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'),
                                                               'T0': (300, 'K', 'J'), 'n': (0.52,)})
    with pytest.raises(ValidationError):
        # non float first value
        TransBase(model='Single Exponential Down', parameters={'alpha0': ('175', 'cm^-1'),
                                                               'T0': (300, 'K'), 'n': (0.52,)})
    with pytest.raises(ValidationError):
        # non string second value
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 15),
                                                               'T0': (300, 'K'), 'n': (0.52,)})
    with pytest.raises(ValidationError):
        # units for n
        TransBase(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'),
                                                               'T0': (300, 'K'), 'n': (0.52, 'units')})
