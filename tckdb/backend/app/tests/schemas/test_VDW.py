"""Tests for the VDW schema"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.VDW import VDWBase


def test_vdw_schema():
    """Test creating a VDWBase instance"""
    vdw = VDWBase(
        inchi_augmented="InChI=1/AB",
        constituents=[1, 2],
        charge=0,
        multiplicity=1,
        labels=["A···B"],
    )
    assert vdw.constituents == [1, 2]
    assert vdw.charge == 0
    assert vdw.multiplicity == 1

    with pytest.raises(ValidationError):
        VDWBase(
            inchi_augmented="InChI=1/AB",
            charge=0,
            multiplicity=1,
        )
