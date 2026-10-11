"""Upload-side hook: extract and store a calculation's Cartesian Hessian.

Sibling of :mod:`app.services.sp_energy_extraction`. That hook reconciles a
scalar single-point energy from an output log; this one recovers the full
symmetric ``3N×3N`` Cartesian force-constant matrix and stores its packed
lower triangle on :class:`~app.db.models.calculation.CalculationHessian`, in
native hartree/bohr² units (no SI conversion — see
:mod:`app.services.hessian_parsing`).

**Fill-when-absent only.** If the calculation already has a ``calc_hessian``
row, the hook does nothing. There is no full-matrix verify/mismatch path in
v1 (deferred).

**Where it runs:** every path that persists a Hessian-bearing artifact — the
dedicated artifacts route (``POST /calculations/{id}/artifacts``) and
artifacts attached *inline* through the contribution-bundle workflows
(``computed_species`` / ``computed_reaction``). Both Gaussian/Molpro output
logs (``kind='output_log'``, matrix in the log) and ORCA ``.hess`` files
(``kind='hessian'``, no program banner) are handled; the ORCA path is
dispatched by artifact *kind* because a ``.hess`` cannot be sniffed by
content.

**Geometry binding (the crux).** A Hessian is meaningless without the exact
geometry, atom ordering, and orientation it was computed at, so
``geometry_id`` is mandatory. The hook binds it to the calculation's single
resolved *input* geometry and refuses to store when that binding is
ambiguous: it requires exactly one input geometry and that the geometry's
atom count matches the parsed matrix dimension. Zero or several input
geometries, a missing geometry, or a ``natoms`` mismatch all mean *skip*
rather than risk binding a Hessian to the wrong geometry.

**Composition and isotopes (#680).** The bound geometry is also held to the
calculation's subject: the same two checks every geometry link gets run before
the row is written, because ``hessian_reanalysis`` takes its masses from this
geometry. A refusal is not an upload failure -- it takes this hook's ordinary
failure path (savepoint rolled back, warning logged, no ``calc_hessian`` row),
so the artifact is stored and the Hessian is simply not.

**Best-effort and never raises.** Artifact upload is canonical and must not
be aborted by an extraction failure — the whole body is wrapped so any
error is logged and swallowed, matching the sibling energy hook.

**Telling the depositor (#691).** When the hook located a Hessian (or was handed
a file whose whole purpose is to be one) and still stored nothing, it returns a
warn-tier :class:`UploadWarning` naming why, as the sibling hooks do. These are
their own codes (``hessian_not_stored_*``), not the geometry-link block codes
reused as warnings, so the refuse-versus-warn split of ADR 0008 stays clean. A
log that simply carries no matrix -- most freq logs, every ORCA output log --
is not a failure and says nothing, and neither does losing a race to a
concurrent uploader that stored the same Hessian.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationHessian,
    CalculationInputGeometry,
)
from app.db.models.common import ArtifactKind, CalculationType
from app.db.models.geometry import Geometry, GeometryAtom
from app.schemas.fragments.artifact import ArtifactIn
from app.services.best_effort import isolated_best_effort
from app.services.calc_isotopes import assert_isotopes
from app.services.calculation_geometry_composition import (
    assert_calculation_geometry_composition,
)
from app.services.hessian_parsing import (
    HESSIAN_PARSER_VERSION,
    ParsedHessian,
    parse_hessian_from_artifact,
)

logger = logging.getLogger(__name__)

#: Warn-tier codes for a Hessian the hook found but did not store (#691).
W_HESSIAN_NOT_STORED_GEOMETRY_MISMATCH = "hessian_not_stored_geometry_mismatch"
W_HESSIAN_NOT_STORED_NO_UNIQUE_INPUT_GEOMETRY = "hessian_not_stored_no_unique_input_geometry"
W_HESSIAN_NOT_STORED_UNPARSEABLE = "hessian_not_stored_unparseable"
W_HESSIAN_NOT_STORED_FAILED = "hessian_not_stored_failed"

_FIELD = "artifacts"
_TAIL = "The artifact itself is stored; only the Hessian is not."


def _not_stored(code: str, filename: str, reason: str) -> UploadWarning:
    """The warning for a Hessian that was not stored, with a plain-language reason."""
    return UploadWarning(
        field=_FIELD,
        code=code,
        message=f"No Hessian was stored from artifact '{filename}': {reason} {_TAIL}",
    )


@dataclass(frozen=True)
class _Outcome:
    """What a completed extraction attempt concluded.

    Always returned by the work function, so the isolating wrapper's ``None``
    can only mean the attempt itself failed.
    """

    warning: UploadWarning | None = None

#: Artifact kinds that can carry a Cartesian Hessian: Gaussian/Molpro output
#: logs, and the ORCA ``.hess`` file.
_HESSIAN_ARTIFACT_KINDS = (ArtifactKind.output_log, ArtifactKind.hessian)

#: Calculation types a Hessian legitimately comes from. Restricted to
#: ``freq`` on purpose: a ``freq`` calc's input geometry IS the frame the
#: force-constant matrix was evaluated at, so the binding is sound. ``opt`` is
#: deliberately excluded — an opt calc's input geometry is the *pre*-opt
#: guess, but the FC matrix in an ``opt freq`` log is printed at the
#: *converged* geometry. The atom count would match trivially, so admitting
#: ``opt`` is exactly the case that would bind a Hessian to the wrong
#: geometry. (A pure ``opt`` with no freq has no matrix and no input-geometry
#: link anyway, so nothing useful is lost.)
_HESSIAN_CALC_TYPES = (CalculationType.freq,)

#: Absolute tolerance (Angstrom) for the ORCA frame cross-check. A genuine
#: orientation/molecule mismatch differs by far more than this; the bound is
#: only loose enough to absorb coordinate rounding.
_COORD_ATOL_ANGSTROM = 1e-2


def try_extract_hessian_from_artifact_upload(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> UploadWarning | None:
    """Extract and store a Cartesian Hessian from an uploaded artifact.

    Runs immediately after the matching ``CalculationArtifact`` row has been
    persisted, decoding bytes from the in-memory base64 payload so no
    object-storage round-trip is needed. Returns an :class:`UploadWarning`
    when a Hessian was found but not stored (see the module docstring), else
    ``None``; it never raises.

    ``kind`` is compared by value across the ``tckdb_schemas`` / ORM enum
    boundary (both are ``(str, Enum)`` with identical members), matching the
    sibling energy hook.

    Isolation is via :func:`isolated_best_effort`, not a bare catch-all. A
    database error swallowed without a ``ROLLBACK TO SAVEPOINT`` leaves the
    transaction aborted, so the previous ``except Exception`` converted a
    loud, immediate failure into a silent one that resurfaced at the caller's
    ``COMMIT`` — where it takes the artifact upload with it.
    """
    if artifact_in.kind not in _HESSIAN_ARTIFACT_KINDS:
        return None
    if calculation.type not in _HESSIAN_CALC_TYPES:
        return None

    outcome = isolated_best_effort(
        session,
        lambda: _extract_and_store(session, calculation, artifact_in),
        what=f"hessian extraction for artifact '{artifact_in.filename}'",
    )
    if outcome is None:
        # The attempt itself failed and was rolled back (logged by the wrapper).
        return _not_stored(
            W_HESSIAN_NOT_STORED_FAILED,
            artifact_in.filename,
            "reading it failed unexpectedly.",
        )
    return outcome.warning


def _extract_and_store(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> _Outcome:
    # Fill-when-absent: never overwrite an existing Hessian.
    if calculation.hessian is not None:
        return _Outcome()

    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        # Should not happen — pass-1 validation already decoded successfully.
        logger.warning(
            "hessian extraction skipped: artifact '%s' could not be "
            "base64-decoded",
            artifact_in.filename,
        )
        return _Outcome()

    text = content.decode("utf-8", errors="replace")

    from_hess_file = artifact_in.kind == ArtifactKind.hessian
    parsed = parse_hessian_from_artifact(text, from_hess_file=from_hess_file)
    if parsed is None:
        if from_hess_file:
            # A ``.hess`` exists to carry a matrix; an output log usually does not.
            return _Outcome(
                _not_stored(
                    W_HESSIAN_NOT_STORED_UNPARSEABLE,
                    artifact_in.filename,
                    "the file was sent as a Hessian but no complete force-constant "
                    "matrix could be read from it.",
                )
            )
        return _Outcome()

    geometry_id, refusal = _resolve_input_geometry_id(session, calculation, parsed, artifact_in.filename)
    if geometry_id is None:
        return _Outcome(refusal)

    return _Outcome(_insert(session, calculation, geometry_id, parsed, artifact_in.filename))


def _resolve_input_geometry_id(
    session: Session,
    calculation: Calculation,
    parsed: ParsedHessian,
    filename: str,
) -> tuple[int | None, UploadWarning | None]:
    """Return ``(geometry_id, None)`` to bind, or ``(None, warning)`` if binding is unsafe.

    Conservative: requires exactly one input geometry for the calculation and
    that its atom count matches the parsed matrix dimension. Queries the rows
    directly (after a flush) rather than trusting the ORM relationship cache,
    so it is correct on the bundle workflows too — there input geometries are
    attached to the pending session just before this hook runs.
    """
    session.flush()
    input_geometry_ids = session.scalars(
        select(CalculationInputGeometry.geometry_id).where(
            CalculationInputGeometry.calculation_id == calculation.id
        )
    ).all()

    if len(input_geometry_ids) != 1:
        logger.info(
            "hessian extraction skipped: calculation has %d input "
            "geometries (need exactly one to bind unambiguously)",
            len(input_geometry_ids),
        )
        return None, _not_stored(
            W_HESSIAN_NOT_STORED_NO_UNIQUE_INPUT_GEOMETRY,
            filename,
            f"the calculation has {len(input_geometry_ids)} input geometries and exactly one "
            "is needed to bind the matrix to the structure it was computed at.",
        )

    geometry_id = input_geometry_ids[0]
    geometry = session.get(Geometry, geometry_id)
    if geometry is None:
        logger.info("hessian extraction skipped: input geometry not found")
        return None, _not_stored(
            W_HESSIAN_NOT_STORED_NO_UNIQUE_INPUT_GEOMETRY,
            filename,
            "the calculation's input geometry could not be found.",
        )

    if geometry.natoms != parsed.natoms:
        logger.info(
            "hessian extraction skipped: input geometry has %d atoms but the "
            "parsed matrix implies %d -- refusing to bind a mismatched Hessian",
            geometry.natoms,
            parsed.natoms,
        )
        return None, _not_stored(
            W_HESSIAN_NOT_STORED_GEOMETRY_MISMATCH,
            filename,
            f"the matrix is for {parsed.natoms} atoms but the calculation's input geometry "
            f"has {geometry.natoms}.",
        )

    # Decisive orientation check: when the artifact records its own atomic
    # frame (ORCA .hess $atoms), the matrix is only valid for a geometry in
    # that exact frame. An atom-count match against a different molecule or a
    # rotated/translated frame is otherwise undetectable.
    if parsed.reference_coords_angstrom is not None:
        if not _frame_matches(session, geometry_id, parsed.reference_coords_angstrom):
            logger.info(
                "hessian extraction skipped: the artifact's atomic frame does "
                "not match the bound input geometry -- refusing to bind a "
                "Hessian computed at a different geometry/orientation"
            )
            return None, _not_stored(
                W_HESSIAN_NOT_STORED_GEOMETRY_MISMATCH,
                filename,
                "the atomic frame recorded in the file does not match the calculation's "
                "input geometry (different atoms, order or orientation).",
            )

    return geometry_id, None


def _frame_matches(
    session: Session,
    geometry_id: int,
    reference_coords_angstrom: list[tuple[str, float, float, float]],
) -> bool:
    """True if the bound geometry's atoms match the artifact's frame.

    Compares element (case-insensitive) and Cartesian coordinates in atom
    order within :data:`_COORD_ATOL_ANGSTROM`. Order matters: the artifact's
    atom ordering is the calculation's atom ordering, which is preserved into
    the stored geometry.
    """
    atoms = session.scalars(
        select(GeometryAtom)
        .where(GeometryAtom.geometry_id == geometry_id)
        .order_by(GeometryAtom.atom_index)
    ).all()
    if len(atoms) != len(reference_coords_angstrom):
        return False
    for atom, (element, x, y, z) in zip(atoms, reference_coords_angstrom, strict=True):
        if atom.element.strip().lower() != element.strip().lower():
            return False
        if (
            abs(atom.x - x) > _COORD_ATOL_ANGSTROM
            or abs(atom.y - y) > _COORD_ATOL_ANGSTROM
            or abs(atom.z - z) > _COORD_ATOL_ANGSTROM
        ):
            return False
    return True


def _insert(
    session: Session,
    calculation: Calculation,
    geometry_id: int,
    parsed: ParsedHessian,
    filename: str,
) -> UploadWarning | None:
    """Insert the ``calc_hessian`` row inside a SAVEPOINT; return a warning if it was refused.

    A concurrent uploader racing to fill the same calculation (PK is
    ``calculation_id``) cannot poison the outer transaction with a
    duplicate-key violation; the loser simply skips.
    """
    savepoint = session.begin_nested()
    try:
        # Same composition and isotope claims as every other geometry link
        # (#680). Inside the savepoint on purpose: a refusal takes the
        # existing path below -- roll back, log a warning, store nothing -- and
        # never fails the artifact upload. The geometry is normally the
        # calculation's own, already-checked input geometry, so this is a
        # backstop that also keeps the structural guard honest.
        assert_calculation_geometry_composition(
            session,
            calc=calculation,
            geometry_id=geometry_id,
            field="hessian_extraction",
        )
        assert_isotopes(
            session,
            calc=calculation,
            geometry_id=geometry_id,
            field="hessian_extraction",
        )
        session.add(
            CalculationHessian(
                calculation=calculation,
                geometry_id=geometry_id,
                natoms=parsed.natoms,
                lower_triangle_hartree_bohr2=parsed.lower_triangle_hartree_bohr2,
                source=parsed.source,
                parser_version=HESSIAN_PARSER_VERSION,
            )
        )
        session.flush()
        return None
    except Exception as exc:
        # Wider than the concurrent-duplicate ``IntegrityError`` this was
        # written for: a savepoint whose failure is not rolled back leaves the
        # transaction aborted, and the caller's safety net would then hide
        # that until COMMIT. Undoing the insert is right regardless of cause.
        savepoint.rollback()
        session.expire(calculation, ["hessian"])
        if isinstance(exc, IntegrityError):
            # A concurrent uploader stored this calculation's Hessian first.
            return None
        logger.warning(
            "hessian insert skipped for calculation id=%s (%s)",
            calculation.id,
            type(exc).__name__,
            exc_info=exc,
        )
        if isinstance(exc, CodedValueError):
            return _not_stored(
                W_HESSIAN_NOT_STORED_GEOMETRY_MISMATCH,
                filename,
                "the input geometry it would be bound to contradicts the calculation's species "
                f"({exc.code}), so it was not trusted with a force-constant matrix.",
            )
        return _not_stored(W_HESSIAN_NOT_STORED_FAILED, filename, "storing it failed unexpectedly.")
