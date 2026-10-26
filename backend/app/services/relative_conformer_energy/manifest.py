"""The replayable relative-energy manifest, and its replay.

The manifest holds the normalised request, the read profile and effective review statuses it was made under, the
snapshot isolation, the subject entry, the compared groups and the determinations assessed, **every normalised
calculation and determination** (public refs and stored facts, no database id), each determination's assessment and
the computed answer. Checksums are per normalised input and over the whole manifest.

Replay recomputes every assessment from the normalised inputs and compares it with the recorded one (a recorded
``eligible`` is never trusted), then recomputes the answer from the *recomputed* assessments and compares it with the
recorded one. The digests are **checksums, not signatures**: they detect an accidental or careless edit, and anyone
who edits a manifest can recompute them, which is exactly why replay does not rely on them alone.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.services.manifest_formats import ManifestFormat, manifest_header, new_format_registry, read_manifest_format
from app.services.relative_conformer_energy.compare import COMPARISON_VERSION, ComparisonRequest, compare_results
from app.services.relative_conformer_energy.compute import compute_relative_energies
from app.services.relative_conformer_energy.models import (
    GROUP_BELOW_REVIEW_FLOOR,
    GROUP_TERMINAL_REVIEW_STATUS,
    RELATIVE_ENERGY_VERSION,
    RelativeEnergyRequest,
    RelativeEnergyResult,
)
from app.services.relative_conformer_energy.population import Population
from app.services.scientific_read.common import check_read_profile_block, evidence_usable_statuses
from app.services.scientific_read.profile import CURATED_REVIEW_FLOOR
from app.services.structure_selection.assessment import assess_unit
from app.services.structure_selection.bounds import check_manifest_size
from app.services.structure_selection.decision import ADMIN_KEY_VERSION
from app.services.structure_selection.manifest import (
    INTEGRITY_NOTE,
    ReplayError,
    canonical,
    check_basin_statuses,
    digest,
    expected_effective_statuses,
    population_block,
)
from app.services.structure_selection.models import (
    ASSESSMENT_VERSION,
    NORMALIZER_VERSION,
    SUPPORTED_FINDING_VERSIONS,
    NormalizedCalculation,
    NormalizedDetermination,
    SelectionBounds,
    StructureAssessmentResult,
    StructureRequest,
    StructureSubject,
)
from app.versioning import same_value

MANIFEST_FORMAT = "relative_conformer_energy_manifest"
#: Version 4: ``read_profile`` records the review statuses whose linked evidence the profile withheld (ADR 0023); under
#: ``curated`` a not-reviewed or under-review record is shown as evidence and only a rejected or deprecated one is a
#: token. Version 5: a recorded basin determination carries its conformer observation's and that observation's group's
#: status apart (``observation_review_status``, ``group_review_status``), so a replay judges the group (a compared record) by
#: the record floor and the observation (evidence) by the evidence rule. A manifest of an earlier format is refused.
MANIFEST_FORMAT_VERSION = 5


def _canon(value: Any) -> Any:
    """``value`` as it is after a JSON round trip (tuples become lists), for comparing recorded with recomputed."""
    return json.loads(canonical(value))


def _with_digest(raw: dict[str, Any]) -> dict[str, Any]:
    return {**raw, "content_sha256": digest(raw)}


def _without_digest(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    return {k: v for k, v in raw.items() if k != "content_sha256"}, raw.get("content_sha256")


def _versions() -> dict[str, Any]:
    return {
        "relative_energy": RELATIVE_ENERGY_VERSION,
        "assessment": ASSESSMENT_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "finding_semantics": sorted(SUPPORTED_FINDING_VERSIONS),
        # A repeat's representative is chosen by the structure administrative key, so its version is part of the answer.
        "administrative_key": ADMIN_KEY_VERSION,
    }


def build_manifest(
    request: RelativeEnergyRequest,
    population: Population,
    assessment: StructureAssessmentResult,
    result: RelativeEnergyResult,
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the manifest of one answer over one assessed population.

    :raises CodedValueError: ``structure_selection_manifest_too_large`` when the canonical manifest exceeds the bound;
        nothing is returned without its whole manifest.
    """
    body: dict[str, Any] = {
        "format": manifest_header(MANIFEST_FORMATS, MANIFEST_FORMAT),
        "versions": _versions(),
        "request": request.to_dict(),
        "structure_request": assessment.request.to_dict(),
        "effective_review_statuses": sorted(s.value for s in assessment.effective_statuses),
        "read_profile": dict(profile),
        "snapshot_isolation": assessment.snapshot_isolation,
        "subject": assessment.subject.to_dict(),
        "population": {
            **population_block(assessment, withhold_excluded=profile.get("review_floor") is not None),
            "groups": list(population.group_refs),
            "excluded_groups": dict(sorted(population.excluded_groups.items())),
            "floor_excluded_groups": sorted(population.floor_excluded_refs),
            "terminal_excluded_groups": sorted(population.terminal_excluded_refs),
            "determinations": list(population.determination_refs),
        },
        "calculations": [_with_digest(c.to_dict()) for c in assessment.calculations],
        "determinations": [_with_digest(d.to_dict()) for d in assessment.determinations],
        "assessments": [a.to_dict() for a in assessment.assessments],
        "result": result.to_dict(),
        "outcome": result.outcome.value,
    }
    body = _canon(body)
    manifest = {**body, "integrity": {"algorithm": "sha256", "manifest_sha256": digest(body), "note": INTEGRITY_NOTE}}
    check_manifest_size(assessment.request.bounds, len(canonical(manifest).encode()))
    return manifest


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _check_versions(manifest: dict[str, Any]) -> None:
    _check_format(manifest, MANIFEST_FORMAT)
    versions = manifest.get("versions") or {}
    for name, value in _versions().items():
        if not same_value(versions.get(name), value):
            raise ReplayError(
                "unsupported_semantic_version",
                f"manifest was made under {name} version {versions.get(name)!r}; this release replays {value!r}",
            )


