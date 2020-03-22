"""
TCKDB backend app tests schemas test_freq module
"""

import pytest
from tckdb.backend.app.schemas.common import lowercase_dict


def test_lowercase_dict():
    """Test attaining a dictionary with lowercase keys and values"""
    d_1 = {'D': 1}
    assert lowercase_dict(d_1) == {'d': 1}
    d_2 = {'D': 'Z'}
    assert lowercase_dict(d_2) == {'d': 'z'}
    d_3 = {5: 'Z'}
    assert lowercase_dict(d_3) == {5: 'z'}
    d_4 = {'D': {'A': 'Z', 'v': 'n', 'H': 54, 'f': 'L', 7: 0, 8: {'Q': 9}}}
    assert lowercase_dict(d_4) == {'d': {'a': 'z', 'v': 'n', 'h': 54, 'f': 'l', 7: 0, 8: {'q': 9}}}
    with pytest.raises(TypeError):
        lowercase_dict('D')
