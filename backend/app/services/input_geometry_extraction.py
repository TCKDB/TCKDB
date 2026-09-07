"""Re-derive a calculation's true starting geometry from its own artifacts.

Sibling of :mod:`app.services.sp_energy_extraction` and
:mod:`app.services.hessian_extraction`: pure-text parsing (no DB) feeds a
best-effort, never-fatal DB hook, following the same "reject, don't guess"
discipline and the same banner-sniff dispatch
(:mod:`app.services.ess_software_detection`) the sibling extractors use.

**The gap this closes.** ``app.services.calculation_resolution`` documents,
at ``_INPUT_GEOMETRY_TYPES``, that an ``opt`` calculation never gets an
automatic input-geometry row: "the true input is the pre-opt xyz ... which
the producer does not currently surface". When a depositor's own payload
*does* declare an ``input_geometries`` entry for an ``opt`` calc but has
nothing better to send than the converged result, that entry canonicalizes
to the same ``Geometry`` row as the calc's output -- a placeholder, not a
second observation. Either way (no input row, or one that duplicates the
output), the calculation's own uploaded artifacts usually *do* carry the
real starting point: a Gaussian ``input`` deck's Cartesian block, an ORCA
``input`` deck's inline ``* xyz`` block, or -- when only the output log
survives -- the first geometry block either program prints before doing any
optimisation step.

**Scope: ``opt`` calculations only.** ``freq``/``sp`` already have a
producer-agnostic, non-degenerate input fallback (the calc's own conformer
geometry, via ``_INPUT_GEOMETRY_TYPES``), so they cannot reach the
"input == output" state this module repairs. See
:func:`_calculation_is_eligible_type`.

**Never upgrades a real, distinct input.** Exactly like the Hessian hook's
"fill-when-absent only ... no full-matrix verify/mismatch path in v1", this
module only acts when the calculation's input geometry is *absent* or
*degenerate* (identical to its output). A calculation that already carries
a distinct, producer-declared input geometry is never touched, regardless
of what a later artifact might imply.

**Two callers, one shared operation.** :func:`extract_and_link_input_geometry`
is the one place that decides eligibility, parses, mints/dedupes the
``Geometry`` row (:func:`app.services.geometry_resolution.resolve_geometry_payload`
-- the same dedup-by-hash entry point the upload path uses), and links it.
:func:`try_extract_input_geometry_from_artifact_upload` is the upload-time
hook (mirrors :func:`app.services.sp_energy_extraction.try_reconcile_sp_energy_from_output_upload`:
runs once per persisted artifact, best-effort via
:func:`app.services.best_effort.isolated_best_effort`, decodes the
in-memory base64 payload so no object-storage round-trip is needed).
:func:`extract_and_link_input_geometry_from_stored_artifacts` is the
backfill counterpart (mirrors
:func:`app.services.calculation_parameter_extraction.try_extract_parameters_from_input_artifact_row`:
reads bytes from object storage by SHA-256) -- it has full visibility of
*all* of a calculation's stored ``input``/``output_log`` artifacts up
front, so it can honour "prefer the input file over the log" regardless of
upload order, which the single-artifact upload hook cannot always do (see
the hook's docstring for the documented ordering caveat).

**Basin assignment and coverage are untouched by this module.** Conformer
basin matching (DR-0005) runs once, at initial conformer/species/reaction
upload time, from the *producer-declared* conformer geometry -- all four
call sites (``app.workflows.conformer``, ``computed_species``,
``computed_reaction``, and ``network_pdep``) call ``resolve_conformer_group``
with the payload's own ``xyz_atoms``, never from a calculation's artifacts,
and never re-run later. Nothing in this
module calls or influences that path. The group-evidence coverage counters
(``app.services.scientific_read.conformers._build_group_evidence_summary``)
are also unaffected: ``evidence_coverage`` and ``optimization_chain_count``
are computed from ``Calculation.type``/``conformer_observation_id``/
``calculation_dependency`` alone, and ``geometry_count`` is scoped to
``CalculationOutputGeometry`` only -- this module never writes either of
those, only ``calculation_input_geometry``, which none of the three
counters reads.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from enum import Enum

from rdkit import Chem
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chemistry.geometry import normalize_element_symbol
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactKind,
    CalculationInputGeometrySource,
    CalculationType,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.record_review import RecordReview
from app.schemas.fragments.artifact import ArtifactIn
from app.schemas.fragments.geometry import GeometryPayload
from app.services.artifact_integrity import record_from_error
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    load_artifact_bytes,
)
from app.services.best_effort import isolated_best_effort
from app.services.calculation_geometry_composition import (
    assert_calculation_geometry_composition,
)
from app.services.ess_software_detection import SoftwareName, detect_software_from_text
from app.services.gaussian_output_parser import extract_first_geometry
from app.services.geometry_resolution import resolve_geometry_payload

logger = logging.getLogger(__name__)

#: Bumped when the parsing contract changes. Not currently stored anywhere
#: (``calculation_input_geometry`` carries no parser-version column), kept
#: for parity with the sibling extractors and for log lines.
INPUT_GEOMETRY_PARSER_VERSION = "input_geometry_v1"

#: ``opt`` only -- see the module docstring. ``freq``/``sp`` already get a
#: non-degenerate input fallback and cannot reach the state this repairs.
_ELIGIBLE_CALC_TYPES = (CalculationType.opt,)

#: Artifact kinds that can carry a starting geometry.
_ELIGIBLE_ARTIFACT_KINDS = (ArtifactKind.input, ArtifactKind.output_log)


# ---------------------------------------------------------------------------
# Pure parsing: text in, structured atoms out. No DB dependencies.
# ---------------------------------------------------------------------------


class InputGeometryParseAction(str, Enum):
    """What one parse attempt against one artifact's text concluded."""

    extracted = "extracted"
    not_determinable = "not_determinable"


