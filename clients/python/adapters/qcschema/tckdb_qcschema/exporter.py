"""Stored calculation -> MolSSI QCSchema v2 ``AtomicResult`` document (C-Q3).

The inverse direction of :mod:`tckdb_qcschema.mapping`: given a client and a
calculation handle, :func:`export_calculation` reads the calculation's
*stored* values through ``tckdb-client`` -- never re-deriving anything -- and
returns a v2 ``AtomicResult`` dict, validated with
``qcelemental.models.v2.AtomicResult`` before it is returned.

Only two calculation types are supported:

* ``sp`` -> driver ``energy``. ``return_result`` and
  ``properties.return_energy`` are both the calculation's stored
  ``electronic_energy_hartree``.
* ``freq`` with a stored Hessian -> driver ``hessian``. ``return_result`` is
  the full symmetric 3N x 3N matrix, unpacked from the packed lower
  triangle TCKDB stores in the *exact* inverse of the order the importer
  packs it in (:func:`tckdb_qcschema.hessian.unpack_lower_triangle`).
  ``properties.return_energy`` is populated only when a same-level ``sp``
  calculation sharing this record's conformer observation is found (see
  :func:`_same_level_sp_energy`) -- best-effort, opportunistic enrichment,
  never required; omitted (not ``null``) otherwise.

``opt`` (and every other calculation type -- ``scan``, ``irc``,
``path_search``, ``composite``, ``imported``) is refused with
``export_unsupported_type``: no trajectory is stored for an ``opt`` record,
so relabelling its final energy as a single point would be a claim the
record does not support.

Molecule: geometry Å -> bohr using the *same* ``BOHR_TO_ANGSTROM`` constant
the importer uses (:data:`tckdb_qcschema.molecule.BOHR_TO_ANGSTROM`),
applied in the inverse direction (``bohr = angstrom / BOHR_TO_ANGSTROM``);
symbols and Cartesian coordinates from the calculation's geometry; charge
and multiplicity from the geometry's owning species-entry or
transition-state-entry identity; a single fragment, every atom real (TCKDB
has no ghost-atom representation to begin with, so this is automatic, not
re-checked). ``model.method``/``model.basis`` are the calculation's level
of theory, verbatim.

**Isotopes, a known gap.** The scientific geometry read
(``GET /scientific/geometries/{handle}``, ``GeometryAtomPayload``) carries
no per-atom isotope field -- confirmed empirically against a live capture,
see ``scripts/capture_read_fixtures.py``'s module docstring -- although the
isotope is stored (``GeometryAtom.isotope_mass_number`` in the backend
database) and *is* served by the unrelated, internal-id-only
``GET /geometries/{id}`` route. ``export_calculation`` therefore always
exports ``mass_numbers`` as each element's standard tabulated nuclide
(``qcelemental.periodictable.to_A``), never a recorded non-standard
isotope. This is silently correct for every calculation whose geometry
carries no isotope (the overwhelming common case, and the only case this
adapter's own round-trip corpus exercises), and silently *wrong* -- not
refused, not flagged -- for a record whose stored geometry names a
non-standard nuclide. Closing this needs a backend schema change (adding
``isotope_mass_number`` to ``GeometryAtomPayload``), out of scope for a
client-side adapter package under the sovereignty rule that nothing under
``backend/`` changes here; noted for a future work package instead of
worked around by inventing a second backend read this package has no
authority to add.

**The calculation-id gap for ``freq`` export.** ``GET /calculations/{id}/hessian``
(C-Q2) is a plain, pre-Phase-D route that takes only the integer
``calculation_id`` -- never a public ref -- yet the scientific calculation
detail read hides ``calculation_id`` under this backend's internal-id
visibility policy even with ``include=internal_ids`` requested (also
measured empirically, same capture). So exporting a ``freq`` record needs
either a deployment that permits internal ids, or a caller that already
supplies the integer id as ``calculation_ref_or_id`` -- which
``export_calculation`` uses directly without ever needing it echoed back
by the scientific read. A ref-only handle on a policy-hiding deployment is
refused ``export_calculation_id_unavailable`` rather than guessed.
"""

from __future__ import annotations

from typing import Any

import qcelemental as qcel
import qcelemental.models.v2 as qcel_v2

