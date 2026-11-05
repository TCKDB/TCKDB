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

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.version import ParserStamp
from tckdb_schemas.program_default_settings import uses_dft_grid

from app.chemistry.method_families import MethodFamily, classify_method
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationParameter,
)
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactKind,
    ParameterSource,
    ParameterStatementKind,
)
from app.schemas.fragments.artifact import ArtifactIn
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
from app.services.parameter_observation import ParserObservation
from app.services.parameter_reconciliation import (
    W_OUTPUT_LOG_JOB_NOT_BOUND,
    bind_gaussian_job,
    declared_parameter_warnings,
    level_of_theory_qualifier_warnings,
    level_of_theory_warnings,
    merge_artifact_observations,
    reported_setting_disagreements,
    reported_setting_warnings,
)
from app.services.reported_settings_parser import parse_gaussian_reported, parse_orca_reported

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


def _to_observation(parsed: dict) -> ParserObservation:
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

    return ParserObservation(
        statement_kind=ParameterStatementKind(parsed.get("statement_kind") or "requested"),
        raw_key=str(parsed["raw_key"]),
        raw_value=str(raw_value),
        canonical_key=parsed.get("canonical_key"),
        canonical_value=parsed.get("canonical_value"),
        section=parsed.get("section"),
        value_type=parsed.get("value_type"),
        unit=parsed.get("unit"),
        parameter_index=parameter_index,
    )


def _grid_is_this_calculations(calculation: Calculation) -> bool:
    """True when the calculation's own level of theory integrates on a DFT grid (DFT, double hybrid, or a composite).

    A log can hold the grid of a different job (a ``--Link1--`` file whose second job is a coupled-cluster step that
    prints no grid), so a reported grid is attached only to a calculation whose method has a grid of its own. For a
    composite the reported grid rows already require every step to agree.
    """
    method = calculation.lot.method if calculation.lot is not None else None
    if not method:
        return False
    return classify_method(method).family is MethodFamily.composite or uses_dft_grid(method)


def _observations_of(
    software: SoftwareName, text: str, parsed: dict, artifact_kind: ArtifactKind, calculation: Calculation
) -> list[ParserObservation]:
    """The parser's requested rows, and for an output log the rows the log reports as used (Gaussian and ORCA)."""
    observations = [_to_observation(p) for p in parsed.get("parameters", [])]
    if artifact_kind is ArtifactKind.output_log:
        reporter = {"gaussian": parse_gaussian_reported, "orca": parse_orca_reported}.get(software)
        if reporter is not None:
            reported = reporter(text)
            if not _grid_is_this_calculations(calculation):
                reported = [r for r in reported if r.get("section") != "grid"]
            observations += [_to_observation(p) for p in reported]
    return observations


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


