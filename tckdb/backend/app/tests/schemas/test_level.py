"""
TCKDB backend app tests schemas test_level module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.level import LevelCreate


def test_level_schema():
    """Test creating an instance of Level"""
    level1 = LevelCreate(method="CBS-QB3", grid="UltraFine")
    assert level1.method == "cbs-qb3"  # lowered
    assert level1.basis is None
    assert level1.auxiliary_basis is None
    assert level1.dispersion is None
    assert level1.grid == "UltraFine"
    assert level1.solvation_method is None
    assert level1.solvent is None
    assert level1.solvation_description is None

    level2 = LevelCreate(
        method="DLPNO-CCSD(T)-F12",
        basis="cc-pVTZ-F12",
        auxiliary_basis="aug-cc-pVTZ/C cc-pVTZ-F12-CABS",
        level_arguments="normal-PNO",
        solvation_description="APFD/6-311+G(2d,p) SMD water "
        "e_elect = e_original + sp_e_sol_corrected - sp_e_uncorrected",
    )
    assert level2.method == "dlpno-ccsd(t)-f12"
    assert level2.basis == "cc-pvtz-f12"
    assert level2.auxiliary_basis == "aug-cc-pvtz/c cc-pvtz-f12-cabs"
    assert level2.level_arguments == "normal-PNO"
    assert level2.dispersion is None
    assert level2.solvation_method is None
    assert level2.solvent is None
    assert (
        level2.solvation_description == "APFD/6-311+G(2d,p) SMD water "
        "e_elect = e_original + sp_e_sol_corrected - sp_e_uncorrected"
    )

    with pytest.raises(ValidationError):
        # no method
        LevelCreate(basis="B3lyp")
    with pytest.raises(ValidationError):
        # slash in method
        LevelCreate(method="b3lyp/6-311g+(2d,2p)")
    with pytest.raises(ValidationError):
        # slash in basis
        LevelCreate(method="b3lyp", basis="/6-311g+(2d,2p)")
    with pytest.raises(ValidationError):
        # slash in dispersion
        LevelCreate(method="b3lyp", basis="6-311g+(2d,2p)", dispersion="gd3bj/")
    with pytest.raises(ValidationError):
        # slash in solvation_method
        LevelCreate(
            method="b3lyp",
            basis="6-311g+(2d,2p)",
            solvation_method="SMD/",
            solvent="methanol",
        )
    with pytest.raises(ValidationError):
        # solvation method with no solvent
        LevelCreate(method="wb97xd", basis="def2-tzvp", solvation_method="SMD")
