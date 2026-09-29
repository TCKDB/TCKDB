#!/usr/bin/env python
"""QCSchema interchange demonstration: a Psi4 Hessian through TCKDB and back,
and the same molecule's Gaussian record beside it.

Phase C work package C-Q4 (``docs/research/tckdb-phase-c-implementation-plan.md``,
C1 "Demonstration"). The write-up is ``docs/validation/qcschema_interchange.md``.

Inputs are committed fixtures and nothing else
----------------------------------------------
``backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/``:

* ``water_b3lyp_def2tzvp_hessian_v2.json`` (and its ``_v1`` sibling): a Psi4
  1.11 B3LYP/def2-TZVP Hessian on water, run through qcengine 0.51.0 at the
  Gaussian record's geometry, as a QCSchema ``AtomicResult``;
* ``gaussian_reference.json``: the Gaussian 16 C.02 record at the same
  geometry, read from the development instance (packed Hessian, single-point
  energy, zero-point energy);
* ``meta.json``: the sha256 of every file above. A fixture whose digest
  disagrees is refused before anything is computed.

No network is contacted. The database is touched only inside one
transaction that is always rolled back: see "The round trip".

The round trip (asserted, exit status 1 on any failure)
-------------------------------------------------------
The v2 document is read and mapped by the real adapter
(``clients/python/adapters/qcschema``: ``reader.read_document`` then
``mapping.build_conformer_upload_payload`` with ``--smiles O``), posted to the
real ``POST /api/v1/uploads/conformers`` route of an in-process application
through the real ``tckdb-client`` (an in-process transport, no socket), with a
real API key minted for a scratch user, and exported back with the adapter's
``exporter.export_calculation`` using the integer calculation id (the Hessian
read needs it; see the exporter's "calculation-id gap"). Then:

* **Hessian, exact.** The stored packed lower triangle equals the document's
  lower triangle element for element, and the exported full matrix equals that
  triangle mirrored, element for element. The expected triangle is computed
  here, independently of the adapter's packing code, in the row-major order of
  ``backend/app/services/hessian_parsing.py`` (``M[r][c] for r in range(3N)
  for c in range(r + 1)``). The document's own matrix is not bit-symmetric (a
  few upper-triangle elements differ from their mirror by ~1e-17), and packed
  storage cannot represent that, so those elements are the only ones where the
  export differs from the document; they are counted and their size reported.
* **Geometry, within the ten-decimal bound.** The stored coordinates are within
  0.5e-10 Angstrom of the document's bohr coordinates converted with the
  backend's ``BOHR_TO_ANGSTROM`` (``hessian_parsing.py``), and the exported
  bohr coordinates are within ``0.5e-10 / BOHR_TO_ANGSTROM`` of the
  document's (the bound derived in the adapter's ``tests/test_round_trip.py``).
* **Identity.** Symbols, charge, multiplicity and mass numbers equal.
* **Energy.** Not carried, and asserted not carried. The C1 mapping maps a
  driver-``hessian`` document to a ``freq`` record holding only the matrix;
  ``properties.return_energy`` is not stored, and the exporter fills
  ``properties.return_energy`` only from a same-level ``sp`` record on the same
  conformer, of which there is none. The check is that the export carries no
  energy rather than a wrong one. The energy enters the cross-program
  comparison below directly from the fixture.
* **Loss list, complete.** Every field path of the document is classified as
  equal, changed, lost, added, or checked separately (geometry, Hessian); the
  measured ``lost`` and ``changed`` sets must equal the declared
  :data:`EXPECTED_LOST_PATHS` and :data:`EXPECTED_CHANGED_PATHS` exactly, so a
  field that starts or stops surviving the round trip fails the check.
* **No re-import.** The export is refused by the adapter's reader with
  ``tckdb_export_reimport_refused``.

The raw-artifact POST that ``tckdb-qcschema import --upload`` also makes is not
repeated: it writes to the object store, which this script never contacts. The
backend corpus test ``backend/tests/api/test_api_qcschema_fixtures.py`` covers
it for the C-Q2 corpus.

The cross-program comparison (measured, never judged)
-----------------------------------------------------
Psi4 and Gaussian do not compute B3LYP identically (integration grids differ
in radial scheme and pruning; the Psi4 Hessian is a finite difference of
analytic gradients, the Gaussian one analytic), so no tolerance is applied and
nothing here passes or fails. Reported, with differences taken as Psi4 minus
Gaussian:

* the electronic energy difference at the same geometry, hartree and kJ/mol;
* harmonic frequencies from both Hessians by one implementation, TCKDB's own
  (:mod:`app.chemistry.normal_modes`): mass-weighted with the most abundant
  isotope's mass (``atomic_mass``, RDKit's table: O-16 15.99491462,
  H-1 1.007825032 amu), translations and rotations projected out exactly
  (``rigid_body_subspace`` + ``solve_vibrational_modes``), each Hessian at its
  own stored geometry, which differ by less than 1e-8 Angstrom;
* per-mode frequency differences, the imaginary-mode counts, the harmonic
  zero-point energies (``sum(nu)/2``, unscaled) and their differences from the
  Gaussian record's stored ZPE;
* Hessian symmetry and the element-wise Hessian difference;
* the rigid-body residue before projection: the six lowest eigenvalues of the
  unprojected mass-weighted Hessian and the curvature along each rigid-body
  direction, both as signed wavenumbers -- the units of the Phase B frame
  bound ``FRAME_CONSISTENCY_TOLERANCE_CM1`` (100 cm^-1,
  ``backend/app/chemistry/normal_modes.py``), which is reported beside them.

Usage::

    # A migrated scratch database; nothing is committed to it.
    DB_NAME=tckdb_test_qcschema_demo python backend/scripts/validation/qcschema_interchange_report.py \\
        --json-out qcschema_interchange.json --markdown-out qcschema_interchange.md

Requires ``qcelemental==0.51.2`` (the adapter's pin; in the backend's ``[dev]``
extra). The adapter and ``tckdb-client`` are imported from this checkout's
``clients/python`` tree, not from any installed copy.

Exit status: ``0`` every round-trip check passed; ``1`` a round-trip check
failed (named in the output); ``2`` the fixtures or the environment could not
be used.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import math
import platform
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.chemistry.normal_modes import (  # noqa: E402
    FRAME_CONSISTENCY_TOLERANCE_CM1,
    atomic_mass,
    rigid_body_curvature_cm1,
    rigid_body_subspace,
    solve_normal_modes,
    solve_vibrational_modes,
    unpack_lower_triangle,
)
from app.chemistry.units import HARTREE_TO_KJ_MOL  # noqa: E402
from app.services.hessian_parsing import BOHR_TO_ANGSTROM as BACKEND_BOHR_TO_ANGSTROM  # noqa: E402

FIXTURE_DIR = BACKEND_ROOT / "tests" / "fixtures" / "qcschema" / "water_b3lyp_def2tzvp_psi4"
V1_NAME = "water_b3lyp_def2tzvp_hessian_v1.json"
V2_NAME = "water_b3lyp_def2tzvp_hessian_v2.json"
REFERENCE_NAME = "gaussian_reference.json"
RUN_SCRIPT_NAME = "run_psi4_water_hessian.py"
META_NAME = "meta.json"
FIXTURE_FILES = (V1_NAME, V2_NAME, REFERENCE_NAME, RUN_SCRIPT_NAME)

ADAPTER_ROOT = REPO_ROOT / "clients" / "python" / "adapters" / "qcschema"
CLIENT_SRC = REPO_ROOT / "clients" / "python" / "src"
PSI4_LOCKFILE = ADAPTER_ROOT / "tests" / "fixtures" / "ENVIRONMENT.lock.txt"

#: Identity is declared, never perceived: Psi4 drops ``molecule.identifiers``
#: and TCKDB does not derive a SMILES from 3D coordinates.
DECLARED_SMILES = "O"

#: CODATA 2018 hartree in cm^-1, the constant set ``normal_modes`` uses.
HARTREE_TO_CM1 = 219474.6313632

#: Import rounds each coordinate to ten decimals of Angstrom
#: (``tckdb_qcschema.molecule.to_geometry_payload``); half the last place.
GEOMETRY_BOUND_ANGSTROM = 0.5e-10

#: Round-trip checks by name, in report order. Every one must be true.
ROUND_TRIP_CHECKS = (
    "fixture_digests_match_meta",
    "mapped_as_freq",
    "v1_and_v2_map_to_the_same_payload",
    "stored_triangle_exact",
    "exported_hessian_exact_after_pack_unpack",
    "stored_geometry_within_bound",
    "exported_geometry_within_bound",
    "identity_preserved",
    "energy_not_carried",
    "loss_list_complete",
    "export_reimport_refused",
)

#: Field paths of the Psi4 v2 document, as qcelemental parses it, that the
#: export does not carry. The report splits them by what the import said
#: about each (``lost_by_import_accounting``): stored but not exported,
#: reported unsupported, or dropped without the mapping report naming it
#: (``extras.qcvars`` and the ``properties.*`` entries; they survive only in
#: the raw document).
EXPECTED_LOST_PATHS = (
    "extras.qcvars",
    "input_data.specification.keywords",
    "properties.calcinfo_nalpha",
    "properties.calcinfo_natom",
    "properties.calcinfo_nbasis",
    "properties.calcinfo_nbeta",
    "properties.calcinfo_nmo",
    "properties.nuclear_repulsion_energy",
    "properties.return_energy",
    "properties.return_gradient",
    "properties.return_hessian",
    "provenance.cpu",
    "provenance.hostname",
    "provenance.memory",
    "provenance.module",
    "provenance.nthreads",
    "provenance.qcengine_version",
    "provenance.username",
    "provenance.wall_time",
    "stdout",
)

#: Paths present on both sides with a different value. ``fix_com`` and
#: ``fix_orientation`` are ``true`` in the Psi4 input and read back
#: ``false``: the exporter does not set them, so the export does not declare
#: that its frame is fixed. The top-level ``provenance`` is TCKDB's own.
EXPECTED_CHANGED_PATHS = (
    "input_data.molecule.fix_com",
    "input_data.molecule.fix_orientation",
    "molecule.fix_com",
    "molecule.fix_orientation",
    "provenance.creator",
    "provenance.routine",
    "provenance.version",
)

#: Compared by their own checks, not by the structural diff.
_SEPARATELY_CHECKED = frozenset(
    {
        "return_result",
        "molecule.geometry",
        "input_data.molecule.geometry",
    }
)

EXIT_OK = 0
EXIT_ROUND_TRIP_FAILED = 1
EXIT_UNUSABLE = 2


class FixtureError(RuntimeError):
    """The committed fixtures cannot be used as they are."""


# ---------------------------------------------------------------------------
# fixtures and versions
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_fixtures(fixture_dir: Path = FIXTURE_DIR) -> SimpleNamespace:
    """Read every fixture and check its digest against ``meta.json``."""
    meta_path = fixture_dir / META_NAME
    if not meta_path.is_file():
        raise FixtureError(f"{meta_path} is missing")
    meta = json.loads(meta_path.read_text())
    raw: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for name in FIXTURE_FILES:
        path = fixture_dir / name
        if not path.is_file():
            raise FixtureError(f"{path} is missing")
        raw[name] = path.read_bytes()
        digests[name] = _sha256(raw[name])
    declared = meta.get("sha256") or {}
    mismatched = sorted(name for name in FIXTURE_FILES if declared.get(name) != digests[name])
    return SimpleNamespace(
        meta=meta,
        raw=raw,
        digests=digests,
        digest_mismatches=mismatched,
        v1=json.loads(raw[V1_NAME]),
        v2=json.loads(raw[V2_NAME]),
        reference=json.loads(raw[REFERENCE_NAME]),
    )


def _pyproject_version(path: Path) -> str:
    return str(tomllib.loads(path.read_text())["project"]["version"])


def _installed_version(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def tckdb_commit() -> str | None:
    """``git rev-parse HEAD`` of this checkout, ``None`` outside a git tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def qcelemental_available() -> bool:
    try:
        importlib.import_module("qcelemental")
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# the adapter, imported from this checkout
# ---------------------------------------------------------------------------