def _the_calculations_job(
    session: Session,
    calculation: Calculation,
    software: str,
    text: str,
    artifact_kind: ArtifactKind,
    *,
    deck: CalculationArtifact | None = None,
    client_box: list | None = None,
    strict: bool = False,
) -> tuple[str, bool]:
    """``(text to read, unbound)``: a Gaussian log is cut to the job that is this calculation (see
    :func:`app.services.parameter_reconciliation.bind_gaussian_job`).

    When no single job is, the whole text is returned with ``unbound`` set: the caller reads nothing that belongs to a
    job from it (no echoed request, no reported value), so another job's value never stands in. Decks and the other
    programs' logs pass through. Jobs the log cannot tell apart are told apart by the calculation's stored input deck
    (``deck``, else the most recent one); a deck that cannot be read leaves them untold (``strict``: raises instead).
    """
    if software != "gaussian" or artifact_kind is ArtifactKind.input:
        return text, False
    session.flush()

    def deck_text() -> str | None:
        try:
            artifact = deck
            if artifact is None:
                decks = _stored_artifacts(session, calculation, ArtifactKind.input)
                artifact = decks[-1] if decks else None
            return None if artifact is None else _decode_text(_artifact_bytes(session, artifact, client_box))
        except Exception as exc:
            if strict:
                raise
            logger.warning("deck not read to tell Gaussian jobs apart for calculation id=%s: %s", calculation.id, exc)
            return None

    job = bind_gaussian_job(calculation, text, deck_text)
    return (text, True) if job is None else (job, False)


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
    state a setting the log's echoed row is kept (see :mod:`app.services.parameter_reconciliation`). A log skips
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
    is_deck = artifact_kind is ArtifactKind.input
    artifact_text, unbound_log = _the_calculations_job(session, calculation, software, artifact_text, artifact_kind)
    parsed = _run_parser(software, artifact_text)
    if unbound_log:
        parsed = {**parsed, "parameters": []}

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

    # Disagreements already in place, so the warning below is given for what this read creates and not again.
    try:
        session.flush()
        disagreements_before = reported_setting_disagreements(session, calculation, software)
    except Exception:  # pragma: no cover - defensive, never blocks
        disagreements_before = []
    observations = _observations_of(software, "" if unbound_log else artifact_text, parsed, artifact_kind, calculation)
    # What this artifact requests, before the precedence rule drops any of it: the typed-versus-read judgement is
    # made on this, so it does not depend on which artifact of the request was read first. A reported value is not
    # a request and is never judged against what the depositor typed.
    observed = [
        (o.canonical_key, o.canonical_value)
        for o in observations
        if o.statement_kind is ParameterStatementKind.requested
    ]
    observations, conflicts = merge_artifact_observations(
        session, calculation, observations, artifact_kind=artifact_kind, software=software
    )
    parsed_stamp = parsed.get("parser")
    effective_parser = parser or (ParserStamp(**parsed_stamp) if isinstance(parsed_stamp, dict) else None)
    snapshot_is_ours = is_deck or (calculation.parameters_json is None and not unbound_log)

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

    if warnings is not None:
        # Best effort: a failure comparing with what the depositor typed must not lose the parameters.
        try:
            session.flush()
            warnings.extend(conflicts)
            if unbound_log:
                # One per log: the same log read twice in a request earns one, two different logs earn two.
                unbound = UploadWarning.from_code(
                    field="artifacts",
                    code=W_OUTPUT_LOG_JOB_NOT_BOUND,
                    context={
                        "software": software,
                        "depositor_supplied": {"log_sha256": hashlib.sha256(artifact_text.encode()).hexdigest()[:16]},
                    },
                )
                if not any(w.code == unbound.code and w.context == unbound.context for w in warnings):
                    warnings.append(unbound)
            warnings.extend(reported_setting_warnings(session, calculation, software, before=disagreements_before))
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
                warnings.extend(
                    level_of_theory_qualifier_warnings(
                        calculation, observed, parsed_software=software, artifact_kind=artifact_kind
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
    what = "input deck" if artifact.kind is ArtifactKind.input else "output log"
    return f"stored {what} id={artifact.id} not re-read: {type(exc).__name__}: {exc}"


def _stored_artifacts(session: Session, calculation: Calculation, kind: ArtifactKind) -> list[CalculationArtifact]:
    return list(
        session.scalars(
            select(CalculationArtifact)
            .where(CalculationArtifact.calculation_id == calculation.id, CalculationArtifact.kind == kind)
            .order_by(CalculationArtifact.id)
        )
    )


def _artifact_bytes(session: Session, artifact: CalculationArtifact, client_box: list | None) -> bytes:
    """A stored artifact's bytes: from memory when uploaded in this request, else through a short-timeout client."""
    content = session.info.get("parameter_artifact_bytes", {}).get(artifact.sha256)
    if content is None:
        if client_box is None:
            client_box = []
        if not client_box:
            client_box.append(_short_timeout_client())
        content = load_artifact_bytes(artifact.sha256, client=client_box[0])
    return content


def _parse_stored_artifact(
    session: Session,
    calculation: Calculation,
    artifact: CalculationArtifact,
    client_box: list,
    deck: CalculationArtifact | None = None,
):
    """``(software, observations, parser stamp)`` for one stored deck or log; raises when it cannot be read or parsed.

    Bytes uploaded in this request come from memory; others are read through a short-timeout client (made once and
    kept in ``client_box``).
    """
    text = _decode_text(_artifact_bytes(session, artifact, client_box))
    software = _resolve_software(calculation, text)
    text, unbound = _the_calculations_job(
        session, calculation, software, text, artifact.kind, deck=deck, client_box=client_box, strict=True
    )
    parsed = _run_parser(software, text)
    if unbound:
        parsed = {**parsed, "parameters": []}
    stamp = ParserStamp(**parsed["parser"]) if isinstance(parsed.get("parser"), dict) else None
    return software, _observations_of(software, "" if unbound else text, parsed, artifact.kind, calculation), stamp


def _append_log_observations(session: Session, calculation: Calculation, software, observations, stamp) -> None:
    observations, _ = merge_artifact_observations(
        session, calculation, observations, artifact_kind=ArtifactKind.output_log, software=software
    )
    persist_calculation_parameters(
        session,
        calculation,
        observations,
        source=ParameterSource.parser,
        parser=stamp,
        artifact_kind=ArtifactKind.output_log,
        replace=False,
    )


def refresh_output_log_rows(
    session: Session, calculation: Calculation, *, deck_artifact: CalculationArtifact | None = None
) -> None:
    """Re-derive the calculation's log rows **and its deck rows** from stored artifacts (after a parser fix); all or nothing.

    ``deck_artifact`` is the deck whose rows stand (an operator's ``--artifact-id`` choice); by default it is the
    most recently stored input artifact, the one an upload's replace-all leaves standing.

    Log rows are appended, first statement wins, and carry no source artifact, so a parser improvement cannot
    replace them one by one. A deck row is dropped when a log echoes the same setting (the log outranks the deck),
    so when the log rows are re-derived the deck rows they had shadowed must come back first. That deck and every
    stored log are read and parsed before anything is changed; if any cannot be, this raises
    :class:`ParameterExtractionError` having deleted nothing, so an outage never turns two rows into none. Only
    then are the old log rows dropped, the deck rows rebuilt, and the new log rows appended in upload order.
    """
    client_box: list = []
    deck = None
    if deck_artifact is None:
        decks = _stored_artifacts(session, calculation, ArtifactKind.input)
        deck_artifact = decks[-1] if decks else None
    logs = []
    for artifact in ([deck_artifact] if deck_artifact is not None else []) + _stored_artifacts(
        session, calculation, ArtifactKind.output_log
    ):
        try:
            parsed = _parse_stored_artifact(session, calculation, artifact, client_box, deck_artifact)
        except Exception as exc:
            raise ParameterExtractionError(_stored_log_rows_restore_reason(artifact, exc)) from exc
        if artifact.kind is ArtifactKind.input:
            deck = parsed
        else:
            logs.append(parsed)
    session.query(CalculationParameter).filter(
        CalculationParameter.calculation_id == calculation.id,
        CalculationParameter.source == ParameterSource.parser,
        CalculationParameter.artifact_kind == ArtifactKind.output_log,
    ).delete(synchronize_session=False)
    session.expire_all()
    if deck is not None:
        software, observations, stamp = deck
        persist_calculation_parameters(
            session,
            calculation,
            observations,
            source=ParameterSource.parser,
            parser=stamp,
            artifact_kind=ArtifactKind.input,
            replace=True,
        )
        session.flush()
    for software, observations, stamp in logs:
        _append_log_observations(session, calculation, software, observations, stamp)


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


def inputs_first(artifacts):
    """The artifacts of one request with the input decks first, each group in its listed order.

    A log is read against the decks the calculation already has (a Link1 log's jobs are told apart by the deck), so a
    request must not let the order a producer listed its artifacts in decide what a log binds to.
    """
    return sorted(artifacts, key=lambda a: a.kind != ArtifactKind.input)


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
