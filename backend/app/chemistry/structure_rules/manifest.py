"""The audited manifest behind the candidate structure-energy preference rules.

``structure_rule_candidates.yaml`` is data: for each candidate rule its protocols (exact preferred and yielding
recipes), observable and objective, chemical, state, geometry and domain scope, the sources it rests on with the
anchors of every number, the statement it would make, the predicates it would need, its exceptions, the blockers that
keep it inactive and exactly which papers, tables or data would have to be supplied before activation could even be
considered. This module reads it, refuses it if it is not what the registry needs, and exposes it as typed values. It
does no matching: the rule objects that use it live in ``app.services.structure_selection.rules``.

Fail closed, in both directions, as the other audited manifests do:

* an entry that claims activation approval must carry an owner acceptance (who, when) and no blockers; an entry that
  does not must list at least one blocker and at least one thing it needs;
* every number a rule quotes must be anchored: an entry that cites sources must carry anchors, and every source a rule
  cites must be pinned with a SHA-256 and a ``used_for`` line;
* the bytes are refused unless their SHA-256 is the pinned one, so a change to an entry, a blocker or a source list
  cannot go unnoticed: it is a new manifest version, a new pin and a new rule version.

Activation is the owner's act, recorded in this file. Nothing in the code activates an entry.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

MANIFEST_PATH = Path(__file__).with_name("structure_rule_candidates.yaml")

#: The objectives a structure rule may compare on (the request's ``Objective`` values).
OBJECTIVES = ("physical_accuracy", "expected_accuracy", "model_fidelity")

#: The scope facts every entry must state, so no rule is silent about where it stops.
SCOPE_FIELDS = ("observable", "chemistry", "state_and_geometry", "domain")


class ManifestError(ValueError):
    """The structure rule manifest is not usable as shipped."""


@dataclass(frozen=True)
class RuleCandidate:
    """One audited candidate rule, as stated in the manifest."""

    rule_id: str
    version: str
    objective: str
    objective_key: str
    observable: str
    citation: str
    protocols: dict[str, tuple[str, ...]]
    scope: dict[str, str]
    proposed_statement: str
    required_predicates: tuple[str, ...]
    exceptions: tuple[str, ...]
    activation_blockers: tuple[dict[str, str], ...]
    evidence_needed: tuple[dict[str, str], ...]
    activation_approved: bool
    owner_acceptance: dict[str, Any] | None
    source_ids: tuple[str, ...] = ()
    open_access: bool | None = None
    anchors: tuple[dict[str, str], ...] = ()
    council_observation: str = ""

    @property
    def activatable(self) -> bool:
        """Approved by the owner, with a dated acceptance and nothing left standing in the way.

        Each condition is checked here independently of the parser, so a hand-built entry cannot be activatable by
        leaving blockers listed or by carrying an acceptance with no date.
        """
        acceptance = self.owner_acceptance
        accepted = isinstance(acceptance, dict) and bool(acceptance.get("accepted_by")) and bool(acceptance.get("date"))
        return self.activation_approved and accepted and not self.activation_blockers

    def describe(self) -> dict[str, Any]:
        return {
            "citation": self.citation,
            "council_observation": self.council_observation,
            "protocols": {k: list(v) for k, v in self.protocols.items()},
            "scope": dict(self.scope),
            "proposed_statement": self.proposed_statement,
            "required_predicates": list(self.required_predicates),
            "exceptions": list(self.exceptions),
            "activation_approved": self.activation_approved,
            "activation_blockers": [dict(b) for b in self.activation_blockers],
            "evidence_needed": [dict(e) for e in self.evidence_needed],
            "owner_acceptance": dict(self.owner_acceptance) if self.owner_acceptance else None,
            "source_ids": list(self.source_ids),
            "open_access": self.open_access,
            "anchors": [dict(a) for a in self.anchors],
        }


@dataclass(frozen=True)
class StructureRuleManifest:
    """The loaded, validated manifest."""

    version: str
    candidates: tuple[RuleCandidate, ...]
    #: Pinned sources (id, citation, version read, sha256 of the bytes read, what each was used for).
    sources: tuple[dict[str, Any], ...] = ()
    #: Examples the council raised that support a distinction but are deliberately not preference rules.
    considered_not_rules: tuple[dict[str, str], ...] = ()
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
    name = raw["rule_id"]  # a rule's name, not a database key
    _require(bool(str(raw.get("objective_key") or "").strip()), f"{name}: objective_key is empty")
    _require(raw.get("objective") in OBJECTIVES, f"{name}: unknown objective {raw.get('objective')!r}")
    _require(bool(str(raw.get("observable") or "").strip()), f"{name}: observable is empty")
    protocols = raw.get("protocols") or {}
    _require(
        bool(protocols.get("preferred")) and bool(protocols.get("yielding")),
        f"{name}: a rule states the exact protocols on both its preferred and its yielding side",
    )
    scope = raw.get("scope") or {}
    for field in SCOPE_FIELDS:
        _require(bool(str(scope.get(field) or "").strip()), f"{name}: scope.{field} is not stated")
    approved = raw.get("activation_approved")
    _require(isinstance(approved, bool), f"{name}: activation_approved must be true or false")
    blockers = tuple(dict(b) for b in raw.get("activation_blockers") or ())
    needed = tuple(dict(e) for e in raw.get("evidence_needed") or ())
    acceptance = raw.get("owner_acceptance")
    _require(bool(raw.get("required_predicates")), f"{name}: no predicates are stated")
    _require(bool(raw.get("exceptions")), f"{name}: no exceptions are stated")
    source_ids = tuple(raw.get("source_ids") or ())
    anchors = tuple(dict(a) for a in raw.get("anchors") or ())
    if source_ids:
        _require(bool(anchors), f"{name}: cites sources but anchors no claim to a page, table or figure")
    for anchor in anchors:
        _require(bool(anchor.get("claim")) and bool(anchor.get("where")), f"{name}: an anchor has no claim or no location")
    for blocker in blockers:
        _require(bool(blocker.get("id")) and bool(blocker.get("text")), f"{name}: a blocker has no id or text")
    if approved:
        _require(
            isinstance(acceptance, dict) and bool(acceptance.get("accepted_by")) and bool(acceptance.get("date")),
            f"{name}: approved for activation but carries no owner acceptance",
        )
        _require(not blockers, f"{name}: approved for activation while blockers are still listed")
    else:
        _require(not acceptance, f"{name}: carries an owner acceptance but is not approved for activation")
        _require(bool(blockers), f"{name}: not approved for activation and lists no reason")
        _require(bool(needed), f"{name}: not approved for activation and lists nothing that would be needed")
    return RuleCandidate(
        rule_id=name,
        version=str(raw["version"]),
        objective=raw["objective"],
        objective_key=raw["objective_key"],
        observable=raw["observable"],
        citation=" ".join(str(raw["citation"]).split()),
        protocols={k: tuple(str(x) for x in v) for k, v in protocols.items()},
        scope={k: " ".join(str(v).split()) for k, v in scope.items()},
        proposed_statement=" ".join(str(raw["proposed_statement"]).split()),
        required_predicates=tuple(raw["required_predicates"]),
        exceptions=tuple(raw["exceptions"]),
        activation_blockers=blockers,
        evidence_needed=needed,
        activation_approved=bool(approved),
        owner_acceptance=dict(acceptance) if acceptance else None,
        source_ids=source_ids,
        open_access=raw.get("open_access"),
        anchors=anchors,
        council_observation=" ".join(str(raw.get("council_observation") or "").split()),
    )


def parse_structure_rule_manifest(raw: dict[str, Any]) -> StructureRuleManifest:
    """Validate a parsed manifest document and return it typed.

    :raises ManifestError: on an empty objective key or observable, an unknown objective, a rule that does not state both
        sides' protocols, its scope, predicates and exceptions, a duplicate rule id, an approved entry with no dated
        owner acceptance or with blockers, an unapproved entry that carries an acceptance or lists no reason or no
        need, a rule that cites sources without anchors, or a rule citing a source the manifest does not pin.
    """
    candidates = tuple(_candidate(r) for r in raw["rules"])
    _require(bool(candidates), "the manifest lists no candidate")
    _require(len({c.rule_id for c in candidates}) == len(candidates), "rule ids repeat")
    sources = tuple(dict(x) for x in raw.get("sources") or ())
    for source in sources:
        _require(
            all(bool(source.get(k)) for k in ("id", "citation", "version", "sha256", "used_for")),
            f"source {source.get('id')!r} lacks an id, citation, version read, sha256 or used_for line",
        )
        _require(len(str(source["sha256"])) == 64, f"source {source['id']!r}: sha256 is not 64 hex characters")
    source_ids = {x["id"] for x in sources}
    _require(len(source_ids) == len(sources), "source ids repeat")
    for candidate in candidates:
        unknown = set(candidate.source_ids) - source_ids
        _require(not unknown, f"{candidate.rule_id}: cites sources the manifest does not pin: {sorted(unknown)}")
    declined = tuple(dict(x) for x in raw.get("considered_not_rules") or ())
    for item in declined:
        _require(all(bool(item.get(k)) for k in ("id", "source", "why")), f"considered_not_rules entry {item.get('id')!r} is incomplete")
    return StructureRuleManifest(
        version=str(raw["manifest_version"]), candidates=candidates, sources=sources, considered_not_rules=declined
    )


def parse_structure_rule_manifest_bytes(data: bytes, *, expected_sha256: str | None) -> StructureRuleManifest:
    """Parse manifest bytes, first refusing them if their SHA-256 is not the pinned one.

    :raises ManifestError: on a digest mismatch, or any failure of :func:`parse_structure_rule_manifest`.
    """
    actual = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ManifestError(
            f"structure rule manifest content does not match its pinned digest (expected {expected_sha256}, got {actual})"
        )
    return dataclasses.replace(parse_structure_rule_manifest(yaml.safe_load(data)), sha256=actual)


def load_structure_rule_manifest(*, expected_sha256: str) -> StructureRuleManifest:
    """The shipped manifest, which must be exactly the bytes the registry pins."""
    return parse_structure_rule_manifest_bytes(MANIFEST_PATH.read_bytes(), expected_sha256=expected_sha256)
