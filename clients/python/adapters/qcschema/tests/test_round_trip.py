"""Round-trip test for the QCSchema exporter (C-Q3).

original document -> import mapping -> pinned reads -> export -> compare.

No live server, no database: :class:`StubClient` serves the exact JSON
response bodies a real backend gave for the ``energy_v1`` and ``hessian_v1``
cases of the C-Q2 backend corpus, captured once by
``scripts/capture_read_fixtures.py`` and pinned under
``tests/fixtures/reads/<case>/``.

**Reusing the v1/v2 siblings.** ``energy_v1``/``energy_v2`` (and
``hessian_v1``/``hessian_v2``) are two different QCSchema *family*
wrappings of the byte-identical molecule/method/basis/energy/Hessian
content -- measured: ``backend/tests/fixtures/qcschema/energy_v1/payload.json``
and ``.../energy_v2/payload.json`` diff to nothing outside
``calculation.parameters_json`` bookkeeping. So importing either produces
the same stored calculation, and this test exercises all four original
documents (``energy_v1``, ``energy_v2``, ``hessian_v1``, ``hessian_v2``)
against the two pinned-read captures (``energy_v1``, ``hessian_v1``)
without needing to capture reads for the v2 siblings separately.

**The geometry bound, derived, not guessed.** Two roundings apply on the
way from a genuine bohr coordinate to the coordinate this test compares
against:

1. Import: ``tckdb_qcschema.molecule.to_geometry_payload`` writes each
   Cartesian coordinate to the stored ``xyz_text`` with ``"%.10f"`` --
   ten decimal places, Angstrom. That bounds the coordinate stored in
   TCKDB to within half the last printed decimal place of the true
   converted value: ``0.5e-10`` Angstrom.
2. Export: :func:`tckdb_qcschema.exporter._build_molecule` converts the
   stored Angstrom value back to bohr with the same ``BOHR_TO_ANGSTROM``
   constant (inverse direction), so the same absolute Angstrom error
   carries through as an absolute *bohr* error of
   ``0.5e-10 / BOHR_TO_ANGSTROM``.

qcelemental's own ``Molecule`` construction contributes a third,
independent rounding (``GEOMETRY_NOISE = 8`` decimal places of bohr by
default -- measured directly against the pinned qcelemental==0.51.2
release: constructing a ``Molecule`` from a geometry already accurate to
~1e-12 bohr came back accurate to only ~1e-8, five orders of magnitude
past the bound above). That default would dominate and break this bound,
so the exporter passes ``geometry_noise=14`` explicitly (see the comment
in ``exporter.py``) to keep its own contribution negligible against the
bound this test derives and checks -- the bound below states only the
import-side rounding, matching the C-Q3 brief, because the export-side
qcelemental rounding is deliberately suppressed rather than budgeted for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tckdb_qcschema.errors import (
    E_EXPORT_UNSUPPORTED_TYPE,
    E_TCKDB_EXPORT_REIMPORT_REFUSED,
    QCSchemaAdapterError,
)
from tckdb_qcschema.exporter import export_calculation
from tckdb_qcschema.molecule import BOHR_TO_ANGSTROM
from tckdb_qcschema.reader import read_document

FIXTURES = Path(__file__).parent / "fixtures"
READS = FIXTURES / "reads"

#: See the module docstring's "geometry bound, derived, not guessed".
GEOMETRY_BOUND_BOHR = 0.5e-10 / BOHR_TO_ANGSTROM

#: The exact, fixed set of top-level (or family-appropriate nested) fields
#: on the *original* QCSchema document that ``export_calculation`` never
#: carries into the exported document. Checked in :func:`_assert_loss_set`
#: below by the strongest assertion this corpus's fixtures support --
#: content-inequality against a real, non-trivial original value where one
#: exists (``provenance``, ``extras``, ``stdout``), and "never present in
#: the export regardless" where the fixture's own original value is
#: itself trivial (``id``, ``keywords``, ``wavefunction``, ``native_files``,
#: ``stderr`` are ``None``/``{}``/falsy in every one of these four
#: fixtures -- see ``_assert_loss_set``'s per-field comments).
DELIBERATELY_LOST_FIELDS = frozenset(
    {
        "provenance",
        "keywords",
        "extras",
        "id",
        "wavefunction",
        "native_files",
        "stdout",
        "stderr",
    }
)


class _FakeHTTPError(Exception):
    """Stands in for ``tckdb_client.errors.TCKDBHTTPError``.

    ``exporter.py`` only duck-types the real exception on its
    ``.status_code`` attribute (see ``export_calculation``'s Hessian-fetch
    branch) -- it never imports ``tckdb_client.errors`` -- so a stub client
    can raise this instead of depending on the real client's exception
    hierarchy, keeping these tests free of any live-client/network
    dependency.
    """

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class StubClient:
    """Serves the pinned backend read fixtures under ``tests/fixtures/reads/``.

    No live server, no database -- every method reads and returns already-
    captured JSON. ``get_calculation``/``get_geometry`` ignore their
    ``include``/``profile`` kwargs and their handle argument entirely
    (the pinned fixture already reflects exactly the include set
    ``export_calculation`` asks for -- see
    ``scripts/capture_read_fixtures.py``); ``search_calculations`` always
    reports no siblings, matching every one of this corpus's fixtures
    (neither ``energy_v1`` nor ``hessian_v1`` has a linked ``sp`` at the
    same level of theory).
    """

    def __init__(self, read_case: str) -> None:
        case_dir = READS / read_case
        self.calculation = json.loads((case_dir / "calculation.json").read_text())
        self.geometries = json.loads((case_dir / "geometries.json").read_text())
        self.hessian_response = json.loads((case_dir / "hessian.json").read_text())
        self.legacy_geometry = json.loads(
            (case_dir / "legacy_geometry.json").read_text()
        )

    def get_calculation(self, calculation_ref_or_id, *, include=None, profile=None):
        del calculation_ref_or_id, include, profile
        return self.calculation

    def get_geometry(self, geometry_handle, *, include=None, profile=None):
        del include, profile
        return self.geometries[str(geometry_handle)]

    def get_calculation_hessian(self, calculation_id):
        del calculation_id
        if self.hessian_response["status_code"] != 200:
            raise _FakeHTTPError(self.hessian_response["status_code"])
        return self.hessian_response["body"]

    def get_json(self, path):
        assert path == self.legacy_geometry["request_path"], (
            f"get_json called with {path!r}, expected "
            f"{self.legacy_geometry['request_path']!r}"
        )
        return self.legacy_geometry["response"]

    def search_calculations(self, **filters):
        del filters
        return {"records": []}

    def status(self):
        return {}


def _original_method_basis(document: dict, family: str) -> tuple[str, str | None]:
    if family == "v1":
        model = document["model"]
    else:
        model = document["input_data"]["specification"]["model"]
    return model["method"], model.get("basis")


def _original_keywords(document: dict, family: str) -> dict:
    if family == "v1":
        return document.get("keywords") or {}
    return (document.get("input_data", {}).get("specification", {}) or {}).get(
        "keywords"
    ) or {}


def _assert_loss_set(original: dict, exported: dict, family: str) -> None:
    """Every field in :data:`DELIBERATELY_LOST_FIELDS`, checked -- and only those.

    ``checked`` accumulates one entry per field this function actually
    verified; the final assertion pins that set equal to the declared
    constant, so a checker quietly going missing (or a field's declared
    membership drifting from what is actually verified below) fails loudly
    rather than silently narrowing what this test protects.
    """
    checked: set[str] = set()

    # provenance: exported creator is always "TCKDB", never the original's
    # (which, in this corpus, is always a real ESS: "Psi4").
    original_provenance = original.get("provenance") or {}
    assert original_provenance.get("creator") not in (None, "TCKDB")
    assert exported["provenance"]["creator"] == "TCKDB"
    assert exported["provenance"] != original_provenance
    checked.add("provenance")

    # extras: exported extras is TCKDB's own diagnostic block under the
    # "tckdb" key, never a copy of the original's -- which, in this
    # corpus, always carries a real qcvars dict.
    original_extras = original.get("extras") or {}
    assert "qcvars" in original_extras
    exported_extras = exported.get("extras") or {}
    assert "qcvars" not in exported_extras
    assert set(exported_extras) == {"tckdb"}
    checked.add("extras")

    # stdout: this corpus's fixtures always carry real (non-empty) stdout;
    # never exported.
    assert original.get("stdout")
    assert "stdout" not in exported
    checked.add("stdout")

    # stderr, id, wavefunction, native_files, keywords: falsy/trivial in
    # every fixture this corpus carries, so the strongest check available
    # here is "the export never claims to carry it either" rather than a
    # genuine before/after content inequality.
    assert not original.get("stderr")
    assert "stderr" not in exported
    checked.add("stderr")

    assert original.get("id") is None
    assert exported.get("id") is None
    checked.add("id")

    assert original.get("wavefunction") is None
    assert "wavefunction" not in exported
    checked.add("wavefunction")

    assert original.get("native_files") == {}
    assert exported.get("native_files", {}) == {}
    checked.add("native_files")

    assert _original_keywords(original, family) == {}
    assert exported["input_data"]["specification"].get("keywords", {}) == {}
    checked.add("keywords")

    assert checked == DELIBERATELY_LOST_FIELDS


#: (original document case, pinned-read case). See the module docstring's
#: "reusing the v1/v2 siblings".
ROUND_TRIP_CASES = (
    ("energy_v1", "energy_v1"),
    ("energy_v2", "energy_v1"),
    ("hessian_v1", "hessian_v1"),
    ("hessian_v2", "hessian_v1"),
)


@pytest.mark.parametrize(
    "document_case,read_case", ROUND_TRIP_CASES, ids=[c[0] for c in ROUND_TRIP_CASES]
)
def test_round_trip(document_case: str, read_case: str) -> None:
    original_bytes = (FIXTURES / document_case / "document.json").read_bytes()
    original = json.loads(original_bytes)

    # The source fixture itself must actually import cleanly -- also
    # confirms which family (v1/v2) this document is, for the
    # family-aware original-side field lookups below.
    record = read_document(original_bytes)

    client = StubClient(read_case)
    exported = export_calculation(client, 123)

    # --- symbols, charge, multiplicity ------------------------------
    assert exported["molecule"]["symbols"] == original["molecule"]["symbols"]
    assert int(exported["molecule"]["molecular_charge"]) == int(
        original["molecule"]["molecular_charge"]
    )
    assert int(exported["molecule"]["molecular_multiplicity"]) == int(
        original["molecule"]["molecular_multiplicity"]
    )

    # --- mass_numbers, element-for-element (C-Q3 review round 2) -------
    # Every pinned-read case in this corpus is ordinary water (no recorded
    # isotope on any atom), so this is the standard-nuclide path --
    # test_exporter.py's D2O-style cases cover a recorded non-standard
    # isotope directly against a hand-built stub.
    assert exported["molecule"]["mass_numbers"] == original["molecule"]["mass_numbers"]

    # --- method, basis -----------------------------------------------
    original_method, original_basis = _original_method_basis(original, record.family)
    exported_spec = exported["input_data"]["specification"]
    assert exported_spec["model"]["method"] == original_method
    assert exported_spec["model"].get("basis") == original_basis

    # --- geometry, within the derived bound ---------------------------
    original_geometry = original["molecule"]["geometry"]
    exported_geometry = exported["molecule"]["geometry"]
    assert len(exported_geometry) == len(original_geometry)
    for coordinate_index, (original_value, exported_value) in enumerate(
        zip(original_geometry, exported_geometry)
    ):
        diff = abs(original_value - exported_value)
        assert diff <= GEOMETRY_BOUND_BOHR, (
            f"{document_case}: coordinate {coordinate_index} differs by "
            f"{diff!r} bohr, exceeding the derived bound "
            f"{GEOMETRY_BOUND_BOHR!r}"
        )

    # --- energy / Hessian, exact --------------------------------------
    if document_case.startswith("energy"):
        assert exported_spec["driver"] == "energy"
        assert exported["return_result"] == original["return_result"]
        assert exported["properties"]["return_energy"] == original["return_result"]
    else:
        assert exported_spec["driver"] == "hessian"
        assert len(exported["return_result"]) == len(original["return_result"])
        for element_index, (original_value, exported_value) in enumerate(
            zip(original["return_result"], exported["return_result"])
        ):
            assert exported_value == original_value, (
                f"{document_case}: Hessian element {element_index} is not "
                f"exact: {exported_value!r} != {original_value!r}"
            )
        # Neither pinned-read case has a same-level sp calculation linked
        # to the freq record's conformer observation (StubClient.search_calculations
        # always reports none), so properties.return_energy must be
        # omitted -- never a null placeholder.
        assert "return_energy" not in (exported.get("properties") or {})

    # --- the complete, exact loss set ----------------------------------
    _assert_loss_set(original, exported, record.family)

    # --- an export can never be re-imported ----------------------------
    reexport_bytes = json.dumps(exported).encode("utf-8")
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(reexport_bytes)
    assert excinfo.value.code == E_TCKDB_EXPORT_REIMPORT_REFUSED


def test_export_refuses_opt() -> None:
    """``opt`` calculations carry no trajectory; exporting one is refused."""
    calculation = json.loads(
        (READS / "energy_v1" / "calculation.json").read_text()
    )
    calculation["record"]["calculation"]["type"] = "opt"

    class _OptStubClient(StubClient):
        def __init__(self) -> None:
            super().__init__("energy_v1")
            self.calculation = calculation

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(_OptStubClient(), 1)
    assert excinfo.value.code == E_EXPORT_UNSUPPORTED_TYPE
