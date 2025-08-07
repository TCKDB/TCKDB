"""Tests for the VDW model"""

from tckdb.backend.app.models.VDW import VDW, VDWEntry


def test_vdw_model():
    """Test creating a VDW well with an entry"""
    vdw = VDW(
        inchi_augmented="InChI=1/AB",
        constituents=[1, 2],
        charge=0,
        multiplicity=1,
        labels=["A···B"],
    )
    entry = VDWEntry(vdw=vdw, energy=-0.5)
    assert vdw.constituents == [1, 2]
    assert vdw.entries[0] is entry
    assert entry.energy == -0.5
    assert str(vdw) == "<VDW(id=None, inchi_augmented='InChI=1/AB')>"