_ADAPTER_PACKAGES = ("tckdb_qcschema", "tckdb_client")


def _owned(module_name: str) -> bool:
    return any(module_name == p or module_name.startswith(p + ".") for p in _ADAPTER_PACKAGES)


@contextlib.contextmanager
def adapter_modules() -> Iterator[SimpleNamespace]:
    """Import ``tckdb_qcschema`` and ``tckdb_client`` from this checkout.

    Neither is a backend dependency, and an installed ``tckdb_client`` may be
    another checkout's older copy. So both are imported from
    ``clients/python`` for the duration of the block and removed afterwards:
    any copy already in ``sys.modules`` is set aside and put back, and the
    path entries are taken out again, so no other test in the same process
    sees a different ``tckdb_client`` because this ran.
    """
    stashed = {name: module for name, module in sys.modules.items() if _owned(name)}
    for name in stashed:
        del sys.modules[name]
    inserted = [str(CLIENT_SRC), str(ADAPTER_ROOT)]
    for entry in reversed(inserted):
        sys.path.insert(0, entry)
    try:
        modules = SimpleNamespace(
            package=importlib.import_module("tckdb_qcschema"),
            reader=importlib.import_module("tckdb_qcschema.reader"),
            mapping=importlib.import_module("tckdb_qcschema.mapping"),
            molecule=importlib.import_module("tckdb_qcschema.molecule"),
            exporter=importlib.import_module("tckdb_qcschema.exporter"),
            errors=importlib.import_module("tckdb_qcschema.errors"),
            uploader=importlib.import_module("tckdb_qcschema.uploader"),
            client=importlib.import_module("tckdb_client"),
        )
        for module in (modules.package, modules.client):
            origin = Path(module.__file__).resolve()
            if REPO_ROOT not in origin.parents:
                raise FixtureError(f"{module.__name__} resolved outside this checkout: {origin}")
        # ``tckdb_client.__version__`` comes from installed distribution
        # metadata: "0.0.0+local" when the package is not installed (backend
        # CI), or another checkout's version when an older copy is. The code
        # loaded here is this checkout's, so state this checkout's version.
        # The client sends it as X-TCKDB-Client-Version, and the upload
        # route answers 426 to anything below the supported minimum.
        modules.client.__version__ = _pyproject_version(CLIENT_SRC.parent / "pyproject.toml")
        yield modules
    finally:
        for entry in inserted:
            with contextlib.suppress(ValueError):
                sys.path.remove(entry)
        for name in [n for n in sys.modules if _owned(n)]:
            del sys.modules[name]
        sys.modules.update(stashed)


