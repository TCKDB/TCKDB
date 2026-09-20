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

**Isotopes, closed by reading the legacy surface.** The scientific
geometry read (``GET /scientific/geometries/{handle}``,
``GeometryAtomPayload``) carries no per-atom isotope field -- confirmed
empirically against a live capture, see
``scripts/capture_read_fixtures.py``'s module docstring -- so
``_build_molecule`` never derives ``mass_numbers`` from it. **This does
not need a backend schema change**, and an earlier revision of this
docstring was wrong to say so: the isotope is stored
(``GeometryAtom.isotope_mass_number`` in the backend database) and is
already served, per atom, by the unrelated, internal-id-only,
pre-Phase-D ``GET /geometries/{id}`` route (``GeometryRead.atoms``,
``GeometryAtomRead.isotope_mass_number`` --
:mod:`app.schemas.entities.geometry`) and by its list sibling
``GET /geometries?geom_hash=...`` (``geom_hash`` is ``unique=True`` on
the ``geometry`` table, so it resolves to exactly one row). Both sit
under the exact same legacy-read auth gate
(``require_auth_for_legacy_reads``) as ``GET /calculations/{id}/hessian``,
which this adapter already reads for a ``freq`` export.

So ``export_calculation`` reads per-atom isotopes from that legacy
surface -- ``GET /geometries?geom_hash=<hash>`` for an ``sp`` export
(``geom_hash`` comes from the calculation's own geometry link, no extra
lookup needed) and ``GET /geometries/{id}`` for a ``freq`` export
(``id`` is the Hessian read's own ``geometry_id``) -- via
:func:`tckdb_client.TCKDBClient.get_json`, cross-checks the returned
atoms element-for-element against the atoms already read from the
scientific geometry (refusing ``export_geometry_mismatch`` if they
disagree), and uses each atom's ``isotope_mass_number``: ``null`` means
the element's standard tabulated nuclide (``GeometryAtomBase``'s own
docstring: "``None`` means the element's most abundant natural
isotope"), *not* "unrecorded" -- so ``qcelemental.periodictable.to_A``
is the correct, non-guessing fallback for exactly that atom, never a
blanket default applied regardless of what the row says. A recorded
non-standard value is used verbatim. ``masses`` is left for
``qcelemental.models.v2.Molecule`` to derive on its own from
``symbols`` + ``mass_numbers`` (measured: it does, e.g. ``mass_numbers``
16/2/1 for O/D/H yields ``masses`` 15.995/2.014/1.008) -- never computed
or invented here.

**``D``/``T`` element symbols.** ``geometry_atom.element`` keeps a
deuterium/tritium label exactly as deposited (``D``/``T``, not collapsed
to ``H`` -- ``backend/app/chemistry/isotopes.py``'s own docstring: doing
so "would destroy the depositor's own isotope labelling"), and both
geometry reads hand this exporter that raw symbol. Two things follow,
mirroring ``backend/app/chemistry/normal_modes.py``'s ``atomic_mass``
(lines 405-444) and ``backend/app/chemistry/geometry.py``'s
``resolve_element_symbol`` (~lines 64-95) exactly rather than by
coincidence: a ``null`` ``isotope_mass_number`` on a ``D``/``T`` atom
resolves to the mass number the symbol itself names (2/3), and
``Molecule.symbols`` collapses ``D``/``T`` to ``H`` (measured
2026-09-20: constructing a ``Molecule`` with a raw ``"D"`` symbol raises
``qcelemental.exceptions.NotAnElementError``, even though
``to_A("D")`` itself returns ``2`` without complaint) -- the isotope
stays carried in ``mass_numbers``, never lost by the collapse. An
*explicit*, non-standard ``isotope_mass_number`` that contradicts a
``D``/``T`` label (e.g. ``D`` recorded with mass number 1 or 3) is
refused ``export_geometry_mismatch`` -- ``atomic_mass`` itself has no
such check (an explicit value simply overrides there), but silently
picking one of two contradictory claims is exactly the guess this
adapter exists to refuse instead of making.

When the legacy read cannot be completed (this deployment's legacy-read
auth gate rejects it -- 401/403, e.g. a hosted deployment with no API
key configured -- the id/hash is not found, or a ``geom_hash`` query
comes back with an empty ``items`` list), the export is refused
``export_isotopes_unavailable`` rather than silently falling back to
standard nuclides for every atom.

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
    E_EXPORT_GEOMETRY_MISMATCH,
    E_EXPORT_GEOMETRY_UNAVAILABLE,
    E_EXPORT_HESSIAN_UNAUTHORIZED,
    E_EXPORT_HESSIAN_UNAVAILABLE,
    E_EXPORT_IDENTITY_UNAVAILABLE,
    E_EXPORT_ISOTOPES_UNAVAILABLE,
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


#: Element symbols that name a *nuclide* rather than an element, mapped to
#: the mass number they stand for -- the exact mirror of
#: ``HYDROGEN_ISOTOPE_SYMBOLS`` in ``backend/app/chemistry/isotopes.py``
#: (only hydrogen has these, and only these two). ``geometry_atom.element``
#: keeps ``D``/``T`` verbatim rather than collapsing them to ``H`` at
#: deposit time (same module, same reasoning: collapsing would destroy the
#: depositor's own isotope labelling), so both the scientific and the
#: legacy geometry read can hand this exporter a raw ``"D"``/``"T"``
#: symbol. Duplicated here rather than imported: this adapter package has
#: no dependency on the backend's ``app.*`` code (sovereignty -- the
#: adapter only ever talks to the backend over HTTP).
_HYDROGEN_ISOTOPE_SYMBOLS: dict[str, int] = {"D": 2, "T": 3}


def _resolve_nuclide_symbol(symbol: str) -> str:
    """The *element* an XYZ symbol names, not the nuclide it names.

    Mirrors ``backend/app/chemistry/geometry.py``'s ``resolve_element_symbol``
    (~lines 64-95) exactly: ``D``/``T`` resolve to ``H``, everything else is
    unchanged. Required because ``qcelemental.models.v2.Molecule.symbols``
    must be real periodic-table element symbols -- constructing one with a
    raw ``"D"`` raises ``qcelemental.exceptions.NotAnElementError`` (measured
    2026-09-20) even though ``qcelemental.periodictable.to_A("D")`` happily
    returns ``2``. The isotope this atom actually is stays carried
    separately, in ``mass_numbers``.
    """
    return "H" if symbol in _HYDROGEN_ISOTOPE_SYMBOLS else symbol


def _mass_numbers_from_isotope_atoms(
    raw_symbols: list[str], isotope_atoms: list[dict]
) -> list[int]:
    """Per-atom ``mass_numbers``, honest per the module docstring's
    "Isotopes" section: a ``null`` ``isotope_mass_number`` means the
    element's standard tabulated nuclide (``to_A`` is the correct
    fallback for *that* atom, not a blanket default) -- except for a
    ``D``/``T`` symbol, whose standard nuclide is the mass number the
    symbol itself names (2/3), read off ``_HYDROGEN_ISOTOPE_SYMBOLS``
    rather than ``to_A`` so the fallback matches
    ``backend/app/chemistry/normal_modes.py``'s ``atomic_mass`` (~line
    429: ``mass_number = HYDROGEN_ISOTOPE_SYMBOLS.get(symbol)`` when
    ``isotope_mass_number is None``) exactly rather than coincidentally.
    A recorded value is used verbatim -- *except* an explicit,
    non-standard mass number on a ``D``/``T``-labelled atom (e.g. ``D``
    with ``isotope_mass_number=1`` or ``3``) contradicts the label the
    depositor themselves wrote, and is refused
    ``export_geometry_mismatch`` rather than silently exported as one
    value or the other (this refusal is this adapter's own "reject,
    don't guess" choice -- ``atomic_mass`` itself has no such check,
    since an explicit ``isotope_mass_number`` simply overrides there;
    see its docstring's "which an explicit isotope_mass_number
    overrides").

    ``raw_symbols`` are compared and resolved *before* any ``D``/``T``
    -> ``H`` collapse (:func:`_resolve_nuclide_symbol` is applied
    separately, only to what actually goes into ``Molecule.symbols``) --
    comparing post-collapse would hide a legacy read naming ``T`` where
    the scientific read named ``D`` behind an identical ``"H"``.

    Refuses ``export_geometry_mismatch`` if the legacy read's atoms do
    not line up, element-for-element in ``atom_index`` order, with the
    atoms already read from the scientific geometry -- two reads of what
    should be the same stored geometry disagreeing is a refusal, not
    something to silently paper over.
    """
    sorted_isotope_atoms = sorted(
        isotope_atoms, key=lambda a: a["atom_index"]
    )
    if len(sorted_isotope_atoms) != len(raw_symbols):
        raise QCSchemaAdapterError(
            E_EXPORT_GEOMETRY_MISMATCH,
            "the legacy per-atom isotope read returned "
            f"{len(sorted_isotope_atoms)} atoms, but the scientific "
            f"geometry read returned {len(raw_symbols)}; refusing rather "
            "than exporting mismatched coordinates and isotopes.",
        )

    mass_numbers: list[int] = []
    for index, (symbol, isotope_atom) in enumerate(
        zip(raw_symbols, sorted_isotope_atoms)
    ):
        # GeometryAtom.element is a Postgres CHAR(2); a single-letter symbol
        # comes back blank-padded from the legacy read (the scientific read
        # already strips this server-side -- see
        # app.services.scientific_read.geometry -- the legacy route does
        # not), so strip before comparing/using it.
        legacy_symbol = (isotope_atom.get("element") or "").strip()
        if legacy_symbol != symbol:
            raise QCSchemaAdapterError(
                E_EXPORT_GEOMETRY_MISMATCH,
                f"atom {index + 1}: the legacy per-atom isotope read names "
                f"element {legacy_symbol!r}, but the scientific geometry "
                f"read names {symbol!r} at the same atom_index; refusing "
                "rather than exporting mismatched coordinates and "
                "isotopes.",
            )
        mass_number = isotope_atom.get("isotope_mass_number")
        nuclide_mass_number = _HYDROGEN_ISOTOPE_SYMBOLS.get(symbol)
        if mass_number is None:
            # null means the element's most abundant natural isotope for
            # *this* atom (GeometryAtomBase's own docstring), not
            # "unrecorded". For a plain element symbol that is to_A; for
            # a D/T nuclide symbol, D/T's own implied mass number *is*
            # that standard nuclide -- both are non-guessing fallbacks
            # scoped to the one atom whose row actually says so.
            if nuclide_mass_number is not None:
                mass_numbers.append(nuclide_mass_number)
            else:
                mass_numbers.append(int(_periodic_table.to_A(symbol)))
        else:
            mass_number = int(mass_number)
            if (
                nuclide_mass_number is not None
                and mass_number != nuclide_mass_number
            ):
                raise QCSchemaAdapterError(
                    E_EXPORT_GEOMETRY_MISMATCH,
                    f"atom {index + 1}: element {symbol!r} names a specific "
                    f"nuclide (mass number {nuclide_mass_number}), but "
                    f"isotope_mass_number={mass_number} was recorded, "
                    "contradicting the atom's own element label; refusing "
                    "rather than exporting either value.",
                )
            mass_numbers.append(mass_number)
    return mass_numbers


def _build_molecule(geometry: dict, isotope_atoms: list[dict]) -> qcel_v2.Molecule:
    """One exported ``Molecule``: Å -> bohr (inverse of the importer's
    conversion, same constant), per-atom ``mass_numbers`` read from the
    legacy isotope surface (see the module docstring's "Isotopes"
    section), single fragment, every atom real.
    """
    atoms = sorted(geometry.get("atoms") or [], key=lambda a: a["atom_index"])
    if not atoms:
        raise QCSchemaAdapterError(
            E_EXPORT_GEOMETRY_UNAVAILABLE,
            "the calculation's geometry carries no atoms to export.",
        )
    raw_symbols = [a["element"] for a in atoms]
    geometry_bohr: list[float] = []
    for a in atoms:
        geometry_bohr.extend(
            v / BOHR_TO_ANGSTROM for v in (a["x"], a["y"], a["z"])
        )
    mass_numbers = _mass_numbers_from_isotope_atoms(raw_symbols, isotope_atoms)
    # Resolved only now, after both the coordinate-order symbols and the
    # mass-number fallback/contradiction check have used the raw (possibly
    # D/T) symbol -- Molecule.symbols itself must be real element symbols.
    symbols = [_resolve_nuclide_symbol(s) for s in raw_symbols]
    charge, multiplicity = _geometry_identity(geometry)

    return qcel_v2.Molecule(
        symbols=symbols,
        geometry=geometry_bohr,
        molecular_charge=charge,
        molecular_multiplicity=multiplicity,
        mass_numbers=mass_numbers,
        # masses is deliberately never passed: qcelemental.models.v2.Molecule
        # derives it itself from symbols + mass_numbers (measured 2026-09-20:
        # mass_numbers [16, 2, 1] for O/D/H yields masses
        # [15.99491462, 2.01410178, 1.00782503]) -- inventing it here would
        # risk disagreeing with qcelemental's own tabulated values.
        #
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


def _fetch_isotope_atoms(
    client: Any,
    *,
    calc_type: str,
    geometry_link: dict | None,
    hessian: dict | None,
) -> list[dict]:
    """Per-atom isotopes from the legacy ``GET /geometries`` surface (see
    the module docstring's "Isotopes" section) -- ``GET
    /geometries?geom_hash=<hash>`` for an ``sp`` export (``geom_hash`` is
    already on the calculation's own geometry link, no extra lookup
    needed), ``GET /geometries/{id}`` for a ``freq`` export (``id`` is the
    Hessian read's own ``geometry_id``). Any way this read fails to
    produce a usable row -- the legacy-read auth gate rejecting it
    (401/403), not found (404), an empty ``items`` list for a ``geom_hash``
    query, or any other error -- is refused ``export_isotopes_unavailable``
    rather than silently falling back to standard nuclides for every atom.
    """
    try:
        if calc_type == "sp":
            geom_hash = (geometry_link or {}).get("geom_hash")
            if not geom_hash:
                raise QCSchemaAdapterError(
                    E_EXPORT_ISOTOPES_UNAVAILABLE,
                    "the sp calculation's geometry link carries no "
                    "geom_hash to look up per-atom isotopes on the legacy "
                    "GET /geometries route.",
                )
            response = client.get_json(f"/geometries?geom_hash={geom_hash}")
            items = (
                response.get("items") if isinstance(response, dict) else None
            )
            if not items:
                raise QCSchemaAdapterError(
                    E_EXPORT_ISOTOPES_UNAVAILABLE,
                    "GET /geometries?geom_hash=<hash> returned no rows; "
                    "cannot recover per-atom isotopes for this sp export.",
                )
            atoms = items[0].get("atoms")
        else:  # calc_type == "freq"
            geometry_id = (hessian or {}).get("geometry_id")
            if geometry_id is None:
                raise QCSchemaAdapterError(
                    E_EXPORT_ISOTOPES_UNAVAILABLE,
                    "the stored Hessian carries no geometry_id to look up "
                    "per-atom isotopes on the legacy GET /geometries/{id} "
                    "route.",
                )
            response = client.get_json(f"/geometries/{geometry_id}")
            atoms = (
                response.get("atoms") if isinstance(response, dict) else None
            )
        if not atoms:
            raise QCSchemaAdapterError(
                E_EXPORT_ISOTOPES_UNAVAILABLE,
                "the legacy geometry read returned no atoms; cannot "
                "recover per-atom isotopes for this export.",
            )
        return atoms
    except QCSchemaAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure here is refused
        raise QCSchemaAdapterError(
            E_EXPORT_ISOTOPES_UNAVAILABLE,
            "could not read per-atom isotopes from the legacy "
            f"GET /geometries route ({exc.__class__.__name__}: {exc}); "
            "refused rather than defaulting every atom to its standard "
            "nuclide.",
        ) from exc


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
        isotope_atoms = _fetch_isotope_atoms(
            client, calc_type="sp", geometry_link=geometry_link, hessian=None
        )

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
            status_code = getattr(exc, "status_code", None)
            if status_code == 404:
                raise QCSchemaAdapterError(
                    E_EXPORT_HESSIAN_UNAVAILABLE,
                    "the freq calculation has no stored Hessian "
                    "(GET /calculations/{id}/hessian returned 404).",
                ) from exc
            if status_code in (401, 403):
                raise QCSchemaAdapterError(
                    E_EXPORT_HESSIAN_UNAUTHORIZED,
                    "GET /calculations/{id}/hessian was rejected by this "
                    "deployment's legacy-read auth gate "
                    f"(status {status_code}); configure an API key rather "
                    "than treating this as \"no Hessian\".",
                ) from exc
            raise

        natoms = hessian["natoms"]
        full_matrix = unpack_lower_triangle(
            hessian["lower_triangle_hartree_bohr2"], natoms
        )
        geometry = client.get_geometry(hessian["geometry_id"])
        isotope_atoms = _fetch_isotope_atoms(
            client, calc_type="freq", geometry_link=None, hessian=hessian
        )

        driver = "hessian"
        return_result = full_matrix
        return_energy = _same_level_sp_energy(
            client, record=record, level_of_theory_ref=level_of_theory_ref
        )

    molecule = _build_molecule(geometry, isotope_atoms)

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