def _check_integrity(manifest: dict[str, Any]) -> None:
    integrity = manifest.get("integrity") or {}
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("manifest_sha256") != digest(body):
        raise ReplayError("manifest_checksum_mismatch", "the manifest does not match its own checksum")
    for section in ("calculations", "determinations"):
        for raw in manifest[section]:
            inner, recorded = _without_digest(raw)
            if recorded != digest(inner):
                ref = inner.get("calculation_ref") or inner.get("determination_ref")
                raise ReplayError("input_checksum_mismatch", f"normalised input {ref} does not match its checksum")


def _check_consistency(
    manifest: dict[str, Any], request: StructureRequest, dets: list[NormalizedDetermination], calcs: list[NormalizedCalculation]
) -> None:
    profile = manifest.get("read_profile") or {}
    problem = check_read_profile_block(profile)
    if problem is not None:
        raise ReplayError("inconsistent_manifest", problem)
    name, floor_raw = profile.get("profile"), profile.get("review_floor")
    if name not in ("exploratory", "curated"):
        raise ReplayError("inconsistent_manifest", f"unknown read profile {name!r}")
    floor = RecordReviewStatus(floor_raw) if floor_raw is not None else None
    if (name == "curated") != (floor == CURATED_REVIEW_FLOOR) or (name == "exploratory" and floor is not None):
        raise ReplayError("inconsistent_manifest", "the recorded read profile and its review floor disagree")
    recorded = frozenset(RecordReviewStatus(s) for s in manifest["effective_review_statuses"])
    if recorded != expected_effective_statuses(floor, request.min_review_status):
        raise ReplayError(
            "inconsistent_manifest",
            "the effective review statuses are not what the recorded profile and request floor allow",
        )
    if name == "curated" and recorded != frozenset({RecordReviewStatus.approved}):
        raise ReplayError("inconsistent_manifest", "a curated profile admits approved records only")
    # ADR 0023: the compared GROUPS are the records (the floor above); a determination rests on a conformer observation and on
    # calculations, which are evidence, so they may be any status the evidence rule uses (everything but rejected and
    # deprecated, over the caller's own floor). Computed from the recorded request alone, so a manifest cannot widen it.
    usable = evidence_usable_statuses(request.min_review_status)
    for calc in calcs:
        if calc.review_status not in usable:
            raise ReplayError("inconsistent_manifest", f"{calc.calculation_ref} has a review status its own request cannot see")
    for det in dets:
        check_basin_statuses(det, det.determination_ref, recorded, usable)
    population = manifest.get("population", {})
    if population.get("visible_units") != len(dets) or sorted(population.get("determinations", [])) != sorted(
        d.determination_ref for d in dets
    ):
        raise ReplayError("inconsistent_manifest", "the recorded determinations are not the ones the population names")
    _check_population_claims(
        population,
        dets,
        curated=name == "curated",
        floor_set=floor is not None or request.min_review_status is not None,
        caller_floor=request.min_review_status is not None,
    )


