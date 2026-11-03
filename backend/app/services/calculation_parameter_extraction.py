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
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.version import ParserStamp

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
from app.services.parameter_reconciliation import (
    declared_parameter_warnings,
    level_of_theory_warnings,
    merge_artifact_observations,
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
        # Every job of a ``--Link1--`` file or a multi-step log, for the declared-level check.
        "method_basis_jobs": g.parse_method_basis_jobs(text),
        "parser": g.PARSER.stamp().model_dump(),
    }


def extract_and_store_calculation_parameters(
    session: Session,
    calculation: Calculation,
    artifact_text: str,
    *,
    parser: ParserStamp | None = None,
    warnings: list[UploadWarning] | None = None,
    artifact_kind: ArtifactKind = ArtifactKind.input,
) -> list[CalculationParameter]:
    """Parse calculation input text and persist normalized parameters.

    ``artifact_text`` is an input deck (``artifact_kind=input``) or an output log whose echoed input is read
    (``artifact_kind=output_log``). The two are stored side by side and never erase each other; where both
    state a setting the deck's row is kept (see :mod:`app.services.parameter_reconciliation`). A log skips
    the software reconciliation, which the output-log hooks already run once per batch, and does not
    overwrite the ``parameters_json`` snapshot a deck wrote.

    Pipeline:

    1. Resolve which parser to use from the artifact's own banner
       (``calculation.software_release.software.name`` is the fallback
       for a banner sniffing cannot identify).
    2. Invoke that parser on ``artifact_text``.
    3. Replace every existing ``CalculationParameter`` row on this
       calculation with ``source='parser'`` and the same ``artifact_kind``
       (true replace-all, not scoped by parser_version — see
       :func:`persist_calculation_parameters` for the rationale).
    4. Insert the new batch with ``source=ParameterSource.parser`` and the
       parser's version tag.
    5. Mirror the JSON snapshot, parser version, and an extraction
       timestamp onto the ``Calculation`` row.

    :param session: Active SQLAlchemy session. Caller owns commit.
    :param calculation: Target calculation row.
    :param artifact_text: Decoded input/log text from the ESS.
    :param parser: Override for the parser stamp (name and version) recorded on
        each row and on ``calculation.parameters_parser_name`` / ``parameters_parser_version``.
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
    is_deck = artifact_kind is ArtifactKind.input

    # DR-0008: reconcile the declared software_release against the version
    # banner the parser just observed, and persist the outcome on the
    # calculation. Non-blocking: a mismatch is recorded, never raised, and
    # any failure here must not abort parameter extraction / the upload.
    # Output logs are reconciled once per batch by the output-log hooks.
    if is_deck:
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
    # What this artifact says, before the deck-wins rule drops any of it: the typed-versus-read judgement is made on
    # this, so it does not depend on which artifact of the request was read first.
    observed = [(o.canonical_key, o.canonical_value) for o in observations]
    # A deck read that replaces earlier deck rows is a re-read; only then can log rows it shadowed need restoring.
    is_reread = is_deck and (
        session.scalar(
            select(func.count())
            .select_from(CalculationParameter)
            .where(
                CalculationParameter.calculation_id == calculation.id,
                CalculationParameter.source == ParameterSource.parser,
                CalculationParameter.artifact_kind == ArtifactKind.input,
            )
        )
        or 0
    ) > 0
    observations, conflicts = merge_artifact_observations(
        session, calculation, observations, artifact_kind=artifact_kind, software=software
    )
    parsed_stamp = parsed.get("parser")
    effective_parser = parser or (ParserStamp(**parsed_stamp) if isinstance(parsed_stamp, dict) else None)
    snapshot_is_ours = is_deck or calculation.parameters_json is None

    rows = persist_calculation_parameters(
        session,
        calculation,
        observations,
        parameters_json=parsed.get("parameters_json") if snapshot_is_ours else None,
        parameters_parser=effective_parser if snapshot_is_ours else None,
        parameters_extracted_at=datetime.now(timezone.utc).replace(tzinfo=None) if snapshot_is_ours else None,
        source=ParameterSource.parser,
        parser=effective_parser,
        artifact_kind=artifact_kind,
        # An output log is appended, never replaced: a setting's first statement stands (see
        # :mod:`app.services.parameter_reconciliation`).
        replace=is_deck,
    )
    if is_reread:
        _restore_log_rows(session, calculation)

    if warnings is not None:
        # Best effort: a failure comparing with what the depositor typed must not lose the parameters.
        try:
            session.flush()
            warnings.extend(conflicts)
            warnings.extend(
                declared_parameter_warnings(
                    session, calculation, observed, parsed_software=software, artifact_kind=artifact_kind
                )
            )
            if software in ("gaussian", "orca"):
                warnings.extend(
                    level_of_theory_warnings(
                        calculation,
                        parsed.get("method_basis"),
                        parsed.get("method_basis_jobs"),
                        parsed_software=software,
                        artifact_kind=artifact_kind,
                        dispersions={v for k, v in observed if k == "dispersion.correction" and v},
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive, never blocks
            logger.warning(
                "parameter reconciliation skipped for calculation id=%s: %s: %s",
                calculation.id,
                type(exc).__name__,
                exc,
            )

    return rows


def remember_artifact_bytes(session: Session, content: bytes) -> None:
    """Keep the bytes of an artifact uploaded in this request, so a re-read in the same request does not go to storage."""
    cache = session.info.setdefault("parameter_artifact_bytes", {})
    cache[hashlib.sha256(content).hexdigest()] = content


def _short_timeout_client():
    """An S3 client that gives up in seconds: a storage outage must not stall a deck upload for a minute."""
    from botocore.config import Config

    from app.services.artifact_storage import _get_s3_client

    return _get_s3_client(Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}))


def _stored_log_rows_restore_reason(artifact: CalculationArtifact, exc: Exception) -> str:
    return f"stored output log id={artifact.id} not re-read: {type(exc).__name__}: {exc}"


def _restore_log_rows(session: Session, calculation: Calculation) -> None:
    """Re-read the stored output logs so a setting a re-read deck no longer states is not lost.

    A deck read drops the log rows for the settings it states. When the deck is re-read with fewer settings, the
    dropped log rows are re-derived here from the calculation's stored output logs, in upload order, and appended
    where no stored row states the setting. Runs only on a deck *re-read*, never on a calculation's first deck.
    Bytes uploaded in this request come from memory; others are read from storage through a short-timeout client,
    and an artifact that cannot be read or parsed is skipped with a logged reason (the deck's own rows are kept).
    Conflicts were reported when the log arrived and are not repeated.
    """
    artifacts = session.scalars(
        select(CalculationArtifact)
        .where(
            CalculationArtifact.calculation_id == calculation.id,
            CalculationArtifact.kind == ArtifactKind.output_log,
        )
        .order_by(CalculationArtifact.id)
    ).all()
    cache = session.info.get("parameter_artifact_bytes", {})
    client = None
    for artifact in artifacts:
        try:
            content = cache.get(artifact.sha256)
            if content is None:
                if client is None:
                    client = _short_timeout_client()
                content = load_artifact_bytes(artifact.sha256, client=client)
            text = _decode_text(content)
            log_software = _resolve_software(calculation, text)
            parsed = _run_parser(log_software, text)
            observations = [_to_observation(p) for p in parsed.get("parameters", [])]
            observations, _ = merge_artifact_observations(
                session, calculation, observations, artifact_kind=ArtifactKind.output_log, software=log_software
            )
            persist_calculation_parameters(
                session,
                calculation,
                observations,
                source=ParameterSource.parser,
                parser=ParserStamp(**parsed["parser"]) if isinstance(parsed.get("parser"), dict) else None,
                artifact_kind=ArtifactKind.output_log,
                replace=False,
            )
        except Exception as exc:  # best effort, never blocks the deck's own rows
            logger.warning(_stored_log_rows_restore_reason(artifact, exc))


def refresh_output_log_rows(session: Session, calculation: Calculation) -> None:
    """Drop the calculation's log-derived rows and re-derive them from its stored output logs (after a parser fix).

    Log rows are appended, first statement wins, and carry no source artifact, so a parser improvement cannot
    replace them one by one; the backfill calls this to re-derive them all, in upload order, under the deck-wins rule.
    """
    session.query(CalculationParameter).filter(
        CalculationParameter.calculation_id == calculation.id,
        CalculationParameter.source == ParameterSource.parser,
        CalculationParameter.artifact_kind == ArtifactKind.output_log,
    ).delete(synchronize_session=False)
    session.expire_all()
    _restore_log_rows(session, calculation)


def _decode_text(content: bytes) -> str:
    """Decode artifact bytes to text for parser consumption.

    Input artifacts are required to be valid UTF-8 by
    :func:`validate_artifact`, but the backfill path may also encounter
    older rows; ``errors="replace"`` keeps the parser running on
    encoding edge cases instead of failing the whole row.
    """

    return content.decode("utf-8", errors="replace")


#: The artifact kinds whose text is read for parameters: a deck, and a log that echoes the deck.
_PARAMETER_ARTIFACT_KINDS = (ArtifactKind.input, ArtifactKind.output_log)


def try_extract_parameters_from_artifact_upload(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
    *,
    warnings: list[UploadWarning] | None = None,
) -> list[CalculationParameter] | None:
    """Opportunistic upload-side hook: extract from an ArtifactIn (an input deck or an output log).

    Used by the artifact upload route and inline-bundle workflows
    immediately after the matching ``CalculationArtifact`` row has been
    persisted. Bytes are decoded from the in-memory base64 payload so
    no S3 round-trip is needed.

    Returns ``None`` (and logs a warning) on any failure or when the
    artifact kind is neither ``input`` nor ``output_log``. Never raises — artifact upload is
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
    kind = next((k for k in _PARAMETER_ARTIFACT_KINDS if artifact_in.kind == k), None)
    if kind is None:
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

    remember_artifact_bytes(session, content)
    text = _decode_text(content)
    return _extract_safe(
        session, calculation, text, source=artifact_in.filename, warnings=warnings, artifact_kind=kind
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

    if artifact.kind not in _PARAMETER_ARTIFACT_KINDS:
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
        artifact_kind=artifact.kind,
    )


def _extract_safe(
    session: Session,
    calculation: Calculation,
    text: str,
    *,
    source: str,
    warnings: list[UploadWarning] | None = None,
    artifact_kind: ArtifactKind = ArtifactKind.input,
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
                session, calculation, text, warnings=warnings, artifact_kind=artifact_kind
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