from . import __version__ as _ADAPTER_VERSION
from .errors import (
    E_EXPORT_CALCULATION_ID_UNAVAILABLE,
    E_EXPORT_ENERGY_UNAVAILABLE,
    E_EXPORT_GEOMETRY_UNAVAILABLE,
    E_EXPORT_HESSIAN_UNAVAILABLE,
    E_EXPORT_IDENTITY_UNAVAILABLE,
    E_EXPORT_LEVEL_OF_THEORY_UNAVAILABLE,
    E_EXPORT_UNSUPPORTED_TYPE,
    QCSchemaAdapterError,
)
from .hessian import unpack_lower_triangle
from .molecule import BOHR_TO_ANGSTROM

_periodic_table = qcel.periodictable

#: Calculation types this adapter can export. Every other type (``opt``
#: above all -- see the module docstring) is refused.
_EXPORTABLE_TYPES = frozenset({"sp", "freq"})


def _client_version(client: Any) -> tuple[str, str]:
    """``(version, source)`` -- the API version if the deployment names one,
    else the ``tckdb-client`` package version.

    Measured 2026-09-20: this backend's ``GET /status`` reports only the
    Alembic schema revision (``alembic_version``), not an application
    semver, and ``GET /meta`` reports client-compatibility bounds, not its
    own version either. So there is no genuine "API version" this client
    can read today, and ``source`` is always ``"tckdb_client_package"`` in
    practice -- the ``client_status_api_version`` branch exists for a
    future backend that starts reporting one (``status()["api_version"]``,
    the one key this checks for) rather than being dead code no deployment
    can reach.
    """
    try:
        status = client.status()
    except Exception:  # noqa: BLE001 - status is best-effort, never fatal here
        status = None
    if isinstance(status, dict):
        api_version = status.get("api_version")
        if isinstance(api_version, str) and api_version:
            return api_version, "client_status_api_version"

    from tckdb_client import __version__ as client_package_version

    return client_package_version, "tckdb_client_package"


def _geometry_identity(geometry: dict) -> tuple[int, int]:
    identity = geometry.get("identity")
    kind = identity.get("kind") if identity else None
    if not identity or not kind:
        raise QCSchemaAdapterError(
            E_EXPORT_IDENTITY_UNAVAILABLE,
            "the calculation's geometry has no resolvable owner identity "
            "(identity is null or ambiguous across owners); cannot "
            "recover molecular_charge/molecular_multiplicity to export.",
        )
    owner = identity.get(kind) or {}
    charge, multiplicity = owner.get("charge"), owner.get("multiplicity")
    if charge is None or multiplicity is None:
        raise QCSchemaAdapterError(
            E_EXPORT_IDENTITY_UNAVAILABLE,
            f"the calculation's geometry owner ({kind}) carries no "
            f"charge/multiplicity.",
        )
    return int(charge), int(multiplicity)


def _build_molecule(geometry: dict) -> qcel_v2.Molecule:
    """One exported ``Molecule``: Å -> bohr (inverse of the importer's
    conversion, same constant), standard-nuclide ``mass_numbers`` (see the
    module docstring's isotope gap), single fragment, every atom real.
    """
    atoms = sorted(geometry.get("atoms") or [], key=lambda a: a["atom_index"])
    if not atoms:
        raise QCSchemaAdapterError(
            E_EXPORT_GEOMETRY_UNAVAILABLE,
            "the calculation's geometry carries no atoms to export.",
        )
    symbols = [a["element"] for a in atoms]
    geometry_bohr: list[float] = []
    for a in atoms:
        geometry_bohr.extend(
            v / BOHR_TO_ANGSTROM for v in (a["x"], a["y"], a["z"])
        )
    mass_numbers = [int(_periodic_table.to_A(sym)) for sym in symbols]
    charge, multiplicity = _geometry_identity(geometry)

    return qcel_v2.Molecule(
        symbols=symbols,
        geometry=geometry_bohr,
        molecular_charge=charge,
        molecular_multiplicity=multiplicity,
        mass_numbers=mass_numbers,
        # qcelemental rounds Molecule.geometry to GEOMETRY_NOISE=8 decimal
        # places (bohr) by default on every construction -- not merely for
        # its own hashing, see qcelemental.models.v2.molecule.Molecule.__init__
        # (measured 2026-09-20: constructing with the default noise turned
        # a stored geometry already accurate to ~1e-12 bohr into one only
        # accurate to ~1e-8 bohr, five orders of magnitude past the
        # importer's own ten-decimal-Angstrom bound). 14 decimal places is
        # near float64's full precision for these O(1) bohr magnitudes and
        # keeps the export step's own rounding contribution negligible
        # against that importer-side bound -- see the geometry bound
        # derivation in tests/test_round_trip.py.
        geometry_noise=14,
    )