@dataclass(frozen=True)
class ParsedInputGeometry:
    """The outcome of one parse attempt.

    ``reason`` is populated only for ``not_determinable`` and is written for
    a human reading a log line or a backfill summary -- never a coordinate,
    never a database id.
    """

    action: InputGeometryParseAction
    atoms: tuple[tuple[str, float, float, float], ...] | None
    origin: str | None
    reason: str | None = None


def _extracted(
    atoms: list[tuple[str, float, float, float]], *, origin: str
) -> ParsedInputGeometry:
    return ParsedInputGeometry(
        action=InputGeometryParseAction.extracted,
        atoms=tuple(atoms),
        origin=origin,
    )


def _not_determinable(reason: str) -> ParsedInputGeometry:
    return ParsedInputGeometry(
        action=InputGeometryParseAction.not_determinable,
        atoms=None,
        origin=None,
        reason=reason,
    )


# --- Element-token normalisation, shared by both input-deck parsers -------

#: A bare element symbol: one or two letters, any case (the DB's own
#: canonicality CHECK -- ``ck_geometry_atom_element_canonical`` -- is
#: ``btrim(element) ~ '^[A-Z][a-z]?$'``, one-or-two letters, so this mirrors
#: that shape rather than a stricter one). Shape alone is *not* sufficient
#: to accept a token, though -- see :func:`_normalize_element_token`.
_PLAIN_ELEMENT_TOKEN = re.compile(r"^[A-Za-z]{1,2}$")
#: An element symbol with a trailing Gaussian/ORCA atom-instance label
#: (``O1``, ``Cl2``) -- digits appended purely to distinguish otherwise-
#: identical atoms (ONIOM layers, per-atom basis-set assignment), not an
#: isotope or fragment marker.
_LABELLED_ELEMENT_TOKEN = re.compile(r"^([A-Za-z]{1,2})\d+$")

#: The only tokens accepted *without* a periodic-table lookup: deuterium
#: and tritium. Both are legitimate deposited symbols every ESS program in
#: this codebase's scope accepts (ADR 0008; see
#: :func:`app.chemistry.geometry.resolve_element_symbol`), and neither is
#: a real element RDKit's periodic table recognises, so validating them
#: the same way as everything else would refuse correct, common
#: isotope-labelled decks. Every other one-or-two-letter token -- Gaussian's
#: ``X`` (dummy atom), ``Bq`` (ghost/counterpoise atom), or nonsense like
#: ``Xx`` -- has exactly this shape too and names no real element, so shape
#: alone must not be the acceptance test.
_ISOTOPE_SYMBOL_ALLOWLIST = frozenset({"D", "T"})

_PERIODIC_TABLE = Chem.GetPeriodicTable()


def _normalize_element_token(token: str) -> str | None:
    """Resolve one atom-column token to a plain element symbol, or ``None``.

    Handles the two extra forms Gaussian/ORCA accept in a Cartesian atom
    column beyond a bare symbol:

    * an **atomic number** (``8`` -> ``O``), looked up through
      :class:`rdkit.Chem.GetPeriodicTable`;
    * a **trailing numeric label** used to distinguish atom instances
      (``O1`` -> ``O``, ``Cl2`` -> ``Cl``) -- stripped, then validated
      the same way a bare symbol is.

    A bare (or label-stripped) symbol is normalised to title case
    (:func:`app.chemistry.geometry.normalize_element_symbol` -- RDKit's
    periodic table is case-sensitive: ``GetAtomicNumber("o")`` fails where
    ``GetAtomicNumber("O")`` succeeds) and accepted only if it is ``D``/``T``
    (:data:`_ISOTOPE_SYMBOL_ALLOWLIST`) or a real element the periodic
    table recognises. This is what rejects Gaussian's ``X`` (dummy atom)
    and ``Bq`` (ghost/counterpoise atom) -- both have the shape of a plain
    symbol but name no element -- rather than accepting anything shaped
    like one or two letters.

    Anything else -- isotope/fragment syntax such as ``C-13`` (Gaussian's
    dash-isotope notation) or ``C-0.5``, or a token that is not a plausible
    one-or-two-letter symbol even after stripping a label -- also returns
    ``None`` so the caller rejects the whole deck rather than handing an
    unresolvable token to :func:`app.services.geometry_resolution.resolve_geometry_payload`,
    which would otherwise mint a ``Geometry``/``GeometryAtom`` row that
    fails the database's element-canonicality CHECK and returns ``None``
    from a code path that does not expect it.
    """
    if token.isdigit():
        atomic_number = int(token)
        if not (1 <= atomic_number <= 118):
            return None
        try:
            symbol = _PERIODIC_TABLE.GetElementSymbol(atomic_number)
        except (RuntimeError, OverflowError, ValueError):
            return None
        return symbol or None

    match = _LABELLED_ELEMENT_TOKEN.match(token)
    candidate = match.group(1) if match else token
    if not _PLAIN_ELEMENT_TOKEN.match(candidate):
        return None

    normalized = normalize_element_symbol(candidate)
    if normalized in _ISOTOPE_SYMBOL_ALLOWLIST:
        return normalized
    try:
        _PERIODIC_TABLE.GetAtomicNumber(normalized)
    except RuntimeError:
        return None
    return normalized


# --- Gaussian input deck (.gjf / .com): Cartesian block only --------------

#: A Cartesian atom row: element, optional integer freeze-code, then three
#: floats. The freeze-code column (``0``/``-1``) appears in ``opt=ModRedundant``
#: decks; ordinary decks omit it. Anything else on the line -- a Z-matrix
#: reference/variable name, a mismatched column count -- fails this and the
#: whole block is rejected rather than guessed at.
_GAUSSIAN_CART_ROW_FLOAT = re.compile(r"^[+-]?\d+\.\d+$")
_GAUSSIAN_CART_ROW_INT = re.compile(r"^-?\d+$")

