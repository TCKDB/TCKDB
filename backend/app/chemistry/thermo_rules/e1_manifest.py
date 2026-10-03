"""The audited membership manifest behind rule E1 (standard G4 over standard G3).

``e1_g4_over_g3_manifest.yaml`` is data: the 38 hydrocarbons of the published
G3/G4 benchmark scope, the recipe facts that tell standard G4 and standard G3
from their variants, the development-set evidence limits, and the sources and
curator acceptance that make it activatable. This module reads it, refuses it
if it is not what the rule needs, and exposes it as typed values. It does no
matching: the rule that uses it lives in ``app.services.thermo_selection``.

Fail closed. A manifest that is not approved for activation, whose member count
disagrees with the count its sources state, or that lacks the curator's
acceptance does not load, so the rule cannot be registered over it. There is no
"small hydrocarbon" predicate anywhere: scope is exactly the listed members.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

MANIFEST_PATH = Path(__file__).with_name("e1_g4_over_g3_manifest.yaml")

#: How a member is matched against a species.
MATCH_FULL_INCHIKEY = "full_inchikey"
#: First InChIKey block (connectivity) plus molecular formula. Used where sources
#: disagree on the stereo layer of the full key (cyclooctatetraene).
MATCH_CONNECTIVITY_AND_FORMULA = "connectivity_block_and_formula"


class ManifestError(ValueError):
    """The E1 manifest is not usable as shipped."""


@dataclass(frozen=True)
class E1Member:
    """One benchmark species/state.

    ``charge`` and ``multiplicity`` are matched exactly for every member. The
    multiplicity is what separates singlet CH2(1A1), the only state-specific
    member, from triplet CH2: they share SMILES, InChI and InChIKey.
    """

    member_id: str
    name: str
    molecular_formula: str
    inchikey: str
    charge: int
    multiplicity: int
    match_mode: str
    state_specific: bool
    membership_evidence: str

    @property
    def connectivity_block(self) -> str:
        return self.inchikey.split("-", 1)[0]


@dataclass(frozen=True)
class E1Manifest:
    """The loaded, validated manifest."""

    rule_id: str
    rule_statement: str
    version: str
    members: tuple[E1Member, ...]
    sources: tuple[dict[str, Any], ...]
    curator_acceptance: dict[str, Any]
    evidence_limits: tuple[dict[str, Any], ...]
    recipe_facts: dict[str, Any]
    scope_conditions: dict[str, Any]
    #: Recipes the manifest names as distinct from the two standard recipes.
    #: Keys of ``recipe_facts`` whose entry says ``distinct_from_standard_*``.
    distinct_variants: tuple[str, ...]

    def describe(self) -> dict[str, Any]:
        """A JSON-ready summary for a decision manifest: version, counts, citations, limits."""
        return {
            "manifest_version": self.version,
            "member_count": len(self.members),
            "curator_acceptance": dict(self.curator_acceptance),
            "sources": [s["id"] for s in self.sources],
            "evidence_limits": [dict(e) for e in self.evidence_limits],
            "distinct_variants": list(self.distinct_variants),
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _member(raw: dict[str, Any]) -> E1Member:
    identity = raw["identity_status"]
    _require(
        identity in {"resolved", "resolved_state_specific", "resolved_identifier_caveat"},
        f"{raw['member_id']}: identity_status {identity!r} is not resolved",
    )
    _require(raw.get("phase") == "gas" and raw.get("charge") == 0, f"{raw['member_id']}: not a neutral gas-phase member")
    return E1Member(
        member_id=raw["member_id"],
        name=raw["name_as_printed"],
        molecular_formula=raw["molecular_formula"],
        inchikey=raw["inchikey"],
        charge=int(raw["charge"]),
        multiplicity=int(raw["multiplicity"]),
        match_mode=MATCH_CONNECTIVITY_AND_FORMULA if identity == "resolved_identifier_caveat" else MATCH_FULL_INCHIKEY,
        state_specific=identity == "resolved_state_specific",
        membership_evidence=raw["membership_evidence"],
    )


def parse_e1_manifest(raw: dict[str, Any]) -> E1Manifest:
    """Validate a parsed manifest document and return it typed.

    :raises ManifestError: if the manifest is not approved for activation, is
        not count-complete, lacks the curator's acceptance, or has a member
        whose identity is unresolved.
    """
    status = raw.get("status") or {}
    _require(status.get("activation_approved") is True, "E1 manifest is not approved for activation")
    _require(status.get("count_complete") is True, "E1 manifest membership is not count-complete")
    acceptance = status.get("curator_acceptance") or {}
    _require(
        bool(acceptance.get("accepted_by")) and bool(acceptance.get("date")),
        "E1 manifest carries no curator acceptance",
    )
    members = tuple(_member(m) for m in raw["members"])
    _require(
        len(members) == status["count_stated_by_source"] == status["count_found"],
        f"E1 manifest lists {len(members)} members; its sources state {status['count_stated_by_source']}",
    )
    _require(len({m.member_id for m in members}) == len(members), "E1 manifest member ids repeat")
    _require(
        len({(m.inchikey, m.multiplicity) for m in members}) == len(members),
        "E1 manifest lists one species/state twice",
    )
    recipe_facts = raw["recipe_facts"]
    for needed in ("standard_G4", "standard_G3"):
        _require(needed in recipe_facts, f"E1 manifest has no recipe facts for {needed}")
    formation = recipe_facts.get("formation_enthalpy") or {}
    _require("atomization" in str(formation.get("route", "")), "E1 manifest does not state an atomization route")
    distinct = tuple(
        name
        for name, facts in recipe_facts.items()
        if isinstance(facts, dict) and any(key.startswith("distinct_from_standard_") for key in facts)
    )
    return E1Manifest(
        rule_id=raw["rule_id"],
        rule_statement=" ".join(str(raw["rule_statement"]).split()),
        version=str(raw["manifest_version"]),
        members=members,
        sources=tuple(raw["sources"]),
        curator_acceptance=dict(acceptance),
        evidence_limits=tuple(raw["evidence_limits"]),
        recipe_facts=recipe_facts,
        scope_conditions=raw["scope_conditions"],
        distinct_variants=distinct,
    )


def parse_e1_manifest_bytes(data: bytes, *, expected_sha256: str | None) -> E1Manifest:
    """Parse manifest bytes, first refusing them if their SHA-256 is not the pinned one.

    :raises ManifestError: on a digest mismatch, or any failure of :func:`parse_e1_manifest`.
    """
    if expected_sha256 is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            raise ManifestError(
                f"E1 manifest content does not match its pinned digest (expected {expected_sha256}, got {actual})"
            )
    return parse_e1_manifest(yaml.safe_load(data))


@lru_cache(maxsize=4)
def load_e1_manifest(expected_sha256: str | None = None) -> E1Manifest:
    """Load and validate the shipped manifest (cached; the file is immutable at runtime).

    :param expected_sha256: The pinned digest of the file's bytes; ``None`` skips the pin check.
    """
    return parse_e1_manifest_bytes(MANIFEST_PATH.read_bytes(), expected_sha256=expected_sha256)