def _level_of_theory(record: dict) -> tuple[str, str | None]:
    lot = record.get("level_of_theory")
    if not lot or not lot.get("method"):
        raise QCSchemaAdapterError(
            E_EXPORT_LEVEL_OF_THEORY_UNAVAILABLE,
            "the calculation carries no level of theory (method/basis) "
            "to export.",
        )
    return lot["method"], lot.get("basis")


def _first_geometry_link(record: dict) -> dict | None:
    for block_name in ("input_geometries", "output_geometries"):
        links = record.get(block_name) or []
        if links:
            return links[0]
    return None


def _same_level_sp_energy(
    client: Any, *, record: dict, level_of_theory_ref: str | None
) -> float | None:
    """Best-effort ``properties.return_energy`` for a ``freq`` export.

    Opportunistic enrichment (mirrors the DAG-edges-are-opportunistic
    convention elsewhere in TCKDB), never required: an ``sp`` calculation
    is not linked to a ``freq`` calculation by any ``calculation_dependency``
    edge (that table has no such role -- ``single_point_on`` names an
    ``opt`` parent, not a ``freq`` sibling), so the only load-bearing
    anchor two calculations of the same conformer share is
    ``conformer_observation_id``. Searches for an ``sp`` calculation on the
    same conformer observation at the *identical* level-of-theory ref (not
    method/basis text, which case-fragments identity across programs --
    see the C1 mapping table); returns the first one found with a
    recorded energy, or ``None`` when none exists. A search failure is
    never fatal to the export -- this field is optional.
    """
    conformer = record.get("conformer")
    conformer_observation_ref = (
        conformer.get("conformer_observation_ref") if conformer else None
    )
    if not conformer_observation_ref or not level_of_theory_ref:
        return None

    try:
        search = client.search_calculations(
            conformer_observation_ref=conformer_observation_ref,
            calculation_type="sp",
            lot_ref=level_of_theory_ref,
            include=["results"],
        )
    except Exception:  # noqa: BLE001 - opportunistic; never fails the export
        return None

    records = search.get("records") if isinstance(search, dict) else None
    for sibling in records or []:
        sp = ((sibling.get("results") or {}).get("sp")) or {}
        energy = sp.get("electronic_energy_hartree")
        if energy is not None:
            return float(energy)
    return None


def _resolve_calculation_id(
    record: dict, *, calculation_ref_or_id: str | int
) -> int:
    calc_id = record.get("calculation", {}).get("calculation_id")
    if calc_id is not None:
        return int(calc_id)
    if isinstance(calculation_ref_or_id, int):
        return calculation_ref_or_id
    if isinstance(calculation_ref_or_id, str) and calculation_ref_or_id.isdigit():
        return int(calculation_ref_or_id)
    raise QCSchemaAdapterError(
        E_EXPORT_CALCULATION_ID_UNAVAILABLE,
        "exporting a freq record needs the integer calculation_id for "
        "GET /calculations/{id}/hessian, which this deployment's "
        "internal-id visibility policy hides from the scientific read "
        "and which the supplied handle "
        f"{calculation_ref_or_id!r} does not itself carry as an integer. "
        "Call export_calculation(client, <integer calculation id>) "
        "instead of a public ref on this deployment.",
    )