class _TestClientForwarder(httpx.BaseTransport):
    """An ``httpx`` transport that hands each request to a ``TestClient``.

    Through the ``TestClient``'s public ``request()`` only. Borrowing its
    private ``_transport`` worked on Starlette 0.52 and broke on the 1.3.1
    that ``backend/environment.yml`` pins: its transport's response stream is
    not the ``SyncByteStream`` a plain ``httpx.Client`` asserts on. The
    response is rebuilt from the already-decoded body, so the headers that
    describe the wire encoding are dropped rather than applied twice.
    """

    _ENCODING_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})

    def __init__(self, test_client: Any) -> None:
        self._test_client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        forwarded = self._test_client.request(
            request.method,
            str(request.url),
            headers=[(k, v) for k, v in request.headers.items() if k.lower() != "content-length"],
            content=request.read(),
        )
        headers = [(k, v) for k, v in forwarded.headers.items() if k.lower() not in self._ENCODING_HEADERS]
        return httpx.Response(forwarded.status_code, headers=headers, content=forwarded.content, request=request)


class _InProcessClient:
    """The five reads the exporter makes, forwarded to a real ``TCKDBClient``.

    ``status()`` is the one method not forwarded: ``GET /status`` probes the
    object store over the network, which this script never contacts. The
    exporter asks it only for an ``api_version`` key no deployment reports
    today, and records the ``tckdb-client`` package version instead, exactly
    as it does against a live server.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_calculation(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.get_calculation(*args, **kwargs)

    def get_geometry(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.get_geometry(*args, **kwargs)

    def get_calculation_hessian(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.get_calculation_hessian(*args, **kwargs)

    def get_json(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.get_json(*args, **kwargs)

    def search_calculations(self, *args: Any, **kwargs: Any) -> Any:
        return self._client.search_calculations(*args, **kwargs)

    def status(self) -> dict:
        return {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _r(value: float, decimals: int) -> float:
    """Round for byte-stable output; never emit ``-0.0``."""
    out = round(float(value), decimals)
    return 0.0 if out == 0.0 else out


def _lower_triangle(full_flat: list[float], dim: int) -> list[float]:
    """Row-major lower triangle incl. diagonal, the order ``hessian_parsing`` packs."""
    return [float(full_flat[r * dim + c]) for r in range(dim) for c in range(r + 1)]


def _mirror(packed: list[float], dim: int) -> list[float]:
    full = [0.0] * (dim * dim)
    index = 0
    for r in range(dim):
        for c in range(r + 1):
            full[r * dim + c] = full[c * dim + r] = float(packed[index])
            index += 1
    return full


def _present(value: Any) -> bool:
    return value is not None and value != {} and value != [] and value != ""


def structural_diff(original: Any, exported: Any, path: str = "") -> dict[str, list[str]]:
    """Classify every field path of ``original`` against ``exported``.

    A dict on both sides is walked key by key; anything else is a leaf. A
    subtree present in the original and absent (or empty) in the export is
    reported once, at its root.
    """
    out: dict[str, list[str]] = {"equal": [], "changed": [], "lost": [], "added": [], "separately_checked": []}

    def walk(orig: Any, exp: Any, here: str) -> None:
        if here in _SEPARATELY_CHECKED:
            out["separately_checked"].append(here)
            return
        if not _present(orig):
            if _present(exp):
                out["added"].append(here)
            return
        if not _present(exp):
            out["lost"].append(here)
        elif isinstance(orig, dict) and isinstance(exp, dict):
            for key in sorted(set(orig) | set(exp)):
                walk(orig.get(key), exp.get(key), f"{here}.{key}" if here else key)
        elif orig == exp:
            out["equal"].append(here)
        else:
            out["changed"].append(here)

    walk(original, exported, path)
    return {key: sorted(value) for key, value in out.items()}


def _as_parsed(document: dict) -> dict:
    """The document as a QCSchema consumer sees it: parsed by qcelemental's
    v2 ``AtomicResult`` and dumped with every default filled in.

    So a field qcelemental derives on its own (``masses`` from
    ``mass_numbers``, ``real``, ``fragments``, ``atomic_numbers``) counts as
    carried when the export omits it and a reader gets it back by parsing,
    and only what a reader genuinely cannot recover is reported lost.
    """
    import qcelemental.models.v2 as qcel_v2

    result = qcel_v2.AtomicResult.model_validate(document)
    dumped = result.model_dump(mode="json")
    # qcelemental's dump omits a field it defaulted, though the attribute
    # carries the value a reader gets; put those back so they compare.
    for molecule, target in (
        (result.molecule, dumped["molecule"]),
        (result.input_data.molecule, dumped["input_data"]["molecule"]),
    ):
        for name in _MOLECULE_DERIVED_FIELDS:
            value = getattr(molecule, name, None)
            if value is not None:
                target[name] = _jsonable(value)
    return dumped


_MOLECULE_DERIVED_FIELDS = (
    "atom_labels",
    "atomic_numbers",
    "fragment_charges",
    "fragment_multiplicities",
    "fragments",
    "mass_numbers",
    "masses",
    "real",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _spectrum(packed: list[float], natoms: int, elements: list[str], coords_angstrom: np.ndarray) -> dict[str, Any]:
    """One Hessian through TCKDB's normal-mode code: the same calls for both programs."""
    masses = []
    for element in elements:
        mass = atomic_mass(element, None)
        if mass is None:
            raise FixtureError(f"no mass for element {element!r}")
        masses.append(mass)
    matrix = unpack_lower_triangle(packed, natoms)
    rigid = rigid_body_subspace(coords_angstrom, masses)
    vibrational = solve_vibrational_modes(matrix, masses, rigid)
    unprojected = solve_normal_modes(matrix, masses)
    curvature = rigid_body_curvature_cm1(matrix, masses, rigid)
    frequencies = [mode.frequency_cm1 for mode in vibrational]
    zpe_hartree = 0.5 * sum(f for f in frequencies if f > 0.0) / HARTREE_TO_CM1
    return {
        "masses_amu": masses,
        "matrix": matrix,
        "frequencies_cm1": frequencies,
        "imaginary_count": sum(1 for f in frequencies if f < 0.0),
        "zpe_hartree": zpe_hartree,
        "lowest_six_unprojected_cm1": [mode.frequency_cm1 for mode in unprojected[:6]],
        "rigid_body_curvature_cm1": list(curvature),
        "rigid_body_dimension": int(rigid.dimension),
    }