#: Gaussian's ``Units`` route keyword, non-Angstrom option. ``Units(Bohr)``,
#: ``units=bohr`` and ``units=(bohr,...)`` (a multi-option parenthesized
#: form, like ``opt=(maxcycles=100,tight)``) are all Gaussian-legal
#: spellings; ``AU`` is a documented synonym for ``Bohr``. Angstrom is
#: the default and the only unit this module converts none of, so any of
#: these must reject the deck rather than silently store Bohr-magnitude
#: numbers as if they were Angstrom (a 1/0.529177 = 1.8897x inflation).
#:
#: Gaussian's parenthesized option lists are order-free --
#: ``units=(rad,bohr)`` and ``units=(bohr,rad)`` are the same declaration
#: -- so ``bohr``/``au`` is matched *anywhere* inside the list
#: (``[^)\n]*?`` up to the closing paren or line end), not only as the
#: first option. A first-token-only anchor here previously let
#: ``units=(rad,bohr)`` and its permutations through unrejected.
_GAUSSIAN_NON_ANGSTROM_UNITS = re.compile(
    r"\bunits\s*[=(]\s*\(?[^)\n]*?\b(?:bohrs?|au)\b", re.IGNORECASE
)


def _gaussian_route_text(lines: list[str]) -> str | None:
    """Return the joined route-section text (the ``#...`` line block).

    Mirrors the route-finding half of :func:`_locate_gaussian_molecule_spec`
    (first line starting with ``#``, collected through the next blank
    line) but returns the joined text rather than an atom-start index, so
    a units declaration that may wrap across physical lines is checked as
    one string.
    """
    route_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            route_start = i
            break
    if route_start is None:
        return None

    parts: list[str] = []
    i = route_start
    while i < len(lines) and lines[i].strip() != "":
        parts.append(lines[i].strip())
        i += 1
    return " ".join(parts)


def _locate_gaussian_molecule_spec(lines: list[str]) -> int | None:
    """Return the index of the first atom line, or ``None`` if not found.

    Gaussian free-format input is Link0 (``%...``) * -> route (starts with
    ``#``, ends at the next blank line) -> blank -> title (one non-blank
    line) -> blank -> charge/multiplicity (two integers) -> atom lines ->
    blank/EOF. Only the landmarks needed to find where atom lines start are
    parsed; charge and multiplicity are not needed by this module (geometry
    rows carry no charge/multiplicity) and are not returned.
    """
    route_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            route_start = i
            break
    if route_start is None:
        return None

    i = route_start
    while i < len(lines) and lines[i].strip() != "":
        i += 1
    while i < len(lines) and lines[i].strip() == "":  # blank(s) after route
        i += 1
    if i >= len(lines):
        return None
    while i < len(lines) and lines[i].strip() != "":  # title line(s)
        i += 1
    while i < len(lines) and lines[i].strip() == "":  # blank(s) after title
        i += 1
    if i >= len(lines):
        return None

    charge_mult = lines[i].split()
    if len(charge_mult) != 2 or not all(
        _GAUSSIAN_CART_ROW_INT.match(tok) for tok in charge_mult
    ):
        return None
    return i + 1


def parse_gaussian_input_geometry(text: str) -> ParsedInputGeometry:
    """Parse a raw Gaussian input deck's Cartesian coordinate block.

    Rejects (``not_determinable``, never guesses) when: the route declares
    non-Angstrom (``Bohr``/``AU``) units (see
    :data:`_GAUSSIAN_NON_ANGSTROM_UNITS` -- this module never converts,
    only Angstrom decks are accepted), no route/title/charge-multiplicity
    landmark is found, the block is empty, any atom line is not a plain
    ``element [flag] x y z`` row (the shape a Z-matrix or mixed
    Cartesian/Z-matrix deck, which Gaussian also accepts, fails, since
    converting internal coordinates to Cartesian is out of scope), or an
    atom's element column cannot be resolved to a plain symbol
    (:func:`_normalize_element_token`).
    """
    lines = text.splitlines()

    route = _gaussian_route_text(lines)
    if route is not None and _GAUSSIAN_NON_ANGSTROM_UNITS.search(route):
        return _not_determinable("declares Bohr units; not converted")

    atom_start = _locate_gaussian_molecule_spec(lines)
    if atom_start is None:
        return _not_determinable(
            "could not locate a Gaussian route/title/charge-multiplicity "
            "block in the input file"
        )

    atoms: list[tuple[str, float, float, float]] = []
    for line in lines[atom_start:]:
        if not line.strip():
            break
        parts = line.split()
        if len(parts) == 4:
            element, xs, ys, zs = parts
        elif len(parts) == 5 and _GAUSSIAN_CART_ROW_INT.match(parts[1]):
            element, _flag, xs, ys, zs = parts
        else:
            return _not_determinable(
                "Gaussian input coordinate block is not a simple Cartesian "
                "layout (element [flag] x y z) -- possibly Z-matrix/internal "
                "coordinates, which are not converted"
            )
        if not (
            _GAUSSIAN_CART_ROW_FLOAT.match(xs)
            and _GAUSSIAN_CART_ROW_FLOAT.match(ys)
            and _GAUSSIAN_CART_ROW_FLOAT.match(zs)
        ):
            return _not_determinable(
                "Gaussian input coordinate block contains a non-numeric "
                "coordinate -- possibly Z-matrix/internal coordinates, "
                "which are not converted"
            )
        normalized_element = _normalize_element_token(element)
        if normalized_element is None:
            return _not_determinable(
                f"Gaussian input element token '{element}' could not be "
                "resolved to a plain symbol or atomic number -- "
                "isotope/fragment labels are not converted"
            )
        atoms.append((normalized_element, float(xs), float(ys), float(zs)))

    if not atoms:
        return _not_determinable("Gaussian input coordinate block is empty")
    return _extracted(atoms, origin="gaussian_input")