def export_calculation(client: Any, calculation_ref_or_id: str | int) -> dict:
    """Read one stored calculation and export it as a QCSchema v2 ``AtomicResult``.

    :param client: A ``tckdb_client.TCKDBClient`` (or, in tests, a stub
        exposing ``get_calculation``, ``get_geometry``,
        ``get_calculation_hessian`` and ``search_calculations`` with the
        same contracts).
    :param calculation_ref_or_id: A ``calc_…`` public ref, or an integer
        (or digit-string) ``calculation_id``. An integer is required to
        export a ``freq`` record on a deployment that hides
        ``calculation_id`` from the scientific read -- see the module
        docstring's "calculation-id gap".
    :returns: A plain dict, already validated with
        ``qcelemental.models.v2.AtomicResult.model_validate`` before
        being returned (``model_dump(mode="json", exclude_none=True)``).
    :raises QCSchemaAdapterError: one of the ``export_*`` codes in
        :mod:`tckdb_qcschema.errors`.
    """
    detail = client.get_calculation(
        calculation_ref_or_id,
        include=["results", "input_geometries", "output_geometries"],
    )
    record = detail["record"]
    calc_type = record["calculation"]["type"]

    if calc_type not in _EXPORTABLE_TYPES:
        raise QCSchemaAdapterError(
            E_EXPORT_UNSUPPORTED_TYPE,
            f"calculation type {calc_type!r} is not exportable by "
            f"tckdb-qcschema -- only {sorted(_EXPORTABLE_TYPES)} are. "
            f"'opt' in particular is refused deliberately: no trajectory "
            f"is stored, so relabelling a final energy as a single point "
            f"would be a claim the record does not support.",
            calculation_type=calc_type,
        )

    method, basis = _level_of_theory(record)
    level_of_theory_ref = (record.get("level_of_theory") or {}).get(
        "level_of_theory_ref"
    )

    if calc_type == "sp":
        results = record.get("results") or {}
        sp = results.get("sp") or {}
        energy = sp.get("electronic_energy_hartree")
        if energy is None:
            raise QCSchemaAdapterError(
                E_EXPORT_ENERGY_UNAVAILABLE,
                "the sp calculation has no recorded "
                "electronic_energy_hartree to export.",
            )
        energy = float(energy)

        geometry_link = _first_geometry_link(record)
        if geometry_link is None:
            raise QCSchemaAdapterError(
                E_EXPORT_GEOMETRY_UNAVAILABLE,
                "the sp calculation has no linked geometry to export.",
            )
        geometry = client.get_geometry(geometry_link["geometry_ref"])

        driver = "energy"
        return_result: Any = energy
        return_energy: float | None = energy

    else:  # calc_type == "freq"
        calc_id = _resolve_calculation_id(
            record, calculation_ref_or_id=calculation_ref_or_id
        )
        try:
            hessian = client.get_calculation_hessian(calc_id)
        except Exception as exc:  # noqa: BLE001 - narrowed by status_code below
            if getattr(exc, "status_code", None) == 404:
                raise QCSchemaAdapterError(
                    E_EXPORT_HESSIAN_UNAVAILABLE,
                    "the freq calculation has no stored Hessian "
                    "(GET /calculations/{id}/hessian returned 404).",
                ) from exc
            raise

        natoms = hessian["natoms"]
        full_matrix = unpack_lower_triangle(
            hessian["lower_triangle_hartree_bohr2"], natoms
        )
        geometry = client.get_geometry(hessian["geometry_id"])

        driver = "hessian"
        return_result = full_matrix
        return_energy = _same_level_sp_energy(
            client, record=record, level_of_theory_ref=level_of_theory_ref
        )

    molecule = _build_molecule(geometry)

    software_release = record.get("software_release") or {}
    api_version, api_version_source = _client_version(client)

    specification = qcel_v2.AtomicSpecification(
        driver=driver,
        model=qcel_v2.Model(method=method, basis=basis),
    )
    input_data = qcel_v2.AtomicInput(molecule=molecule, specification=specification)
    properties = qcel_v2.AtomicProperties(return_energy=return_energy)
    provenance = qcel_v2.Provenance(
        creator="TCKDB",
        version=api_version,
        routine=f"tckdb-qcschema export/{_ADAPTER_VERSION}",
    )
    extras = {
        "tckdb": {
            "calculation_ref": record["calculation"].get("calculation_ref"),
            "software_release": (
                f"{software_release.get('software')}/{software_release.get('version')}"
                if software_release.get("software")
                else None
            ),
            "level_of_theory": level_of_theory_ref,
            "hessian_source": hessian.get("source") if calc_type == "freq" else None,
            "hessian_parser_version": (
                hessian.get("parser_version") if calc_type == "freq" else None
            ),
            "export_adapter_version": _ADAPTER_VERSION,
            "qcelemental_version": qcel.__version__,
            "api_version_source": api_version_source,
        }
    }

    result = qcel_v2.AtomicResult(
        input_data=input_data,
        molecule=molecule,
        properties=properties,
        return_result=return_result,
        success=True,
        provenance=provenance,
        extras=extras,
    )

    # Validated once at construction by pydantic; re-validate explicitly
    # from the dict form about to be returned, per the C-Q3 brief -- this
    # is what actually catches a round-trip-breaking mistake in the dict
    # shape (e.g. exclude_none stripping something qcelemental requires),
    # not merely the in-memory model qcelemental already accepted.
    document = result.model_dump(mode="json", exclude_none=True)
    qcel_v2.AtomicResult.model_validate(document)
    return document


__all__ = ["export_calculation"]
