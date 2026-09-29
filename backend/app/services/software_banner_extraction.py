"""Upload-side hook: read the program's version banner out of an output log.

Sibling to :mod:`app.services.sp_energy_extraction` and
:mod:`app.services.charge_multiplicity_extraction`. Those reconcile the
energy and the charge/multiplicity an output log states; this one
reconciles the *software release* it states (DR-0008, issue #305 decision
(c), 2026-09-29).

Before this hook, DR-0008 reconciliation ran only inside parameter
extraction, which reads only ``input`` artifacts -- and an input deck
carries no version banner. So a calculation declared as "ORCA, no version"
stayed on the version-less release even when its own output log said
``Program Version 6.1.0``.

What it does, once per calculation per upload batch
---------------------------------------------------
1. Detect the program and parse the banner of every ``output_log`` in the
   batch (:func:`observe_software_banner`). No banner, nothing done. Each
   banner is compared with the *declared* release; two banners of the
   declared program that disagree with each other fill nothing and are
   recorded as ``mismatch``, so the result never depends on arrival order.
2. **Different program: nothing done.** Correcting a declared software
   *identity* from an artifact stays where it already was, on the input
   path (``record_software_reconciliation``'s name-mismatch branch). This
   hook is scoped to the owner's decision: the *same* program supplying a
   version the declaration lacks.
3. Same program: :func:`record_software_reconciliation` records the banner
   and the DR-0008 status on the calculation. When the declared release
   has a NULL ``version`` and the banner supplies one (``enriched`` with a
   ``version`` gap), it resolves the release the banner describes and
   re-points the calculation at it -- the same resolution the curator fill
   tool uses. A declared non-NULL version is never overwritten: a
   disagreeing banner records ``mismatch`` and the declared release stays.
   A banner reading the declared version to fewer components (``2015.1``
   for ``2015.1.37``) agrees with it (``matched``); a declared ``build``
   the banner contradicts is a ``mismatch``, never filled around.

This now runs for calculations that *declare* a version too, so their
DR-0008 status moves from ``declared_only`` to ``matched`` / ``enriched`` /
``mismatch`` when an output log arrives. The reproducibility rubric's
``calculation_metadata`` check fails on ``mismatch`` -- which is the point
of recording it, and why agreement is judged by whole version components.

Execution-environment manifests
-------------------------------
A calculation bound to a manifest is recorded, never re-pointed. The
manifest is a content-addressed, immutable closure the depositor declared,
and its digest covers its software release; ``trg_calculation_execution_
environment_binding`` requires the calculation to cite that same release.
Re-pointing both would mean minting a closure nobody declared. So the
banner and status are recorded and both releases are left as declared.

Accepted science
----------------
Every caller runs at ingest: the bundle workflows on a calculation created
in the same transaction, and ``POST /calculations/{id}/artifacts``, which
refuses (409) a calculation that was ever approved. So the accepted-science
root trigger never sees this write.

Best-effort and never raises: artifact upload is canonical and must not be
aborted by a reconciliation failure.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
from app.db.models.common import ArtifactKind, SoftwareReconciliationStatus
from app.schemas.fragments.artifact import ArtifactIn
from app.services import (
    gaussian_parameter_parser,
    molpro_parameter_parser,
    orca_parameter_parser,
)
from app.services.best_effort import isolated_best_effort
from app.services.calculation_resolution import (
    format_observed_banner,
    record_software_reconciliation,
    software_release_to_declared_ref,
)
from app.services.ess_software_detection import detect_software_from_text
from app.services.software_reconciliation import parsed_dict_to_ref

logger = logging.getLogger(__name__)

#: Version-banner parsers per detected program. Shared with the curator
#: fill tool (``software_release_version_fill``) so ingest and backfill read
#: a banner identically.
VERSION_PARSERS: dict[str, Callable[[str], dict | None]] = {
    "gaussian": gaussian_parameter_parser.parse_software_version,
    "orca": orca_parameter_parser.parse_software_version,
    "molpro": molpro_parameter_parser.parse_software_version,
}

#: Informational: the declared release had no version, the output log's
#: banner named the same program with one, and the calculation now cites
#: the versioned release.
W_SOFTWARE_RELEASE_VERSION_FILLED_FROM_ARTIFACT = (
    "software_release_version_filled_from_artifact"
)


def observe_software_banner(text: str) -> tuple[dict | None, str | None]:
    """Parse the program's own version banner out of *text*.

    :returns: ``(parsed_software, program)`` where ``parsed_software`` is the
        parser's ``software`` dict (it has a ``version``) and ``program`` the
        lowercase detected program; ``(None, None)`` when no recognised
        program or no version was found.
    """

    program = detect_software_from_text(text)
    parser = VERSION_PARSERS.get(program or "")
    if parser is None:
        return None, None
    parsed = parser(text)
    if not parsed or not parsed.get("version"):
        return None, None
    return parsed, program


def try_reconcile_software_from_output_uploads(
    session: Session,
    calculation: Calculation,
    artifacts: Sequence[ArtifactIn],
) -> UploadWarning | None:
    """Reconcile the calculation's software release against its output logs.

    Called **once per calculation per upload batch**, after every artifact of
    the batch is persisted, not once per artifact. Each banner is compared
    with the *declared* release, never with a release an earlier log in the
    same batch filled in, so the outcome does not depend on the order the
    logs arrive in. When two logs naming the declared program disagree with
    each other, nothing is filled: the disagreement is recorded instead
    (``mismatch``, both banners on ``observed_software_banner``).

    :returns: an informational warning when the calculation was re-pointed
        at the versioned release its logs name, otherwise ``None`` (also when
        there is no output log, and on any failure).
    """

    # ``kind`` is the wire enum; value comparison works across the boundary
    # with the ORM enum (see the sibling hooks).
    logs = [a for a in artifacts if a.kind == ArtifactKind.output_log]
    if not logs:
        return None
    return isolated_best_effort(
        session,
        lambda: _reconcile(session, calculation, logs),
        what="software banner reconciliation for "
        + ", ".join(repr(a.filename) for a in logs),
    )


def _banner(artifact_in: ArtifactIn) -> tuple[dict | None, str | None]:
    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        logger.warning(
            "software banner reconciliation skipped: artifact '%s' could not "
            "be base64-decoded",
            artifact_in.filename,
        )
        return None, None
    return observe_software_banner(content.decode("utf-8", errors="replace"))


def _reconcile(
    session: Session,
    calculation: Calculation,
    logs: Sequence[ArtifactIn],
) -> UploadWarning | None:
    release = calculation.software_release
    if release is None or release.software is None or not release.software.name:
        return None
    declared_name = release.software.name

    # Banners naming the declared program, in filename order so the recorded
    # text does not depend on arrival order either. Case-insensitive: parsers
    # emit lowercase tokens and the alias table does not canonicalise every
    # program ("molpro").
    same_program: list[tuple[str, dict]] = []
    for artifact_in in sorted(logs, key=lambda a: a.filename):
        parsed, program = _banner(artifact_in)
        if parsed is not None and (program or "").lower() == declared_name.lower():
            same_program.append((artifact_in.filename, parsed))
    if not same_program:
        return None

    distinct = {
        (ref.version, ref.revision, ref.build)
        for ref in (parsed_dict_to_ref(parsed) for _name, parsed in same_program)
    }
    if len(distinct) > 1:
        # The logs contradict each other about which release ran. Nothing is
        # filled and nothing is re-pointed; the disagreement is the record.
        banners = sorted({format_observed_banner(parsed) or "" for _n, parsed in same_program})
        calculation.software_reconciliation_status = SoftwareReconciliationStatus.mismatch
        calculation.observed_software_banner = " | ".join(banners)
        return None

    before = release.public_ref
    result = record_software_reconciliation(
        session,
        calculation,
        declared_ref=software_release_to_declared_ref(release),
        parsed_software=same_program[0][1],
    )
    after = calculation.software_release
    if result is None or after is None or after.public_ref == before:
        return None
    return UploadWarning(
        field="software_release",
        code=W_SOFTWARE_RELEASE_VERSION_FILLED_FROM_ARTIFACT,
        message=(
            f"The declared {declared_name} release gave no version; the "
            f"uploaded output log '{same_program[0][0]}' states version "
            f"{after.version!r}. This calculation now cites that release "
            f"({after.public_ref}). The banner is kept on "
            "observed_software_banner."
        ),
    )
