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

**Best-effort and never raises.** Artifact upload is canonical and must not
be aborted by an extraction failure — the whole body is wrapped so any
error is logged and swallowed, matching the sibling energy hook.
"""

from __future__ import annotations

import base64
import binascii
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationHessian,
    CalculationInputGeometry,
)
from app.db.models.common import ArtifactKind, CalculationType
from app.db.models.geometry import Geometry
from app.schemas.fragments.artifact import ArtifactIn
from app.services.hessian_parsing import (
    HESSIAN_PARSER_VERSION,
    ParsedHessian,
    parse_hessian_from_artifact,
)

logger = logging.getLogger(__name__)

#: Artifact kinds that can carry a Cartesian Hessian: Gaussian/Molpro output
#: logs, and the ORCA ``.hess`` file.
_HESSIAN_ARTIFACT_KINDS = (ArtifactKind.output_log, ArtifactKind.hessian)

#: Calculation types a Hessian legitimately comes from. Hessians are the
#: product of frequency jobs; ``opt`` is admitted because an opt+freq job may
#: be recorded as ``opt`` and still print the force-constant matrix. The parse
#: itself only succeeds when a matrix is actually present, so this gate simply
#: keeps the hook off single-point / scan / IRC logs.
_HESSIAN_CALC_TYPES = (CalculationType.freq, CalculationType.opt)


def try_extract_hessian_from_artifact_upload(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> None:
    """Extract and store a Cartesian Hessian from an uploaded artifact.

    Runs immediately after the matching ``CalculationArtifact`` row has been
    persisted, decoding bytes from the in-memory base64 payload so no
    object-storage round-trip is needed. Returns ``None`` always — the hook
    has no user-facing warning surface in v1; it either fills the
    ``calc_hessian`` row or silently skips.

    ``kind`` is compared by value across the ``tckdb_schemas`` / ORM enum
    boundary (both are ``(str, Enum)`` with identical members), matching the
    sibling energy hook.
    """
    if artifact_in.kind not in _HESSIAN_ARTIFACT_KINDS:
        return
    if calculation.type not in _HESSIAN_CALC_TYPES:
        return

    try:
        _extract_and_store(session, calculation, artifact_in)
    except Exception:
        # Artifact upload is canonical and must never be aborted by an
        # extraction failure — swallow anything and log it.
        logger.warning(
            "hessian extraction failed for artifact '%s'",
            artifact_in.filename,
            exc_info=True,
        )
        return


def _extract_and_store(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> None:
    # Fill-when-absent: never overwrite an existing Hessian.
    if calculation.hessian is not None:
        return

    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        # Should not happen — pass-1 validation already decoded successfully.
        logger.warning(
            "hessian extraction skipped: artifact '%s' could not be "
            "base64-decoded",
            artifact_in.filename,
        )
        return

    text = content.decode("utf-8", errors="replace")

    from_hess_file = artifact_in.kind == ArtifactKind.hessian
    parsed = parse_hessian_from_artifact(text, from_hess_file=from_hess_file)
    if parsed is None:
        return

    geometry_id = _resolve_input_geometry_id(session, calculation, parsed)
    if geometry_id is None:
        return

    _insert(session, calculation, geometry_id, parsed)


def _resolve_input_geometry_id(
    session: Session,
    calculation: Calculation,
    parsed: ParsedHessian,
) -> int | None:
    """Return the geometry_id to bind, or ``None`` if binding is unsafe.

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
        return None

    geometry_id = input_geometry_ids[0]
    geometry = session.get(Geometry, geometry_id)
    if geometry is None:
        logger.info("hessian extraction skipped: input geometry not found")
        return None

    if geometry.natoms != parsed.natoms:
        logger.info(
            "hessian extraction skipped: input geometry has %d atoms but the "
            "parsed matrix implies %d — refusing to bind a mismatched Hessian",
            geometry.natoms,
            parsed.natoms,
        )
        return None

    return geometry_id


def _insert(
    session: Session,
    calculation: Calculation,
    geometry_id: int,
    parsed: ParsedHessian,
) -> None:
    """Insert the ``calc_hessian`` row inside a SAVEPOINT.

    A concurrent uploader racing to fill the same calculation (PK is
    ``calculation_id``) cannot poison the outer transaction with a
    duplicate-key violation; the loser simply skips.
    """
    savepoint = session.begin_nested()
    try:
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
    except IntegrityError:
        savepoint.rollback()
        session.expire(calculation, ["hessian"])
        return