# ---------------------------------------------------------------------------
# the round trip
# ---------------------------------------------------------------------------


def _round_trip(session: Session, adapter: SimpleNamespace, fixtures: SimpleNamespace) -> dict[str, Any]:
    """Import, read back and export inside one always-rolled-back transaction."""
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.api import deps as api_deps
    from app.api.app import create_app
    from app.db.models.app_user import AppUser
    from app.db.models.calculation import Calculation, CalculationHessian
    from app.db.models.common import AppUserRole
    from app.db.models.geometry import GeometryAtom
    from app.services.auth import create_api_key
    from app.services.hessian_reanalysis import reanalyse_calculation

    raw_v2 = fixtures.raw[V2_NAME]
    record = adapter.reader.read_document(raw_v2)
    payload, mapping_report = adapter.mapping.build_conformer_upload_payload(
        record,
        raw_bytes=raw_v2,
        raw_artifact_filename=V2_NAME.removesuffix(".json") + ".qcschema.json",
        raw_artifact_sha256=_sha256(raw_v2),
        declared_smiles=DECLARED_SMILES,
    )

    connection = session.connection()
    nested = connection.begin_nested()
    scratch = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    factory = api_deps.SessionLocal
    previous_bind = factory.kw.get("bind")
    factory.configure(bind=connection)
    client = None
    try:
        user = AppUser(username="qcschema-interchange-report", role=AppUserRole.user)
        scratch.add(user)
        scratch.flush()
        _, api_key = create_api_key(scratch, user, label="qcschema interchange report (rolled back)")

        app = create_app()
        app.dependency_overrides[api_deps.get_db] = lambda: scratch
        app.dependency_overrides[api_deps.get_write_db] = lambda: scratch
        # No ``with``: the lifespan's startup probes reach the object store.
        test_client = TestClient(app)
        client = adapter.client.TCKDBClient(
            "http://testserver/api/v1",
            api_key=api_key,
            transport=_TestClientForwarder(test_client),
        )

        upload = client.request_json(
            "POST",
            "/uploads/conformers",
            json=payload,
            idempotency_key=adapter.uploader.conformers_idempotency_key(record.canonical_sha256),
        ).data
        primary = upload["primary_calculation"]
        calculation_id = int(primary["calculation_id"])

        scratch.expire_all()
        hessian_row = scratch.get(CalculationHessian, calculation_id)
        stored_triangle = [float(v) for v in hessian_row.lower_triangle_hartree_bohr2] if hessian_row else []
        atoms = list(
            scratch.scalars(
                select(GeometryAtom)
                .where(GeometryAtom.geometry_id == hessian_row.geometry_id)
                .order_by(GeometryAtom.atom_index)
            ).all()
        ) if hessian_row else []
        stored_atoms = [
            {
                "element": atom.element.strip(),
                "xyz": [float(atom.x), float(atom.y), float(atom.z)],
                "isotope_mass_number": atom.isotope_mass_number,
            }
            for atom in atoms
        ]
        reanalysis = reanalyse_calculation(scratch, scratch.get(Calculation, calculation_id))

        exported = adapter.exporter.export_calculation(_InProcessClient(client), calculation_id)
    finally:
        if client is not None:
            client.close()
        scratch.close()
        nested.rollback()
        factory.configure(bind=previous_bind)

    return {
        "record": record,
        "payload": payload,
        "mapping_report": mapping_report.to_dict(),
        "calculation_type": primary["type"],
        "stored_triangle": stored_triangle,
        "stored_hessian_source": hessian_row.source.value if hessian_row else None,
        "stored_atoms": stored_atoms,
        "reanalysis_status": reanalysis.status.value,
        "exported": exported,
    }


