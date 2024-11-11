"""
TCKDB backend app tests models test_trans module
"""

from tckdb.backend.app.models.trans import Trans


def test_trans_model():
    """Test creating an instance of Trans"""
    trans_1 = Trans(
        model="Single Exponential Down",
        parameters={"alpha0": (175, "cm^-1"), "T0": (300, "K"), "n": 0.52},
    )
    assert trans_1.model == "Single Exponential Down"
    assert trans_1.parameters == {"alpha0": (175, "cm^-1"), "T0": (300, "K"), "n": 0.52}
    assert (
        repr(trans_1)
        == "<Trans(id=None, model=Single Exponential Down, parameters={'alpha0': (175, 'cm^-1'), "
        "'T0': (300, 'K'), 'n': 0.52})>"
    )
    assert (
        str(trans_1)
        == "<Trans(model='Single Exponential Down', parameters={'alpha0': (175, 'cm^-1'), "
        "'T0': (300, 'K'), 'n': 0.52})>"
    )
