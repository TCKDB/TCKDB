"""``POST /uploads/computed-species``: E and Z sigma-vinyl radicals stay apart.

1-propenyl (``[CH]=CC``) deposited with an E geometry and with a Z geometry is
one species and two species entries. SMILES cannot say which isomer was
computed; the geometry is the only source of the label.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.common import StereoKind
from app.db.models.species import SpeciesEntry
from tests.services.test_sigma_vinyl_stereo import _PROPENYL_E, _PROPENYL_Z

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}


def _bundle(xyz: str) -> dict:
    return {
        "species_entry": {"smiles": "[CH]=CC", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": xyz},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }


def test_e_and_z_propenyl_upload_to_two_entries(client: TestClient, db_session) -> None:
    ids = {}
    for name, xyz in (("E", _PROPENYL_E), ("Z", _PROPENYL_Z)):
        resp = client.post("/api/v1/uploads/computed-species", json=_bundle(xyz))
        assert resp.status_code == 201, resp.text[:800]
        ids[name] = resp.json()["species_entry_id"]

    assert ids["E"] != ids["Z"]
    entries = {
        name: db_session.scalar(select(SpeciesEntry).where(SpeciesEntry.id == entry_id))
        for name, entry_id in ids.items()
    }
    assert entries["E"].species_id == entries["Z"].species_id
    assert entries["E"].species.stereo_kind == StereoKind.ez_isomer
    assert {name: e.stereo_label for name, e in entries.items()} == {"E": "E", "Z": "Z"}
