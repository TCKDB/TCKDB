"""
TCKDB backend app tests schemas test_ess module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.ess import ESSBase


def test_ess_schema():
    """Test creating an instance of ESS"""
    ess_1 = ESSBase(name='QChem', version='5.3', url='https://www.q-chem.com/')
    assert ess_1.name == 'QChem'
    assert ess_1.version == '5.3'
    assert ess_1.url == 'https://www.q-chem.com/'

    with pytest.raises(ValidationError):
        # wrong url (no dot)
        ESSBase(name='QChem', version='5.3', url='https://wwwq-chem>com/')
    with pytest.raises(ValidationError):
        # wrong url (has space)
        ESSBase(name='QChem', version='5.3', url='https://www.q-chem com/')