# --- Gaussian output log: first Input/Standard orientation block ----------


def parse_gaussian_log_input_geometry(text: str) -> ParsedInputGeometry:
    """Parse the first geometry block from a Gaussian output log.

    Delegates to :func:`app.services.gaussian_output_parser.extract_first_geometry`
    (Input orientation preferred, falling back to Standard orientation --
    see that function's docstring for why the preference is reversed from
    the *final*-geometry extractor).
    """
    try:
        atoms = list(extract_first_geometry(text.splitlines()))
    except ValueError as exc:
        return _not_determinable(str(exc))
    if not atoms:
        return _not_determinable("Gaussian log geometry block is empty")
    return _extracted(atoms, origin="gaussian_log")


# --- ORCA input deck (.inp): inline "* xyz" block only ---------------------

_ORCA_XYZ_OPEN = re.compile(r"^\*\s*xyz\s+(-?\d+)\s+(\d+)\s*$", re.IGNORECASE)
_ORCA_XYZFILE = re.compile(r"^\*\s*xyzfile\b", re.IGNORECASE)
_ORCA_INTERNAL = re.compile(r"^\*\s*(int|gzmt)\b", re.IGNORECASE)

#: ORCA's simple-input ``!`` keyword line declaring non-Angstrom
#: coordinates (``! B3LYP def2-TZVP Opt Bohrs``). Angstrom is the default.
_ORCA_BANG_LINE_BOHRS = re.compile(r"^\s*!.*\bbohrs?\b", re.IGNORECASE | re.MULTILINE)
#: The longer-form ``%coords ... Units Bohrs ... end`` block declares the
#: same thing for coordinates given that way. Matched anywhere in the text
#: rather than scoped strictly to a ``%coords`` block: over-rejecting a
#: deck that happens to contain this exact phrase elsewhere is far safer
#: than under-rejecting one that genuinely declares Bohr coordinates.
_ORCA_COORDS_UNITS_BOHRS = re.compile(r"\bunits\s+bohrs?\b", re.IGNORECASE)


def _orca_declares_bohr_units(text: str) -> bool:
    return bool(
        _ORCA_BANG_LINE_BOHRS.search(text) or _ORCA_COORDS_UNITS_BOHRS.search(text)
    )


def parse_orca_input_geometry(text: str) -> ParsedInputGeometry:
    """Parse an ORCA input deck's inline ``* xyz <charge> <mult>`` block.

    Rejects (never converts) a deck declaring Bohr coordinates -- either
    the ``!`` simple-input line (``Bohrs``) or a ``%coords ... Units
    Bohrs`` block -- since this module only stores Angstrom. Also rejects
    ``* xyzfile`` (coordinates live in a separate file this module never
    sees) and ``* int`` / ``* gzmt`` (internal coordinates / Z-matrix --
    converting to Cartesian is out of scope) with a reason naming which
    one, rather than guessing. The first matching directive line wins.
    """
    if _orca_declares_bohr_units(text):
        return _not_determinable("declares Bohr units; not converted")

    lines = text.splitlines()
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if _ORCA_XYZFILE.match(stripped):
            return _not_determinable(
                "ORCA input specifies coordinates via '* xyzfile', an "
                "external file this artifact does not carry -- the "
                "starting geometry cannot be recovered from the input "
                "deck alone"
            )
        if _ORCA_INTERNAL.match(stripped):
            directive = stripped.split()[0] + " " + stripped.split()[1]
            return _not_determinable(
                f"ORCA input specifies coordinates via '{directive}' "
                "(internal coordinates/Z-matrix), which is not converted "
                "to Cartesian"
            )
        if _ORCA_XYZ_OPEN.match(stripped):
            atom_lines: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() != "*":
                atom_lines.append(lines[j])
                j += 1
            if j >= len(lines):
                return _not_determinable(
                    "ORCA input '* xyz' block has no closing '*' -- "
                    "truncated input"
                )
            atoms: list[tuple[str, float, float, float]] = []
            for atom_line in atom_lines:
                if not atom_line.strip():
                    continue
                parts = atom_line.split()
                if len(parts) != 4:
                    return _not_determinable(
                        "ORCA input '* xyz' block contains a row that is "
                        "not 'element x y z'"
                    )
                element, xs, ys, zs = parts
                normalized_element = _normalize_element_token(element)
                if normalized_element is None:
                    return _not_determinable(
                        f"ORCA input element token '{element}' could not be "
                        "resolved to a plain symbol or atomic number -- "
                        "isotope/fragment labels are not converted"
                    )
                try:
                    atoms.append(
                        (normalized_element, float(xs), float(ys), float(zs))
                    )
                except ValueError:
                    return _not_determinable(
                        "ORCA input '* xyz' block contains a non-numeric "
                        "coordinate"
                    )
            if not atoms:
                return _not_determinable("ORCA input '* xyz' block is empty")
            return _extracted(atoms, origin="orca_input")
    return _not_determinable(
        "no ORCA coordinate directive ('* xyz' / '* xyzfile' / '* int' / "
        "'* gzmt') found in input"
    )


# --- ORCA output log: first "CARTESIAN COORDINATES (ANGSTROEM)" block -----

_ORCA_CART_ANGSTROM_HEADER = "CARTESIAN COORDINATES (ANGSTROEM)"


