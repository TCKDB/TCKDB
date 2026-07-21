from app.chemistry.species import derive_term_symbol


def test_closed_shell_term_symbol_requires_recognized_point_group() -> None:
    assert derive_term_symbol(1, point_group="C2v") is None
    assert derive_term_symbol(1, point_group="C2v", is_closed_shell=True) == "1A1"
    assert (
        derive_term_symbol(1, point_group="Dinfh", is_closed_shell=True)
        == "1Sigma_g+"
    )
    assert (
        derive_term_symbol(1, point_group="unknown", is_closed_shell=True) is None
    )


def test_open_shell_term_symbol_is_not_inferred_from_symmetry() -> None:
    # O2(X 3Sigma_g-) must not be mislabeled as the totally symmetric
    # 3Sigma_g+ state solely from multiplicity and Dinfh symmetry.
    assert derive_term_symbol(3, point_group="Dinfh", is_linear=True) is None

    # Likewise, C2v plus triplet multiplicity cannot distinguish the spatial
    # state of triplet methylene (X 3B1).
    assert derive_term_symbol(3, point_group="C2v", is_linear=False) is None


def test_linearity_or_missing_symmetry_is_not_an_identity_grade_term_symbol() -> None:
    assert derive_term_symbol(1, is_linear=True) is None
    assert derive_term_symbol(1, is_linear=False) is None
    assert derive_term_symbol(1, is_linear=None) is None
