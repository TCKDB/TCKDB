"""Read-only coverage inventory: how much of the stored network corpus selection could act on, and why not.

Counts, over every stored network solve, fit and determination, what each states and what it leaves unstated, in the
four categories the plan names: **target** (partition, boundaries, regime, bath scope, output catalog, product sets),
**grouping** (fits with and without a determination, roles, alternates), **domain** (solve scope, declared validity,
fit support) and **protocol** (the recipe and the validation evidence). It runs no assessment against a request: a
request is what supplies the window and the bath, and an inventory has none. It reports the facts the assessor would
find missing, so the owner can see how much a selection could say before depositors declare anything more.

It writes nothing, backfills nothing and states no claim for a record that did not state it: an absence is counted as
an absence. It reads declarations with the same typed models the loader uses, so an unreadable stored claim is
counted as unreadable, never as "not stated".

Nothing here is run against a deployed database by this repository's tooling; the script wrapper only builds the
report from whatever database it is pointed at, inside one read-only snapshot.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import (
    NetworkObservableDeclaration,
    NetworkProtocolDeclaration,
    NetworkRepresentationDeclaration,
    NetworkValidationDeclaration,
    StoredNetworkTargetDeclaration,
)

from app.db.models.common import NetworkKineticsModelKind, NetworkRepresentationRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.network_pdep import (
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsDetermination,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
    NetworkSolveBathGas,
)
from app.services.network_selection.models import BOUNDS_V1
from app.services.scientific_read.common import fetch_review_badges

TARGET_CLAIMS = ("partition", "boundaries", "regime", "validity", "bath_scope", "outputs", "product_sets")

#: Every category the inventory can count. Each is reported, with zero when nothing was found, so that "zero found"
#: and "not measured" read differently.
UNRESOLVED_CATEGORIES = (
    "target:not_declared",
    "target:unreadable",
    *(f"target:{claim}_not_declared" for claim in TARGET_CLAIMS),
    "protocol:not_declared",
    "protocol:unreadable",
    "grouping:fit_without_determination",
    "grouping:determination_has_no_fit",
    "grouping:representation_declaration_not_declared",
    "grouping:representation_declaration_unreadable",
    "grouping:observable_declaration_not_declared",
    "grouping:observable_declaration_unreadable",
    "domain:bath_not_stated",
    "domain:solve_scope_not_stated",
    "domain:physical_validity_not_declared",
    "domain:fit_rate_units_not_stated",
    "domain:axis_units_not_stated",
    "domain:chebyshev_mapping_domain_missing",
    "domain:chebyshev_log_convention_not_stated",
    "domain:plog_temperature_support_not_bounded",
)
FIT_SUPPORT_GAPS = (
    "chebyshev_without_coefficients",
    "chebyshev_mapping_domain_missing",
    "chebyshev_log_convention_not_stated",
    "plog_without_entries",
    "plog_without_temperature_bounds",
    "tabulated",
    "tabulated_points",
)
STATES = ("absent", "valid", "unreadable")
BUNDLE_READINESS = ("declares_a_product_set", "declares_catalog_and_boundaries", "full_network_ready")


def _zeros(keys) -> Counter[str]:
    return Counter({key: 0 for key in keys})


def _batches(session: Session, size: int) -> Iterator[list[int]]:
    last = 0
    while True:
        ids = list(session.scalars(select(NetworkSolve.id).where(NetworkSolve.id > last).order_by(NetworkSolve.id).limit(size)))
        if not ids:
            return
        yield ids
        last = ids[-1]


def _state(model: type[BaseModel], raw: Any) -> tuple[str, Any]:
    if raw is None:
        return "absent", None
    try:
        return "valid", model.model_validate(raw)
    except ValidationError:
        return "unreadable", None


def network_coverage_inventory(session: Session, *, batch_size: int = 100) -> dict[str, Any]:
    """Count stored network solves, fits and determinations by what they state. Read-only."""
    solves: Counter[str] = Counter()
    by_kind: Counter[str] = _zeros(("computed", "reported"))
    by_review: Counter[str] = _zeros(status.value for status in RecordReviewStatus)
    target_state: Counter[str] = _zeros(STATES)
    claims: Counter[str] = _zeros(TARGET_CLAIMS)
    protocol_state: Counter[str] = _zeros(STATES)
    validation_state: Counter[str] = _zeros(STATES)
    representation_state: Counter[str] = _zeros(STATES)
    observable_state: Counter[str] = _zeros(STATES)
    evidence_kinds: Counter[str] = Counter()
    bath: Counter[str] = _zeros(("none", "one_species", "mixture"))
    unresolved: Counter[str] = _zeros(UNRESOLVED_CATEGORIES)
    bundle_ready: Counter[str] = _zeros(BUNDLE_READINESS)
    fit_model: Counter[str] = _zeros(kind.value for kind in NetworkKineticsModelKind)
    fit_group: Counter[str] = _zeros(("grouped", "ungrouped"))
    fit_roles: Counter[str] = _zeros([*(role.value for role in NetworkRepresentationRole), "unstated"])
    fit_domain: Counter[str] = _zeros(FIT_SUPPORT_GAPS)
    fit_units: Counter[str] = _zeros(("rate_units_not_stated", "axis_units_not_stated"))
    determination_fit_counts: Counter[str] = _zeros(("one_fit", "alternates", "no_fit"))

    for ids in _batches(session, batch_size):
        rows = list(session.scalars(select(NetworkSolve).where(NetworkSolve.id.in_(ids)).order_by(NetworkSolve.id)))
        badges = fetch_review_badges(session, record_type=SubmissionRecordType.network_solve, record_ids=ids)
        bath_counts = dict(
            session.execute(
                select(NetworkSolveBathGas.solve_id, func.count())
                .where(NetworkSolveBathGas.solve_id.in_(ids))
                .group_by(NetworkSolveBathGas.solve_id)
            ).tuples().all()
        )
        fits = list(session.scalars(select(NetworkKinetics).where(NetworkKinetics.solve_id.in_(ids))))
        fit_ids = [f.id for f in fits]
        plog_counts = dict(
            session.execute(
                select(NetworkKineticsPlog.network_kinetics_id, func.count())
                .where(NetworkKineticsPlog.network_kinetics_id.in_(fit_ids))
                .group_by(NetworkKineticsPlog.network_kinetics_id)
            ).tuples().all()
        )
        has_cheb = set(
            session.scalars(
                select(NetworkKineticsChebyshev.network_kinetics_id).where(NetworkKineticsChebyshev.network_kinetics_id.in_(fit_ids))
            )
        )
        point_counts = dict(
            session.execute(
                select(NetworkKineticsPoint.network_kinetics_id, func.count())
                .where(NetworkKineticsPoint.network_kinetics_id.in_(fit_ids))
                .group_by(NetworkKineticsPoint.network_kinetics_id)
            ).tuples().all()
        )
        determinations = list(
            session.scalars(select(NetworkKineticsDetermination).where(NetworkKineticsDetermination.solve_id.in_(ids)))
        )
        det_ids = {d.id: d.solve_id for d in determinations}
        solves["determinations"] += len(det_ids)
        for determination in determinations:
            obs_state, _ = _state(NetworkObservableDeclaration, determination.observable_declaration)
            observable_state[obs_state] += 1
            if obs_state != "valid":
                unresolved["grouping:observable_declaration_" + ("not_declared" if obs_state == "absent" else "unreadable")] += 1
        fits_by_solve: dict[int, list[NetworkKinetics]] = {}
        for fit in fits:
            fits_by_solve.setdefault(fit.solve_id, []).append(fit)
            fit_model[fit.model_kind.value] += 1
            if fit.determination_id is None:
                fit_group["ungrouped"] += 1
                unresolved["grouping:fit_without_determination"] += 1
            else:
                fit_group["grouped"] += 1
                fit_roles[fit.representation_role.value if fit.representation_role else "unstated"] += 1
                rep_state, _ = _state(NetworkRepresentationDeclaration, fit.representation_declaration)
                representation_state[rep_state] += 1
                if rep_state != "valid":
                    unresolved["grouping:representation_declaration_" + ("not_declared" if rep_state == "absent" else "unreadable")] += 1
            if fit.rate_units is None:
                fit_units["rate_units_not_stated"] += 1
                unresolved["domain:fit_rate_units_not_stated"] += 1
            if fit.model_kind.value == "chebyshev":
                if fit.id not in has_cheb:
                    fit_domain["chebyshev_without_coefficients"] += 1
                if None in (fit.tmin_k, fit.tmax_k, fit.pmin_bar, fit.pmax_bar):
                    fit_domain["chebyshev_mapping_domain_missing"] += 1
                    unresolved["domain:chebyshev_mapping_domain_missing"] += 1
                if fit.stores_log10_k is None:
                    fit_domain["chebyshev_log_convention_not_stated"] += 1
                    unresolved["domain:chebyshev_log_convention_not_stated"] += 1
                if fit.pressure_units is None or fit.temperature_units is None:
                    fit_units["axis_units_not_stated"] += 1
                    unresolved["domain:axis_units_not_stated"] += 1
            elif fit.model_kind.value == "plog":
                if not plog_counts.get(fit.id):
                    fit_domain["plog_without_entries"] += 1
                if fit.tmin_k is None or fit.tmax_k is None:
                    fit_domain["plog_without_temperature_bounds"] += 1
                    unresolved["domain:plog_temperature_support_not_bounded"] += 1
            else:
                fit_domain["tabulated"] += 1
                fit_domain["tabulated_points"] += point_counts.get(fit.id, 0)

        for det_id in det_ids:
            count = sum(1 for fit in fits if fit.determination_id == det_id)
            if count == 0:
                determination_fit_counts["no_fit"] += 1
                unresolved["grouping:determination_has_no_fit"] += 1
            else:
                determination_fit_counts["one_fit" if count == 1 else "alternates"] += 1

        for solve in rows:
            solves["solves"] += 1
            by_kind[solve.kind.value] += 1
            by_review[badges[solve.id].status.value] += 1
            state, target = _state(StoredNetworkTargetDeclaration, solve.target_declaration)
            target_state[state] += 1
            p_state, _ = _state(NetworkProtocolDeclaration, solve.protocol_declaration)
            protocol_state[p_state] += 1
            v_state, validation = _state(NetworkValidationDeclaration, solve.validation_declaration)
            validation_state[v_state] += 1
            if validation is not None:
                for kind in {entry.kind.value for entry in validation.entries}:
                    evidence_kinds[kind] += 1
            if state != "valid":
                unresolved["target:" + ("not_declared" if state == "absent" else "unreadable")] += 1
            else:
                for claim in TARGET_CLAIMS:
                    value = getattr(target, claim)
                    if value:
                        claims[claim] += 1
                    else:
                        unresolved[f"target:{claim}_not_declared"] += 1
                complete_catalog = bool(target.outputs) and bool(target.boundaries)
                bundle_ready["declares_a_product_set"] += int(bool(target.product_sets))
                bundle_ready["declares_catalog_and_boundaries"] += int(complete_catalog)
                bundle_ready["full_network_ready"] += int(complete_catalog and bool(target.product_sets))
            if p_state != "valid":
                unresolved["protocol:" + ("not_declared" if p_state == "absent" else "unreadable")] += 1
            n_bath = bath_counts.get(solve.id, 0)
            bath["none" if n_bath == 0 else "one_species" if n_bath == 1 else "mixture"] += 1
            if n_bath == 0:
                unresolved["domain:bath_not_stated"] += 1
            if None in (solve.tmin_k, solve.tmax_k, solve.pmin_bar, solve.pmax_bar):
                unresolved["domain:solve_scope_not_stated"] += 1
            if state == "valid" and target is not None and target.validity is None and not any(o.validity for o in target.outputs):
                unresolved["domain:physical_validity_not_declared"] += 1
            if not fits_by_solve.get(solve.id):
                solves["solves_without_fits"] += 1

    return {
        "bounds_version": BOUNDS_V1.version,
        "network_solves": solves["solves"],
        "network_kinetics_fits": sum(fit_model.values()),
        "determinations": solves["determinations"],
        "solves_without_fits": solves["solves_without_fits"],
        "solves_by_kind": dict(sorted(by_kind.items())),
        "solves_by_review_status": dict(sorted(by_review.items())),
        "target": {
            "declaration": dict(sorted(target_state.items())),
            "claims_stated": {claim: claims.get(claim, 0) for claim in TARGET_CLAIMS},
        },
        "grouping": {
            "fits": dict(sorted(fit_group.items())),
            "roles": dict(sorted(fit_roles.items())),
            "determinations_by_fit_count": dict(sorted(determination_fit_counts.items())),
            "representation_declaration": dict(sorted(representation_state.items())),
            "observable_declaration": dict(sorted(observable_state.items())),
        },
        "domain": {
            "bath": dict(sorted(bath.items())),
            "fit_models": dict(sorted(fit_model.items())),
            "fit_support_gaps": dict(sorted(fit_domain.items())),
            "fit_units": dict(sorted(fit_units.items())),
        },
        "protocol": {
            "declaration": dict(sorted(protocol_state.items())),
            "validation_declaration": dict(sorted(validation_state.items())),
            "validation_kinds_declared": dict(sorted(evidence_kinds.items())),
        },
        "bundle_readiness": dict(sorted(bundle_ready.items())),
        "unresolved_categories": dict(sorted(unresolved.items())),
        "notes": [
            "A solve or fit is counted as stating only what it stated; nothing is inferred and nothing is backfilled.",
            "Validation evidence is counted as declared, never as verified: no verification exists in this release.",
            "No request is assessed here: an inventory has no window or bath, so unresolved_categories name the facts "
            "an assessment would find missing, not the verdict for any request.",
        ],
    }