#: Where a v2 document path appears under a different name in the import's
#: mapping report (which names v1/v2-neutral field names).
_MAPPING_REPORT_NAMES = {"input_data.specification.keywords": "keywords"}


def _lost_by_import_accounting(lost: list[str], trip: dict[str, Any]) -> dict[str, list[str]]:
    """What the import said about each path the export does not carry.

    ``stored_not_exported``: the import mapped or retained it (keywords
    become parameter observations; the allow-listed provenance keys go to
    ``parameters_json``), and the exporter has no QCSchema field it writes
    them back to. ``reported_unsupported``: the import's mapping report
    names it as unsupported. ``not_named_by_import_report``: dropped without
    the mapping report saying so; it survives only in the raw document.
    """
    report = trip["mapping_report"]
    transformed = set(report["transformed"])
    unsupported = set(report["unsupported"]) | set(report["retained_only"])
    retained = {
        f"provenance.{key}"
        for key in trip["payload"]["calculation"]["parameters_json"]["tckdb_qcschema"]["provenance"]
    }
    out: dict[str, list[str]] = {"stored_not_exported": [], "reported_unsupported": [], "not_named_by_import_report": []}
    for path in lost:
        name = _MAPPING_REPORT_NAMES.get(path, path)
        if name in unsupported:
            out["reported_unsupported"].append(path)
        elif name in transformed or path in retained:
            out["stored_not_exported"].append(path)
        else:
            out["not_named_by_import_report"].append(path)
    return {key: sorted(value) for key, value in out.items()}


def _payload_without_bookkeeping(payload: dict) -> dict:
    copy = json.loads(json.dumps(payload))
    copy.get("calculation", {}).pop("parameters_json", None)
    return copy


def _check_round_trip(adapter: SimpleNamespace, fixtures: SimpleNamespace, trip: dict[str, Any]) -> dict[str, Any]:
    original = fixtures.v2
    exported = trip["exported"]
    natoms = len(original["molecule"]["symbols"])
    dim = 3 * natoms
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    checks["fixture_digests_match_meta"] = not fixtures.digest_mismatches
    checks["mapped_as_freq"] = trip["calculation_type"] == "freq" and trip["payload"]["calculation"]["type"] == "freq"

    # v1 and v2 are two wrappings of one result; both must map to one payload.
    raw_v1 = fixtures.raw[V1_NAME]
    record_v1 = adapter.reader.read_document(raw_v1)
    payload_v1, _ = adapter.mapping.build_conformer_upload_payload(
        record_v1,
        raw_bytes=raw_v1,
        raw_artifact_filename=V1_NAME.removesuffix(".json") + ".qcschema.json",
        raw_artifact_sha256=_sha256(raw_v1),
        declared_smiles=DECLARED_SMILES,
    )
    checks["v1_and_v2_map_to_the_same_payload"] = _payload_without_bookkeeping(payload_v1) == _payload_without_bookkeeping(
        trip["payload"]
    )

    # Hessian: stored triangle and exported matrix, exact.
    original_full = [float(v) for v in original["return_result"]]
    expected_triangle = _lower_triangle(original_full, dim)
    expected_full = _mirror(expected_triangle, dim)
    stored = trip["stored_triangle"]
    exported_full = [float(v) for v in exported.get("return_result") or []]
    checks["stored_triangle_exact"] = stored == expected_triangle
    checks["exported_hessian_exact_after_pack_unpack"] = exported_full == expected_full
    differing = [
        abs(e - o) for e, o in zip(exported_full, original_full, strict=False) if e != o
    ] if len(exported_full) == len(original_full) else []
    asymmetric_pairs = [
        abs(original_full[r * dim + c] - original_full[c * dim + r])
        for r in range(dim)
        for c in range(r)
        if original_full[r * dim + c] != original_full[c * dim + r]
    ]
    details["hessian"] = {
        "packed_length": len(expected_triangle),
        "stored_length": len(stored),
        "full_length": len(original_full),
        "stored_source": trip["stored_hessian_source"],
        "export_vs_document_differing_elements": len(differing),
        "export_vs_document_max_abs_difference_hartree_bohr2": max(differing) if differing else 0.0,
        "document_asymmetric_pairs": len(asymmetric_pairs),
        "document_max_asymmetry_hartree_bohr2": max(asymmetric_pairs) if asymmetric_pairs else 0.0,
    }

    # Geometry: stored (Angstrom) and exported (bohr), within the derived bounds.
    original_bohr = [float(v) for v in original["molecule"]["geometry"]]
    stored_angstrom = [c for atom in trip["stored_atoms"] for c in atom["xyz"]]
    stored_deviation = (
        max(abs(s - o * BACKEND_BOHR_TO_ANGSTROM) for s, o in zip(stored_angstrom, original_bohr, strict=True))
        if len(stored_angstrom) == len(original_bohr)
        else math.inf
    )
    exported_bohr = [float(v) for v in exported["molecule"]["geometry"]]
    exported_bound_bohr = GEOMETRY_BOUND_ANGSTROM / adapter.molecule.BOHR_TO_ANGSTROM
    exported_deviation = (
        max(abs(e - o) for e, o in zip(exported_bohr, original_bohr, strict=True))
        if len(exported_bohr) == len(original_bohr)
        else math.inf
    )
    checks["stored_geometry_within_bound"] = stored_deviation <= GEOMETRY_BOUND_ANGSTROM
    checks["exported_geometry_within_bound"] = exported_deviation <= exported_bound_bohr
    details["geometry"] = {
        "stored_max_abs_deviation_angstrom": _r(stored_deviation, 15),
        "stored_bound_angstrom": GEOMETRY_BOUND_ANGSTROM,
        "exported_max_abs_deviation_bohr": _r(exported_deviation, 15),
        "exported_bound_bohr": _r(exported_bound_bohr, 18),
        "backend_bohr_to_angstrom": BACKEND_BOHR_TO_ANGSTROM,
        "adapter_bohr_to_angstrom": adapter.molecule.BOHR_TO_ANGSTROM,
    }

    om, em = original["molecule"], exported["molecule"]
    checks["identity_preserved"] = (
        om["symbols"] == em["symbols"]
        and int(om["molecular_charge"]) == int(em["molecular_charge"])
        and int(om["molecular_multiplicity"]) == int(em["molecular_multiplicity"])
        and om["mass_numbers"] == em["mass_numbers"]
        and len(om.get("fragments") or [[0]]) == len(em.get("fragments") or [[0]])
    )

    exported_energy = (exported.get("properties") or {}).get("return_energy")
    checks["energy_not_carried"] = exported_energy is None and trip["payload"]["calculation"].get("sp_result") is None
    details["energy"] = {
        "document_return_energy_hartree": original["properties"]["return_energy"],
        "stored": False,
        "exported_return_energy_hartree": exported_energy,
        "reason": (
            "profile v1 maps a driver-hessian document to a freq record holding the matrix only; "
            "the exporter fills properties.return_energy only from a same-level sp record on the "
            "same conformer, and none exists"
        ),
    }

    diff = structural_diff(_as_parsed(original), _as_parsed(exported))
    checks["loss_list_complete"] = diff["lost"] == sorted(EXPECTED_LOST_PATHS) and diff["changed"] == sorted(
        EXPECTED_CHANGED_PATHS
    )
    details["loss_list"] = {
        "lost": diff["lost"],
        "changed": diff["changed"],
        "added": diff["added"],
        "separately_checked": diff["separately_checked"],
        "equal_count": len(diff["equal"]),
        "unexpected_lost": sorted(set(diff["lost"]) - set(EXPECTED_LOST_PATHS)),
        "missing_from_lost": sorted(set(EXPECTED_LOST_PATHS) - set(diff["lost"])),
        "unexpected_changed": sorted(set(diff["changed"]) - set(EXPECTED_CHANGED_PATHS)),
        "missing_from_changed": sorted(set(EXPECTED_CHANGED_PATHS) - set(diff["changed"])),
        "lost_by_import_accounting": _lost_by_import_accounting(diff["lost"], trip),
    }

    try:
        adapter.reader.read_document(json.dumps(exported).encode("utf-8"))
    except adapter.errors.QCSchemaAdapterError as exc:
        reimport_code = exc.code
    else:
        reimport_code = None
    checks["export_reimport_refused"] = reimport_code == adapter.errors.E_TCKDB_EXPORT_REIMPORT_REFUSED
    details["reimport_refusal_code"] = reimport_code

    details["mapping_report"] = trip["mapping_report"]
    details["hessian_reanalysis_status"] = trip["reanalysis_status"]
    details["identity_source"] = trip["payload"]["calculation"]["parameters_json"]["tckdb_qcschema"]["identity_source"]
    details["level_of_theory"] = trip["payload"]["calculation"]["level_of_theory"]

    ordered = {name: bool(checks[name]) for name in ROUND_TRIP_CHECKS}
    return {"checks": ordered, "passed": all(ordered.values()), **details}


