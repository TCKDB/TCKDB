"""
TCKDB backend app tests schemas test_LJ module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.LJ import LJBase


def test_lj_schema():
    """Test creating an instance of LJ"""
    lj_1 = LJBase(sigma=(4.467, 'angstroms'),
                  epsilon=(387.557, 'K'),
                  )
    assert lj_1.sigma == (4.467, 'angstroms')
    assert lj_1.epsilon == (387.557, 'K')

    lj_2 = LJBase(sigma=('4.467', 'angstroms'),
                  epsilon=('387.557', 'K'),
                  )
    assert lj_2.sigma == (4.467, 'angstroms')
    assert lj_2.epsilon == (387.557, 'K')

    with pytest.raises(ValidationError):
        # wrong length
        LJBase(sigma=(4.467, 'angstroms'), epsilon=(387.557, 'K', 'LJ'))
    with pytest.raises(ValidationError):
        # not a float
        LJBase(sigma=([4, 6, 7], 'angstroms'), epsilon=(387.557, 'K'))
    with pytest.raises(ValidationError):
        # not a string
        LJBase(sigma=(4.467, [4, 6, 7]), epsilon=(387.557, 'K'))
    with pytest.raises(ValidationError):
        # missing sigma
        LJBase(epsilon=(387.557, 'K'))
    with pytest.raises(ValidationError):
        # missing epsilon
        LJBase(sigma=(4.467, 'angstroms'))
