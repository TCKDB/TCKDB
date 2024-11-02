"""
TCKDB backend app tests models test_freq module
"""

from tckdb.backend.app.models.freqscale import FreqScale


def test_freq_model():
    """Test creating an instance of Freq"""
    freq1 = FreqScale(
        factor=0.99 * 1.014,
        source="J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924",
    )
    assert freq1.factor == 0.99 * 1.014
    assert (
        freq1.source
        == "J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924"
    )
    assert (
        repr(freq1)
        == "<Freq(id=None, factor=1.00386, level_id=None, source='J.A. Montgomery, M.J. Frisch, "
        "J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924')>"
    )
    assert (
        str(freq1) == "<Freq(factor=1.00386, level_id=None, source='J.A. Montgomery, "
        "M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827, DOI: 10.1063/1.477924')>"
    )

    freq2 = FreqScale(factor=0.98, source="Calculated using the Truhlar method")
    assert (
        str(freq2)
        == "<Freq(factor=0.98, level_id=None, source='Calculated using the Truhlar method')>"
    )

    freq3 = FreqScale(factor=0.98, source="Calculated using the Truhlar method")
    assert (
        str(freq3)
        == "<Freq(factor=0.98, level_id=None, source='Calculated using the Truhlar method')>"
    )

    freq4 = FreqScale(factor=0.98, source="Calculated using the Truhlar method")
    assert (
        str(freq4)
        == "<Freq(factor=0.98, level_id=None, source='Calculated using the Truhlar method')>"
    )
