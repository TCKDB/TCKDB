"""The versioned contract of how a candidate's *eligibility* is assessed, kept apart from the decision procedure.

A selection manifest says two different things and they change for different reasons:

* the **decision procedure** (``policy``: outcomes, front construction, ordering) is what ``replay_decision`` re-runs. Its
  version changes when the procedure does;
* the **assessment semantics** (this module) are how each candidate's ``eligible`` flag was produced: which trust rubric
  version graded its evidence, and which structure findings were consulted for its sources. The assessment runs when a
  selection is *made*; replay never reruns it. It reads the recorded ``eligible`` flags.

So a change to assessment semantics (a trust rubric bump, a new source-finding gate) does not change the policy version
and does not break replay of an older manifest; it changes this block. A manifest made before the block existed carries
none, and is read as ``pre_v2_assessment`` explicitly, never as the current semantics. Replay of such a manifest is the
reproduction of a recorded decision under the semantics it was made with; it is not a fresh assessment and not an
endorsement under today's rules (``describe_replay`` says so in words).
"""

from __future__ import annotations

from typing import Any

#: The label of a manifest that predates the structured block.
PRE_V2 = "pre_v2_assessment"
#: The version of the source-finding gate (``structure_selection.source_findings``) recorded in the block.
SOURCE_FINDINGS_VERSION = "1"


class UnsupportedAssessmentSemantics(ValueError):
    """The manifest records assessment semantics this release does not know."""


def semantics_block(*, version: str, evidence_rubric: str) -> dict[str, Any]:
    """The block a new manifest records. ``evidence_rubric`` is ``<rubric name>@<rubric version>``."""
    return {
        "version": version,
        "evidence_rubric": evidence_rubric,
        "source_findings": SOURCE_FINDINGS_VERSION,
    }


def recorded_version(manifest: dict[str, Any]) -> str:
    """The assessment semantics a manifest was made under: its block's version, or ``pre_v2_assessment``."""
    block = manifest.get("assessment_semantics")
    if block is None:
        return PRE_V2
    if not isinstance(block, dict) or not isinstance(block.get("version"), str):
        raise UnsupportedAssessmentSemantics("the manifest's assessment_semantics block is malformed")
    return block["version"]


def check_supported(manifest: dict[str, Any], *, supported: frozenset[str]) -> str:
    """The manifest's assessment semantics version, refusing one this release does not carry."""
    recorded = recorded_version(manifest)
    if recorded not in supported:
        raise UnsupportedAssessmentSemantics(
            f"the manifest was made under assessment semantics {recorded!r}; this release reads {sorted(supported)}"
        )
    return recorded


def describe_replay(manifest: dict[str, Any], *, current: str, supported: frozenset[str]) -> dict[str, Any]:
    """Label a replay as historical or current, in words and in fields. Pure; never reassesses anything."""
    recorded = check_supported(manifest, supported=supported)
    historical = recorded != current
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
