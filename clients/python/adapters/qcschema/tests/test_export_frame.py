"""A Hessian export declares its Cartesian frame fixed (issue #573).

The exported Hessian is expressed in the exported coordinates' axes. With
``fix_com``/``fix_orientation`` false (qcelemental's default), a consumer is
told it may recentre and rotate the molecule, which would detach the matrix
from its frame. These tests export the pinned ``hessian_v1`` read corpus
through :class:`test_round_trip.StubClient`, re-validate the result through
qcelemental, and check that the frame survives: the flags, the coordinates,
and the program input qcelemental writes for the consumer.
"""

from __future__ import annotations

import numpy as np
import qcelemental.models.v2 as qcel_v2
from tckdb_qcschema.exporter import export_calculation
from test_round_trip import StubClient


def _export(read_case: str) -> dict:
    client = StubClient(read_case)
    handle = client.calculation["record"]["calculation"].get("calculation_id") or 1
    return export_calculation(client, handle)


def test_hessian_export_declares_both_molecules_frame_fixed() -> None:
    exported = _export("hessian_v1")
    assert exported["input_data"]["specification"]["driver"] == "hessian"
    for molecule in (exported["molecule"], exported["input_data"]["molecule"]):
        assert molecule["fix_com"] is True
        assert molecule["fix_orientation"] is True


def test_the_frame_survives_qcelemental_revalidation() -> None:
    """Round trip: export -> JSON -> qcelemental ``AtomicResult`` -> a fully
    re-validated ``Molecule`` (qcelemental's molparse path) -> Psi4 input.

    The coordinates come back bit-identical, the flags stay set, and the
    program input qcelemental writes carries ``no_com``/``no_reorient``, so
    the ESS a consumer runs keeps the frame the Hessian is expressed in.
    """
    exported = _export("hessian_v1")
    exported_geometry = np.asarray(exported["molecule"]["geometry"], dtype=float).reshape(-1, 3)

    result = qcel_v2.AtomicResult.model_validate(exported)
    revalidated = qcel_v2.Molecule(
        validate=True, geometry_noise=14, **result.molecule.model_dump()
    )

    assert revalidated.fix_com is True
    assert revalidated.fix_orientation is True
    assert np.array_equal(revalidated.geometry, exported_geometry)
    assert np.array_equal(
        np.asarray(result.return_result, dtype=float).reshape(-1),
        np.asarray(exported["return_result"], dtype=float).reshape(-1),
    )

    psi4_input = revalidated.to_string(dtype="psi4")
    assert "no_com" in psi4_input
    assert "no_reorient" in psi4_input

    # The flags carry information here: the stored frame is not the one
    # qcelemental would choose on its own, so a consumer allowed to
    # reorient would move the atoms away from the Hessian's axes.
    reoriented = result.molecule.orient_molecule()
    assert not np.allclose(reoriented.geometry, exported_geometry, atol=1e-6)


def test_energy_export_keeps_the_default_free_frame() -> None:
    """An energy is frame-invariant: an sp export claims nothing about it."""
    exported = _export("energy_v1")
    assert exported["input_data"]["specification"]["driver"] == "energy"
    for molecule in (exported["molecule"], exported["input_data"]["molecule"]):
        assert molecule.get("fix_com", False) is False
        assert molecule.get("fix_orientation", False) is False
