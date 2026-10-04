"""The network coverage inventory counts what stored solves and fits state and leave unstated, and writes nothing.

The corpus is built by construction and every expected number below is the increment over what the shared test
database already holds, worked out by hand from the four solves seeded here. The inventory is never run against a
deployed database by anything in this repository.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from app.db.models.common import NetworkSolveKind
from app.db.models.network_pdep import NetworkKinetics, NetworkKineticsDetermination, NetworkSolve
from app.services.network_selection.inventory import network_coverage_inventory
from tests.services.network_selection._world import RecordReviewStatus as S
from tests.services.network_selection._world import add_solve, fit_spec, set_review, target, validity


def delta(after, before, *path):
    def dig(report):
        for key in path:
            report = report.get(key, 0) if isinstance(report, dict) else 0
        return report if isinstance(report, int) else 0

    return dig(after) - dig(before)


@pytest.fixture
def seeded(db_session, world):
    """Four solves with a known inventory; returns the baseline report taken before them."""
    baseline = network_coverage_inventory(db_session)
    # S1: a legacy deposit that declares nothing: ungrouped fits, no bath, a PLOG with no temperature bounds, a
    # Chebyshev with no mapping domain, no log convention and no units.
    add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", det=None, tmin=None, tmax=None),
            fit_spec("elim", det=None, model="chebyshev", pmin=None, pmax=None, stores_log10=None, units=None),
        ],
        solve_target=None,
        bath=[],
        review=S.not_reviewed,
    )
    # S2: fully declared, with alternates, an additive component, a product set, a protocol and evidence.
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("assoc", "elim")]
    add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", rep="plog1"),
            fit_spec("assoc", rep="part", role="additive_component"),
            fit_spec("elim"),
        ],
        solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
        protocol={"version": 1, "reduction_method": "chemically_significant_eigenvalues"},
        validation={
            "version": 1,
            "entries": [
                {"kind": "convergence", "domain": validity()},
                {"kind": "representation_validation", "domain": validity(), "reference_dataset": "a table"},
            ],
        },
    )
    # S3: reported, tabulated, a bath mixture, a target with no validity, catalog or product set.
    points = [(1000.0, 1.0, 1.0e-12), (1200.0, 1.0, 2.0e-12)]
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", model="tabulated", points=points)],
        solve_target=target(world, validity=None, outputs=[]),
        bath=[(world.ar, 0.5), (world.he, 0.5)],
        kind=NetworkSolveKind.reported,
    )
    # S4: stored declarations this server can no longer read.
    s4 = add_solve(db_session, world, fits=[fit_spec("assoc", det=None)], review=None)
    s4.target_declaration = {"version": 1, "claim_origin": "nobody_we_know"}
    s4.protocol_declaration = {"version": 1, "reduction_method": "a_method_this_server_does_not_know"}
    db_session.flush()
    set_review(db_session, world, s4, S.approved)
    # S5: a catalog and boundaries but no product set: not a bundle candidate, not full-network ready.
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc")],
        solve_target=target(world, outputs=[{"channel_key": "assoc", "availability": "supplied", "required": True}]),
    )
    return baseline


def test_the_inventory_counts_what_each_solve_states_and_leaves_unstated(db_session, seeded):
    after = network_coverage_inventory(db_session)
    d = lambda *path: delta(after, seeded, *path)  # noqa: E731

    assert d("network_solves") == 5 and d("network_kinetics_fits") == 8 and d("determinations") == 4
    assert (d("solves_by_kind", "computed"), d("solves_by_kind", "reported")) == (4, 1)
    assert d("solves_by_review_status", "not_reviewed") == 1 and d("solves_by_review_status", "approved") == 4

    # target
    assert (d("target", "declaration", "absent"), d("target", "declaration", "valid"), d("target", "declaration", "unreadable")) == (1, 3, 1)
    stated = {c: d("target", "claims_stated", c) for c in ("partition", "boundaries", "regime", "validity", "bath_scope", "outputs", "product_sets")}
    assert stated == {"partition": 3, "boundaries": 3, "regime": 3, "validity": 2, "bath_scope": 3, "outputs": 2, "product_sets": 1}

    # grouping
    assert (d("grouping", "fits", "grouped"), d("grouping", "fits", "ungrouped")) == (5, 3)
    assert (d("grouping", "roles", "complete"), d("grouping", "roles", "additive_component")) == (4, 1)
    assert d("grouping", "determinations_with_one_fit_or_alternates", "alternates") == 1
    assert d("grouping", "determinations_with_one_fit_or_alternates", "one_fit") == 3

    # domain
    assert (d("domain", "bath", "none"), d("domain", "bath", "one_species"), d("domain", "bath", "mixture")) == (1, 3, 1)
    assert (d("domain", "fit_models", "plog"), d("domain", "fit_models", "chebyshev"), d("domain", "fit_models", "tabulated")) == (6, 1, 1)
    gaps = {k: d("domain", "fit_support_gaps", k) for k in (
        "plog_without_temperature_bounds", "chebyshev_mapping_domain_missing", "chebyshev_log_convention_not_stated",
        "tabulated", "tabulated_points")}
    assert gaps == {
        "plog_without_temperature_bounds": 1,
        "chebyshev_mapping_domain_missing": 1,
        "chebyshev_log_convention_not_stated": 1,
        "tabulated": 1,
        "tabulated_points": 2,
    }
    assert d("domain", "fit_units", "rate_units_not_stated") == 1

    # protocol and evidence
    assert (d("protocol", "declaration", "absent"), d("protocol", "declaration", "valid"), d("protocol", "declaration", "unreadable")) == (3, 1, 1)
    assert (d("protocol", "validation_declaration", "valid"), d("protocol", "validation_declaration", "absent")) == (1, 4)
    assert (d("protocol", "validation_kinds_declared", "convergence"), d("protocol", "validation_kinds_declared", "representation_validation")) == (1, 1)

    # bundle readiness
    assert d("bundle_readiness", "declares_a_product_set") == 1
    assert d("bundle_readiness", "declares_catalog_and_boundaries") == 2
    assert d("bundle_readiness", "full_network_ready") == 1

    # the facts an assessment would find missing, by category
    expected = {
        "target:not_declared": 1,
        "target:unreadable": 1,
        "target:outputs_not_declared": 1,
        "target:product_sets_not_declared": 2,
        "target:validity_not_declared": 1,
        "protocol:not_declared": 3,
        "protocol:unreadable": 1,
        "domain:bath_not_stated": 1,
        "domain:physical_validity_not_declared": 1,
        "domain:fit_rate_units_not_stated": 1,
        "domain:plog_temperature_support_not_bounded": 1,
        "grouping:fit_without_determination": 3,
    }
    assert {k: d("unresolved_categories", k) for k in expected} == expected


def test_an_unreadable_claim_is_counted_as_unreadable_and_never_as_not_stated(db_session, seeded):
    after = network_coverage_inventory(db_session)
    assert delta(after, seeded, "target", "declaration", "unreadable") == 1
    assert delta(after, seeded, "target", "declaration", "absent") == 1  # only the legacy solve states nothing
    assert delta(after, seeded, "unresolved_categories", "target:unreadable") == 1


def test_the_report_is_json_and_says_what_it_does_not_claim(db_session, seeded):
    report = network_coverage_inventory(db_session)
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert report["bounds_version"] == "1"
    assert any("declared, never as verified" in note for note in report["notes"])


def test_the_inventory_issues_only_reads_and_changes_nothing(db_session, seeded):
    engine = db_session.get_bind()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    models = (NetworkSolve, NetworkKinetics, NetworkKineticsDetermination)
    before = [db_session.scalar(select(func.count()).select_from(m)) for m in models]
    event.listen(engine, "before_cursor_execute", record)
    try:
        network_coverage_inventory(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements and set(statements) <= {"SELECT", "WITH"}, sorted(set(statements))
    assert [db_session.scalar(select(func.count()).select_from(m)) for m in models] == before
    assert not db_session.dirty and not db_session.new and not db_session.deleted


def test_the_script_is_a_thin_read_only_wrapper_over_the_inventory():
    source = (Path(__file__).parents[3] / "scripts" / "inventory_network_coverage.py").read_text()
    assert "SET TRANSACTION READ ONLY" in source and "REPEATABLE READ" in source
    assert "network_coverage_inventory(session)" in source and "current_database()" in source
    for forbidden in (".add(", ".commit(", ".flush(", "INSERT", "UPDATE", "DELETE"):
        assert forbidden not in source, forbidden
    # The script imports and exposes main; it is not run here (that would touch whatever database is configured).
    compiled = subprocess.run([sys.executable, "-m", "py_compile", str(Path(__file__).parents[3] / "scripts" / "inventory_network_coverage.py")], capture_output=True)
    assert compiled.returncode == 0, compiled.stderr
