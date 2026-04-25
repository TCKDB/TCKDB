"""Reconcile user-declared vs parser-extracted software provenance.

Three-source model:
- **declared**: user-provided SoftwareReleaseRef in the upload payload
- **observed**: parser-extracted from the output artifact
- **reconciled**: the service's decision on what to trust

Match statuses:
- ``matched``: both sources agree
- ``declared_only``: user provided, parser could not extract
- ``parsed_only``: parser extracted, user omitted
- ``enriched``: user provided partial info, parser filled gaps
- ``mismatch``: user and parser disagree on at least one field
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.fragments.refs import SoftwareReleaseRef


@dataclass
class SoftwareReconciliationResult:
    """Outcome of reconciling declared vs observed software provenance."""

    resolved_ref: SoftwareReleaseRef
    match_status: str  # matched, declared_only, parsed_only, enriched, mismatch
    mismatches: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    raw_banner: str | None = None


def parsed_dict_to_ref(parsed: dict) -> SoftwareReleaseRef:
    """Convert parser output dict to a SoftwareReleaseRef.

    Gaussian parser returns:
        {"name": "gaussian", "version": "09", "build": "EM64L-G09RevD.01",
         "release_date_raw": "24-Apr-2013"}

    The build string contains platform prefix + revision. Extract the
    revision (e.g. "D.01") for the ``revision`` field, keep the full
    build string in ``build``.
    """
    name = parsed.get("name", "")
    version = parsed.get("version")
    raw_build = parsed.get("build", "")

    # Extract revision from Gaussian build strings like "EM64L-G09RevD.01"
    revision = None
    m = re.search(r"Rev([A-Z]\.\d+)", raw_build)
    if m:
        revision = m.group(1)

    return SoftwareReleaseRef(
        name=name,
        version=version,
        revision=revision,
        build=raw_build or None,
    )


def reconcile_software_provenance(
    *,
    declared: SoftwareReleaseRef | None,
    parsed: dict | None,
) -> SoftwareReconciliationResult:
    """Reconcile user-declared and parser-extracted software provenance.

    :param declared: User-provided software reference (may be None).
    :param parsed: Parser-extracted software dict (may be None).
    :returns: Reconciliation result with resolved ref and match status.
    """
    parsed_ref = parsed_dict_to_ref(parsed) if parsed else None
    raw_banner = parsed.get("build") if parsed else None

    # --- Neither source ---
    if declared is None and parsed_ref is None:
        raise ValueError(
            "No software provenance available: neither user-declared "
            "nor parser-extracted."
        )

    # --- Declared only ---
    if declared is not None and parsed_ref is None:
        return SoftwareReconciliationResult(
            resolved_ref=declared,
            match_status="declared_only",
        )

    # --- Parsed only ---
    if declared is None and parsed_ref is not None:
        return SoftwareReconciliationResult(
            resolved_ref=parsed_ref,
            match_status="parsed_only",
            raw_banner=raw_banner,
        )

    # --- Both available: compare ---
    assert declared is not None and parsed_ref is not None
    mismatches = _compare_refs(declared, parsed_ref)

    if not mismatches:
        return SoftwareReconciliationResult(
            resolved_ref=declared,
            match_status="matched",
            raw_banner=raw_banner,
        )

    # Check if this is pure enrichment (user gave partial, parser fills gaps)
    enrichment_only = all(
        declared_val is None and parsed_val is not None
        for declared_val, parsed_val in mismatches.values()
    )
    if enrichment_only:
        enriched = _enrich_ref(declared, parsed_ref)
        return SoftwareReconciliationResult(
            resolved_ref=enriched,
            match_status="enriched",
            mismatches=mismatches,
            raw_banner=raw_banner,
        )

    # Real mismatch — declared takes precedence, but flag it
    return SoftwareReconciliationResult(
        resolved_ref=declared,
        match_status="mismatch",
        mismatches=mismatches,
        raw_banner=raw_banner,
    )


def _compare_refs(
    declared: SoftwareReleaseRef,
    parsed: SoftwareReleaseRef,
) -> dict[str, tuple[str | None, str | None]]:
    """Compare two refs field-by-field, ignoring None on the declared side.

    Returns a dict of {field_name: (declared_value, parsed_value)} for
    fields that differ. A declared field of None is treated as "not
    specified" and does not count as a mismatch.
    """
    mismatches: dict[str, tuple[str | None, str | None]] = {}

    # Compare name (case-insensitive)
    d_name = (declared.name or "").strip().lower()
    p_name = (parsed.name or "").strip().lower()
    if d_name and p_name and d_name != p_name:
        mismatches["name"] = (declared.name, parsed.name)

    # Compare version
    if declared.version is not None and parsed.version is not None:
        if declared.version.strip() != parsed.version.strip():
            mismatches["version"] = (declared.version, parsed.version)
    elif declared.version is None and parsed.version is not None:
        mismatches["version"] = (None, parsed.version)

    # Compare revision
    if declared.revision is not None and parsed.revision is not None:
        if declared.revision.strip() != parsed.revision.strip():
            mismatches["revision"] = (declared.revision, parsed.revision)
    elif declared.revision is None and parsed.revision is not None:
        mismatches["revision"] = (None, parsed.revision)

    return mismatches


def _enrich_ref(
    declared: SoftwareReleaseRef,
    parsed: SoftwareReleaseRef,
) -> SoftwareReleaseRef:
    """Fill gaps in the declared ref with parsed values."""
    return SoftwareReleaseRef(
        name=declared.name,
        version=declared.version or parsed.version,
        revision=declared.revision or parsed.revision,
        build=declared.build or parsed.build,
        release_date=declared.release_date,
        notes=declared.notes,
    )
