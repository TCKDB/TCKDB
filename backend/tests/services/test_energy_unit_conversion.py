"""Tests for the molar-energy → hartree conversion used by the read layer.

The read layer serves an applied energy correction's stored value beside
the same quantity in hartree, so a consumer can add it to a calculation's
``electronic_energy_hartree`` without carrying a conversion factor. That
makes the arithmetic here part of the wire contract, and it is pinned
against externally known pairs rather than against itself.
"""

from __future__ import annotations

import pytest

from app.chemistry.units import HARTREE_TO_KJ_MOL, convert_energy_to_hartree
from app.db.models.common import EnergyUnit

#: 1 hartree in kcal/mol, to the precision the constant supports. Not
#: derived from ``HARTREE_TO_KJ_MOL`` in the assertion below — it is the
#: independently quotable figure the conversion has to reproduce, so
#: deriving it would make the test agree with any constant at all.
HARTREE_IN_KCAL_MOL = 627.5094740630797

#: 1 hartree in kJ/mol. Same reasoning.
HARTREE_IN_KJ_MOL = 2625.4996394798254


class TestConvertEnergyToHartree:
    def test_hartree_is_exact_identity(self) -> None:
        """The pivot must not cost precision on the unit that needs none."""
        for value in (0.0, 1.0, -78.62375631, 630.0442600297998):
            assert convert_energy_to_hartree(value, EnergyUnit.hartree) == value

    def test_kcal_mol_against_known_pair(self) -> None:
        result = convert_energy_to_hartree(
            HARTREE_IN_KCAL_MOL, EnergyUnit.kcal_mol
        )
        assert result == pytest.approx(1.0, abs=1e-12)

    def test_kj_mol_against_known_pair(self) -> None:
        result = convert_energy_to_hartree(HARTREE_IN_KJ_MOL, EnergyUnit.kj_mol)
        assert result == pytest.approx(1.0, abs=1e-12)

    def test_sign_is_preserved(self) -> None:
        """A negative correction converts to a negative addend.

        The sign is the difference between a correction that lowers an
        energy and one that raises it, and no scaling may flip it.
        """
        result = convert_energy_to_hartree(
            -HARTREE_IN_KCAL_MOL, EnergyUnit.kcal_mol
        )
        assert result == pytest.approx(-1.0, abs=1e-12)

    def test_real_deposited_bac_value(self) -> None:
        """A magnitude taken from the hosted instance's own data.

        ``-1.4083247988699648 kcal/mol`` is the C-H contribution of a
        deposited Petersson BAC. In hartree it is a couple of
        millihartree — which is the point of serving it converted: added
        to a ``-78.6 hartree`` electronic energy in its stored unit it
        would be wrong by a factor of 627.
        """
        result = convert_energy_to_hartree(
            -1.4083247988699648, EnergyUnit.kcal_mol
        )
        assert result == pytest.approx(-0.0022443084241441243, rel=1e-12)

    def test_every_energy_unit_member_converts(self) -> None:
        """No member of the enum may silently return ``None``.

        A unit the read layer cannot convert serves ``None``, which is
        honest but useless. This test is what makes adding a member to
        ``EnergyUnit`` without deciding its factor a failing build rather
        than a quietly nulled field.
        """
        assert list(EnergyUnit), "EnergyUnit must not be empty"
        for unit in EnergyUnit:
            assert convert_energy_to_hartree(1.0, unit) is not None, unit

    def test_constant_matches_the_other_two_copies(self) -> None:
        """The three copies of this constant must not drift apart."""
        from app.importers.cccbdb.normalizers.units import _HARTREE_TO_KJ_MOL
        from app.services.scientific_read.ml_dataset import (
            HARTREE_TO_KJ_MOL as ML_HARTREE_TO_KJ_MOL,
        )

        assert HARTREE_TO_KJ_MOL == _HARTREE_TO_KJ_MOL
        assert HARTREE_TO_KJ_MOL == ML_HARTREE_TO_KJ_MOL
