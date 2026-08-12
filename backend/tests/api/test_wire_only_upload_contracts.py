"""A consumer with only ``tckdb-schemas`` can build these upload payloads.

``POST /api/v1/uploads/conformers`` and
``POST /api/v1/uploads/transition-states`` are published contracts: their
request bodies live in the versioned wire package, so a client can pin a
version and a breaking change forces a bump. The claim only holds if the
models are genuinely constructible without the server, so the payloads here
are built in a **separate process that cannot import ``app`` at all** and
are then posted to the live routes.

If someone reintroduces a backend import under
``tckdb_schemas.workflows.conformer_upload`` or
``...transition_state_upload``, the subprocess stops being able to build the
payload and this test fails — which is the point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import pytest
import tckdb_schemas

WIRE_PACKAGE_ROOT = str(Path(tckdb_schemas.__file__).resolve().parents[1])

def _hosts_the_backend(path: str) -> bool:
    """True if importing from ``path`` would resolve ``app``."""
    return (Path(path) / "app" / "__init__.py").is_file()


def _subprocess_pythonpath() -> str:
    """Everything this interpreter can import, minus the backend.

    The subprocess runs with ``-S``, which skips ``site`` and therefore
    every ``.pth`` file. That is the only way to get a backend-free
    interpreter out of a dev environment: ``tckdb-backend`` is
    editable-installed here, and an editable install is reachable through a
    ``.pth`` finder that no ``PYTHONPATH`` setting can hide. Skipping
    ``site`` also drops ``site-packages``, so the search path is rebuilt
    from this process's own ``sys.path`` — third-party distributions stay
    importable wherever CI happens to put them, the ``.pth`` hooks do not
    come back, and any entry that would resolve ``app`` is dropped.
    """
    candidates = [WIRE_PACKAGE_ROOT, *sysconfig.get_paths().values(), *sys.path]
    keep: list[str] = []
    for entry in candidates:
        # "" means the subprocess's own cwd, which is a temp dir it must not
        # inherit a meaning for; drop it rather than reinterpret it.
        if not entry or entry in keep or _hosts_the_backend(entry):
            continue
        keep.append(entry)
    assert WIRE_PACKAGE_ROOT in keep
    return os.pathsep.join(keep)


_SUBPROCESS_PYTHONPATH = _subprocess_pythonpath()

#: Built entirely from ``tckdb_schemas``. The only backend-side knowledge is
#: the chemistry: H + CH4 -> H2 + CH3 saddles on CH5, so the TS geometry has
#: to be made of its own reaction's atoms.
_BUILDER = r'''
import json
import sys

# Prove the backend is genuinely out of reach before building anything.
try:
    import app  # noqa: F401
except ImportError:
    pass
else:
    print("BACKEND_IMPORTABLE", file=sys.stderr)
    raise SystemExit(2)

from tckdb_schemas.workflows import (
    ConformerUploadRequest,
    TransitionStateUploadRequest,
)
from tckdb_schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from tckdb_schemas.workflows.transport_upload import TransportUploadPayload
from tckdb_schemas.statmech_bits import StatmechTorsionCreate

SOFTWARE = {"name": "Gaussian", "version": "16"}
LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

conformer = ConformerUploadRequest(
    species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
    geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    calculation={
        "type": "sp",
        "software_release": SOFTWARE,
        "level_of_theory": LOT,
    },
    additional_calculations=[
        {
            "type": "freq",
            "software_release": SOFTWARE,
            "level_of_theory": LOT,
            "freq_result": {"n_imag": 0},
        }
    ],
    statmech=ConformerUploadStatmechPayload(
        external_symmetry=1,
        point_group="K",
        is_linear=False,
        optical_isomers=1,
        electronic_levels=[{"level_index": 1, "energy_cm1": 0.0, "degeneracy": 2}],
    ),
    transport=TransportUploadPayload(
        sigma_angstrom=2.05,
        epsilon_over_k_k=145.0,
        note="from a wire-only consumer",
    ),
    label="wire-only-conf",
    note="built without importing app",
)

transition_state = TransitionStateUploadRequest(
    reaction={
        "reversible": True,
        "reactants": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            {"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}},
        ],
        "products": [
            {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
        ],
    },
    charge=0,
    multiplicity=2,
    geometry={
        "xyz_text": (
            "6\nH...H-CH3 abstraction TS\n"
            "C  0.000  0.000  0.000\n"
            "H -0.510  0.883  0.000\n"
            "H -0.510 -0.883  0.000\n"
            "H  0.000  0.000 -1.090\n"
            "H  0.000  0.000  1.350\n"
            "H  0.000  0.000  2.250"
        )
    },
    primary_opt={
        "type": "opt",
        "software_release": SOFTWARE,
        "level_of_theory": LOT,
    },
    additional_calculations=[
        {
            "type": "freq",
            "software_release": SOFTWARE,
            "level_of_theory": LOT,
            "freq_result": {"n_imag": 1, "imag_freq_cm1": -1500.0},
        }
    ],
    label="wire-only-ts",
)

# Nested wire types that used to be backend-only, exercised for
# constructibility even where the smoke payloads above do not carry them.
StatmechTorsionCreate(
    torsion_index=1,
    dimension=1,
    coordinates=[
        {
            "coordinate_index": 1,
            "atom1_index": 1,
            "atom2_index": 2,
            "atom3_index": 3,
            "atom4_index": 4,
        }
    ],
)

# The models must also reject what the server rejects, in this process.
rejected = 0
try:
    TransportUploadPayload(note="no transport property at all")
except ValueError:
    rejected += 1
try:
    ConformerUploadRequest(
        species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
        geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        calculation={
            "type": "sp",
            "software_release": SOFTWARE,
            "level_of_theory": LOT,
        },
        additional_calculations=[
            {
                "type": "freq",
                "software_release": SOFTWARE,
                "level_of_theory": LOT,
                "freq_result": {"n_imag": 1, "imag_freq_cm1": -400.0},
            }
        ],
    )
except ValueError:
    rejected += 1

print(
    json.dumps(
        {
            "conformer": conformer.model_dump(mode="json", exclude_none=True),
            "transition_state": transition_state.model_dump(
                mode="json", exclude_none=True
            ),
            "rejected": rejected,
            "loaded_app_modules": [
                name for name in sys.modules if name.split(".", 1)[0] == "app"
            ],
        }
    )
)
'''


@pytest.fixture(scope="module")
def wire_only_payloads() -> dict:
    """Build both payloads in a process where ``app`` is not importable."""
    with tempfile.TemporaryDirectory() as neutral_cwd:
        result = subprocess.run(
            [sys.executable, "-S", "-c", _BUILDER],
            capture_output=True,
            text=True,
            # A neutral cwd, so the implicit ``''`` on ``sys.path`` cannot
            # reach the backend package root either.
            cwd=neutral_cwd,
            env={**os.environ, "PYTHONPATH": _SUBPROCESS_PYTHONPATH},
            check=False,
        )
    assert result.returncode == 0, (
        "wire-only payload construction failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return json.loads(result.stdout)


def test_wire_only_process_never_touched_the_backend(wire_only_payloads) -> None:
    assert wire_only_payloads["loaded_app_modules"] == []


def test_wire_only_models_enforce_their_own_contracts(wire_only_payloads) -> None:
    """Validation travelled with the models, not just the field names."""
    assert wire_only_payloads["rejected"] == 2


def test_live_conformer_route_accepts_a_wire_only_payload(
    client, wire_only_payloads
) -> None:
    resp = client.post(
        "/api/v1/uploads/conformers", json=wire_only_payloads["conformer"]
    )
    assert resp.status_code == 201, resp.text
    assert "conformer_group_id" in resp.json()


def test_live_transition_state_route_accepts_a_wire_only_payload(
    client, wire_only_payloads
) -> None:
    resp = client.post(
        "/api/v1/uploads/transition-states",
        json=wire_only_payloads["transition_state"],
    )
    assert resp.status_code == 201, resp.text