# ---------------------------------------------------------------------------
# the cross-program comparison
# ---------------------------------------------------------------------------


def _cross_program(fixtures: SimpleNamespace, trip: dict[str, Any]) -> dict[str, Any]:
    reference = fixtures.reference
    original = fixtures.v2
    natoms = int(reference["natoms"])
    dim = 3 * natoms

    psi4_elements = [atom["element"] for atom in trip["stored_atoms"]]
    psi4_coords = np.array([atom["xyz"] for atom in trip["stored_atoms"]], dtype=float)
    gaussian_elements = list(reference["geometry_angstrom"]["symbols"])
    gaussian_coords = np.array(reference["geometry_angstrom"]["coords"], dtype=float)
    if psi4_elements != gaussian_elements:
        raise FixtureError(f"element order differs: psi4 {psi4_elements} vs gaussian {gaussian_elements}")

    # The Psi4 side is read from what TCKDB stored, not from the document.
    psi4 = _spectrum(trip["stored_triangle"], natoms, psi4_elements, psi4_coords)
    gaussian = _spectrum(reference["hessian_lower_triangle_hartree_bohr2"], natoms, gaussian_elements, gaussian_coords)

    psi4_energy = float(original["properties"]["return_energy"])
    gaussian_energy = float(reference["sp_electronic_energy_hartree"])
    delta_energy = psi4_energy - gaussian_energy

    modes = [
        {
            "mode": index + 1,
            "gaussian_cm1": _r(g, 4),
            "psi4_cm1": _r(p, 4),
            "delta_cm1": _r(p - g, 4),
        }
        for index, (g, p) in enumerate(zip(gaussian["frequencies_cm1"], psi4["frequencies_cm1"], strict=True))
    ]

    stored_zpe = float(reference["zpe_hartree"])
    element_diff = np.abs(psi4["matrix"] - gaussian["matrix"])
    original_full = np.array(original["return_result"], dtype=float).reshape(dim, dim)

    def residue(side: dict[str, Any]) -> dict[str, Any]:
        curvature = side["rigid_body_curvature_cm1"]
        return {
            "lowest_six_unprojected_eigenvalues_cm1": [_r(v, 4) for v in side["lowest_six_unprojected_cm1"]],
            "rigid_body_curvature_cm1": [_r(v, 4) for v in curvature],
            "max_abs_rigid_body_curvature_cm1": _r(max(abs(v) for v in curvature), 4),
            "rigid_body_dimension": side["rigid_body_dimension"],
            "within_frame_consistency_bound": max(abs(v) for v in curvature) <= FRAME_CONSISTENCY_TOLERANCE_CM1,
        }

    return {
        "sign_convention": "delta = psi4 - gaussian",
        "energy": {
            "psi4_hartree": psi4_energy,
            "gaussian_hartree": gaussian_energy,
            "delta_hartree": _r(delta_energy, 13),
            "delta_kj_mol": _r(delta_energy * HARTREE_TO_KJ_MOL, 10),
            "gaussian_printed_decimals": 10,
        },
        "geometry": {
            "max_abs_difference_angstrom": _r(float(np.abs(psi4_coords - gaussian_coords).max()), 13),
            # The geometry is Gaussian's; at Psi4's level it is not exactly
            # stationary, which is what leaves rotational curvature behind.
            "psi4_max_abs_gradient_hartree_bohr": _r(
                max(abs(float(g)) for g in np.asarray(original["properties"]["return_gradient"]).reshape(-1)), 10
            ),
        },
        "masses_amu": {
            element: _r(mass, 9) for element, mass in zip(psi4_elements, psi4["masses_amu"], strict=True)
        },
        "frequencies": modes,
        "vibrational_mode_count": {"psi4": len(psi4["frequencies_cm1"]), "gaussian": len(gaussian["frequencies_cm1"])},
        "imaginary_mode_count": {"psi4": psi4["imaginary_count"], "gaussian": gaussian["imaginary_count"]},
        "zpe": {
            "psi4_hartree": _r(psi4["zpe_hartree"], 12),
            "gaussian_recomputed_hartree": _r(gaussian["zpe_hartree"], 12),
            "gaussian_stored_hartree": stored_zpe,
            "gaussian_stored_printed_decimals": 7,
            "psi4_minus_gaussian_stored_hartree": _r(psi4["zpe_hartree"] - stored_zpe, 12),
            "psi4_minus_gaussian_stored_kj_mol": _r((psi4["zpe_hartree"] - stored_zpe) * HARTREE_TO_KJ_MOL, 9),
            "gaussian_recomputed_minus_stored_hartree": _r(gaussian["zpe_hartree"] - stored_zpe, 12),
            "psi4_minus_gaussian_recomputed_hartree": _r(psi4["zpe_hartree"] - gaussian["zpe_hartree"], 12),
            "convention": "harmonic, unscaled, sum(nu)/2 over real modes, hartree = 219474.6313632 cm^-1",
        },
        "hessian_symmetry": {
            "psi4_document_max_abs_asymmetry_hartree_bohr2": float(np.abs(original_full - original_full.T).max()),
            "psi4_stored": "packed lower triangle; symmetric by construction",
            "gaussian_stored": "packed lower triangle; symmetric by construction",
        },
        "hessian_elementwise_difference": {
            "max_abs_hartree_bohr2": _r(float(element_diff.max()), 10),
            "rms_hartree_bohr2": _r(float(np.sqrt((element_diff**2).mean())), 10),
            "gaussian_printed_significant_figures": 6,
        },
        "rigid_body_residue": {
            "psi4": residue(psi4),
            "gaussian": residue(gaussian),
            "frame_consistency_bound_cm1": FRAME_CONSISTENCY_TOLERANCE_CM1,
            "bound_source": "backend/app/chemistry/normal_modes.py FRAME_CONSISTENCY_TOLERANCE_CM1",
        },
    }


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def _versions(adapter: SimpleNamespace, fixtures: SimpleNamespace) -> dict[str, Any]:
    import qcelemental
    import rdkit
    import sqlalchemy

    provenance = fixtures.v2.get("provenance") or {}
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "rdkit": rdkit.__version__,
        "sqlalchemy": sqlalchemy.__version__,
        "qcelemental": qcelemental.__version__,
        "tckdb_qcschema": adapter.package.__version__,
        "tckdb_client": _pyproject_version(REPO_ROOT / "clients" / "python" / "pyproject.toml"),
        "tckdb_schemas": _pyproject_version(REPO_ROOT / "schemas" / "python" / "tckdb-schemas" / "pyproject.toml"),
        "tckdb_backend": _pyproject_version(BACKEND_ROOT / "pyproject.toml"),
        "psi4": provenance.get("version"),
        "qcengine": provenance.get("qcengine_version"),
        "gaussian": fixtures.reference.get("software"),
    }


