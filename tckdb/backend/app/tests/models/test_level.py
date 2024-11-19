"""
TCKDB backend app tests models test_level module
"""

from tckdb.backend.app.models.level import Level


def test_level_model():
    """Test creating an instance of Level"""
    level1 = Level(method="cbs-qb3")
    assert level1.method == "cbs-qb3"
    assert level1.basis is None
    assert level1.auxiliary_basis is None
    assert level1.dispersion is None
    assert level1.grid is None
    assert level1.level_arguments is None
    assert level1.solvation_method is None
    assert level1.solvent is None
    assert level1.solvation_description is None
    assert level1.reviewer_flags is None
    assert repr(level1) == "<Level(id=None, method='cbs-qb3')>"
    assert str(level1) == "cbs-qb3"

    level2 = Level(
        method="wB97xd",
        basis="def2TZVP",
        solvation_method="PCM",
        solvent="water",
        grid="UltraFine",
    )
    assert level2.method == "wB97xd"
    assert level2.basis == "def2TZVP"
    assert level2.auxiliary_basis is None
    assert level2.dispersion is None
    assert level2.solvation_method == "PCM"
    assert level2.solvent == "water"
    assert (
        repr(level2)
        == "<Level(id=None, method='wB97xd', basis='def2TZVP', grid='UltraFine', "
        "solvation_method=PCM, solvent=water)>"
    )
    assert str(level2) == "wB97xd/def2TZVP UltraFine solvation: PCM in water"

    level3 = Level(method="B3LYP", basis="6-31G(d,p)", dispersion="gd3bj")
    assert (
        repr(level3)
        == "<Level(id=None, method='B3LYP', basis='6-31G(d,p)', dispersion='gd3bj')>"
    )
    assert str(level3) == "B3LYP/6-31G(d,p) gd3bj"

    level4 = Level(
        method="DLPNO-CCSD(T)-F12",
        basis="cc-pVTZ-F12",
        auxiliary_basis="aug-cc-pVTZ/C cc-pVTZ-F12-CABS",
        level_arguments="tight-PNO",
        solvation_description="APFD/6-311+G(2d,p) SMD water "
        "e_elect = e_original + sp_e_sol_corrected - sp_e_uncorrected",
    )
    assert level4.method == "DLPNO-CCSD(T)-F12"
    assert level4.basis == "cc-pVTZ-F12"
    assert level4.auxiliary_basis == "aug-cc-pVTZ/C cc-pVTZ-F12-CABS"
    assert level4.level_arguments == "tight-PNO"
    assert (
        level4.solvation_description == "APFD/6-311+G(2d,p) SMD water "
        "e_elect = e_original + sp_e_sol_corrected - sp_e_uncorrected"
    )
    assert (
        repr(level4)
        == "<Level(id=None, method='DLPNO-CCSD(T)-F12', basis='cc-pVTZ-F12', "
        "auxiliary_basis='aug-cc-pVTZ/C cc-pVTZ-F12-CABS', level_arguments='tight-PNO', "
        "solvation_description=APFD/6-311+G(2d,p) SMD water e_elect = e_original + "
        "sp_e_sol_corrected - sp_e_uncorrected)>"
    )
    assert (
        str(level4)
        == "DLPNO-CCSD(T)-F12/cc-pVTZ-F12/aug-cc-pVTZ/C cc-pVTZ-F12-CABS tight-PNO APFD/6-311+G(2d,p) "
        "SMD water e_elect = e_original + sp_e_sol_corrected - sp_e_uncorrected"
    )
