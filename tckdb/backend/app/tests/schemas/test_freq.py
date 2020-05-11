"""
TCKDB backend app tests schemas test_freq module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.freq import FreqBase


def test_freq_schema():
    """Test creating an instance of Literature"""
    freq_1 = FreqBase(factor=0.99 * 1.014, level_id=1,
                      source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, '
                             'DOI: 10.1063/1.477924'
                      )
    assert freq_1.factor == 1.00386

    freq_2 = FreqBase(factor=0.97, level_id=1,
                      source='Calculated using the Truhlar method'
                      )
    assert freq_2.factor == 0.97
    assert freq_2.source == 'Calculated using the Truhlar method'

    with pytest.raises(ValidationError):
        # no factor
        FreqBase(level_id=1, source='Calculated using the Truhlar method')
    with pytest.raises(ValidationError):
        # no level_id
        FreqBase(factor=1.01, source='Calculated using the Truhlar method')
    with pytest.raises(ValidationError):
        # no source
        FreqBase(factor=0.95, level_id=1)
    with pytest.raises(ValidationError):
        # negative factor
        FreqBase(factor=-0.95, level_id=1)
    with pytest.raises(ValidationError):
        # large factor
        FreqBase(factor=95, level_id=1)
