"""An xtb output uploaded to an opt calculation: stored, recognised as xtb, nothing extracted.

xtb's ``g98.out`` is written in Gaussian 98's layout and prints the optimized geometry under "Standard orientation".
It must not be read as the calculation's *input* geometry (it was, while its Gaussian header won the sniff).
"""

from __future__ import annotations

from pathlib import Path

from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_artifact_input_geometry_hook import (  # noqa: F401  (stub_store_artifact is a fixture)
    _artifact,
    _create_opt_calc,
    _input_links,
    stub_store_artifact,
)

XTB = Path(__file__).resolve().parent.parent / "fixtures" / "xtb"
ETHANOL_XYZ = "9\nethanol\n" + "\n".join(
    f"{el} {i * 5.0:.4f} 0.0 0.0" for i, el in enumerate(["C", "C", "O", "H", "H", "H", "H", "H", "H"])
)


def _post(client, calc_id: int, filename: str):
    content = (XTB / filename).read_bytes()
    return client.post(
        artifacts_url(client, calc_id),
        json={"artifacts": [_artifact(content, kind="output_log", filename=filename)]},
    )


def test_g98_out_is_accepted_and_stores_no_input_geometry(client, db_session, stub_store_artifact):
    calc_id = _create_opt_calc(client, smiles="CCO", conformer_xyz=ETHANOL_XYZ, software="xtb")
    resp = _post(client, calc_id, "g98_gfn2_ethanol.out")
    assert resp.status_code == 201, resp.text
    assert _input_links(db_session, calc_id) == []


def test_xtb_out_is_accepted_and_stores_no_input_geometry(client, db_session, stub_store_artifact):
    calc_id = _create_opt_calc(client, smiles="CCO", conformer_xyz=ETHANOL_XYZ, software="xtb")
    resp = _post(client, calc_id, "opt_freq_gfn2_ethanol.out")
    assert resp.status_code == 201, resp.text
    assert _input_links(db_session, calc_id) == []