_GROUP_EXCLUSION_CODES = frozenset({GROUP_BELOW_REVIEW_FLOOR, GROUP_TERMINAL_REVIEW_STATUS})


def _check_population_claims(
    population: dict[str, Any], dets: list[NormalizedDetermination], *, curated: bool, floor_set: bool, caller_floor: bool
) -> None:
    """Refuse a population that contradicts itself: the claims about which groups were held out must agree with each
    other, with the groups compared, with the determinations recorded and with the profile."""

    def refuse(message: str) -> None:
        raise ReplayError("inconsistent_manifest", message)

    groups = set(population.get("groups", []))
    excluded: dict[str, str] = population.get("excluded_groups", {})
    floor_held = set(population.get("floor_excluded_groups", []))
    terminal_held = set(population.get("terminal_excluded_groups", []))
    # Under curated the groups are approved records, so none is excluded; an observation inside a group is evidence, so
    # only the caller's own floor can hold one out (a rejected or deprecated one is withheld, never held).
    if curated and (excluded or terminal_held or (floor_held and not caller_floor)):
        refuse("a curated profile shows approved groups only, so it has no excluded groups and holds out none the caller's own floor does not")
    if not set(excluded) <= groups:
        refuse("an excluded group is not among the compared groups")
    if not set(excluded.values()) <= _GROUP_EXCLUSION_CODES:
        refuse("an excluded group carries a reason that is not a group exclusion")
    if not (floor_held <= groups and terminal_held <= groups):
        refuse("a held-out group is not among the compared groups")
    if (floor_held | terminal_held) & set(excluded) or floor_held & terminal_held:
        refuse("a group is claimed excluded and held out, or held out twice")
    if any(d.conformer_group_ref in excluded for d in dets):
        refuse("a recorded determination belongs to an excluded group, whose determinations are never assessed")
    if not floor_set and (GROUP_BELOW_REVIEW_FLOOR in excluded.values() or floor_held):
        refuse("no review floor is set, so no group can be below one")


