"""The network coverage inventory counts what stored solves and fits state and leave unstated, and writes nothing.

The corpus is built by construction and every expected number below is the increment over what the shared test
database already holds, worked out by hand from the four solves seeded here. The inventory is never run against a
deployed database by anything in this repository.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event, func, select, text

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
            fit_spec("elim", det=None, model="chebyshev", pmin=None, pmax=None, stores_log10=None, units=None, pressure_units=None),
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
    assert d("grouping", "determinations_by_fit_count", "alternates") == 1
    assert d("grouping", "determinations_by_fit_count", "one_fit") == 3

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
    assert d("domain", "fit_units", "rate_units_not_stated") == 1 and d("domain", "fit_units", "axis_units_not_stated") == 1

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
        "domain:chebyshev_mapping_domain_missing": 1,
        "domain:chebyshev_log_convention_not_stated": 1,
        "domain:axis_units_not_stated": 1,
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


def test_the_inventory_report_runs_read_only_and_a_write_in_it_is_refused_by_the_database(db_engine, capsys):
    """Behavioural, through the shared report helper the script uses: the database itself refuses a write."""
    from sqlalchemy.exc import DBAPIError

    from app.services.read_only_report import print_read_only_report

    url = db_engine.url.render_as_string(hide_password=False)
    print_read_only_report(url, network_coverage_inventory)
    assert json.loads(capsys.readouterr().out)["bounds_version"] == "1"

    def write(session):
        session.execute(text("UPDATE network_solve SET note = note WHERE false"))
        return {}

    with pytest.raises(DBAPIError, match="read-only"):
        print_read_only_report(url, write)


# -- the review fixes: declarations, orphan determinations, gaps, batching and zeros ---------------------------


@pytest.fixture
def seeded_declarations(db_session, world):
    """One solve whose fits and determinations state their claims badly, in every way the assessor distinguishes."""
    baseline = network_coverage_inventory(db_session)
    # d_assoc states an observable this server cannot read.
    fits = [fit_spec("assoc", observable={"observable": "nonsense"}), fit_spec("elim"), fit_spec("diss")]
    solve = add_solve(db_session, world, fits=fits, review=None)
    assoc_fit, _elim_fit, diss_fit = solve._fits
    assoc_fit.representation_declaration = {"version": 1, "key": "x", "fit_origin": "nobody_we_know"}  # unreadable
    db_session.delete(diss_fit)  # d_diss keeps a valid observable and now has no fit
    db_session.flush()
    return baseline


def test_unreadable_fit_and_observable_declarations_are_counted_not_dropped(db_session, seeded_declarations):
    after = network_coverage_inventory(db_session)
    d = lambda *path: delta(after, seeded_declarations, *path)  # noqa: E731
    representation = ("grouping", "representation_declaration")
    assert (d(*representation, "unreadable"), d(*representation, "absent"), d(*representation, "valid")) == (1, 0, 1)  # absent: a CHECK
    observable = ("grouping", "observable_declaration")
    assert (d(*observable, "unreadable"), d(*observable, "absent"), d(*observable, "valid")) == (1, 0, 2)  # never absent: NOT NULL
    for key in (
        "grouping:representation_declaration_unreadable",
        "grouping:observable_declaration_unreadable",
    ):
        assert d("unresolved_categories", key) == 1, key


def test_a_determination_with_no_fit_is_its_own_category_and_the_counts_sum_to_the_total(db_session, seeded_declarations):
    after = network_coverage_inventory(db_session)
    d = lambda *path: delta(after, seeded_declarations, *path)  # noqa: E731
    by_count = ("grouping", "determinations_by_fit_count")
    assert (d(*by_count, "no_fit"), d(*by_count, "one_fit"), d(*by_count, "alternates")) == (1, 2, 0)
    assert d("unresolved_categories", "grouping:determination_has_no_fit") == 1
    assert d("determinations") == 3
    assert sum(after["grouping"]["determinations_by_fit_count"].values()) == after["determinations"]


@pytest.fixture
def seeded_domain(db_session, world):
    baseline = network_coverage_inventory(db_session)
    # No solve scope, and a PLOG fit with no entries.
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", plog=[])],
        scope={"tmin_k": None, "tmax_k": None, "pmin_bar": None, "pmax_bar": None},
        review=None,
    )
    # A physical validity stated only on an output: that is a stated validity.
    on_output = [{"channel_key": "assoc", "availability": "supplied", "required": True, "validity": validity()}]
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=target(world, validity=None, outputs=on_output), review=None)
    # No validity anywhere.
    bare = [{"channel_key": "assoc", "availability": "supplied", "required": True}]
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=target(world, validity=None, outputs=bare), review=None)
    return baseline


def test_missing_solve_scope_and_plog_entries_and_output_only_validity_are_counted_exactly(db_session, seeded_domain):
    after = network_coverage_inventory(db_session)
    d = lambda *path: delta(after, seeded_domain, *path)  # noqa: E731
    assert d("unresolved_categories", "domain:solve_scope_not_stated") == 1
    assert d("domain", "fit_support_gaps", "plog_without_entries") == 1
    # Only the third solve states no validity at all; the second states one on its output.
    assert d("unresolved_categories", "domain:physical_validity_not_declared") == 1
    assert d("target", "claims_stated", "validity") == 1  # the first solve's default target; the other two state none


def test_the_report_is_the_same_whatever_the_batch_size(db_session, seeded):
    whole = network_coverage_inventory(db_session)
    assert whole["network_solves"] >= 5
    assert network_coverage_inventory(db_session, batch_size=1) == whole
    assert network_coverage_inventory(db_session, batch_size=2) == whole


def test_every_known_category_is_reported_even_when_nothing_was_found(db_session, world):
    from app.services.network_selection.inventory import FIT_SUPPORT_GAPS, UNRESOLVED_CATEGORIES

    report = network_coverage_inventory(db_session)
    assert set(UNRESOLVED_CATEGORIES) <= set(report["unresolved_categories"])
    assert set(FIT_SUPPORT_GAPS) <= set(report["domain"]["fit_support_gaps"])
    assert {"absent", "valid", "unreadable"} <= set(report["grouping"]["representation_declaration"])
    assert {"one_fit", "alternates", "no_fit"} <= set(report["grouping"]["determinations_by_fit_count"])
    assert all(isinstance(v, int) and v >= 0 for v in report["unresolved_categories"].values())
