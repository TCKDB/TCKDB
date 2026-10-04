"""Builders shared by the kinetics-selection tests: plain normalised records and requests for the pure
assessor tests (no database)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tckdb_schemas.kinetics_declarations import KineticsCoefficientBasis

from app.db.models.common import KineticsDeterminationTargetKind, KineticsDirection, RecordReviewStatus
from app.services.kinetics_selection.models import (
    ColliderRequest,
    DeterminationFacts,
    KineticsRequest,
    NormalizedKinetics,
    PressureKind,
    PressureRequest,
    TargetRequest,
)

T0 = datetime(2026, 6, 1, 12, 0, 0)

#: A complete, pressure-independent, elementary, forward, whole-reaction declaration.
APPLICABILITY: dict[str, Any] = {
    "version": 1,
    "phase": "gas",
    "observable": "rate_coefficient",
    "coefficient_basis": "elementary_coefficient",
    "scope": "whole_reaction",
    "reaction_order": 2,
    "rate_progress_convention": None,
    "pressure_dependence": "independent",
    "pressure_domain_min_bar": None,
    "pressure_domain_max_bar": None,
    "collider_kind": None,
    "colliders": [],
    "default_third_body_efficiency": None,
    "claim_origin": "source_publication",
}


def applicability(**changes: Any) -> dict[str, Any]:
    return {**APPLICABILITY, **changes}


def determination(ref: str = "kdet_a", **changes: Any) -> DeterminationFacts:
    facts = {
        "determination_ref": ref,
        "direction": "forward",
        "target_kind": "whole_reaction",
        "transition_state_entry_ref": None,
        "network_ref": None,
        "channel_key": None,
    }
    return DeterminationFacts(**{**facts, **changes})


def norm(ref: str = "kin_a", *, rank: int = 1, age_days: float = 0, **changes: Any) -> NormalizedKinetics:
    """A record that answers :func:`request` completely; every keyword changes one fact."""
    base: dict[str, Any] = {
        "kinetics_ref": ref,
        "review_status": RecordReviewStatus.approved,
        "created_at": T0 - timedelta(days=age_days),
        "id_rank": rank,
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "direction": "forward",
        "is_third_body": False,
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "pressure_context": None,
        "pressure_bar": None,
        "a": 1.2e-12,
        "a_units": "cm3_molecule_s",
        "degeneracy": None,
        "degeneracy_convention": "unknown",
        "representation_role": "complete",
        "determination": determination(),
        "applicability_state": "valid",
        "applicability": applicability(),
        "protocol_state": "absent",
        "protocol": None,
    }
    base.update(changes)
    return NormalizedKinetics(**base)


def request(**changes: Any) -> KineticsRequest:
    base: dict[str, Any] = {
        "direction": KineticsDirection.forward,
        "target": TargetRequest(KineticsDeterminationTargetKind.whole_reaction),
        "coefficient_basis": KineticsCoefficientBasis.elementary_coefficient,
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure": PressureRequest(PressureKind.independent),
    }
    base.update(changes)
    return KineticsRequest(**base)


def finite(low: float, high: float | None = None) -> PressureRequest:
    return PressureRequest(PressureKind.finite, low, low if high is None else high)


def collider(*refs: str, fractions: tuple[float, ...] | None = None) -> ColliderRequest:
    return ColliderRequest(tuple(refs), fractions)


def pinned_rule(raw: dict[str, Any], monkeypatch):
    """The XYG3 rule over an edited manifest document, which is only possible by pinning the edited bytes.

    The rule refuses any manifest whose digest is not the pinned one; a test that wants to see what an approved
    manifest would do has to say so by moving the pin, exactly what a real approval is (a new manifest, a new pin).
    """
    import hashlib

    import yaml

    from app.chemistry.kinetics_rules.xyg3_barrier_manifest import parse_xyg3_barrier_manifest_bytes
    from app.services.kinetics_selection import rules as rules_module

    data = yaml.safe_dump(raw, sort_keys=False).encode()
    monkeypatch.setattr(rules_module, "XYG3_MANIFEST_SHA256", hashlib.sha256(data).hexdigest())
    return rules_module.XYG3B3LYPBarrierRule(parse_xyg3_barrier_manifest_bytes(data, expected_sha256=None))
