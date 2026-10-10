"""The audited manifest behind the XYG3-over-B3LYP classical-barrier rule.

``xyg3_b3lyp_barrier_manifest.yaml`` is data: the 38 reactions (76 barrier heights) of the HTBH38/04
and NHTBH38/04 benchmark sets read from the supplied papers, with their identities, the protocol facts
the sources state, the corrections applied, the evidence limits, and the blockers that keep the rule
from being activated. This module reads it, refuses it if it is not what the rule needs, and exposes it
as typed values. It does no matching: the rule that uses it lives in
``app.services.kinetics_selection.rules``.

Two things are checked independently and must not be conflated:

* **Membership**: complete by listing (count-complete and every identity resolved).
* **Activation**: whether the evidence supports the comparison the rule would make. A manifest can be
  complete and still unactivatable; this one is. It loads, and it reports ``activatable`` false with the
  reasons, so the rule can be registered inactive and disclose why.

Fail closed in both directions: a manifest that claims activation approval must carry a curator
acceptance and no blockers, and one whose members disagree with the counts its sources state does not
load.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.chemistry.manifest_attestation import attest

MANIFEST_PATH = Path(__file__).with_name("xyg3_b3lyp_barrier_manifest.yaml")

#: Subsets and the number of barrier heights each must hold (two per reaction).
EXPECTED_SUBSET_REACTIONS = {"HT": 19, "HAT": 6, "NS": 8, "UM": 5}


class ManifestError(ValueError):
    """The XYG3 barrier manifest is not usable as shipped."""


@dataclass(frozen=True)
class BenchmarkSpecies:
    """One reactant or product, matched by identity, charge and multiplicity."""

    key: str
    name: str
    smiles: str
    inchikey: str
    molecular_formula: str
    charge: int
    multiplicity: int
    kind: str


@dataclass(frozen=True)
class BarrierMember:
    """One benchmark reaction with its forward and reverse classical barrier heights (kcal/mol)."""

    member_id: str
    dataset: str
    subset: str
    reactants: tuple[BenchmarkSpecies, ...]
    products: tuple[BenchmarkSpecies, ...]
    forward_kcal_mol: float
    reverse_kcal_mol: float
    identity_status: str
    source: str

    @staticmethod
    def _signature(side: tuple[BenchmarkSpecies, ...]) -> tuple[tuple[str, int, int, str], ...]:
        return tuple(sorted((s.inchikey, s.charge, s.multiplicity, s.kind) for s in side))

    @property
    def reactant_signature(self) -> tuple[tuple[str, int, int, str], ...]:
        return self._signature(self.reactants)

    @property
    def product_signature(self) -> tuple[tuple[str, int, int, str], ...]:
        return self._signature(self.products)


@dataclass(frozen=True)
class XYG3BarrierManifest:
    """The loaded, validated manifest."""

    rule_id: str
    rule_statement: str
    version: str
    members: tuple[BarrierMember, ...]
    sources: tuple[dict[str, Any], ...]
    corrections_applied: tuple[dict[str, Any], ...]
    evidence_limits: tuple[dict[str, Any], ...]
    protocol_facts: dict[str, Any]
    activation_approved: bool
    activation_blockers: tuple[dict[str, str], ...]
    curator_acceptance: dict[str, Any] | None
    #: SHA-256 of the bytes this manifest was parsed from; ``None`` when built from a parsed document alone.
    sha256: str | None = None

    @property
    def activatable(self) -> bool:
        """Approved by a curator and with nothing left standing in the way."""
        return self.activation_approved and not self.activation_blockers

    @property
    def barrier_height_count(self) -> int:
        return 2 * len(self.members)

    def describe(self) -> dict[str, Any]:
        """A JSON-ready summary for a decision manifest: version, counts, citations, limits, blockers."""
        return {
            "manifest_version": self.version,
            "member_count": len(self.members),
            "barrier_height_count": self.barrier_height_count,
            "activation_approved": self.activation_approved,
            "activation_blockers": [dict(b) for b in self.activation_blockers],
            "curator_acceptance": dict(self.curator_acceptance) if self.curator_acceptance else None,
            "sources": [s["id"] for s in self.sources],
            "corrections_applied": [c["source"] + ": " + c["target"] for c in self.corrections_applied],
            "evidence_limits": [dict(e) for e in self.evidence_limits],
            "sha256": self.sha256,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _species(raw: dict[str, Any]) -> BenchmarkSpecies:
    return BenchmarkSpecies(
        key=raw["key"],
        name=raw["name"],
        smiles=raw["smiles"],
        inchikey=raw["inchikey"],
        molecular_formula=raw["molecular_formula"],
        charge=int(raw["charge"]),
        multiplicity=int(raw["multiplicity"]),
        kind=raw["kind"],
    )


def _member(raw: dict[str, Any]) -> BarrierMember:
    _require(
        raw["identity_status"] in {"resolved", "resolved_complex_caveat"},
        f"{raw['member_id']}: identity_status {raw['identity_status']!r} is not resolved",
    )
    _require(bool(raw["reactants"]) and bool(raw["products"]), f"{raw['member_id']}: a side has no species")
    member = BarrierMember(
        member_id=raw["member_id"],
        dataset=raw["dataset"],
        subset=raw["subset"],
        reactants=tuple(_species(s) for s in raw["reactants"]),
        products=tuple(_species(s) for s in raw["products"]),
        forward_kcal_mol=float(raw["classical_barrier_forward_kcal_mol"]),
        reverse_kcal_mol=float(raw["classical_barrier_reverse_kcal_mol"]),
        identity_status=raw["identity_status"],
        source=raw["best_estimate_source"],
    )
    flag = raw.get("degenerate_identity_reaction")
    _require(isinstance(flag, bool), f"{member.member_id}: degenerate_identity_reaction must be true or false")
    identical = member.reactant_signature == member.product_signature
    _require(
        flag == identical,
        f"{member.member_id}: degenerate_identity_reaction is {flag} but its reactants "
        f"{'equal' if identical else 'differ from'} its products",
    )
    return member


def parse_xyg3_barrier_manifest(raw: dict[str, Any]) -> XYG3BarrierManifest:
    """Validate a parsed manifest document and return it typed.

    :raises ManifestError: if the membership is not count-complete, a member is unresolved, the corrections
        are not recorded, or the activation claims disagree with each other (approval with no curator
        acceptance, or approval with blockers still listed).
    """
    status = raw.get("status") or {}
    _require(status.get("membership_listed_complete") is True, "membership is not marked complete by listing")
    stated = status["count_stated_by_source"]
    found = status["count_found"]
    members = tuple(_member(m) for m in raw["members"])
    _require(
        len(members) == stated["reactions"] == found["reactions"],
        f"manifest lists {len(members)} reactions; its sources state {stated['reactions']}",
    )
    _require(
        2 * len(members) == stated["barrier_heights"] == found["barrier_heights"],
        "manifest barrier-height count disagrees with its sources",
    )
    _require(len({m.member_id for m in members}) == len(members), "member ids repeat")
    for subset, reactions in EXPECTED_SUBSET_REACTIONS.items():
        count = sum(m.subset == subset for m in members)
        _require(count == reactions, f"subset {subset} lists {count} reactions, the sources state {reactions}")
    _require(
        stated["HT38"] == 2 * EXPECTED_SUBSET_REACTIONS["HT"]
        and stated["HAT12"] == 2 * EXPECTED_SUBSET_REACTIONS["HAT"]
        and stated["NS16"] == 2 * EXPECTED_SUBSET_REACTIONS["NS"]
        and stated["UM10"] == 2 * EXPECTED_SUBSET_REACTIONS["UM"],
        "subset barrier-height counts do not follow from the reactions listed",
    )
    _require(status.get("identity_unresolved") == 0, "a member identity is unresolved")
    _require(bool(raw.get("corrections_applied")), "the manifest records no corrections; the audit applies the published one")
    claimed = status.get("activation_approved")
    _require(isinstance(claimed, bool), "activation_approved must be true or false")
    approved = bool(claimed)
    blockers = tuple(dict(b) for b in status.get("activation_blockers") or ())
    acceptance = status.get("curator_acceptance")
    if approved:
        _require(
            isinstance(acceptance, dict) and bool(acceptance.get("accepted_by")) and bool(acceptance.get("date")),
            "manifest is approved for activation but carries no curator acceptance",
        )
        _require(not blockers, "manifest is approved for activation while blockers are still listed")
    else:
        _require(bool(blockers), "manifest is not approved for activation and lists no reason")
    return XYG3BarrierManifest(
        rule_id=raw["rule_id"],
        rule_statement=" ".join(str(raw["rule_statement"]).split()),
        version=str(raw["manifest_version"]),
        members=members,
        sources=tuple(raw["sources"]),
        corrections_applied=tuple(raw["corrections_applied"]),
        evidence_limits=tuple(raw["evidence_limits"]),
        protocol_facts=raw["protocol_facts"],
        activation_approved=approved,
        activation_blockers=blockers,
        curator_acceptance=dict(acceptance) if acceptance else None,
    )


def parse_xyg3_barrier_manifest_bytes(data: bytes, *, expected_sha256: str | None) -> XYG3BarrierManifest:
    """Parse manifest bytes, first refusing them if their SHA-256 is not the pinned one.

    :raises ManifestError: on a digest mismatch, or any failure of :func:`parse_xyg3_barrier_manifest`.
    """
    actual = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ManifestError(
            f"XYG3 barrier manifest content does not match its pinned digest (expected {expected_sha256}, got {actual})"
        )
    return attest(dataclasses.replace(parse_xyg3_barrier_manifest(yaml.safe_load(data)), sha256=actual))


@lru_cache(maxsize=4)
def load_xyg3_barrier_manifest(expected_sha256: str | None = None) -> XYG3BarrierManifest:
    """Load and validate the shipped manifest (cached; the file is immutable at runtime).

    :param expected_sha256: The pinned digest of the file's bytes; ``None`` skips the pin check.
    """
    return parse_xyg3_barrier_manifest_bytes(MANIFEST_PATH.read_bytes(), expected_sha256=expected_sha256)
