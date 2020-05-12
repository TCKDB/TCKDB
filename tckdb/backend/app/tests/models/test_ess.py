"""
TCKDB backend app tests models test_ess module
"""

from tckdb.backend.app.models.ess import ESS


def test_ess_model():
    """Test creating an instance of ESS"""
    ess_1 = ESS(name='Psi4',
                version='1.1',
                url='http://www.psicode.org/')
    assert ess_1.name == 'Psi4'
    assert ess_1.version == '1.1'
    assert ess_1.revision is None
    assert ess_1.url == 'http://www.psicode.org/'
    assert str(ess_1) == "Psi4 1.1"
    assert repr(ess_1) == "<ESS(id=None, name='Psi4', version='1.1', url='http://www.psicode.org/')>"

    ess_1 = ESS(name='Gaussian',
                version='16',
                revision='C.01',
                url='https://gaussian.com/')
    assert ess_1.name == 'Gaussian'
    assert ess_1.version == '16'
    assert ess_1.revision == 'C.01'
    assert ess_1.url == 'https://gaussian.com/'
    assert str(ess_1) == "Gaussian 16"
    assert repr(ess_1) == "<ESS(id=None, name='Gaussian', version='16', revision='C.01', url='https://gaussian.com/')>"
