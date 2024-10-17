"""
TCKDB backend app tests schemas test_freq module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.freq_scale import FreqScaleCreate



def test_freq_schema():
    """Test creating an instance of Literature"""
    freq_1 = FreqScaleCreate(factor=0.99 * 1.014,
                             level={
                                    'method': 'B3LYP',
                             },
                      source='J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, '
                             'DOI: 10.1063/1.477924'
                      )
    assert freq_1.factor == 1.00386

    freq_2 = FreqScaleCreate(factor=0.97,
                             level={
                                    'method': 'B3LYP',
                                },
                      source='Calculated using the Truhlar method'
                      )
    assert freq_2.factor == 0.97
    assert freq_2.source == 'Calculated using the Truhlar method'
    assert freq_2.level.method == 'b3lyp'

    with pytest.raises(ValidationError):
        # no factor
        FreqScaleCreate(source='Calculated using the Truhlar method')
    with pytest.raises(ValidationError):
        # no level
        FreqScaleCreate(factor=1.01, source='Calculated using the Truhlar method')
    with pytest.raises(ValidationError):
        # no source
        FreqScaleCreate(factor=0.95)
    with pytest.raises(ValidationError):
        # negative factor
        FreqScaleCreate(factor=-0.95)
    with pytest.raises(ValidationError):
        # large factor
        FreqScaleCreate(factor=95)
