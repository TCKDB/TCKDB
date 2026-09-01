"""Bridge between the pure-function ESS parsers and parameter persistence.

The Gaussian and ORCA parsers
(:mod:`app.services.gaussian_parameter_parser`,
:mod:`app.services.orca_parameter_parser`) are deliberately DB-free: they
take text and return parameter dicts plus a JSON snapshot. This module
takes that output and writes it through
:func:`persist_calculation_parameters` with ``source=ParameterSource.parser``,
which performs true replace-all over previously parser-derived rows.

Software dispatch:

1. Read ``calculation.software_release.software.name`` when present.
2. Fall back to text sniffing (``Gaussian``/``Program Version`` markers)
   when the calculation has no software_release wired up.

Parser failures must not corrupt the calling transaction. A
``ParameterExtractionError`` is raised for callers that want to surface
the failure (e.g. an explicit admin endpoint), but the caller is
responsible for swallowing it when the extraction is opportunistic and
must not break the upload path.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationParameter,
)
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactKind,
    ParameterSource,
)
from app.schemas.fragments.artifact import ArtifactIn
from app.schemas.fragments.calculation import CalculationParameterObservation
from app.schemas.upload_warning import UploadWarning
from app.services import (
    gaussian_parameter_parser,
    molpro_parameter_parser,
    orca_parameter_parser,
)
from app.services.artifact_integrity import record_from_error
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    load_artifact_bytes,
)
from app.services.best_effort import isolated_best_effort
from app.services.calculation_resolution import (
    persist_calculation_parameters,
    record_software_reconciliation,
    software_reconciliation_warning,
    software_release_to_declared_ref,
)
from app.services.ess_software_detection import (
    SoftwareName,
    detect_software_from_text,
)

logger = logging.getLogger(__name__)


class ParameterExtractionError(RuntimeError):
    """Raised when artifact text cannot be parsed into parameter rows.

    Callers in opportunistic contexts (e.g. an artifact upload hook) must
    catch this and log/skip rather than letting it abort the transaction.
    """


def _resolve_software(
    calculation: Calculation, artifact_text: str
) -> SoftwareName:
    """Resolve which parser to use for this artifact.

    Text-sniffed evidence wins when the banner unambiguously names a
    program; the declared ``software_release`` name is the fallback for
    artifacts that carry no recognisable banner. Inverted 2026-09 (issue
    #305's ARC follow-up): the declared name used to win outright
    whenever it was one of the three parsed programs, even when the
    artifact's own banner named a different one. ARC's own per-job-type
    version map can carry a stale/inherited software name into a
    ``software_release`` that never ran the job in question — a real
    banner is evidence; a declared label is not, when the two disagree.
    ``detect_software_from_text`` already follows "reject, don't guess":
    an inconclusive sniff returns ``None`` rather than a wrong guess, so
    falling back to the declared name there loses nothing.

    Resolving a program is not a promise that a parser exists for it —
    ``_run_parser`` is the single place that decides that, and raises for a
    program it cannot parse. The allow-list below stays at the three
    programs with a wired parameter parser so a mislabelled
    ``software_release`` cannot override what the log itself says.
    """

    sniffed = detect_software_from_text(artifact_text)
    if sniffed is not None:
        return sniffed

    release = calculation.software_release
    if release is not None and release.software is not None:
        name = (release.software.name or "").strip().lower()
        if name in ("gaussian", "orca", "molpro"):
            return name  # type: ignore[return-value]

    raise ParameterExtractionError(
        "Cannot determine ESS software for parameter extraction: "
        "calculation has no software_release and the artifact text "
        "contains no recognised electronic-structure program banner."
    )


def _to_observation(parsed: dict) -> CalculationParameterObservation:
    """Coerce one parser-emitted dict into a Pydantic observation.

    The parser modules already emit dicts whose keys line up with
    :class:`CalculationParameterObservation`; this helper just normalises
    the value types (everything stringified, ``parameter_index``
    coerced to int when present) and ignores any unknown extras.
    """

    raw_value = parsed.get("raw_value")
    if raw_value is None:
        raw_value = ""
    parameter_index = parsed.get("parameter_index")
    if parameter_index is not None:
        parameter_index = int(parameter_index)

    return CalculationParameterObservation(
        raw_key=str(parsed["raw_key"]),
        raw_value=str(raw_value),
        canonical_key=parsed.get("canonical_key"),
        canonical_value=parsed.get("canonical_value"),
        section=parsed.get("section"),
        value_type=parsed.get("value_type"),
        unit=parsed.get("unit"),
        parameter_index=parameter_index,
    )


def _run_parser(software: SoftwareName, artifact_text: str) -> dict:
    """Invoke the matching parser and return its result dict.

    Wraps any parser exception in :class:`ParameterExtractionError` so
    callers have a single failure mode to catch.

    Dispatch is exhaustive on purpose. A recognised program with no wired
    parameter parser raises rather than falling through to another
    program's parser: feeding a Psi4 log to the ORCA parser would emit
    parameter rows attributed to a run that never happened. The
    opportunistic callers funnel through :func:`_extract_safe`, which
    downgrades the raise to a logged skip, so the artifact upload still
    succeeds.
    """

    try:
        if software == "gaussian":
            # The Gaussian parser exposes parse_gaussian_log(filepath).
            # Inline a text-mode call by reproducing the steps from the
            # filepath wrapper without touching disk.
            return _parse_gaussian_text(artifact_text)
        if software == "molpro":
            return molpro_parameter_parser.parse_molpro_log(text=artifact_text)
        if software == "orca":
            return orca_parameter_parser.parse_orca_log(text=artifact_text)
        raise ParameterExtractionError(
            f"No parameter parser is wired for {software}; parameters were "
            "not extracted. The artifact itself is stored unchanged."
        )
    except ParameterExtractionError:
        raise
    except Exception as exc:
        raise ParameterExtractionError(
            f"{software} parser failed: {type(exc).__name__}: {exc}"
        ) from exc


def _parse_gaussian_text(text: str) -> dict:
    """Run the Gaussian parser against in-memory text.

    The public ``parse_gaussian_log`` only accepts a filepath; this helper
    re-uses its module-private extractors to keep the bridge purely
    in-memory and avoid a temp-file write per upload.
    """

    g = gaussian_parameter_parser
    # extract_gaussian_route_text handles both log-echo blocks and raw
    # .gjf/.com input layouts (returning None when neither matches).
    route = g.extract_gaussian_route_text(text) or ""
    link0_params = g._extract_link0(text)
    route_params = g._parse_route_tokens(route)
    all_params = link0_params + route_params

    parameters_json: dict = {"route_line": route, "sections": {}}
    for p in all_params:
        section = p.get("section", "unknown")
        parameters_json["sections"].setdefault(section, {})[p["raw_key"]] = p["raw_value"]

    return {
        "parameters": all_params,
        "parameters_json": parameters_json,
        "route_line": route,
        "software": g.parse_software_version(text),
        "charge_multiplicity": g.parse_charge_multiplicity(text),
        "method_basis": g.parse_method_basis(route),
        "parser_version": g.PARSER_VERSION,
    }


def extract_and_store_calculation_parameters(
    session: Session,
    calculation: Calculation,
    artifact_text: str,
    *,
    parser_version: str | None = None,
    warnings: list[UploadWarning] | None = None,
) -> list[CalculationParameter]:
    """Parse calculation input text and persist normalized parameters.

    Pipeline:

    1. Resolve which parser to use from the artifact's own banner
       (``calculation.software_release.software.name`` is the fallback
       for a banner sniffing cannot identify).
    2. Invoke that parser on ``artifact_text``.
    3. Replace every existing ``CalculationParameter`` row on this
       calculation with ``source='parser'`` (true replace-all, not
       scoped by parser_version — see
       :func:`persist_calculation_parameters` for the rationale).
    4. Insert the new batch with ``source=ParameterSource.parser`` and the
       parser's version tag.
    5. Mirror the JSON snapshot, parser version, and an extraction
       timestamp onto the ``Calculation`` row.

    :param session: Active SQLAlchemy session. Caller owns commit.
    :param calculation: Target calculation row.
    :param artifact_text: Decoded input/log text from the ESS.
    :param parser_version: Override for the parser version recorded on
        each row and on ``calculation.parameters_parser_version``.
        Defaults to whatever the parser self-reports.
    :param warnings: Optional list to append a depositor-facing warning to
        when DR-0008 reconciliation corrects the calculation's software
        identity from this artifact (see
        :func:`app.services.calculation_resolution.software_reconciliation_warning`).
        Callers that do not track warnings (the backfill script) simply
        omit it.
    :returns: Newly inserted parameter rows.
    :raises ParameterExtractionError: Software cannot be determined or
        the parser raises. Callers in opportunistic contexts must catch
        and log this so the artifact upload still succeeds.
    """

    software = _resolve_software(calculation, artifact_text)
    parsed = _run_parser(software, artifact_text)

    # DR-0008: reconcile the declared software_release against the version
    # banner the parser just observed, and persist the outcome on the
    # calculation. Non-blocking: a mismatch is recorded, never raised, and
    # any failure here must not abort parameter extraction / the upload.
    try:
        result = record_software_reconciliation(
            session,
            calculation,
            declared_ref=software_release_to_declared_ref(
                calculation.software_release
            ),
            parsed_software=parsed.get("software"),
        )
        if warnings is not None:
            warning = software_reconciliation_warning(result)
            if warning is not None:
                warnings.append(warning)
    except Exception as exc:  # pragma: no cover - defensive, never blocks
        logger.warning(
            "software provenance reconciliation skipped for calculation "
            "id=%s: %s: %s",
            calculation.id,
            type(exc).__name__,
            exc,
        )

    observations = [_to_observation(p) for p in parsed.get("parameters", [])]
    effective_parser_version = parser_version or parsed.get("parser_version")

    return persist_calculation_parameters(
        session,
        calculation,
        observations,
        parameters_json=parsed.get("parameters_json"),
        parameters_parser_version=effective_parser_version,
        parameters_extracted_at=datetime.now(timezone.utc).replace(tzinfo=None),
        source=ParameterSource.parser,
        parser_version=effective_parser_version,
    )


def _decode_text(content: bytes) -> str:
    """Decode artifact bytes to text for parser consumption.

    Input artifacts are required to be valid UTF-8 by
    :func:`validate_artifact`, but the backfill path may also encounter
    older rows; ``errors="replace"`` keeps the parser running on
    encoding edge cases instead of failing the whole row.
    """

    return content.decode("utf-8", errors="replace")


def try_extract_parameters_from_input_upload(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
    *,
    warnings: list[UploadWarning] | None = None,
) -> list[CalculationParameter] | None:
    """Opportunistic upload-side hook: extract from an ArtifactIn.

    Used by the artifact upload route and inline-bundle workflows
    immediately after the matching ``CalculationArtifact`` row has been
    persisted. Bytes are decoded from the in-memory base64 payload so
    no S3 round-trip is needed.

    Returns ``None`` (and logs a warning) on any failure or when the
    artifact kind is not ``input``. Never raises — artifact upload is
    canonical and must not be aborted by parameter-extraction failure.

    :param warnings: Optional list to append a depositor-facing warning to
        when this artifact corrects the calculation's software identity
        (DR-0008) — see :func:`extract_and_store_calculation_parameters`.
    """

    # ``artifact_in`` is a Pydantic model whose ``kind`` field is typed
    # against ``tckdb_schemas.enums.ArtifactKind``; ``ArtifactKind`` here
    # is the parallel ``app.db.models.common.ArtifactKind`` used by the
    # ORM. Both are ``(str, Enum)`` with identical members, so a value
    # comparison (``!=``) works across the boundary — an identity check
    # would always fail because the enum classes are not the same object.
    if artifact_in.kind != ArtifactKind.input:
        return None
    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        # Should not happen — pass-1 validation already decoded successfully.
        logger.warning(
            "calculation_parameter extraction skipped: artifact "
            "'%s' could not be base64-decoded",
            artifact_in.filename,
        )
        return None

    text = _decode_text(content)
    return _extract_safe(
        session, calculation, text, source=artifact_in.filename, warnings=warnings
    )


def try_extract_parameters_from_input_artifact_row(
    session: Session,
    calculation: Calculation,
    artifact: CalculationArtifact,
    *,
    warnings: list[UploadWarning] | None = None,
) -> list[CalculationParameter] | None:
    """Opportunistic backfill hook: extract from a stored artifact row.

    Reads bytes from object storage by SHA-256, decodes, and runs the
    extractor. Used by the backfill script. Returns ``None`` and logs
    on any failure (including storage-read failure) so the backfill
    can continue across the rest of the corpus.
    """

    if artifact.kind is not ArtifactKind.input:
        return None
    if not artifact.sha256:
        logger.warning(
            "calculation_parameter extraction skipped: artifact id=%s "
            "has no sha256 to load by",
            artifact.id,
        )
        return None
    try:
        content = load_artifact_bytes(artifact.sha256)
    except ArtifactStorageUnavailable as exc:
        logger.warning(
            "calculation_parameter extraction skipped: storage read "
            "failed for artifact id=%s: %s",
            artifact.id,
            exc,
        )
        return None
    except ArtifactIntegrityError as exc:
        # The docstring promises this returns None on *any* failure so the
        # backfill can continue across the corpus, and it did not: an
        # integrity failure escaped and stopped the loop at the first
        # corrupt object. Record the break (ADR 0014) and keep going —
        # aborting the backfill is the one response that guarantees the
        # rest of the corpus stays unexamined.
        record_from_error(
            exc,
            detected_during=ArtifactIntegrityDetectionContext.parameter_extraction,
            artifact=artifact,
        )
        logger.error(
            "calculation_parameter extraction skipped: artifact id=%s "
            "failed integrity verification and has been recorded: %s",
            artifact.id,
            exc,
        )
        return None
    text = _decode_text(content)
    return _extract_safe(
        session,
        calculation,
        text,
        source=f"artifact id={artifact.id}",
        warnings=warnings,
    )


def _extract_safe(
    session: Session,
    calculation: Calculation,
    text: str,
    *,
    source: str,
    warnings: list[UploadWarning] | None = None,
) -> list[CalculationParameter] | None:
    """Run extraction as a genuinely best-effort operation.

    ``extract_and_store_calculation_parameters`` is parse-first /
    write-second, and ``ParameterExtractionError`` is raised only by the parse
    half, where no ``calculation_parameter`` rows have been written yet. That
    is why catching it alone used to look sufficient.

    It was not. The write half does real work — ``persist_calculation_parameters``
    issues a ``DELETE`` of the previous parser rows and then ``INSERT``s the new
    batch — and a database error there is not a ``ParameterExtractionError``.
    It escaped this guard entirely and aborted the artifact upload, despite the
    calling route declaring the whole step "best-effort (never abort the
    upload)". The guard existed; it just did not cover the writes.

    :func:`isolated_best_effort` now covers both halves: the artifact rows the
    route already persisted are flushed outside the savepoint, the extraction
    runs and flushes inside it, and any failure rolls back to the savepoint
    with the upload intact and the session still usable for the artifacts that
    follow in the same request.
    """

    def _run() -> list[CalculationParameter] | None:
        try:
            return extract_and_store_calculation_parameters(
                session, calculation, text, warnings=warnings
            )
        except ParameterExtractionError as exc:
            # A parse-layer refusal is expected and unremarkable — an
            # unrecognised or malformed input file — so it keeps its quieter,
            # message-carrying log line rather than a traceback.
            logger.warning(
                "calculation_parameter extraction skipped (%s): %s", source, exc
            )
            return None

    return isolated_best_effort(
        session, _run, what=f"calculation_parameter extraction ({source})"
    )