def parse_orca_log_input_geometry(text: str) -> ParsedInputGeometry:
    """Parse the first Angstrom Cartesian block from an ORCA output log.

    This is the *resolved* geometry ORCA echoes for its first SCF/step --
    the atoms and coordinates it actually used, in Angstrom -- regardless of
    whether the input deck declared them inline or via ``* xyzfile``, so
    unlike :func:`parse_orca_input_geometry` this path is not blocked by an
    ``* xyzfile`` input. The distinct ``CARTESIAN COORDINATES (A.U.)`` block
    that immediately follows in a real log is a different section (extra
    columns, bohr units) and is never matched by the exact header string
    compared here.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _ORCA_CART_ANGSTROM_HEADER:
            start = i
            break
    if start is None:
        return _not_determinable(
            "no 'CARTESIAN COORDINATES (ANGSTROEM)' block found in ORCA "
            "output log"
        )

    j = start + 1
    if j >= len(lines) or not lines[j].strip() or set(lines[j].strip()) != {"-"}:
        return _not_determinable(
            "malformed ORCA CARTESIAN COORDINATES (ANGSTROEM) block -- no "
            "dashed separator"
        )
    j += 1

    atoms: list[tuple[str, float, float, float]] = []
    while j < len(lines) and lines[j].strip():
        parts = lines[j].split()
        if len(parts) != 4:
            return _not_determinable(
                "ORCA CARTESIAN COORDINATES (ANGSTROEM) row is not "
                "'element x y z'"
            )
        element, xs, ys, zs = parts
        try:
            atoms.append((element, float(xs), float(ys), float(zs)))
        except ValueError:
            return _not_determinable(
                "ORCA CARTESIAN COORDINATES (ANGSTROEM) row has a "
                "non-numeric coordinate"
            )
        j += 1

    if not atoms:
        return _not_determinable(
            "ORCA CARTESIAN COORDINATES (ANGSTROEM) block is empty"
        )
    return _extracted(atoms, origin="orca_log")


# --- Dispatch ---------------------------------------------------------------


def extract_input_geometry(
    *, software: SoftwareName, artifact_kind: ArtifactKind, text: str
) -> ParsedInputGeometry:
    """Pick the right parser for *software*/*artifact_kind* and run it.

    Dispatch is exhaustive on purpose, mirroring
    :func:`app.services.sp_energy_reconciliation.parse_sp_energy_from_log`:
    a program/kind pair with no wired parser returns ``not_determinable``
    rather than falling through to another program's parser. Molpro and
    Psi4 are recognised by :mod:`app.services.ess_software_detection` but
    have no input-geometry parser wired here -- out of scope for this PR
    (Gaussian and ORCA cover the motivating case: coarse ``opt`` passes
    from ARC).
    """
    if artifact_kind == ArtifactKind.input:
        if software == "gaussian":
            return parse_gaussian_input_geometry(text)
        if software == "orca":
            return parse_orca_input_geometry(text)
        return _not_determinable(
            f"no input-file geometry parser wired for {software}"
        )
    if artifact_kind == ArtifactKind.output_log:
        if software == "gaussian":
            return parse_gaussian_log_input_geometry(text)
        if software == "orca":
            return parse_orca_log_input_geometry(text)
        return _not_determinable(
            f"no output-log geometry parser wired for {software}"
        )
    return _not_determinable(
        f"artifact kind '{artifact_kind.value}' carries no starting geometry"
    )


def _atoms_to_xyz_text(atoms: tuple[tuple[str, float, float, float], ...]) -> str:
    """Render parsed atoms as the plain XYZ text ``GeometryPayload`` expects."""
    lines = [str(len(atoms)), ""]
    lines.extend(f"{el} {x:.8f} {y:.8f} {z:.8f}" for el, x, y, z in atoms)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB-aware: eligibility, minting/dedup, linking. Shared by the upload hook
# and the backfill script.
# ---------------------------------------------------------------------------


class InputGeometryOutcomeKind(str, Enum):
    """What :func:`extract_and_link_input_geometry` (or a candidate loop
    built on top of it) concluded for one calculation."""

    #: A new (or replacement) ``calculation_input_geometry`` row was written.
    extracted = "extracted"
    #: The calculation already carries a distinct, real input geometry --
    #: nothing was inspected or changed.
    already_distinct = "already_distinct"
    #: A starting geometry was parsed, but it hashes identically to the
    #: calculation's own output geometry -- there is nothing new to record.
    identical_to_output = "identical_to_output"
    #: The calculation has zero or more than one output-geometry row, so
    #: "does the input equal the output" is not a well-posed question.
    ambiguous_output = "ambiguous_output"
    #: The calculation's evidence is frozen (``record_review.first_approved_at``
    #: is set): the accepted-science-immutability trigger on
    #: ``calculation_input_geometry`` would refuse any write, so this is
    #: checked up front rather than surfacing as a raw database error.
    frozen_after_approval = "frozen_after_approval"
    #: No ``input``/``output_log`` artifact was available to try.
    no_artifact = "no_artifact"
    #: One or more artifacts were tried; none parsed an unambiguous
    #: starting geometry. ``reason`` names the last (most informative)
    #: failure.
    not_determinable = "not_determinable"


@dataclass(frozen=True)
class InputGeometryOutcome:
    kind: InputGeometryOutcomeKind
    reason: str | None = None


def _resolve_software_for_extraction(
    calculation: Calculation, text: str
) -> SoftwareName | None:
    """Resolve which parser to use for *text*.

    A recognised banner in the text wins (mirrors
    :func:`app.services.calculation_parameter_extraction._resolve_software`):
    real evidence in the bytes outranks a declared label. An ``output_log``
    artifact virtually always sniffs cleanly. A raw ``input`` deck (``.gjf``/
    ``.inp``) carries no program banner at all -- Gaussian's and ORCA's
    banners are things the *program* prints when it runs, not text a human
    or ARC writes into a deck -- so the calculation's own declared
    ``software_release`` is the fallback there. ``None`` when neither is
    available; the caller records that as ``not_determinable`` rather than
    guessing.
    """
    sniffed = detect_software_from_text(text)
    if sniffed is not None:
        return sniffed
    release = calculation.software_release
    if release is not None and release.software is not None:
        name = (release.software.name or "").strip().lower()
        if name in ("gaussian", "orca"):
            return name  # type: ignore[return-value]
    return None


@dataclass(frozen=True)
class _CalculationGeometryState:
    output_geometry_id: int | None
    #: ``geometry.natoms`` of :attr:`output_geometry_id`. Compared against
    #: a candidate's parsed atom count before minting -- see
    #: :func:`_mint_and_link_extracted_geometry`.
    output_natoms: int | None
    existing_input_link: CalculationInputGeometry | None
    eligible: bool
    ineligible_outcome: InputGeometryOutcomeKind | None = None
    ineligible_reason: str | None = None


def _current_state(session: Session, calculation: Calculation) -> _CalculationGeometryState:
    """Read this calculation's current input/output-geometry links.

    Queries the rows directly rather than trusting the ORM relationship
    cache (mirrors :func:`app.services.hessian_extraction._resolve_input_geometry_id`),
    so this is correct whichever caller flushed the rows most recently --
    the upload hook (geometries attached earlier in the same request) or
    the backfill script (geometries attached in a prior, already-committed
    request).

    Checked before either geometry query: once a calculation has been
    approved (``record_review.first_approved_at`` set), the deployed
    accepted-science-immutability trigger
    (``tckdb_guard_accepted_child`` on ``calculation_input_geometry``,
    see ``c6f2a9d4e7b1``/``a1f6c3e9b527``) refuses any insert or update
    against it -- "Artifacts are frozen after calculation approval;
    publish a corrected calculation instead of changing accepted
    evidence," the same rule the artifacts route enforces before this
    hook ever runs. Checking it here turns what would otherwise surface
    as a raw database error into a clean, informative outcome (relevant
    mainly to the backfill, which walks calculations the upload hook
    never sees in this state).

    ``ambiguous_output`` covers **zero** output-geometry rows as much as
    more than one: an ``opt`` calc that (for whatever reason) never got an
    output geometry attached has nothing to compare a candidate's atom
    count against either, so it is skipped the same way a genuinely
    multi-output calc is -- both are "not a well-posed question", not two
    different states.
    """
    is_frozen = session.scalar(
        select(RecordReview.id)
        .where(
            RecordReview.record_type == SubmissionRecordType.calculation,
            RecordReview.record_id == calculation.id,
            RecordReview.first_approved_at.isnot(None),
        )
        .limit(1)
    )
    if is_frozen is not None:
        return _CalculationGeometryState(
            output_geometry_id=None,
            output_natoms=None,
            existing_input_link=None,
            eligible=False,
            ineligible_outcome=InputGeometryOutcomeKind.frozen_after_approval,
            ineligible_reason=(
                "calculation's evidence is frozen after approval; "
                "publish a corrected calculation instead"
            ),
        )

    output_rows = session.execute(
        select(CalculationOutputGeometry.geometry_id, Geometry.natoms)
        .join(Geometry, Geometry.id == CalculationOutputGeometry.geometry_id)
        .where(CalculationOutputGeometry.calculation_id == calculation.id)
    ).all()
    if len(output_rows) != 1:
        return _CalculationGeometryState(
            output_geometry_id=None,
            output_natoms=None,
            existing_input_link=None,
            eligible=False,
            ineligible_outcome=InputGeometryOutcomeKind.ambiguous_output,
            ineligible_reason=(
                f"calculation has {len(output_rows)} output geometries "
                "(need exactly one -- zero or more than one are both "
                "ambiguous -- to compare an extracted starting geometry "
                "against)"
            ),
        )
    output_geometry_id, output_natoms = output_rows[0]

    input_rows = session.scalars(
        select(CalculationInputGeometry).where(
            CalculationInputGeometry.calculation_id == calculation.id
        )
    ).all()
    if not input_rows:
        return _CalculationGeometryState(
            output_geometry_id=output_geometry_id,
            output_natoms=output_natoms,
            existing_input_link=None,
            eligible=True,
        )
    if len(input_rows) == 1 and input_rows[0].geometry_id == output_geometry_id:
        return _CalculationGeometryState(
            output_geometry_id=output_geometry_id,
            output_natoms=output_natoms,
            existing_input_link=input_rows[0],
            eligible=True,
        )
    return _CalculationGeometryState(
        output_geometry_id=output_geometry_id,
        output_natoms=output_natoms,
        existing_input_link=None,
        eligible=False,
        ineligible_outcome=InputGeometryOutcomeKind.already_distinct,
        ineligible_reason=(
            "calculation already has a distinct, real input geometry "
            "recorded"
        ),
    )


def _mint_and_link_extracted_geometry(
    session: Session,
    calculation: Calculation,
    *,
    state: _CalculationGeometryState,
    atoms: tuple[tuple[str, float, float, float], ...],
) -> InputGeometryOutcome:
    """Mint (dedupe) *atoms* as a ``Geometry`` and link it, or reject atomically.

    **The atom-count guard runs first, before anything is minted.** A
    parsed geometry whose atom count does not match the calculation's own
    output geometry (:attr:`_CalculationGeometryState.output_natoms`) is
    refused outright -- most often a truncated/malformed artifact (see
    :func:`app.services.gaussian_output_parser.extract_first_geometry`'s
    docstring: a malformed "Input orientation" block does not raise, it
    returns whatever partial, non-empty atom list it parsed before hitting
    the truncation). This check is independent of species/subject
    identity, and runs whether or not
    :func:`app.services.calculation_geometry_composition.assert_calculation_geometry_composition`
    would itself judge the geometry -- that check silently declines for a
    ``pseudo`` owner, an unparsable stored SMILES, or a transition state
    whose reaction has a ``pseudo`` reactant
    (:func:`app.services.calculation_geometry_composition._reference_for`),
    and a truncated log must be refused regardless of which owner kind
    linked it.

    **Everything after the guard runs inside one ``SAVEPOINT``, opened
    before ``resolve_geometry_payload`` is ever called.** Minting a new
    ``Geometry``/``GeometryAtom`` row and then rejecting the link (a
    composition mismatch, a concurrent-duplicate ``IntegrityError``, the
    accepted-science-immutability trigger firing on a race with an
    approval) used to leave that row orphaned -- nothing pointed at it,
    because the mint happened *before* any savepoint that could roll it
    back. Wrapping the mint in the same savepoint as the composition check
    and the link write means a rejection undoes the mint too. Savepoints
    nest freely in Postgres: ``resolve_geometry_payload`` opens its own
    inner savepoint only for a genuinely new row, and rolling back this
    outer one discards that regardless of whether the inner one already
    committed.

    This function is called both from inside the upload hook's own
    :func:`app.services.best_effort.isolated_best_effort` savepoint and
    directly from the backfill script's per-row loop, which has no such
    wrapper of its own -- hence the self-contained savepoint here rather
    than relying on a caller to supply one. Never raises.
    """
    if len(atoms) != state.output_natoms:
        return InputGeometryOutcome(
            kind=InputGeometryOutcomeKind.not_determinable,
            reason=(
                f"parsed geometry has {len(atoms)} atoms but the "
                f"calculation's own output geometry has "
                f"{state.output_natoms} -- refusing to bind a mismatched "
                "starting geometry (the artifact may be truncated)"
            ),
        )

    savepoint = session.begin_nested()
    try:
        xyz_text = _atoms_to_xyz_text(atoms)
        geometry = resolve_geometry_payload(session, GeometryPayload(xyz_text=xyz_text))
        if geometry is None:
            # resolve_geometry_payload's own insert failed a DB-level check
            # (e.g. the element-canonicality CHECK on geometry_atom) and
            # found no existing row to fall back to under the concurrent-
            # duplicate branch -- see its docstring. The parser-level
            # element-token normalisation is meant to make this
            # unreachable; this is the defensive backstop.
            savepoint.rollback()
            return InputGeometryOutcome(
                kind=InputGeometryOutcomeKind.not_determinable,
                reason="the parsed geometry could not be stored (see the server log)",
            )

        if geometry.id == state.output_geometry_id:
            savepoint.commit()  # nothing new was minted (hash-deduped onto
            # the output row); nothing to roll back either way.
            return InputGeometryOutcome(kind=InputGeometryOutcomeKind.identical_to_output)

        assert_calculation_geometry_composition(
            session,
            calc=calculation,
            geometry_id=geometry.id,
            field="input_geometry_extraction",
        )
        if state.existing_input_link is not None:
            state.existing_input_link.geometry_id = geometry.id
            state.existing_input_link.source = (
                CalculationInputGeometrySource.extracted_from_artifact
            )
        else:
            session.add(
                CalculationInputGeometry(
                    calculation_id=calculation.id,
                    geometry_id=geometry.id,
                    input_order=1,
                    source=CalculationInputGeometrySource.extracted_from_artifact,
                )
            )
        session.flush()
    except Exception as exc:
        savepoint.rollback()
        session.expire(calculation, ["input_geometries"])
        if not isinstance(exc, IntegrityError):
            logger.warning(
                "input_geometry mint/link skipped for calculation id=%s (%s)",
                calculation.id,
                type(exc).__name__,
                exc_info=exc,
            )
        return InputGeometryOutcome(
            kind=InputGeometryOutcomeKind.not_determinable,
            reason="the geometry could not be minted/linked (see the server log)",
        )
    savepoint.commit()
    return InputGeometryOutcome(kind=InputGeometryOutcomeKind.extracted)


def extract_and_link_input_geometry(
    session: Session,
    calculation: Calculation,
    *,
    candidates: list[tuple[ArtifactKind, str]],
) -> InputGeometryOutcome:
    """Try to extract and link a starting geometry from *candidates*.

    *candidates* is a list of ``(artifact_kind, decoded_text)`` pairs, tried
    in order; the first one that parses unambiguously wins. Eligibility
    (:func:`_current_state`) is checked exactly once, before any candidate
    is parsed, since it depends only on the calculation's already-persisted
    links -- not on artifact content.

    :returns: The outcome. ``extracted`` and ``identical_to_output`` are
        both "this calculation is now settled" states; the rest are all
        "nothing changed, and here (roughly) is why".
    """
    state = _current_state(session, calculation)
    if not state.eligible:
        return InputGeometryOutcome(
            kind=state.ineligible_outcome or InputGeometryOutcomeKind.already_distinct,
            reason=state.ineligible_reason,
        )

    if not candidates:
        return InputGeometryOutcome(kind=InputGeometryOutcomeKind.no_artifact)

    last_reason = "no candidate artifact could be attributed to a supported ESS program"
    for artifact_kind, text in candidates:
        software = _resolve_software_for_extraction(calculation, text)
        if software is None:
            last_reason = (
                "could not determine ESS software from the artifact's own "
                "content or the calculation's declared software_release"
            )
            continue

        parsed = extract_input_geometry(
            software=software, artifact_kind=artifact_kind, text=text
        )
        if parsed.action is not InputGeometryParseAction.extracted:
            last_reason = parsed.reason or "not determinable"
            continue

        outcome = _mint_and_link_extracted_geometry(
            session, calculation, state=state, atoms=parsed.atoms  # type: ignore[arg-type]
        )
        if outcome.kind in (
            InputGeometryOutcomeKind.extracted,
            InputGeometryOutcomeKind.identical_to_output,
        ):
            return outcome
        last_reason = outcome.reason or "not determinable"

    return InputGeometryOutcome(
        kind=InputGeometryOutcomeKind.not_determinable, reason=last_reason
    )


# ---------------------------------------------------------------------------
# Upload-time hook
# ---------------------------------------------------------------------------


def try_extract_input_geometry_from_artifact_upload(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> None:
    """Best-effort: extract and link a starting geometry from one uploaded artifact.

    Runs immediately after the matching ``CalculationArtifact`` row has been
    persisted and after this calc's input/output geometries have been
    attached (mirrors :func:`app.services.hessian_extraction.try_extract_hessian_from_artifact_upload`'s
    ordering requirement), decoding bytes from the in-memory base64 payload
    so no object-storage round-trip is needed. Returns ``None`` always --
    no user-facing warning surface in v1, matching the Hessian hook.

    **Known ordering caveat.** This hook only ever looks at the one
    artifact that triggered it. If a calculation's ``output_log`` is
    persisted (and processed) before its ``input`` deck in the same
    upload, the hook fills from the log first; a later ``input`` artifact
    then finds the calculation already eligible-but-settled
    (``already_distinct``) and does not upgrade the link. This mirrors the
    Hessian hook's "fill-when-absent only ... no upgrade path in v1"
    precedent rather than adding a verify/replace path here. The backfill
    script does not have this limitation: it inspects all of a
    calculation's stored artifacts before choosing
    (:func:`extract_and_link_input_geometry_from_stored_artifacts`), so it
    always prefers the input file over the log regardless of upload order.

    Isolation is via :func:`isolated_best_effort`, matching the sibling
    hooks' rationale: a database error swallowed without a
    ``ROLLBACK TO SAVEPOINT`` leaves the transaction aborted, converting a
    loud, immediate failure into a silent one that resurfaces at the
    caller's ``COMMIT`` -- taking the artifact upload with it.
    """
    if artifact_in.kind not in _ELIGIBLE_ARTIFACT_KINDS:
        return
    if calculation.type not in _ELIGIBLE_CALC_TYPES:
        return

    isolated_best_effort(
        session,
        lambda: _extract_and_link_from_upload(session, calculation, artifact_in),
        what=f"input geometry extraction for artifact '{artifact_in.filename}'",
    )


def _extract_and_link_from_upload(
    session: Session, calculation: Calculation, artifact_in: ArtifactIn
) -> None:
    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        # Should not happen — pass-1 validation already decoded successfully.
        logger.warning(
            "input_geometry extraction skipped: artifact '%s' could not be "
            "base64-decoded",
            artifact_in.filename,
        )
        return
    text = content.decode("utf-8", errors="replace")

    outcome = extract_and_link_input_geometry(
        session, calculation, candidates=[(artifact_in.kind, text)]
    )
    if outcome.kind is not InputGeometryOutcomeKind.extracted:
        detail = f" ({outcome.reason})" if outcome.reason else ""
        logger.info(
            "input_geometry extraction for artifact '%s': %s%s",
            artifact_in.filename,
            outcome.kind.value,
            detail,
        )


# ---------------------------------------------------------------------------
# Backfill counterpart: reads this calculation's own stored artifacts.
# ---------------------------------------------------------------------------


def extract_and_link_input_geometry_from_stored_artifacts(
    session: Session, calculation: Calculation
) -> InputGeometryOutcome:
    """Backfill counterpart of :func:`try_extract_input_geometry_from_artifact_upload`.

    Reads this calculation's own ``input``/``output_log``
    ``CalculationArtifact`` rows from object storage by SHA-256
    (:func:`app.services.artifact_storage.load_artifact_bytes` -- the
    backfill/download path, never the upload hook, which works from
    already-decoded bytes to avoid the round-trip), ``input``-kind
    artifacts first, so "prefer the input file over the log" holds
    regardless of the order artifacts were originally uploaded in.

    A storage-read failure or integrity break on one candidate artifact is
    logged (and, for an integrity break, recorded via
    :func:`app.services.artifact_integrity.record_from_error`) and that
    candidate is skipped -- never raised -- so one corrupt object does not
    stop the calling backfill loop from reaching the rest of the corpus.
    """
    rows = session.scalars(
        select(CalculationArtifact)
        .where(
            CalculationArtifact.calculation_id == calculation.id,
            CalculationArtifact.kind.in_(_ELIGIBLE_ARTIFACT_KINDS),
        )
        .order_by(
            case((CalculationArtifact.kind == ArtifactKind.input, 0), else_=1),
            CalculationArtifact.id,
        )
    ).all()
    if not rows:
        return InputGeometryOutcome(kind=InputGeometryOutcomeKind.no_artifact)

    candidates: list[tuple[ArtifactKind, str]] = []
    for artifact in rows:
        try:
            content = load_artifact_bytes(artifact.sha256)
        except ArtifactStorageUnavailable as exc:
            logger.warning(
                "input_geometry backfill: storage read failed for artifact "
                "id=%s: %s",
                artifact.id,
                exc,
            )
            continue
        except ArtifactIntegrityError as exc:
            record_from_error(
                exc,
                detected_during=ArtifactIntegrityDetectionContext.input_geometry_extraction,
                artifact=artifact,
            )
            logger.error(
                "input_geometry backfill: artifact id=%s failed integrity "
                "verification and has been recorded: %s",
                artifact.id,
                exc,
            )
            continue
        candidates.append((artifact.kind, content.decode("utf-8", errors="replace")))

    if not candidates:
        return InputGeometryOutcome(
            kind=InputGeometryOutcomeKind.no_artifact,
            reason="artifacts exist but none could be read from storage",
        )

    return extract_and_link_input_geometry(session, calculation, candidates=candidates)


__all__ = [
    "INPUT_GEOMETRY_PARSER_VERSION",
    "InputGeometryOutcome",
    "InputGeometryOutcomeKind",
    "InputGeometryParseAction",
    "ParsedInputGeometry",
    "extract_and_link_input_geometry",
    "extract_and_link_input_geometry_from_stored_artifacts",
    "extract_input_geometry",
    "parse_gaussian_input_geometry",
    "parse_gaussian_log_input_geometry",
    "parse_orca_input_geometry",
    "parse_orca_log_input_geometry",
    "try_extract_input_geometry_from_artifact_upload",
]