def build_report(session: Session, *, adapter: SimpleNamespace | None = None, include_commit: bool = True) -> dict[str, Any]:
    """The whole demonstration as one dictionary.

    ``session`` supplies a connection; everything the round trip writes is
    inside a SAVEPOINT that is rolled back before this returns. Pass
    ``adapter`` (from :func:`adapter_modules`) to reuse an already-imported
    adapter -- the tests do, to perturb it. ``include_commit=False`` drops the
    one value that depends on the checkout rather than on the fixtures.
    """
    if adapter is None:
        with adapter_modules() as modules:
            return build_report(session, adapter=modules, include_commit=include_commit)

    fixtures = load_fixtures()
    trip = _round_trip(session, adapter, fixtures)
    round_trip = _check_round_trip(adapter, fixtures, trip)
    report: dict[str, Any] = {
        "case": FIXTURE_DIR.name,
        "fixtures": {
            "directory": str(FIXTURE_DIR.relative_to(REPO_ROOT)),
            "sha256": dict(sorted(fixtures.digests.items())),
            "digest_mismatches": fixtures.digest_mismatches,
            "psi4_environment_lockfile_sha256": _sha256(PSI4_LOCKFILE.read_bytes()),
        },
        "versions": _versions(adapter, fixtures),
        "declared_smiles": DECLARED_SMILES,
        "round_trip": round_trip,
        "cross_program": _cross_program(fixtures, trip),
    }
    if include_commit:
        report["tckdb_commit"] = tckdb_commit()
    return report


def failed_checks(report: dict[str, Any]) -> list[str]:
    return [name for name, ok in report["round_trip"]["checks"].items() if not ok]


