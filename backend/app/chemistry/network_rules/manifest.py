"""The audited manifest behind the candidate pressure-dependent network rules.

``network_rule_candidates.yaml`` is data: for each candidate rule its source, what the council recorded, the
statement it would make, the predicates it would need, its exceptions, the blockers that keep it inactive and
exactly which papers, tables and data would have to be supplied before activation could even be considered. This
module reads it, refuses it if it is not what the registry needs, and exposes it as typed values. It does no
matching: the rule objects that use it live in ``app.services.network_selection.rules``.

Fail closed, in both directions, as the other audited manifests do:

* an entry that claims activation approval must carry an owner acceptance (who, when) and no blockers; an entry
  that does not must list at least one blocker and at least one thing it needs;
* the bytes are refused unless their SHA-256 is the pinned one, so a change to an entry, a blocker or a source
  list cannot go unnoticed: it is a new manifest version, a new pin and a new rule version.

Activation is the owner's act, recorded in this file. Nothing in the code activates an entry.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from tckdb_schemas.network_declarations import NetworkComparisonObjective

from app.chemistry.manifest_attestation import attest

MANIFEST_PATH = Path(__file__).with_name("network_rule_candidates.yaml")

LEVEL_CANDIDATE = "candidate"
LEVEL_REPRESENTATION = "representation"
_LEVELS = (LEVEL_CANDIDATE, LEVEL_REPRESENTATION)


class ManifestError(ValueError):
    """The network rule manifest is not usable as shipped."""


@dataclass(frozen=True)
class RuleCandidate:
    """One audited candidate rule, as stated in the manifest."""

    rule_id: str
    version: str
    objective: NetworkComparisonObjective
    objective_key: str
    observable: str
    citation: str
    council_observation: str
    proposed_statement: str
    required_predicates: tuple[str, ...]
    exceptions: tuple[str, ...]
    activation_blockers: tuple[dict[str, str], ...]
    evidence_needed: tuple[dict[str, str], ...]
    activation_approved: bool
    owner_acceptance: dict[str, Any] | None
    #: ``candidate`` ranks solves or their members; ``representation`` ranks only alternate fits of one solve.
    level: str = "candidate"
    source_ids: tuple[str, ...] = ()
    open_access: bool | None = None
    anchors: tuple[dict[str, str], ...] = ()

    @property
    def activatable(self) -> bool:
        """Approved by the owner, with a dated acceptance and nothing left standing in the way.

        Each condition is checked here independently of the parser, so a hand-built entry cannot be activatable
        by leaving blockers listed or by carrying an acceptance with no date.
        """
        acceptance = self.owner_acceptance
        accepted = isinstance(acceptance, dict) and bool(acceptance.get("accepted_by")) and bool(acceptance.get("date"))
        return self.activation_approved and accepted and not self.activation_blockers

    def describe(self) -> dict[str, Any]:
        return {
            "citation": self.citation,
            "council_observation": self.council_observation,
            "proposed_statement": self.proposed_statement,
            "required_predicates": list(self.required_predicates),
            "exceptions": list(self.exceptions),
            "activation_approved": self.activation_approved,
            "activation_blockers": [dict(b) for b in self.activation_blockers],
            "evidence_needed": [dict(e) for e in self.evidence_needed],
            "owner_acceptance": dict(self.owner_acceptance) if self.owner_acceptance else None,
            "level": self.level,
            "source_ids": list(self.source_ids),
            "open_access": self.open_access,
            "anchors": [dict(a) for a in self.anchors],
        }


@dataclass(frozen=True)
class NetworkRuleManifest:
    """The loaded, validated manifest."""

    version: str
    candidates: tuple[RuleCandidate, ...]
    #: Pinned sources (id, citation, sha256 of the bytes read, what each was used for).
    sources: tuple[dict[str, Any], ...] = ()
    #: SHA-256 of the bytes this manifest was parsed from; ``None`` when built from a parsed document alone.
    sha256: str | None = None

    def candidate(self, rule_id: str) -> RuleCandidate:
        for candidate in self.candidates:
            if candidate.rule_id == rule_id:
                return candidate
        raise ManifestError(f"the manifest has no candidate {rule_id!r}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _candidate(raw: dict[str, Any]) -> RuleCandidate:
    rule_id = raw["rule_id"]
    _require(bool(str(raw.get("objective_key") or "").strip()), f"{rule_id}: objective_key is empty")
    try:
        objective = NetworkComparisonObjective(raw["objective"])
    except ValueError as exc:
        raise ManifestError(f"{rule_id}: unknown objective {raw['objective']!r}") from exc
    level = raw.get("level", LEVEL_CANDIDATE)
    _require(level in _LEVELS, f"{rule_id}: unknown level {level!r}")
    if level == LEVEL_REPRESENTATION:
        _require(
            objective is NetworkComparisonObjective.representation_fidelity,
            f"{rule_id}: a representation-level rule must have the representation_fidelity objective",
        )
    approved = raw.get("activation_approved")
    _require(isinstance(approved, bool), f"{rule_id}: activation_approved must be true or false")
    blockers = tuple(dict(b) for b in raw.get("activation_blockers") or ())
    needed = tuple(dict(e) for e in raw.get("evidence_needed") or ())
    acceptance = raw.get("owner_acceptance")
    _require(bool(raw.get("required_predicates")), f"{rule_id}: no predicates are stated")
    for blocker in blockers:
        _require(bool(blocker.get("id")) and bool(blocker.get("text")), f"{rule_id}: a blocker has no id or text")
    if approved:
        _require(
            isinstance(acceptance, dict) and bool(acceptance.get("accepted_by")) and bool(acceptance.get("date")),
            f"{rule_id}: approved for activation but carries no owner acceptance",
        )
        _require(not blockers, f"{rule_id}: approved for activation while blockers are still listed")
    else:
        _require(not acceptance, f"{rule_id}: carries an owner acceptance but is not approved for activation")
        _require(bool(blockers), f"{rule_id}: not approved for activation and lists no reason")
        _require(bool(needed), f"{rule_id}: not approved for activation and lists nothing that would be needed")
    return RuleCandidate(
        rule_id=rule_id,
        version=str(raw["version"]),
        objective=objective,
        objective_key=raw["objective_key"],
        observable=raw["observable"],
        citation=" ".join(str(raw["citation"]).split()),
        council_observation=" ".join(str(raw["council_observation"]).split()),
        proposed_statement=" ".join(str(raw["proposed_statement"]).split()),
        required_predicates=tuple(raw["required_predicates"]),
        exceptions=tuple(raw.get("exceptions") or ()),
        activation_blockers=blockers,
        evidence_needed=needed,
        activation_approved=bool(approved),
        owner_acceptance=dict(acceptance) if acceptance else None,
        level=level,
        source_ids=tuple(raw.get("source_ids") or ()),
        open_access=raw.get("open_access"),
        anchors=tuple(dict(a) for a in raw.get("anchors") or ()),
    )


def parse_network_rule_manifest(raw: dict[str, Any]) -> NetworkRuleManifest:
    """Validate a parsed manifest document and return it typed.

    :raises ManifestError: on an empty objective key, an unknown objective or level, a duplicate rule id, an approved
        entry with no dated owner acceptance or with blockers, an unapproved entry that carries an acceptance or lists
        no reason or no need, or a rule citing a source the manifest does not pin.
    """
    candidates = tuple(_candidate(r) for r in raw["rules"])
    _require(bool(candidates), "the manifest lists no candidate")
    _require(len({c.rule_id for c in candidates}) == len(candidates), "rule ids repeat")
    sources = tuple(dict(x) for x in raw.get("sources") or ())
    for source in sources:
        _require(
            all(bool(source.get(k)) for k in ("id", "citation", "sha256", "used_for")),
            f"source {source.get('id')!r} lacks an id, citation, sha256 or used_for line",
        )
    source_ids = {x["id"] for x in sources}
    _require(len(source_ids) == len(sources), "source ids repeat")
    for candidate in candidates:
        unknown = set(candidate.source_ids) - source_ids
        _require(not unknown, f"{candidate.rule_id}: cites sources the manifest does not pin: {sorted(unknown)}")
    return NetworkRuleManifest(version=str(raw["manifest_version"]), candidates=candidates, sources=sources)


def parse_network_rule_manifest_bytes(data: bytes, *, expected_sha256: str | None) -> NetworkRuleManifest:
    """Parse manifest bytes, first refusing them if their SHA-256 is not the pinned one.

    :raises ManifestError: on a digest mismatch, or any failure of :func:`parse_network_rule_manifest`.
    """
    actual = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ManifestError(
            f"network rule manifest content does not match its pinned digest (expected {expected_sha256}, got {actual})"
        )
    return attest(dataclasses.replace(parse_network_rule_manifest(yaml.safe_load(data)), sha256=actual))


def load_network_rule_manifest(*, expected_sha256: str) -> NetworkRuleManifest:
    """The shipped manifest, which must be exactly the bytes the registry pins."""
    return parse_network_rule_manifest_bytes(MANIFEST_PATH.read_bytes(), expected_sha256=expected_sha256)