def replay_relative_energies(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute the answer from the manifest's normalised inputs and compare it with the recorded one.

    Every assessment is recomputed first and compared with the recorded one; the answer is then recomputed from the
    *recomputed* assessments, never the recorded ones.

    :returns: ``RelativeEnergyResult.to_dict()`` of the replayed answer.
    :raises ReplayError: unsupported format or semantic version; a failed checksum; a self-inconsistent manifest; a
        determination whose recorded assessment is not what its inputs give; an answer that is not what the
        assessments give.
    """
    _check_versions(manifest)
    _check_integrity(manifest)
    try:
        request = RelativeEnergyRequest.from_dict(manifest["request"])
        structure_request = StructureRequest.from_dict(manifest["structure_request"])
        subject = StructureSubject.from_dict(manifest["subject"])
        calcs = [NormalizedCalculation.from_dict(_without_digest(c)[0]) for c in manifest["calculations"]]
        dets = [NormalizedDetermination.from_dict(_without_digest(d)[0]) for d in manifest["determinations"]]
        groups = tuple(manifest["population"]["groups"])
        excluded = dict(manifest["population"]["excluded_groups"])
        floor_excluded = frozenset(manifest["population"]["floor_excluded_groups"])
        terminal_excluded = frozenset(manifest["population"]["terminal_excluded_groups"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayError("unreadable_manifest", f"the manifest cannot be read back: {exc}") from exc
    _check_consistency(manifest, structure_request, dets, calcs)

    by_ref = {c.calculation_ref: c for c in calcs}
    recomputed = [assess_unit(d, request=structure_request, subject=subject, calculations=by_ref) for d in dets]
    refs = [a["unit_ref"] for a in manifest["assessments"]]
    if len(set(refs)) != len(refs) or set(refs) != {a.unit_ref for a in recomputed} or len(refs) != len(recomputed):
        raise ReplayError("assessment_mismatch", "the recorded assessments do not cover exactly the recorded determinations")
    recorded = {a["unit_ref"]: a for a in manifest["assessments"]}
    for a in recomputed:
        if _canon(a.to_dict()) != _canon(recorded[a.unit_ref]):
            raise ReplayError("assessment_mismatch", f"the recorded assessment of {a.unit_ref} is not what its inputs give")

    result = compute_relative_energies(
        request,
        structure_request,
        group_refs=groups,
        excluded_groups=excluded,
        floor_excluded_refs=floor_excluded,
        terminal_excluded_refs=terminal_excluded,
        determinations=dets,
        assessments=recomputed,
        calculations=by_ref,
    )
    replayed = _canon(result.to_dict())
    if replayed != _canon(manifest["result"]) or result.outcome.value != manifest["outcome"]:
        raise ReplayError("result_mismatch", "the recorded answer is not what the recomputed assessments give")
    return replayed


def replay_matches(manifest: dict[str, Any]) -> bool:
    """True when replay reproduces the recorded answer; any refusal is raised as :class:`ReplayError`."""
    return replay_relative_energies(manifest) == _canon(manifest["result"])


# ---------------------------------------------------------------------------
# The benchmark comparison manifest
# ---------------------------------------------------------------------------

COMPARISON_MANIFEST_FORMAT = "relative_conformer_energy_comparison_manifest"
COMPARISON_MANIFEST_FORMAT_VERSION = 5
_RUNS = ("candidate", "reference")

#: The two manifest documents this module writes and reads, each under its stable name. The reader looks up the
#: version a manifest recorded; the writer asks for the current one. Held here, after both constants exist.
MANIFEST_FORMATS = new_format_registry(
    "relative_conformer_energy_manifest_formats", MANIFEST_FORMAT, MANIFEST_FORMAT_VERSION
)
MANIFEST_FORMATS.register(
    COMPARISON_MANIFEST_FORMAT,
    COMPARISON_MANIFEST_FORMAT_VERSION,
    ManifestFormat(COMPARISON_MANIFEST_FORMAT, COMPARISON_MANIFEST_FORMAT_VERSION),
    current=True,
)
MANIFEST_FORMATS.validate()


def _refuse_format(manifest: dict[str, Any]) -> Callable[[str], ReplayError]:
    """The refusal both manifests raise: it names the recorded format block."""
    return lambda _message: ReplayError(
        "unsupported_manifest_format", f"unknown manifest format {manifest.get('format')!r}"
    )


def _check_format(manifest: dict[str, Any], name: str) -> None:
    """Refuse a manifest recorded under another format name or a version the registry does not hold."""
    read_manifest_format(MANIFEST_FORMATS, name, manifest, _refuse_format(manifest))


def build_comparison_manifest(
    request: ComparisonRequest,
    runs: dict[str, dict[str, Any]],
    comparison: dict[str, Any],
    *,
    profile: dict[str, Any],
    snapshot_isolation: str,
    bounds: SelectionBounds,
) -> dict[str, Any]:
    """The manifest of one comparison: both runs' complete manifests, the pairing and the comparison.

    Each run is a whole relative-energy manifest (its own inputs, assessments, answer and checksums), so replay of the
    comparison is replay of the two runs and a recomputation of the pairing from their replayed answers. The digests
    are checksums, not signatures.

    :raises CodedValueError: ``structure_selection_manifest_too_large`` when the combined canonical manifest (not only
        each run) exceeds the bound; nothing is returned without its whole manifest.
    """
    body: dict[str, Any] = {
        "format": manifest_header(MANIFEST_FORMATS, COMPARISON_MANIFEST_FORMAT),
        "versions": {**_versions(), "comparison": COMPARISON_VERSION},
        "request": request.to_dict(),
        "read_profile": dict(profile),
        "snapshot_isolation": snapshot_isolation,
        "runs": {name: runs[name] for name in _RUNS},
        "result": comparison,
        "outcome": comparison["outcome"],
    }
    body = _canon(body)
    manifest = {**body, "integrity": {"algorithm": "sha256", "manifest_sha256": digest(body), "note": INTEGRITY_NOTE}}
    check_manifest_size(bounds, len(canonical(manifest).encode()))
    return manifest


def replay_comparison(manifest: dict[str, Any]) -> dict[str, Any]:
    """Replay both runs, then recompute the comparison from the *replayed* answers and compare it with the recorded one.

    A recorded outcome, statistic or pair is never trusted: each run is replayed from its own normalised inputs, and the
    pairing and statistics are recomputed from what that replay gives.

    :raises ReplayError: unsupported format or version, a failed checksum, runs that do not match the recorded request or
        each other, a run that does not replay, or a comparison that is not what the replayed answers give.
    """
    _check_format(manifest, COMPARISON_MANIFEST_FORMAT)
    versions = manifest.get("versions") or {}
    for name, value in {**_versions(), "comparison": COMPARISON_VERSION}.items():
        if not same_value(versions.get(name), value):
            raise ReplayError(
                "unsupported_semantic_version",
                f"manifest was made under {name} version {versions.get(name)!r}; this release replays {value!r}",
            )
    integrity = manifest.get("integrity") or {}
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("manifest_sha256") != digest(body):
        raise ReplayError("manifest_checksum_mismatch", "the manifest does not match its own checksum")
    try:
        request = ComparisonRequest.from_dict(manifest["request"])
        runs = {name: manifest["runs"][name] for name in _RUNS}
        recorded = manifest["result"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayError("unreadable_manifest", f"the manifest cannot be read back: {exc}") from exc
    expected = {"candidate": request.candidate, "reference": request.reference}
    for name in _RUNS:
        run = runs[name]
        if _canon(run.get("request")) != _canon(expected[name].to_dict()):
            raise ReplayError("inconsistent_manifest", f"the {name} run was not made for the recorded request")
        if (
            run.get("read_profile") != manifest["read_profile"]
            or run.get("snapshot_isolation") != manifest["snapshot_isolation"]
        ):
            raise ReplayError("inconsistent_manifest", f"the {name} run was made under another profile or snapshot")
    first, second = runs["candidate"], runs["reference"]
    for key in ("groups", "excluded_groups", "floor_excluded_groups", "terminal_excluded_groups", "determinations"):
        if first["population"].get(key) != second["population"].get(key):
            raise ReplayError("inconsistent_manifest", "the two runs were not made over one population")
    for name in _RUNS:
        # Both runs are made under the comparison's own versions, which are the running ones (checked above).
        if not same_value(runs[name].get("versions"), _canon(_versions())):
            raise ReplayError("inconsistent_manifest", f"the {name} run was made under another semantic version")
    replayed = {name: replay_relative_energies(runs[name]) for name in _RUNS}
    result = _canon(
        compare_results(
            replayed["candidate"],
            replayed["reference"],
            pairing=request.pairing,
            coverage_requirement=request.candidate.coverage,
            reference_conformer_ref=request.candidate.reference_conformer_ref,
        )
    )
    if result != _canon(recorded) or result["outcome"] != manifest["outcome"]:
        raise ReplayError("result_mismatch", "the recorded comparison is not what the replayed answers give")
    return result


def replay_comparison_matches(manifest: dict[str, Any]) -> bool:
    """True when replay reproduces the recorded comparison; any refusal is raised as :class:`ReplayError`."""
    return replay_comparison(manifest) == _canon(manifest["result"])