def canonical_json(report: dict[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _f(value: Any, fmt: str) -> str:
    return "-" if value is None else format(value, fmt)


def markdown_summary(report: dict[str, Any]) -> str:
    rt = report["round_trip"]
    cp = report["cross_program"]
    lines = [
        "# QCSchema interchange report",
        "",
        f"- TCKDB commit: `{report.get('tckdb_commit') or 'not recorded'}`",
        f"- Fixtures: `{report['fixtures']['directory']}`",
    ]
    for name, digest in report["fixtures"]["sha256"].items():
        lines.append(f"  - `{name}` sha256 `{digest}`")
    lines.append(
        "- Versions: " + ", ".join(f"{k} {v}" for k, v in sorted(report["versions"].items()) if v is not None)
    )
    lines += ["", "## Round trip", "", "| check | result |", "| --- | --- |"]
    for name, ok in rt["checks"].items():
        lines.append(f"| {name} | {'pass' if ok else 'FAIL'} |")
    hess = rt["hessian"]
    geom = rt["geometry"]
    lines += [
        "",
        f"Hessian: {hess['stored_length']} of {hess['packed_length']} packed elements stored exactly; "
        f"the export differs from the document in {hess['export_vs_document_differing_elements']} of "
        f"{hess['full_length']} elements, by at most {hess['export_vs_document_max_abs_difference_hartree_bohr2']:.3e} "
        f"hartree/bohr^2, which is the document's own asymmetry ({hess['document_asymmetric_pairs']} pairs, "
        f"max {hess['document_max_asymmetry_hartree_bohr2']:.3e}).",
        f"Geometry: stored within {geom['stored_max_abs_deviation_angstrom']:.3e} Angstrom "
        f"(bound {geom['stored_bound_angstrom']:.1e}); exported within {geom['exported_max_abs_deviation_bohr']:.3e} bohr "
        f"(bound {geom['exported_bound_bohr']:.4e}).",
        f"Energy: not carried ({rt['energy']['reason']}).",
        f"Hessian reanalysis status of the imported record: `{rt['hessian_reanalysis_status']}`.",
        "",
        "Lost in the round trip: " + ", ".join(f"`{p}`" for p in rt["loss_list"]["lost"]) + ".",
        "",
        "Changed: " + ", ".join(f"`{p}`" for p in rt["loss_list"]["changed"]) + ".",
        "",
        "## Cross-program measurements (Psi4 minus Gaussian, no tolerance)",
        "",
        "| quantity | Gaussian | Psi4 | delta |",
        "| --- | ---: | ---: | ---: |",
        f"| E (hartree) | {cp['energy']['gaussian_hartree']:.10f} | {cp['energy']['psi4_hartree']:.10f} | "
        f"{cp['energy']['delta_hartree']:.3e} |",
        f"| E (kJ/mol) | | | {cp['energy']['delta_kj_mol']:.3e} |",
    ]
    for mode in cp["frequencies"]:
        lines.append(
            f"| nu{mode['mode']} (cm^-1) | {mode['gaussian_cm1']:.4f} | {mode['psi4_cm1']:.4f} | {mode['delta_cm1']:+.4f} |"
        )
    zpe = cp["zpe"]
    lines += [
        f"| ZPE recomputed (hartree) | {zpe['gaussian_recomputed_hartree']:.10f} | {zpe['psi4_hartree']:.10f} | "
        f"{zpe['psi4_minus_gaussian_recomputed_hartree']:.3e} |",
        f"| ZPE vs stored Gaussian {zpe['gaussian_stored_hartree']} (hartree) | "
        f"{zpe['gaussian_recomputed_minus_stored_hartree']:.3e} | {zpe['psi4_minus_gaussian_stored_hartree']:.3e} | |",
        f"| imaginary modes | {cp['imaginary_mode_count']['gaussian']} | {cp['imaginary_mode_count']['psi4']} | |",
        f"| max rigid-body curvature (cm^-1) | "
        f"{cp['rigid_body_residue']['gaussian']['max_abs_rigid_body_curvature_cm1']:.4f} | "
        f"{cp['rigid_body_residue']['psi4']['max_abs_rigid_body_curvature_cm1']:.4f} | |",
        "",
        f"Rigid-body bound (Phase B): {cp['rigid_body_residue']['frame_consistency_bound_cm1']} cm^-1 "
        f"({cp['rigid_body_residue']['bound_source']}).",
        "Six lowest unprojected eigenvalues (cm^-1): Gaussian "
        + ", ".join(_f(v, ".4f") for v in cp["rigid_body_residue"]["gaussian"]["lowest_six_unprojected_eigenvalues_cm1"])
        + "; Psi4 "
        + ", ".join(_f(v, ".4f") for v in cp["rigid_body_residue"]["psi4"]["lowest_six_unprojected_eigenvalues_cm1"])
        + ".",
        f"Hessian element-wise |Psi4 - Gaussian|: max {cp['hessian_elementwise_difference']['max_abs_hartree_bohr2']:.3e}, "
        f"rms {cp['hessian_elementwise_difference']['rms_hartree_bohr2']:.3e} hartree/bohr^2.",
        f"Psi4 document asymmetry: max {cp['hessian_symmetry']['psi4_document_max_abs_asymmetry_hartree_bohr2']:.3e} "
        "hartree/bohr^2.",
    ]
    return "\n".join(lines) + "\n"


def paper_generator(session: Session) -> dict[str, Any]:
    """Registered in ``backend/scripts/paper/registry.py``.

    The same report without the checkout commit, which a restored deposit's
    byte comparison could not reproduce from a different checkout. Reads the
    committed fixtures; the session only lends a connection for the rolled-
    back round trip.
    """
    return build_report(session, include_commit=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json-out", type=Path, default=None, help="write the canonical JSON report here")
    parser.add_argument("--markdown-out", type=Path, default=None, help="write the Markdown summary here")
    parser.add_argument("--quiet", action="store_true", help="do not print the summary to stdout")
    args = parser.parse_args(argv)

    if not qcelemental_available():
        print("error: qcelemental is not installed; pip install 'qcelemental==0.51.2'", file=sys.stderr)
        return EXIT_UNUSABLE

    from app.api.deps import engine

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(bind=connection) as session:
                    report = build_report(session)
            finally:
                transaction.rollback()
    except FixtureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    summary = markdown_summary(report)
    if args.json_out is not None:
        args.json_out.write_text(canonical_json(report))
    if args.markdown_out is not None:
        args.markdown_out.write_text(summary)
    if not args.quiet:
        print(summary)

    failures = failed_checks(report)
    if failures:
        print("RESULT: ROUND TRIP FAILED: " + ", ".join(failures), file=sys.stderr)
        return EXIT_ROUND_TRIP_FAILED
    print("RESULT: round trip exact; cross-program numbers are measurements, not judged.", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
