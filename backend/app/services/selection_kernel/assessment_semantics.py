"""The versioned contract of how a candidate's *eligibility* is assessed, kept apart from the decision procedure.

A selection manifest says two different things and they change for different reasons:

* the **decision procedure** (``policy``: outcomes, front construction, ordering) is what ``replay_decision`` re-runs. Its
  version changes when the procedure does;
* the **assessment semantics** (this module) are how each candidate's ``eligible`` flag was produced: which trust rubric
  (name and version) graded its evidence, and which structure findings were consulted for its sources. The assessment runs when a
  selection is *made*; replay never reruns it. It reads the recorded ``eligible`` flags.

So a change to assessment semantics (a trust rubric bump, a new source-finding gate) does not change the policy version
and does not break replay of a manifest made under another assessment; it changes this block. A manifest with no block,
or a malformed one, is refused: nothing reads it as the current semantics by default. Replay is the reproduction of a
recorded decision under the semantics it was made with; it is not a fresh assessment and not an endorsement under
today's rules (``describe_replay`` says so in words).
"""

from __future__ import annotations

from typing import Any

from tckdb_schemas.fragments.version import VersionRef

from app.versioning import UnknownVersion, VersionRegistry

#: The version of the source-finding gate (``structure_selection.source_findings``) recorded in the block.
SOURCE_FINDINGS_VERSION = 1


class UnsupportedAssessmentSemantics(ValueError):
    """The manifest records assessment semantics this release does not know."""


def semantics_block(*, version: int, evidence_rubric: VersionRef) -> dict[str, Any]:
    """The block a new manifest records: the semantics version, the rubric that graded the evidence, the finding gate.

    ``evidence_rubric`` is recorded as a name beside a separate integer version, never as one string.
    """
    return {
        "version": version,
        "evidence_rubric": {"name": evidence_rubric.name, "version": evidence_rubric.version},
        "source_findings": SOURCE_FINDINGS_VERSION,
    }


def recorded_version(manifest: dict[str, Any]) -> int:
    """The assessment semantics a manifest was made under: its block's version.

    :raises UnsupportedAssessmentSemantics: the block is missing or malformed (a version that is not exactly an integer,
        ``True`` and ``1.0`` included, is malformed).
    """
    block = manifest.get("assessment_semantics")
    if not isinstance(block, dict) or type(block.get("version")) is not int:
        raise UnsupportedAssessmentSemantics("the manifest has no well-formed assessment_semantics block")
    return block["version"]


def check_supported(manifest: dict[str, Any], *, semantics: VersionRegistry[VersionRef], name: str) -> int:
    """The manifest's assessment semantics version, refusing one the registry does not hold.

    *semantics* is the engine's registry of the assessment semantics it can read and *name* the semantics' name in it.
    """
    recorded = recorded_version(manifest)
    try:
        semantics.get(name, recorded)
    except UnknownVersion:
        known = sorted(ref.version for ref in semantics.refs() if ref.name == name)
        raise UnsupportedAssessmentSemantics(
            f"the manifest was made under assessment semantics {recorded!r}; this release reads {known}"
        ) from None
    return recorded


def describe_replay(manifest: dict[str, Any], *, semantics: VersionRegistry[VersionRef], name: str) -> dict[str, Any]:
    """Label a replay as historical or current, in words and in fields. Pure; never reassesses anything."""
    recorded = check_supported(manifest, semantics=semantics, name=name)
    current = semantics.current(name).version
    historical = not semantics.is_current(VersionRef(name=name, version=recorded))
    return {
        "recorded_assessment_semantics": recorded,
        "current_assessment_semantics": current,
        "kind": "historical" if historical else "current",
        "reassessed": False,
        "statement": (
            "Replay reproduces the ordering from the eligibility recorded in the manifest. It does not reassess any "
            "candidate and is not an endorsement under today's rules"
            + (
                f"; this manifest was made under {recorded}, and a fresh selection would be assessed under {current}."
                if historical
                else "."
            )
        ),
    }
