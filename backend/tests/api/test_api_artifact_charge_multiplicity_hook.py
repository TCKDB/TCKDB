"""End-to-end tests for the charge / multiplicity artifact hook.

Proves the contradiction check fires on the paths where an output log is
actually uploaded: the dedicated artifacts route and inline bundle uploads.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# A real Gaussian UB3LYP log whose header states ``Charge = 0 Multiplicity = 3``.
GAUSSIAN_TRIPLET_LOG = (FIXTURES / "gaussian" / "sp_ub3lyp_g16.log").read_bytes()
LOG_CHARGE = 0
LOG_MULTIPLICITY = 3

# A real Molpro CH4 log: ``wf,spin=0,charge=0`` -> charge 0, multiplicity 1.
MOLPRO_CH4_LOG = (
    FIXTURES / "molpro" / "ch4_closed_shell" / "input.out"
).read_bytes()

W_CHARGE = "charge_mismatch"
W_MULT = "multiplicity_mismatch"

# Every payload below used to carry the same one-atom geometry,
# ``1\nC atom\nC 0.0 0.0 0.0``, whatever species it declared -- a lone carbon
# under methyl, methylene, methylene cation and methane alike. The geometry is
# incidental to what these tests assert (charge and multiplicity read out of a
# log header), which is exactly why nobody looked, and
# ``assert_geometry_composition_matches_identity`` now refuses it: a structure
# has to be made of the atoms its own identifier declares.
#
# Coordinates are schematic but the atoms are the molecules', per species:
_GEOMETRY_BY_SMILES = {
    # Methyl radical, CH3. Planar D3h: C at the origin (index 1), three H in
    # the z = 0 plane at 1.079 A (indices 2-4).
    "[CH3]": (
        "4\nmethyl\n"
        "C  0.0000  0.0000  0.0000\n"
        "H  1.0790  0.0000  0.0000\n"
        "H -0.5395  0.9345  0.0000\n"
        "H -0.5395 -0.9345  0.0000"
    ),
    # Methylene cation, CH2+. C at the origin (index 1), two H at 1.09 A
    # (indices 2-3); CH2+ is quasi-linear, so they sit opposite each other.
    "[CH2+]": (
        "3\nmethylene cation\n"
        "C  0.0000  0.0000  0.0000\n"
        "H  1.0900  0.0000  0.0000\n"
        "H -1.0900  0.0000  0.0000"
    ),
    # Methylene, CH2. C at the origin (index 1), two H at 1.03 A (indices 2-3)
    # with the ~134 degree H-C-H angle of the triplet ground state.
    "[CH2]": (
        "3\nmethylene\n"
        "C  0.0000  0.0000  0.0000\n"
        "H  0.4020  0.9480  0.0000\n"
        "H  0.4020 -0.9480  0.0000"
    ),
    # Methane, CH4. C at the origin (index 1), four H at the tetrahedral
    # vertices (indices 2-5).
    "C": (
        "5\nmethane\n"
        "C  0.000  0.000  0.000\n"
        "H  0.629  0.629  0.629\n"
        "H -0.629 -0.629  0.629\n"
        "H -0.629  0.629 -0.629\n"
        "H  0.629 -0.629 -0.629"
    ),
}


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _output_log(content: bytes = GAUSSIAN_TRIPLET_LOG, filename: str = "job.log") -> dict:
    return {
        "kind": "output_log",
        "filename": filename,
        "content_base64": _b64(content),
    }


def _conformer_payload(*, smiles: str, charge: int, multiplicity: int) -> dict:
    return {
        "species_entry": {
            "smiles": smiles,
            "charge": charge,
            "multiplicity": multiplicity,
        },
        "geometry": {"xyz_text": _GEOMETRY_BY_SMILES[smiles]},
        # An ``opt`` calculation keeps the single-point-energy hook out of the
        # way so the warnings under test are unambiguous.
        "calculation": {
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {"converged": True},
        },
        "label": "charge-mult-hook",
    }


def _create_calc(client, *, smiles: str, charge: int, multiplicity: int) -> int:
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles=smiles, charge=charge, multiplicity=multiplicity
        ),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _post_artifact(client, calc_id: int, artifact: dict):
    return client.post(
        f"/api/v1/calculations/{calc_id}/artifacts",
        json={"artifacts": [artifact]},
    )


def _codes(resp) -> list[str]:
    return [w["code"] for w in resp.json().get("warnings", [])]


def _relevant(resp) -> list[str]:
    return [c for c in _codes(resp) if c in {W_CHARGE, W_MULT}]


class TestChargeMultiplicityArtifactHook:
    def test_genuine_multiplicity_mismatch_is_detected(
        self, client, stub_store_artifact
    ):
        """The whole point: log says triplet, uploader declared a doublet."""
        calc_id = _create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
        resp = _post_artifact(client, calc_id, _output_log())
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == [W_MULT]

        warning = next(
            w for w in resp.json()["warnings"] if w["code"] == W_MULT
        )
        assert warning["field"] == "species_entry.multiplicity"
        assert str(LOG_MULTIPLICITY) in warning["message"]

    def test_genuine_charge_mismatch_is_detected(
        self, client, stub_store_artifact
    ):
        calc_id = _create_calc(client, smiles="[CH2+]", charge=1, multiplicity=3)
        resp = _post_artifact(client, calc_id, _output_log())
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == [W_CHARGE]

    def test_matching_values_emit_nothing(self, client, stub_store_artifact):
        calc_id = _create_calc(
            client,
            smiles="[CH2]",
            charge=LOG_CHARGE,
            multiplicity=LOG_MULTIPLICITY,
        )
        resp = _post_artifact(client, calc_id, _output_log())
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == []

    def test_program_without_a_wired_parser_emits_nothing(
        self, client, stub_store_artifact
    ):
        """Psi4 is accepted by the route but has no charge/multiplicity parser.

        The log plainly states values that differ from the declared ones,
        but TCKDB cannot read them for this program, so it must stay silent
        rather than invent a contradiction.
        """
        calc_id = _create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
        psi4_log = (
            b"    Psi4: An Open-Source Quantum Chemistry Package\n"
            b"    Molecular charge  = 1\n"
            b"    Spin multiplicity = 3\n"
        )
        resp = _post_artifact(
            client, calc_id, _output_log(content=psi4_log, filename="psi4.out")
        )
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == []

    def test_log_with_no_ess_signature_never_reaches_the_hook(
        self, client, stub_store_artifact
    ):
        """The route rejects unrecognised output logs before the hook runs."""
        calc_id = _create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
        resp = _post_artifact(
            client,
            calc_id,
            _output_log(
                content=b"SOME UNSUPPORTED QC CODE v1.0\nCHARGE 7 SPIN 9\n",
                filename="mystery.out",
            ),
        )
        assert resp.status_code == 422, resp.text

    def test_absent_artifact_emits_nothing(self, client):
        """A conformer upload with no log at all must not warn."""
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(smiles="[CH3]", charge=0, multiplicity=2),
        )
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == []

    def test_truncated_log_emits_nothing(self, client, stub_store_artifact):
        """A recognised banner with the header cut off is still unknown."""
        calc_id = _create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
        resp = _post_artifact(
            client,
            calc_id,
            _output_log(
                content=GAUSSIAN_TRIPLET_LOG[:200], filename="truncated.log"
            ),
        )
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == []

    def test_input_artifact_kind_is_not_checked(
        self, client, stub_store_artifact
    ):
        """Only output logs describe what the run actually did."""
        calc_id = _create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
        resp = _post_artifact(
            client,
            calc_id,
            {
                "kind": "input",
                "filename": "job.gjf",
                "content_base64": _b64(GAUSSIAN_TRIPLET_LOG),
            },
        )
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == []

    def test_declared_values_are_never_overwritten(
        self, client, db_session, stub_store_artifact
    ):
        """A mismatch flags for review; identity fields stay as submitted."""
        from app.db.models.calculation import Calculation

        calc_id = _create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
        resp = _post_artifact(client, calc_id, _output_log())
        assert resp.status_code == 201, resp.text
        assert _relevant(resp) == [W_MULT]

        # Charge/multiplicity are identity columns on ``species`` (DR-0031),
        # reached through the entry. A mismatch must never rewrite them:
        # doing so would silently repoint the upload at a different species.
        calc = db_session.get(Calculation, calc_id)
        assert calc.species_entry.species.multiplicity == 2
        assert calc.species_entry.species.charge == 0


def _computed_species_bundle(*, charge: int, multiplicity: int) -> dict:
    """A bundle whose calc carries its output log INLINE (ARC bundle mode)."""
    return {
        "species_entry": {
            "smiles": "C",
            "charge": charge,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _GEOMETRY_BY_SMILES["C"]},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": {"name": "Molpro", "version": "2022.1"},
                    "level_of_theory": {
                        "method": "CCSD(T)-F12",
                        "basis": "cc-pVTZ-F12",
                    },
                    "opt_result": {"converged": True},
                    "artifacts": [
                        {
                            "kind": "output_log",
                            "filename": "opt0.out",
                            "content_base64": _b64(MOLPRO_CH4_LOG),
                        }
                    ],
                },
            }
        ],
    }


class TestChargeMultiplicityBundleHook:
    """The check must also fire on logs uploaded inline in a bundle."""

    def test_bundle_inline_log_detects_mismatch(
        self, client, stub_store_artifact
    ):
        # The Molpro CH4 deck declares a closed-shell singlet; declare a triplet.
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json=_computed_species_bundle(charge=0, multiplicity=3),
        )
        assert resp.status_code == 201, resp.text
        assert W_MULT in [w["code"] for w in resp.json().get("warnings", [])]

    def test_bundle_inline_log_agreement_is_silent(
        self, client, stub_store_artifact
    ):
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json=_computed_species_bundle(charge=0, multiplicity=1),
        )
        assert resp.status_code == 201, resp.text
        codes = [w["code"] for w in resp.json().get("warnings", [])]
        assert W_MULT not in codes
        assert W_CHARGE not in codes
