"""
TCKDB backend app tests models test_LJ module
"""

from tckdb.backend.app.models.LJ import LJ


def test_LJ_model():
    """Test creating an instance of LJ"""
    lj_1 = LJ(sigma=(4.467, "angstroms"), epsilon=(387.557, "K"))
    assert lj_1.sigma == (4.467, "angstroms")
    assert lj_1.epsilon == (387.557, "K")
    assert (
        str(lj_1) == "<LJ(id=None, sigma=(4.467, 'angstroms'), epsilon=(387.557, 'K'))>"
    )
