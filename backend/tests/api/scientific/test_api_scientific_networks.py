"""API tests for the scientific Network / PDep read+search endpoints."""

from __future__ import annotations

import hashlib

from app.db.models.common import (
    CalculationType,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSpeciesRole,
    NetworkStateKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    attach_network_kinetics_chebyshev,
    attach_network_kinetics_plog,
    attach_network_kinetics_point,
    attach_network_reaction,
    attach_network_solve_bath_gas,
    attach_network_solve_source_calculation,
    attach_network_species,
    attach_network_state_participant,
    make_calculation,
    make_chem_reaction,
    make_lot,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _make_simple_network(
    db_session,
    *,
    with_species: bool = True,
    with_reaction: bool = False,
    with_state: bool = False,
    with_channel: bool = False,
    with_solve: bool = False,
    with_kinetics_kind: NetworkKineticsModelKind | None = None,
    with_source_calc: bool = False,
    source_calc_lot=None,
):
    """Build a network + optional child rows. Returns a dict of refs."""
    n = make_network(db_session, name=f"net-{next_inchi_key('NET')}")
    out = {"network": n}

    sp = make_species(db_session, inchi_key=next_inchi_key("NA"))
    se = make_species_entry(db_session, sp)
    out["species"] = sp
    out["species_entry"] = se
    if with_species:
        attach_network_species(
            db_session, network=n, species_entry=se, role=NetworkSpeciesRole.well
        )

    if with_reaction:
        sp2 = make_species(
            db_session, inchi_key=next_inchi_key("NB")
        )
        chem = make_chem_reaction(db_session, reactants=[sp], products=[sp2])
        se2 = make_species_entry(db_session, sp2)
        rxe = make_reaction_entry(
            db_session,
            reaction=chem,
            reactant_entries=[se],
            product_entries=[se2],
        )
        out["reaction"] = chem
        out["reaction_entry"] = rxe
        attach_network_reaction(
            db_session, network=n, reaction_entry=rxe
        )

    state_a = state_b = channel = solve = kin = None
    if with_state or with_channel or with_kinetics_kind:
        state_a = make_network_state(
            db_session,
            network=n,
            kind=NetworkStateKind.well,
            composition_hash=_h(f"state-a-{n.id}"),
            label="A",
        )
        state_b = make_network_state(
            db_session,
            network=n,
            kind=NetworkStateKind.well,
            composition_hash=_h(f"state-b-{n.id}"),
            label="B",
        )
        attach_network_state_participant(
            db_session, state=state_a, species_entry=se, stoichiometry=1
        )
        attach_network_state_participant(
            db_session, state=state_b, species_entry=se, stoichiometry=1
        )
        out["state_a"] = state_a
        out["state_b"] = state_b
    if with_channel or with_kinetics_kind:
        channel = make_network_channel(
            db_session,
            network=n,
            source_state=state_a,
            sink_state=state_b,
            kind=NetworkChannelKind.isomerization,
        )
        out["channel"] = channel
    if with_solve or with_kinetics_kind:
        solve = make_network_solve(db_session, network=n)
        out["solve"] = solve
        if with_source_calc:
            lot_id = source_calc_lot.id if source_calc_lot is not None else None
            calc = make_calculation(
                db_session,
                type=CalculationType.sp,
                species_entry_id=se.id,
                lot_id=lot_id,
            )
            attach_network_solve_source_calculation(
                db_session,
                solve=solve,
                calculation=calc,
                role=NetworkSolveCalculationRole.fit_source,
            )
            out["source_calculation"] = calc
    if with_kinetics_kind:
        kin = make_network_kinetics(
            db_session,
            channel=channel,
            solve=solve,
            model_kind=with_kinetics_kind,
            tmin_k=300.0,
            tmax_k=2000.0,
            pmin_bar=0.01,
            pmax_bar=100.0,
        )
        out["kinetics"] = kin
        if with_kinetics_kind is NetworkKineticsModelKind.chebyshev:
            attach_network_kinetics_chebyshev(
                db_session, kinetics=kin, n_temperature=6, n_pressure=4
            )
        elif with_kinetics_kind is NetworkKineticsModelKind.plog:
            attach_network_kinetics_plog(db_session, kinetics=kin)
        elif with_kinetics_kind is NetworkKineticsModelKind.tabulated:
            attach_network_kinetics_point(
                db_session,
                kinetics=kin,
                temperature_k=500.0,
                pressure_bar=1.0,
                rate_value=1.0,
            )
    return out


def _detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/networks/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _search_url(**params) -> str:
    base = "/api/v1/scientific/networks/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# Detail endpoint
# ===========================================================================


def test_detail_by_ref_returns_record(client, db_session):
    fx = _make_simple_network(db_session)
    n = fx["network"]
    resp = client.get(_detail_url(n.public_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["network"]["network_ref"] == n.public_ref


def test_detail_by_integer_id_works(client, db_session):
    fx = _make_simple_network(db_session)
    n = fx["network"]
    resp = client.get(_detail_url(str(n.id)))
    assert resp.status_code == 200, resp.text


def test_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_detail_url("net_doesnotexist0000"))
    assert resp.status_code == 404
    assert "network not found" in resp.text


def test_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_detail_url("sm_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_detail_url("not-a-handle"))
    assert resp.status_code == 422


def test_detail_default_response_shape(client, db_session):
    fx = _make_simple_network(db_session)
    body = client.get(_detail_url(fx["network"].public_ref)).json()
    record = body["record"]
    for key in ("network", "evidence_summary", "available_sections"):
        assert key in record
    for k in (
        "species",
        "reactions",
        "states",
        "channels",
        "solves",
        "kinetics",
        "source_calculations",
        "review_history",
    ):
        assert record[k] is None


def test_detail_review_badge_present(client, db_session):
    fx = _make_simple_network(db_session)
    body = client.get(_detail_url(fx["network"].public_ref)).json()
    assert body["record"]["network"]["review"]["status"] == "not_reviewed"


def test_detail_evidence_summary_counts(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_reaction=True,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    ev = client.get(_detail_url(fx["network"].public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert ev["species_count"] == 1
    assert ev["reaction_count"] == 1
    assert ev["state_count"] == 2
    assert ev["channel_count"] == 1
    assert ev["solve_count"] == 1
    assert ev["kinetics_count"] == 1
    assert ev["source_calculation_count"] == 1
    assert ev["has_chebyshev"] is True
    assert ev["has_plog"] is False
    assert ev["has_point_kinetics"] is False


def test_detail_solve_envelope_in_core_block(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    core = client.get(_detail_url(fx["network"].public_ref)).json()["record"][
        "network"
    ]
    assert core["solve_temperature_min_k"] == 300.0
    assert core["solve_temperature_max_k"] == 2000.0
    assert core["solve_pressure_min_bar"] == 0.01
    assert core["solve_pressure_max_bar"] == 100.0


def test_detail_include_species(client, db_session):
    fx = _make_simple_network(db_session)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="species")
    ).json()
    sp = body["record"]["species"]
    assert sp is not None
    assert len(sp) == 1
    assert sp[0]["role"] == "well"
    assert sp[0]["species_entry_ref"] == fx["species_entry"].public_ref


def test_detail_include_reactions(client, db_session):
    fx = _make_simple_network(db_session, with_reaction=True)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="reactions")
    ).json()
    rx = body["record"]["reactions"]
    assert rx is not None
    assert len(rx) == 1
    assert rx[0]["reaction_entry_ref"] == fx["reaction_entry"].public_ref


def test_detail_include_states(client, db_session):
    fx = _make_simple_network(db_session, with_state=True)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="states")
    ).json()
    states = body["record"]["states"]
    assert states is not None
    assert len(states) == 2
    assert {s["composition_hash"] for s in states} == {
        fx["state_a"].composition_hash,
        fx["state_b"].composition_hash,
    }


def test_detail_include_channels(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
    )
    body = client.get(
        _detail_url(fx["network"].public_ref, include="channels")
    ).json()
    ch = body["record"]["channels"]
    assert ch is not None
    assert len(ch) == 1
    assert ch[0]["has_kinetics"] is True
    assert ch[0]["kind"] == "isomerization"


def test_detail_include_solves(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="solves")
    ).json()
    s = body["record"]["solves"]
    assert s is not None
    assert len(s) == 1
    assert s[0]["network_solve_ref"] == fx["solve"].public_ref
    assert s[0]["bath_gas_count"] == 0
    assert s[0]["source_calculation_count"] == 0


def test_detail_include_kinetics(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    body = client.get(
        _detail_url(fx["network"].public_ref, include="kinetics")
    ).json()
    kn = body["record"]["kinetics"]
    assert kn is not None
    assert len(kn) == 1
    assert kn[0]["model_kind"] == "chebyshev"
    assert kn[0]["chebyshev_shape"] == "6x4"
    # Forbidden: coefficient payload must NOT inline.
    assert "coefficients" not in kn[0]


def test_detail_include_kinetics_plog_count(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    kn = client.get(
        _detail_url(fx["network"].public_ref, include="kinetics")
    ).json()["record"]["kinetics"]
    assert kn[0]["plog_entry_count"] == 1


def test_detail_include_kinetics_point_count(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.tabulated
    )
    kn = client.get(
        _detail_url(fx["network"].public_ref, include="kinetics")
    ).json()["record"]["kinetics"]
    assert kn[0]["point_count"] == 1


def test_detail_include_source_calculations(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _detail_url(
            fx["network"].public_ref, include="source_calculations"
        )
    ).json()
    sc = body["record"]["source_calculations"]
    assert sc is not None
    assert len(sc) == 1
    assert sc[0]["calculation_ref"] == fx["source_calculation"].public_ref
    assert sc[0]["role"] == "fit_source"


def test_detail_include_review(client, db_session):
    fx = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=fx["network"].id,
        status=RecordReviewStatus.approved,
    )
    rh = client.get(
        _detail_url(fx["network"].public_ref, include="review")
    ).json()["record"]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "approved"


def test_detail_include_all_expands_all_public_tokens(client, db_session):
    fx = _make_simple_network(db_session)
    inc = client.get(
        _detail_url(fx["network"].public_ref, include="all")
    ).json()["request"]["include"]
    for token in (
        "species",
        "reactions",
        "states",
        "channels",
        "solves",
        "kinetics",
        "source_calculations",
        "review",
    ):
        assert token in inc
    assert "internal_ids" not in inc


def test_detail_include_all_does_not_restore_internal_ids(client, db_session):
    fx = _make_simple_network(db_session)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="all")
    ).json()
    assert "network_id" not in body["record"]["network"]


def test_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    fx = _make_simple_network(db_session)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="internal_ids")
    ).json()
    assert body["record"]["network"]["network_id"] == fx["network"].id


def test_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    fx = _make_simple_network(db_session)
    body = client.get(
        _detail_url(fx["network"].public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "network_id" not in body["record"]["network"]


def test_detail_unknown_include_token_returns_422(client, db_session):
    fx = _make_simple_network(db_session)
    resp = client.get(_detail_url(fx["network"].public_ref, include="banana"))
    assert resp.status_code == 422


def test_detail_rejected_record_still_returned_with_badge(client, db_session):
    fx = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=fx["network"].id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_detail_url(fx["network"].public_ref)).json()
    assert body["record"]["network"]["review"]["status"] == "rejected"


def test_detail_no_forbidden_payload_keys(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _detail_url(fx["network"].public_ref, include="all")
    ).json()
    forbidden = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        # Network kinetics coefficient payloads MUST NOT inline.
        "coefficients",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"network detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ===========================================================================
# Search endpoint
# ===========================================================================


def test_search_missing_filter_returns_422_get(client, db_session):
    resp = client.get(_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_search_missing_filter_returns_422_post(client, db_session):
    resp = client.post(_search_url(), json={"limit": 50})
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_search_by_network_ref(client, db_session):
    a = _make_simple_network(db_session)
    _b = _make_simple_network(db_session)
    body = client.get(
        _search_url(network_ref=a["network"].public_ref)
    ).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert refs == {a["network"].public_ref}


def test_search_by_species_entry_ref(client, db_session):
    a = _make_simple_network(db_session)
    _b = _make_simple_network(db_session)
    body = client.get(
        _search_url(species_entry_ref=a["species_entry"].public_ref)
    ).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert refs == {a["network"].public_ref}


def test_search_by_reaction_entry_ref(client, db_session):
    a = _make_simple_network(db_session, with_reaction=True)
    _b = _make_simple_network(db_session, with_reaction=True)
    body = client.get(
        _search_url(reaction_entry_ref=a["reaction_entry"].public_ref)
    ).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert refs == {a["network"].public_ref}


def test_search_by_has_species_true_and_false(client, db_session):
    a = _make_simple_network(db_session, with_species=True)
    b = _make_simple_network(db_session, with_species=False)
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_species="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_species="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_has_channels_true_and_false(client, db_session):
    a = _make_simple_network(db_session, with_channel=True)
    b = _make_simple_network(db_session)
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_channels="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_channels="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_has_solves_true_and_false(client, db_session):
    a = _make_simple_network(db_session, with_solve=True)
    b = _make_simple_network(db_session)
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_solves="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_solves="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_has_kinetics_true_and_false(client, db_session):
    a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    b = _make_simple_network(db_session, with_solve=True)
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_kinetics="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_kinetics="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_has_chebyshev_true_and_false(client, db_session):
    a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    b = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_chebyshev="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_chebyshev="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_has_plog_true_and_false(client, db_session):
    a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    b = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_plog="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_plog="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_has_point_kinetics_true_and_false(client, db_session):
    a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.tabulated
    )
    b = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_point_kinetics="true")).json()[
            "records"
        ]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_point_kinetics="false")).json()[
            "records"
        ]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_search_by_method_and_basis(client, db_session):
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
        source_calc_lot=lot_a,
    )
    _fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
        source_calc_lot=lot_b,
    )
    refs = {
        r["network"]["network_ref"]
        for r in client.get(
            _search_url(method="wb97xd", basis="def2tzvp")
        ).json()["records"]
    }
    assert refs == {fx_a["network"].public_ref}


def test_search_by_software_and_version(client, db_session):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    sw_a = Software(name="gaussian")
    sw_b = Software(name="orca")
    db_session.add_all([sw_a, sw_b])
    db_session.flush()
    sr_a = SoftwareRelease(software_id=sw_a.id, version="g16.a03")
    sr_b = SoftwareRelease(software_id=sw_b.id, version="5.0.4")
    db_session.add_all([sr_a, sr_b])
    db_session.flush()
    fx_a["source_calculation"].software_release_id = sr_a.id
    fx_b["source_calculation"].software_release_id = sr_b.id
    db_session.flush()
    refs = {
        r["network"]["network_ref"]
        for r in client.get(
            _search_url(software="gaussian", software_version="g16.a03")
        ).json()["records"]
    }
    assert refs == {fx_a["network"].public_ref}


def test_search_by_workflow_tool_and_version(client, db_session):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    wt_a = WorkflowTool(name="arc")
    wt_b = WorkflowTool(name="qcelemental")
    db_session.add_all([wt_a, wt_b])
    db_session.flush()
    wtr_a = WorkflowToolRelease(workflow_tool_id=wt_a.id, version="1.2.3")
    wtr_b = WorkflowToolRelease(workflow_tool_id=wt_b.id, version="0.27.0")
    db_session.add_all([wtr_a, wtr_b])
    db_session.flush()
    fx_a["source_calculation"].workflow_tool_release_id = wtr_a.id
    fx_b["source_calculation"].workflow_tool_release_id = wtr_b.id
    db_session.flush()
    refs = {
        r["network"]["network_ref"]
        for r in client.get(
            _search_url(workflow_tool="arc", workflow_tool_version="1.2.3")
        ).json()["records"]
    }
    assert refs == {fx_a["network"].public_ref}


def test_search_by_temperature_range(client, db_session):
    # Network A: T range [300, 2000]; network B: T range [3000, 4000]
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    fx_b["solve"].tmin_k = 3000.0
    fx_b["solve"].tmax_k = 4000.0
    db_session.flush()
    refs = {
        r["network"]["network_ref"]
        for r in client.get(
            _search_url(temperature_min=200.0, temperature_max=500.0)
        ).json()["records"]
    }
    assert fx_a["network"].public_ref in refs
    assert fx_b["network"].public_ref not in refs


def test_search_by_pressure_range(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    fx_b["solve"].pmin_bar = 500.0
    fx_b["solve"].pmax_bar = 1000.0
    db_session.flush()
    refs = {
        r["network"]["network_ref"]
        for r in client.get(
            _search_url(pressure_min=0.1, pressure_max=10.0)
        ).json()["records"]
    }
    assert fx_a["network"].public_ref in refs
    assert fx_b["network"].public_ref not in refs


def test_search_default_hides_rejected(client, db_session):
    a = _make_simple_network(db_session)
    b = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=b["network"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = {
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_species="true")).json()["records"]
    }
    assert a["network"].public_ref in refs
    assert b["network"].public_ref not in refs


def test_search_include_rejected_sorts_last(client, db_session):
    a = _make_simple_network(db_session)
    b = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=b["network"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = [
        r["network"]["network_ref"]
        for r in client.get(
            _search_url(has_species="true", include_rejected="true")
        ).json()["records"]
    ]
    assert a["network"].public_ref in refs
    assert b["network"].public_ref in refs
    assert refs[-1] == b["network"].public_ref


def test_search_pagination_envelope(client, db_session):
    se = make_species_entry(
        db_session,
        make_species(db_session, inchi_key=next_inchi_key("NX")),
    )
    for _ in range(4):
        n = make_network(db_session, name="pag")
        attach_network_species(
            db_session, network=n, species_entry=se, role=NetworkSpeciesRole.well
        )
    body = client.get(
        _search_url(species_entry_ref=se.public_ref, limit=2, offset=0)
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_search_deterministic_ordering_review_then_created(client, db_session):
    a = _make_simple_network(db_session)
    _b = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=a["network"].id,
        status=RecordReviewStatus.approved,
    )
    refs = [
        r["network"]["network_ref"]
        for r in client.get(_search_url(has_species="true")).json()["records"]
    ]
    assert refs[0] == a["network"].public_ref


def test_search_client_sort_rejected(client, db_session):
    _make_simple_network(db_session)
    resp = client.get(_search_url(has_species="true", sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_search_get_post_parity(client, db_session):
    fx = _make_simple_network(db_session)
    get_body = client.get(
        _search_url(network_ref=fx["network"].public_ref)
    ).json()
    post_body = client.post(
        _search_url(), json={"network_ref": fx["network"].public_ref}
    ).json()
    assert get_body["pagination"] == post_body["pagination"]
    assert get_body["records"] == post_body["records"]


def test_search_post_rejects_query_string_search_fields(client, db_session):
    _make_simple_network(db_session)
    resp = client.post(
        "/api/v1/scientific/networks/search?limit=5",
        json={"has_species": True},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_search_include_all_on_records(client, db_session):
    fx = _make_simple_network(db_session)
    body = client.get(
        _search_url(network_ref=fx["network"].public_ref, include="all")
    ).json()
    inc = body["request"]["include"]
    assert "species" in inc
    assert "kinetics" in inc
    assert "internal_ids" not in inc


def test_search_include_all_does_not_restore_internal_ids(client, db_session):
    fx = _make_simple_network(db_session)
    body = client.get(
        _search_url(network_ref=fx["network"].public_ref, include="all")
    ).json()
    assert "network_id" not in body["records"][0]["network"]


def test_search_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    fx = _make_simple_network(db_session)
    body = client.get(
        _search_url(
            network_ref=fx["network"].public_ref, include="internal_ids"
        )
    ).json()
    assert body["records"][0]["network"]["network_id"] == fx["network"].id


def test_search_record_shape_matches_detail(client, db_session):
    """Cross-endpoint anti-drift."""
    fx = _make_simple_network(db_session, with_solve=True)
    search_body = client.get(
        _search_url(network_ref=fx["network"].public_ref, include="solves")
    ).json()
    detail_body = client.get(
        _detail_url(fx["network"].public_ref, include="solves")
    ).json()
    assert search_body["records"][0] == detail_body["record"]


def test_search_unknown_ref_short_circuits_empty(client, db_session):
    body = client.get(
        _search_url(species_entry_ref="spe_doesnotexist00")
    ).json()
    assert body["pagination"]["total"] == 0


def test_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(_search_url(network_ref="sm_abcdef0123456789"))
    assert resp.status_code == 422


def test_search_no_forbidden_payload_keys(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _search_url(network_ref=fx["network"].public_ref, include="all")
    ).json()
    forbidden = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        "coefficients",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"network search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ===========================================================================
# Network-solve standalone detail endpoint
# ===========================================================================


def _solve_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/network-solves/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def test_solve_detail_by_ref_returns_record(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    solve = fx["solve"]
    body = client.get(_solve_url(solve.public_ref)).json()
    assert (
        body["record"]["network_solve"]["network_solve_ref"]
        == solve.public_ref
    )


def test_solve_detail_by_integer_id_works(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    resp = client.get(_solve_url(str(fx["solve"].id)))
    assert resp.status_code == 200, resp.text


def test_solve_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_solve_url("nsolve_doesnotexist00"))
    assert resp.status_code == 404
    assert "network_solve not found" in resp.text


def test_solve_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_solve_url("net_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_solve_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_solve_url("not-a-handle"))
    assert resp.status_code == 422


def test_solve_detail_default_response_shape(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(_solve_url(fx["solve"].public_ref)).json()
    record = body["record"]
    for key in (
        "network_solve",
        "network",
        "evidence_summary",
        "available_sections",
    ):
        assert key in record
    for k in (
        "bath_gas",
        "energy_transfer",
        "source_calculations",
        "kinetics",
        "review_history",
    ):
        assert record[k] is None


def test_solve_detail_parent_network_context_present(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(_solve_url(fx["solve"].public_ref)).json()
    net_ctx = body["record"]["network"]
    assert net_ctx["network_ref"] == fx["network"].public_ref


def test_solve_detail_review_badge_present(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(_solve_url(fx["solve"].public_ref)).json()
    assert body["record"]["network_solve"]["review"]["status"] == "not_reviewed"
    assert body["review_summary"]["total"] == 1


def test_solve_detail_evidence_summary(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(_solve_url(fx["solve"].public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["source_calculation_count"] == 1
    assert ev["kinetics_count"] == 1
    assert ev["has_chebyshev"] is True
    assert ev["has_plog"] is False
    assert ev["has_point_kinetics"] is False


def test_solve_detail_available_sections_keys(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    sections = client.get(_solve_url(fx["solve"].public_ref)).json()[
        "record"
    ]["available_sections"]
    for key in (
        "has_bath_gas",
        "has_energy_transfer",
        "has_source_calculations",
        "has_kinetics",
        "has_review",
    ):
        assert key in sections


def test_solve_detail_include_bath_gas(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    bath_sp = make_species(
        db_session, inchi_key=next_inchi_key("NBA")
    )
    bath_se = make_species_entry(db_session, bath_sp)
    attach_network_solve_bath_gas(
        db_session, solve=fx["solve"], species_entry=bath_se, mole_fraction=0.5
    )
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="bath_gas")
    ).json()
    bg = body["record"]["bath_gas"]
    assert bg is not None
    assert len(bg) == 1
    assert bg[0]["species_entry_ref"] == bath_se.public_ref
    assert bg[0]["mole_fraction"] == 0.5


def test_solve_detail_include_energy_transfer(client, db_session):
    from app.db.models.network_pdep import NetworkSolveEnergyTransfer

    fx = _make_simple_network(db_session, with_solve=True)
    db_session.add(
        NetworkSolveEnergyTransfer(
            solve_id=fx["solve"].id,
            model="exponential_down",
            alpha0_cm_inv=300.0,
            t_exponent=0.85,
            t_ref_k=300.0,
            note="test",
        )
    )
    db_session.flush()
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="energy_transfer")
    ).json()
    et = body["record"]["energy_transfer"]
    assert et is not None
    assert len(et) == 1
    assert et[0]["model"] == "exponential_down"
    assert et[0]["alpha0_cm_inv"] == 300.0
    assert et[0]["t_exponent"] == 0.85
    assert et[0]["t_ref_k"] == 300.0


def test_solve_detail_include_source_calculations(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _solve_url(
            fx["solve"].public_ref, include="source_calculations"
        )
    ).json()
    src = body["record"]["source_calculations"]
    assert src is not None
    assert len(src) == 1
    assert src[0]["calculation_ref"] == fx["source_calculation"].public_ref
    assert src[0]["role"] == "fit_source"


def test_solve_detail_include_kinetics_chebyshev(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="kinetics")
    ).json()
    kn = body["record"]["kinetics"]
    assert kn is not None
    assert len(kn) == 1
    assert kn[0]["model_kind"] == "chebyshev"
    assert kn[0]["chebyshev_shape"] == "6x4"
    # No coefficient payload inline.
    assert "coefficients" not in kn[0]


def test_solve_detail_include_kinetics_plog(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="kinetics")
    ).json()
    kn = body["record"]["kinetics"]
    assert kn is not None
    assert kn[0]["plog_entry_count"] == 1
    # No PLOG row payload inline.
    assert "entries" not in kn[0]
    assert "plog_entries" not in kn[0]


def test_solve_detail_include_kinetics_point(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.tabulated
    )
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="kinetics")
    ).json()
    kn = body["record"]["kinetics"]
    assert kn is not None
    assert kn[0]["point_count"] == 1
    # No point payload inline.
    assert "points" not in kn[0]
    assert "kinetics_points" not in kn[0]


def test_solve_detail_include_review(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx["solve"].id,
        status=RecordReviewStatus.approved,
    )
    rh = client.get(
        _solve_url(fx["solve"].public_ref, include="review")
    ).json()["record"]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "approved"


def test_solve_detail_include_all_expands_all_public_tokens(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    inc = client.get(
        _solve_url(fx["solve"].public_ref, include="all")
    ).json()["request"]["include"]
    for token in (
        "bath_gas",
        "energy_transfer",
        "source_calculations",
        "kinetics",
        "review",
    ):
        assert token in inc
    assert "internal_ids" not in inc


def test_solve_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="all")
    ).json()
    assert "network_solve_id" not in body["record"]["network_solve"]


def test_solve_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="internal_ids")
    ).json()
    assert (
        body["record"]["network_solve"]["network_solve_id"] == fx["solve"].id
    )


def test_solve_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "network_solve_id" not in body["record"]["network_solve"]


def test_solve_detail_unknown_include_token_returns_422(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    resp = client.get(_solve_url(fx["solve"].public_ref, include="banana"))
    assert resp.status_code == 422


def test_solve_detail_rejected_record_still_returned_with_badge(
    client, db_session
):
    fx = _make_simple_network(db_session, with_solve=True)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx["solve"].id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_solve_url(fx["solve"].public_ref)).json()
    assert body["record"]["network_solve"]["review"]["status"] == "rejected"


def test_solve_detail_no_forbidden_payload_keys(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _solve_url(fx["solve"].public_ref, include="all")
    ).json()
    forbidden = {
        # Coefficient / point payloads
        "coefficients",
        "chebyshev_coefficients",
        "plog_entries",
        "entries",
        "points",
        "kinetics_points",
        # Geometry / artifact payloads
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"network-solve detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_solve_detail_kinetics_summary_matches_network_detail(
    client, db_session
):
    """Anti-drift: the kinetics summary on the solve detail surface
    matches the kinetics summary embedded under the network detail
    surface (both reuse the same NetworkKineticsSummary builder).
    """
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    solve_kn = client.get(
        _solve_url(fx["solve"].public_ref, include="kinetics")
    ).json()["record"]["kinetics"]
    network_kn = client.get(
        _detail_url(fx["network"].public_ref, include="kinetics")
    ).json()["record"]["kinetics"]
    # Both surfaces sort by NetworkKinetics.id ASC; with one record
    # apiece, the lists must be identical.
    assert solve_kn == network_kn


def test_solve_detail_source_calc_summary_matches_network_detail(
    client, db_session
):
    """The source calc summary on the solve surface is byte-identical
    to the matching subset on the network detail surface (both reuse
    `_build_source_calculations`)."""
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    solve_sc = client.get(
        _solve_url(fx["solve"].public_ref, include="source_calculations")
    ).json()["record"]["source_calculations"]
    network_sc = client.get(
        _detail_url(
            fx["network"].public_ref, include="source_calculations"
        )
    ).json()["record"]["source_calculations"]
    # Filter the network surface's source calc list to this solve.
    matching = [
        sc
        for sc in network_sc
        if sc["network_solve_ref"] == fx["solve"].public_ref
    ]
    assert solve_sc == matching


# ===========================================================================
# Network-solve search endpoint
# ===========================================================================


def _solve_search_url(**params) -> str:
    base = "/api/v1/scientific/network-solves/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def test_solve_search_missing_filter_returns_422_get(client, db_session):
    resp = client.get(_solve_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_solve_search_missing_filter_returns_422_post(client, db_session):
    resp = client.post(_solve_search_url(), json={"limit": 50})
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_solve_search_by_network_solve_ref(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_search_url(network_solve_ref=fx_a["solve"].public_ref)
    ).json()
    refs = {
        r["network_solve"]["network_solve_ref"] for r in body["records"]
    }
    assert refs == {fx_a["solve"].public_ref}


def test_solve_search_by_network_ref(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_search_url(network_ref=fx_a["network"].public_ref)
    ).json()
    refs = {
        r["network_solve"]["network_solve_ref"] for r in body["records"]
    }
    assert refs == {fx_a["solve"].public_ref}


def test_solve_search_by_solve_method(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    fx_a["solve"].me_method = "RRKM/ME"
    fx_b["solve"].me_method = "CSE"
    db_session.flush()
    body = client.get(_solve_search_url(solve_method="CSE")).json()
    refs = {
        r["network_solve"]["network_solve_ref"] for r in body["records"]
    }
    assert refs == {fx_b["solve"].public_ref}


def test_solve_search_by_temperature_range(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    # Default factory sets tmin/tmax = 300/2000; bump fx_b out of [200,500].
    fx_b["solve"].tmin_k = 3000.0
    fx_b["solve"].tmax_k = 4000.0
    db_session.flush()
    body = client.get(
        _solve_search_url(temperature_min=200.0, temperature_max=500.0)
    ).json()
    refs = {
        r["network_solve"]["network_solve_ref"] for r in body["records"]
    }
    assert fx_a["solve"].public_ref in refs
    assert fx_b["solve"].public_ref not in refs


def test_solve_search_by_pressure_range(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    fx_b["solve"].pmin_bar = 500.0
    fx_b["solve"].pmax_bar = 1000.0
    db_session.flush()
    body = client.get(
        _solve_search_url(pressure_min=0.1, pressure_max=10.0)
    ).json()
    refs = {
        r["network_solve"]["network_solve_ref"] for r in body["records"]
    }
    assert fx_a["solve"].public_ref in refs
    assert fx_b["solve"].public_ref not in refs


def test_solve_search_by_has_bath_gas_true_and_false(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    bath_sp = make_species(
        db_session, inchi_key=next_inchi_key("NSB")
    )
    bath_se = make_species_entry(db_session, bath_sp)
    attach_network_solve_bath_gas(
        db_session, solve=fx_a["solve"], species_entry=bath_se
    )
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_bath_gas="true")).json()[
            "records"
        ]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_bath_gas="false")).json()[
            "records"
        ]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_has_energy_transfer_true_and_false(
    client, db_session
):
    from app.db.models.network_pdep import NetworkSolveEnergyTransfer

    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    db_session.add(
        NetworkSolveEnergyTransfer(
            solve_id=fx_a["solve"].id,
            model="exponential_down",
            alpha0_cm_inv=300.0,
        )
    )
    db_session.flush()
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_energy_transfer="true")
        ).json()["records"]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_energy_transfer="false")
        ).json()["records"]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_has_source_calculations_true_and_false(
    client, db_session
):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(db_session, with_solve=True)
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_source_calculations="true")
        ).json()["records"]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_source_calculations="false")
        ).json()["records"]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_has_kinetics_true_and_false(client, db_session):
    fx_a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    fx_b = _make_simple_network(db_session, with_solve=True)
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_kinetics="true")).json()[
            "records"
        ]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_kinetics="false")).json()[
            "records"
        ]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_has_chebyshev_true_and_false(client, db_session):
    fx_a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    fx_b = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_chebyshev="true")).json()[
            "records"
        ]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_chebyshev="false")).json()[
            "records"
        ]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_has_plog_true_and_false(client, db_session):
    fx_a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.plog
    )
    fx_b = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_plog="true")).json()[
            "records"
        ]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_plog="false")).json()[
            "records"
        ]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_has_point_kinetics_true_and_false(client, db_session):
    fx_a = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.tabulated
    )
    fx_b = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    true_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_point_kinetics="true")
        ).json()["records"]
    }
    false_refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_point_kinetics="false")
        ).json()["records"]
    }
    assert fx_a["solve"].public_ref in true_refs
    assert fx_b["solve"].public_ref in false_refs


def test_solve_search_by_method_and_basis(client, db_session):
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
        source_calc_lot=lot_a,
    )
    _fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
        source_calc_lot=lot_b,
    )
    refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(method="wb97xd", basis="def2tzvp")
        ).json()["records"]
    }
    assert refs == {fx_a["solve"].public_ref}


def test_solve_search_by_software_and_version(client, db_session):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    sw_a = Software(name="gaussian")
    sw_b = Software(name="orca")
    db_session.add_all([sw_a, sw_b])
    db_session.flush()
    sr_a = SoftwareRelease(software_id=sw_a.id, version="g16.a03")
    sr_b = SoftwareRelease(software_id=sw_b.id, version="5.0.4")
    db_session.add_all([sr_a, sr_b])
    db_session.flush()
    fx_a["source_calculation"].software_release_id = sr_a.id
    fx_b["source_calculation"].software_release_id = sr_b.id
    db_session.flush()
    refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(software="gaussian", software_version="g16.a03")
        ).json()["records"]
    }
    assert refs == {fx_a["solve"].public_ref}


def test_solve_search_by_workflow_tool_and_version(client, db_session):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    wt_a = WorkflowTool(name="arc")
    wt_b = WorkflowTool(name="qcelemental")
    db_session.add_all([wt_a, wt_b])
    db_session.flush()
    wtr_a = WorkflowToolRelease(workflow_tool_id=wt_a.id, version="1.2.3")
    wtr_b = WorkflowToolRelease(workflow_tool_id=wt_b.id, version="0.27.0")
    db_session.add_all([wtr_a, wtr_b])
    db_session.flush()
    fx_a["source_calculation"].workflow_tool_release_id = wtr_a.id
    fx_b["source_calculation"].workflow_tool_release_id = wtr_b.id
    db_session.flush()
    refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(workflow_tool="arc", workflow_tool_version="1.2.3")
        ).json()["records"]
    }
    assert refs == {fx_a["solve"].public_ref}


def test_solve_search_default_hides_rejected(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx_b["solve"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = {
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_kinetics="false")).json()[
            "records"
        ]
    }
    assert fx_a["solve"].public_ref in refs
    assert fx_b["solve"].public_ref not in refs


def test_solve_search_include_rejected_sorts_them_last(client, db_session):
    fx_a = _make_simple_network(db_session, with_solve=True)
    fx_b = _make_simple_network(db_session, with_solve=True)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx_b["solve"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = [
        r["network_solve"]["network_solve_ref"]
        for r in client.get(
            _solve_search_url(has_kinetics="false", include_rejected="true")
        ).json()["records"]
    ]
    assert fx_a["solve"].public_ref in refs
    assert fx_b["solve"].public_ref in refs
    assert refs[-1] == fx_b["solve"].public_ref


def test_solve_search_pagination_envelope(client, db_session):
    fx = _make_simple_network(db_session)
    # Attach 4 solves to the same network so the network_ref filter
    # returns a stable candidate set.
    for _ in range(4):
        make_network_solve(db_session, network=fx["network"])
    body = client.get(
        _solve_search_url(
            network_ref=fx["network"].public_ref, limit=2, offset=0
        )
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_solve_search_deterministic_ordering_review_then_created(
    client, db_session
):
    fx_a = _make_simple_network(db_session, with_solve=True)
    _fx_b = _make_simple_network(db_session, with_solve=True)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx_a["solve"].id,
        status=RecordReviewStatus.approved,
    )
    refs = [
        r["network_solve"]["network_solve_ref"]
        for r in client.get(_solve_search_url(has_kinetics="false")).json()[
            "records"
        ]
    ]
    assert refs[0] == fx_a["solve"].public_ref


def test_solve_search_client_sort_rejected(client, db_session):
    _make_simple_network(db_session, with_solve=True)
    resp = client.get(
        _solve_search_url(has_kinetics="false", sort="created_at")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_solve_search_get_post_parity(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    get_body = client.get(
        _solve_search_url(network_solve_ref=fx["solve"].public_ref)
    ).json()
    post_body = client.post(
        _solve_search_url(),
        json={"network_solve_ref": fx["solve"].public_ref},
    ).json()
    assert get_body["pagination"] == post_body["pagination"]
    assert get_body["records"] == post_body["records"]


def test_solve_search_post_rejects_query_string_search_fields(
    client, db_session
):
    _make_simple_network(db_session, with_solve=True)
    resp = client.post(
        "/api/v1/scientific/network-solves/search?limit=5",
        json={"has_kinetics": True},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_solve_search_include_bath_gas_on_records(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    bath_sp = make_species(
        db_session, inchi_key=next_inchi_key("NSBG")
    )
    bath_se = make_species_entry(db_session, bath_sp)
    attach_network_solve_bath_gas(
        db_session, solve=fx["solve"], species_entry=bath_se
    )
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="bath_gas"
        )
    ).json()
    rec = body["records"][0]
    assert rec["bath_gas"] is not None
    assert len(rec["bath_gas"]) == 1


def test_solve_search_include_energy_transfer_on_records(client, db_session):
    from app.db.models.network_pdep import NetworkSolveEnergyTransfer

    fx = _make_simple_network(db_session, with_solve=True)
    db_session.add(
        NetworkSolveEnergyTransfer(
            solve_id=fx["solve"].id, model="exponential_down"
        )
    )
    db_session.flush()
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref,
            include="energy_transfer",
        )
    ).json()
    rec = body["records"][0]
    assert rec["energy_transfer"] is not None
    assert len(rec["energy_transfer"]) == 1


def test_solve_search_include_source_calculations_on_records(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref,
            include="source_calculations",
        )
    ).json()
    rec = body["records"][0]
    assert rec["source_calculations"] is not None
    assert len(rec["source_calculations"]) == 1


def test_solve_search_include_kinetics_on_records(client, db_session):
    fx = _make_simple_network(
        db_session, with_kinetics_kind=NetworkKineticsModelKind.chebyshev
    )
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="kinetics"
        )
    ).json()
    rec = body["records"][0]
    assert rec["kinetics"] is not None
    assert rec["kinetics"][0]["chebyshev_shape"] == "6x4"
    # Coefficients must not inline under search either.
    assert "coefficients" not in rec["kinetics"][0]


def test_solve_search_include_review_on_records(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx["solve"].id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="review"
        )
    ).json()
    rec = body["records"][0]
    assert rec["review_history"] is not None
    assert rec["review_history"][0]["status"] == "approved"


def test_solve_search_include_all_on_records(client, db_session):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="all"
        )
    ).json()
    inc = body["request"]["include"]
    for token in (
        "bath_gas",
        "energy_transfer",
        "source_calculations",
        "kinetics",
        "review",
    ):
        assert token in inc
    assert "internal_ids" not in inc


def test_solve_search_include_all_does_not_restore_internal_ids(
    client, db_session
):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="all"
        )
    ).json()
    assert "network_solve_id" not in body["records"][0]["network_solve"]


def test_solve_search_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    fx = _make_simple_network(db_session, with_solve=True)
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="internal_ids"
        )
    ).json()
    assert (
        body["records"][0]["network_solve"]["network_solve_id"]
        == fx["solve"].id
    )


def test_solve_search_record_shape_matches_detail(client, db_session):
    """Cross-endpoint anti-drift: same solve, same include set → identical."""
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    search_body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="all"
        )
    ).json()
    detail_body = client.get(
        _solve_url(fx["solve"].public_ref, include="all")
    ).json()
    assert search_body["records"][0] == detail_body["record"]


def test_solve_search_unknown_ref_short_circuits_empty(client, db_session):
    body = client.get(
        _solve_search_url(network_solve_ref="nsolve_doesnotexist00")
    ).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_solve_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(_solve_search_url(network_solve_ref="net_abcdef0123"))
    assert resp.status_code == 422


def test_solve_search_no_forbidden_payload_keys(client, db_session):
    fx = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _solve_search_url(
            network_solve_ref=fx["solve"].public_ref, include="all"
        )
    ).json()
    forbidden = {
        "coefficients",
        "chebyshev_coefficients",
        "plog_entries",
        "entries",
        "points",
        "kinetics_points",
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"network-solve search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ===========================================================================
# Network-kinetics standalone detail endpoint
# ===========================================================================


def _nkin_detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/network-kinetics/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _make_kinetics(db_session, model_kind, *, with_source_calc: bool = False):
    """Build a network + minimal solve/channel + one kinetics record of the
    requested ``model_kind``. Returns the same dict shape as
    ``_make_simple_network`` so call sites can reach refs.
    """
    return _make_simple_network(
        db_session,
        with_kinetics_kind=model_kind,
        with_source_calc=with_source_calc,
    )


def test_nkin_detail_by_ref_returns_record(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    kin = fx["kinetics"]
    body = client.get(_nkin_detail_url(kin.public_ref)).json()
    assert body["record"]["network_kinetics"]["network_kinetics_ref"] == (
        kin.public_ref
    )
    assert body["record"]["network_kinetics"]["model_kind"] == "chebyshev"


def test_nkin_detail_by_integer_id_works(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    resp = client.get(_nkin_detail_url(str(fx["kinetics"].id)))
    assert resp.status_code == 200


def test_nkin_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_nkin_detail_url("nkin_doesnotexist0000"))
    assert resp.status_code == 404
    assert "network_kinetics not found" in resp.text


def test_nkin_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_nkin_detail_url("nsolve_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_nkin_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_nkin_detail_url("not-a-handle"))
    assert resp.status_code == 422


def test_nkin_detail_default_response_shape(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    record = body["record"]
    for key in (
        "network_kinetics",
        "network",
        "network_solve",
        "network_channel",
        "evidence_summary",
        "available_sections",
    ):
        assert key in record
    # Heavy includes default to None.
    for key in (
        "coefficients",
        "plog",
        "points",
        "source_calculations",
        "review_history",
    ):
        assert record[key] is None


def test_nkin_detail_parent_network_context_present(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    assert body["record"]["network"]["network_ref"] == fx["network"].public_ref


def test_nkin_detail_parent_solve_context_present(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    assert body["record"]["network_solve"]["network_solve_ref"] == (
        fx["solve"].public_ref
    )


def test_nkin_detail_channel_context_present(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    channel_ctx = body["record"]["network_channel"]
    assert channel_ctx["channel_kind"] == "isomerization"
    assert (
        channel_ctx["source_state_composition_hash"]
        == fx["state_a"].composition_hash
    )
    assert (
        channel_ctx["sink_state_composition_hash"]
        == fx["state_b"].composition_hash
    )
    # No public_ref on NetworkChannel today.
    assert channel_ctx["network_channel_ref"] is None


def test_nkin_detail_evidence_summary_present(client, db_session):
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    ev = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()[
        "record"
    ]["evidence_summary"]
    assert ev["has_chebyshev_coefficients"] is True
    # factory builds a 6x4 zero matrix → 24 coefficients
    assert ev["chebyshev_coefficient_count"] == 24
    assert ev["has_plog_entries"] is False
    assert ev["plog_entry_count"] == 0
    assert ev["has_point_entries"] is False
    assert ev["point_count"] == 0
    assert ev["source_calculation_count"] == 1


def test_nkin_detail_available_sections_present(client, db_session):
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    av = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()[
        "record"
    ]["available_sections"]
    assert av["has_coefficients"] is True
    assert av["has_plog"] is False
    assert av["has_points"] is False
    assert av["has_source_calculations"] is True
    assert av["has_review"] is False


def test_nkin_detail_review_badge_present(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    # NetworkKinetics inherits the parent solve's badge; default solve has
    # no review row → not_reviewed.
    assert body["record"]["network_kinetics"]["review"]["status"] == (
        "not_reviewed"
    )


def test_nkin_detail_include_coefficients_chebyshev(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="coefficients")
    ).json()
    coeffs = body["record"]["coefficients"]
    assert coeffs is not None
    assert coeffs["n_temperature"] == 6
    assert coeffs["n_pressure"] == 4
    # Factory builds a 6x4 matrix → 24 coefficient rows.
    assert len(coeffs["coefficients"]) == 24
    assert coeffs["coefficient_count_total"] == 24
    assert coeffs["coefficients_truncated"] is False
    # First row has zero-order indices.
    first = coeffs["coefficients"][0]
    assert first["temperature_order"] == 0
    assert first["pressure_order"] == 0
    assert first["coefficient"] == 0.0


def test_nkin_detail_include_coefficients_non_chebyshev_is_none(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="coefficients")
    ).json()
    assert body["record"]["coefficients"] is None


def test_nkin_detail_include_plog(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="plog")
    ).json()
    record = body["record"]
    plog = record["plog"]
    assert isinstance(plog, list)
    assert len(plog) == 1
    assert plog[0]["pressure_bar"] == 1.0
    assert plog[0]["a"] == 1e12
    assert record["plog_entry_count_total"] == 1
    assert record["plog_entries_truncated"] is False


def test_nkin_detail_include_plog_non_plog_is_empty(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="plog")
    ).json()
    record = body["record"]
    assert record["plog"] == []
    assert record["plog_entry_count_total"] == 0
    assert record["plog_entries_truncated"] is False


def test_nkin_detail_plog_metadata_absent_when_plog_not_requested(
    client, db_session
):
    """PLOG sibling metadata follows the omittable-field pattern.

    With no ``include=plog`` the bare-list field and both sibling
    metadata fields stay at their unset (``None``) value, mirroring
    the points / coefficients defaults.
    """
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    record = body["record"]
    assert record["plog"] is None
    assert record["plog_entry_count_total"] is None
    assert record["plog_entries_truncated"] is None


def test_nkin_detail_include_points(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="points")
    ).json()
    rec = body["record"]
    assert rec["points"] is not None
    assert len(rec["points"]) == 1
    assert rec["points"][0]["temperature_k"] == 500.0
    assert rec["points"][0]["pressure_bar"] == 1.0
    assert rec["points"][0]["rate_value"] == 1.0
    assert rec["point_count_total"] == 1
    assert rec["points_truncated"] is False


def test_nkin_detail_include_points_capped(client, db_session, monkeypatch):
    """Point payload caps at settings.public_max_limit and signals truncation."""
    from app.api.config import settings as _settings
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_point,
    )

    # Build a tabulated kinetics record with several extra point rows.
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    kin = fx["kinetics"]
    for i in range(5):
        attach_network_kinetics_point(
            db_session,
            kinetics=kin,
            temperature_k=500.0 + (i + 1) * 10,
            pressure_bar=1.0 + (i + 1) * 0.1,
            rate_value=1.0 + i,
        )
    # Drop the cap to 2 so we exercise the truncation branch deterministically.
    monkeypatch.setattr(_settings, "public_max_limit", 2)
    body = client.get(
        _nkin_detail_url(kin.public_ref, include="points")
    ).json()
    rec = body["record"]
    assert len(rec["points"]) == 2
    assert rec["points_truncated"] is True
    assert rec["point_count_total"] == 6


def test_nkin_detail_include_coefficients_capped(
    client, db_session, monkeypatch
):
    """Chebyshev coefficient payload caps at public_max_limit and flags truncation."""
    from app.api.config import settings as _settings

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    # Default factory builds a 6x4 matrix → 24 coefficients.
    monkeypatch.setattr(_settings, "public_max_limit", 5)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="coefficients")
    ).json()
    coeffs = body["record"]["coefficients"]
    assert coeffs is not None
    assert len(coeffs["coefficients"]) == 5
    assert coeffs["coefficients_truncated"] is True
    assert coeffs["coefficient_count_total"] == 24
    # Deterministic order: first 5 rows are the (t,p) prefix
    # (0,0), (0,1), (0,2), (0,3), (1,0).
    indices = [(c["temperature_order"], c["pressure_order"]) for c in coeffs["coefficients"]]
    assert indices == [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0)]


def test_nkin_detail_include_plog_capped(client, db_session, monkeypatch):
    """PLOG entry payload caps at public_max_limit and flags truncation."""
    from app.api.config import settings as _settings
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_plog,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    kin = fx["kinetics"]
    # Factory already attaches one entry at pressure_bar=1.0; add four more
    # at distinct pressures to land six total PLOG rows.
    for i in range(4):
        attach_network_kinetics_plog(
            db_session,
            kinetics=kin,
            pressure_bar=2.0 + i,
            entry_index=1,
            a=1e12,
            n=0.0,
            ea_kj_mol=0.0,
        )
    monkeypatch.setattr(_settings, "public_max_limit", 3)
    body = client.get(
        _nkin_detail_url(kin.public_ref, include="plog")
    ).json()
    record = body["record"]
    plog = record["plog"]
    assert isinstance(plog, list)
    assert len(plog) == 3
    assert record["plog_entries_truncated"] is True
    assert record["plog_entry_count_total"] == 5
    # Deterministic ordering by (pressure_bar ASC, entry_index ASC).
    pressures = [e["pressure_bar"] for e in plog]
    assert pressures == sorted(pressures)


def test_nkin_detail_include_source_calculations(client, db_session):
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _nkin_detail_url(
            fx["kinetics"].public_ref, include="source_calculations"
        )
    ).json()
    sc = body["record"]["source_calculations"]
    assert sc is not None
    assert len(sc) == 1
    assert sc[0]["calculation_ref"] == fx["source_calculation"].public_ref
    assert sc[0]["role"] == "fit_source"


def test_nkin_detail_include_review_uses_parent_solve(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx["solve"].id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="review")
    ).json()
    rh = body["record"]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "approved"


def test_nkin_detail_include_all_expands_public_tokens_without_points(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    inc = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="all")
    ).json()["request"]["include"]
    for token in ("coefficients", "plog", "source_calculations", "review"):
        assert token in inc, f"include=all dropped public token {token!r}"
    # ``all`` must not silently restore internal_ids or pull in the
    # unbounded ``points`` payload.
    assert "internal_ids" not in inc
    assert "points" not in inc


def test_nkin_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="all")
    ).json()
    assert "network_kinetics_id" not in body["record"]["network_kinetics"]


def test_nkin_detail_include_all_does_not_include_points(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="all")
    ).json()
    # ``points`` must require explicit opt-in even with ``all``.
    assert body["record"]["points"] is None


def test_nkin_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="internal_ids")
    ).json()
    assert body["record"]["network_kinetics"]["network_kinetics_id"] == (
        fx["kinetics"].id
    )


def test_nkin_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "network_kinetics_id" not in body["record"]["network_kinetics"]


def test_nkin_detail_unknown_include_token_returns_422(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    resp = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="banana")
    )
    assert resp.status_code == 422


def test_nkin_detail_plog_ordering_deterministic(client, db_session):
    """PLOG rows return sorted by (pressure_bar ASC, entry_index ASC)."""
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_plog,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    kin = fx["kinetics"]
    attach_network_kinetics_plog(
        db_session, kinetics=kin, pressure_bar=10.0, entry_index=1,
        a=2e12, n=0.0, ea_kj_mol=0.0,
    )
    attach_network_kinetics_plog(
        db_session, kinetics=kin, pressure_bar=0.1, entry_index=1,
        a=3e12, n=0.0, ea_kj_mol=0.0,
    )
    body = client.get(
        _nkin_detail_url(kin.public_ref, include="plog")
    ).json()
    pressures = [
        row["pressure_bar"] for row in body["record"]["plog"]
    ]
    assert pressures == sorted(pressures)


def test_nkin_detail_point_ordering_deterministic(client, db_session):
    """Point rows return sorted by (temperature_k ASC, pressure_bar ASC)."""
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_point,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    kin = fx["kinetics"]
    attach_network_kinetics_point(
        db_session, kinetics=kin, temperature_k=800.0, pressure_bar=0.5,
        rate_value=2.0,
    )
    attach_network_kinetics_point(
        db_session, kinetics=kin, temperature_k=400.0, pressure_bar=5.0,
        rate_value=3.0,
    )
    body = client.get(
        _nkin_detail_url(kin.public_ref, include="points")
    ).json()
    temps = [r["temperature_k"] for r in body["record"]["points"]]
    assert temps == sorted(temps)


def test_nkin_detail_coefficient_ordering_deterministic(client, db_session):
    """Chebyshev coefficient rows return sorted by (temperature_order,
    pressure_order)."""
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="coefficients")
    ).json()
    indices = [
        (c["temperature_order"], c["pressure_order"])
        for c in body["record"]["coefficients"]["coefficients"]
    ]
    assert indices == sorted(indices)


def test_nkin_detail_source_calc_ordering_deterministic(client, db_session):
    """source_calculations sort by (role, calculation_id)."""
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    # Add a second source calculation under a different role.
    from tests.services.scientific_read._factories import (
        attach_network_solve_source_calculation,
        make_calculation,
    )

    second_calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=fx["species_entry"].id,
    )
    attach_network_solve_source_calculation(
        db_session,
        solve=fx["solve"],
        calculation=second_calc,
        role=NetworkSolveCalculationRole.well_energy,
    )
    # Ordering is deterministic — PostgreSQL enum sorts by declaration
    # order, so well_energy (declared before fit_source) comes first.
    body_a = client.get(
        _nkin_detail_url(
            fx["kinetics"].public_ref, include="source_calculations"
        )
    ).json()
    body_b = client.get(
        _nkin_detail_url(
            fx["kinetics"].public_ref, include="source_calculations"
        )
    ).json()
    roles_a = [row["role"] for row in body_a["record"]["source_calculations"]]
    roles_b = [row["role"] for row in body_b["record"]["source_calculations"]]
    assert roles_a == roles_b
    assert len(roles_a) == 2
    assert set(roles_a) == {"fit_source", "well_energy"}


def test_nkin_detail_rejected_parent_solve_returns_record_with_badge(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx["solve"].id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    # Detail endpoints don't filter by review state — record still returns
    # with the rejected badge.
    assert body["record"]["network_kinetics"]["review"]["status"] == (
        "rejected"
    )


def test_nkin_detail_no_forbidden_payload_keys(client, db_session):
    """Recursive forbidden-key walk over an ``include=all`` response.

    ``coefficients`` / ``plog`` / ``points`` are the *purpose* of this
    endpoint and are excluded from the forbidden list — they're tested
    individually for include-gating instead.
    """
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="all")
    ).json()
    forbidden = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"network-kinetics detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_nkin_detail_payload_keys_only_appear_when_requested(
    client, db_session
):
    """Coefficient/plog/point payloads must not leak into the default response."""
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    body = client.get(_nkin_detail_url(fx["kinetics"].public_ref)).json()
    rec = body["record"]
    for key in ("coefficients", "plog", "points"):
        assert rec[key] is None


# ---------------------------------------------------------------------------
# Cross-surface agreement: the standalone kinetics core's shape metadata
# must match the embedded NetworkKineticsSummary on the network and
# network-solve surfaces for the shared subset of fields.
# ---------------------------------------------------------------------------


def _shared_kinetics_fields(summary: dict) -> dict:
    """Project the fields the standalone core and the embedded summary
    agree on (the standalone core has extra unit fields)."""
    return {
        k: summary.get(k)
        for k in (
            "model_kind",
            "tmin_k",
            "tmax_k",
            "pmin_bar",
            "pmax_bar",
            "chebyshev_shape",
            "plog_entry_count",
            "point_count",
        )
    }


def test_nkin_detail_summary_matches_network_embedded(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    standalone = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref)
    ).json()["record"]["network_kinetics"]
    embedded = client.get(
        _detail_url(fx["network"].public_ref, include="kinetics")
    ).json()["record"]["kinetics"][0]
    assert _shared_kinetics_fields(standalone) == _shared_kinetics_fields(
        embedded
    )


def test_nkin_detail_summary_matches_solve_embedded(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    standalone = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref)
    ).json()["record"]["network_kinetics"]
    embedded = client.get(
        f"/api/v1/scientific/network-solves/{fx['solve'].public_ref}"
        "?include=kinetics"
    ).json()["record"]["kinetics"][0]
    assert _shared_kinetics_fields(standalone) == _shared_kinetics_fields(
        embedded
    )


# ===========================================================================
# Network-kinetics search endpoint
# ===========================================================================


def _nkin_search_url(**params) -> str:
    base = "/api/v1/scientific/network-kinetics/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _nkin_search_refs(body: dict) -> set[str]:
    return {
        r["network_kinetics"]["network_kinetics_ref"]
        for r in body["records"]
    }


def test_nkin_search_missing_filter_returns_422_get(client, db_session):
    resp = client.get(_nkin_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_nkin_search_missing_filter_returns_422_post(client, db_session):
    resp = client.post(_nkin_search_url(), json={"limit": 50})
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_nkin_search_by_network_kinetics_ref(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx_a["kinetics"].public_ref
        )
    ).json()
    assert _nkin_search_refs(body) == {fx_a["kinetics"].public_ref}


def test_nkin_search_by_network_ref(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(network_ref=fx_a["network"].public_ref)
    ).json()
    assert _nkin_search_refs(body) == {fx_a["kinetics"].public_ref}


def test_nkin_search_by_network_solve_ref(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(network_solve_ref=fx_a["solve"].public_ref)
    ).json()
    assert _nkin_search_refs(body) == {fx_a["kinetics"].public_ref}


def test_nkin_search_by_model_kind(client, db_session):
    fx_cheb = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_plog = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    fx_tab = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    for kind, want in (
        ("chebyshev", fx_cheb),
        ("plog", fx_plog),
        ("tabulated", fx_tab),
    ):
        refs = _nkin_search_refs(
            client.get(_nkin_search_url(model_kind=kind)).json()
        )
        assert want["kinetics"].public_ref in refs
        # And the other two are not returned for this kind.
        for other_kind, other in (
            ("chebyshev", fx_cheb),
            ("plog", fx_plog),
            ("tabulated", fx_tab),
        ):
            if other_kind == kind:
                continue
            assert other["kinetics"].public_ref not in refs


def test_nkin_search_by_temperature_range(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    # Default factory sets tmin/tmax = 300/2000 on the kinetics row.
    fx_b["kinetics"].tmin_k = 3000.0
    fx_b["kinetics"].tmax_k = 4000.0
    db_session.flush()
    body = client.get(
        _nkin_search_url(temperature_min=200.0, temperature_max=500.0)
    ).json()
    refs = _nkin_search_refs(body)
    assert fx_a["kinetics"].public_ref in refs
    assert fx_b["kinetics"].public_ref not in refs


def test_nkin_search_by_pressure_range(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_b["kinetics"].pmin_bar = 500.0
    fx_b["kinetics"].pmax_bar = 1000.0
    db_session.flush()
    body = client.get(
        _nkin_search_url(pressure_min=0.1, pressure_max=10.0)
    ).json()
    refs = _nkin_search_refs(body)
    assert fx_a["kinetics"].public_ref in refs
    assert fx_b["kinetics"].public_ref not in refs


def test_nkin_search_has_chebyshev_true_and_false(client, db_session):
    a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    b = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    true_refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_chebyshev="true")).json()
    )
    false_refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_chebyshev="false")).json()
    )
    assert a["kinetics"].public_ref in true_refs
    assert b["kinetics"].public_ref in false_refs


def test_nkin_search_has_plog_true_and_false(client, db_session):
    a = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    true_refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_plog="true")).json()
    )
    false_refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_plog="false")).json()
    )
    assert a["kinetics"].public_ref in true_refs
    assert b["kinetics"].public_ref in false_refs


def test_nkin_search_has_points_true_and_false(client, db_session):
    a = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    true_refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_points="true")).json()
    )
    false_refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_points="false")).json()
    )
    assert a["kinetics"].public_ref in true_refs
    assert b["kinetics"].public_ref in false_refs


def test_nkin_search_has_source_calculations_true_and_false(client, db_session):
    a = _make_kinetics(
        db_session, NetworkKineticsModelKind.chebyshev, with_source_calc=True
    )
    b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    true_refs = _nkin_search_refs(
        client.get(
            _nkin_search_url(has_source_calculations="true")
        ).json()
    )
    false_refs = _nkin_search_refs(
        client.get(
            _nkin_search_url(has_source_calculations="false")
        ).json()
    )
    assert a["kinetics"].public_ref in true_refs
    assert b["kinetics"].public_ref in false_refs


def test_nkin_search_by_method_basis(client, db_session):
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
        source_calc_lot=lot_a,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
        source_calc_lot=lot_b,
    )
    refs = _nkin_search_refs(
        client.get(
            _nkin_search_url(method="wb97xd", basis="def2tzvp")
        ).json()
    )
    assert refs == {fx_a["kinetics"].public_ref}
    assert fx_b["kinetics"].public_ref not in refs


def test_nkin_search_by_software_and_version(client, db_session):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    sw_a = Software(name="gaussian")
    sw_b = Software(name="orca")
    db_session.add_all([sw_a, sw_b])
    db_session.flush()
    sr_a = SoftwareRelease(software_id=sw_a.id, version="g16.a03")
    sr_b = SoftwareRelease(software_id=sw_b.id, version="5.0.4")
    db_session.add_all([sr_a, sr_b])
    db_session.flush()
    fx_a["source_calculation"].software_release_id = sr_a.id
    fx_b["source_calculation"].software_release_id = sr_b.id
    db_session.flush()
    refs = _nkin_search_refs(
        client.get(
            _nkin_search_url(software="gaussian", software_version="g16.a03")
        ).json()
    )
    assert refs == {fx_a["kinetics"].public_ref}


def test_nkin_search_by_workflow_tool_and_version(client, db_session):
    fx_a = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    fx_b = _make_simple_network(
        db_session,
        with_kinetics_kind=NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    wt_a = WorkflowTool(name="arc")
    wt_b = WorkflowTool(name="qcelemental")
    db_session.add_all([wt_a, wt_b])
    db_session.flush()
    wtr_a = WorkflowToolRelease(workflow_tool_id=wt_a.id, version="1.2.3")
    wtr_b = WorkflowToolRelease(workflow_tool_id=wt_b.id, version="0.27.0")
    db_session.add_all([wtr_a, wtr_b])
    db_session.flush()
    fx_a["source_calculation"].workflow_tool_release_id = wtr_a.id
    fx_b["source_calculation"].workflow_tool_release_id = wtr_b.id
    db_session.flush()
    refs = _nkin_search_refs(
        client.get(
            _nkin_search_url(
                workflow_tool="arc", workflow_tool_version="1.2.3"
            )
        ).json()
    )
    assert refs == {fx_a["kinetics"].public_ref}


def test_nkin_search_default_hides_rejected_parent_solve(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx_b["solve"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = _nkin_search_refs(
        client.get(_nkin_search_url(has_chebyshev="true")).json()
    )
    assert fx_a["kinetics"].public_ref in refs
    assert fx_b["kinetics"].public_ref not in refs


def test_nkin_search_include_rejected_sorts_them_last(client, db_session):
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx_b["solve"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = [
        r["network_kinetics"]["network_kinetics_ref"]
        for r in client.get(
            _nkin_search_url(
                has_chebyshev="true", include_rejected="true"
            )
        ).json()["records"]
    ]
    assert fx_a["kinetics"].public_ref in refs
    assert fx_b["kinetics"].public_ref in refs
    assert refs[-1] == fx_b["kinetics"].public_ref


def test_nkin_search_pagination_envelope(client, db_session):
    """Build 4 kinetics records under one network/solve to exercise paging."""
    from tests.services.scientific_read._factories import (
        make_network_kinetics,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    for _ in range(3):
        make_network_kinetics(
            db_session,
            channel=fx["channel"],
            solve=fx["solve"],
            model_kind=NetworkKineticsModelKind.chebyshev,
        )
    body = client.get(
        _nkin_search_url(
            network_ref=fx["network"].public_ref, limit=2, offset=0
        )
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_nkin_search_deterministic_ordering(client, db_session):
    """Same inputs return same record ordering across calls."""
    fx_a = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_b = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    fx_c = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    _ = (fx_a, fx_b, fx_c)
    r1 = [
        r["network_kinetics"]["network_kinetics_ref"]
        for r in client.get(_nkin_search_url(has_chebyshev="true")).json()[
            "records"
        ]
    ]
    r2 = [
        r["network_kinetics"]["network_kinetics_ref"]
        for r in client.get(_nkin_search_url(has_chebyshev="true")).json()[
            "records"
        ]
    ]
    assert r1 == r2


def test_nkin_search_rejects_client_sort(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    _ = fx
    resp = client.get(
        _nkin_search_url(has_chebyshev="true", sort="created_at")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_nkin_search_get_post_parity(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    get_body = client.get(
        _nkin_search_url(network_kinetics_ref=fx["kinetics"].public_ref)
    ).json()
    post_body = client.post(
        _nkin_search_url(),
        json={"network_kinetics_ref": fx["kinetics"].public_ref},
    ).json()
    assert _nkin_search_refs(get_body) == _nkin_search_refs(post_body)


def test_nkin_search_post_rejects_query_string_fields(client, db_session):
    """POST must not accept search filter fields as query-string keys."""
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    resp = client.post(
        f"{_nkin_search_url()}?has_chebyshev=true",
        json={"network_kinetics_ref": fx["kinetics"].public_ref},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_nkin_search_include_coefficients(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref,
            include="coefficients",
        )
    ).json()
    rec = body["records"][0]
    assert rec["coefficients"] is not None
    assert rec["coefficients"]["n_temperature"] == 6
    assert rec["coefficients"]["n_pressure"] == 4


def test_nkin_search_include_plog(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref, include="plog"
        )
    ).json()
    record = body["records"][0]
    plog = record["plog"]
    assert isinstance(plog, list)
    assert len(plog) == 1
    assert record["plog_entry_count_total"] == 1
    assert record["plog_entries_truncated"] is False


def test_nkin_search_include_points(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref,
            include="points",
        )
    ).json()
    rec = body["records"][0]
    assert rec["points"] is not None
    assert len(rec["points"]) == 1


def test_nkin_search_include_source_calculations(client, db_session):
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref,
            include="source_calculations",
        )
    ).json()
    sc = body["records"][0]["source_calculations"]
    assert sc is not None
    assert sc[0]["calculation_ref"] == fx["source_calculation"].public_ref


def test_nkin_search_include_review(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=fx["solve"].id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref,
            include="review",
        )
    ).json()
    rh = body["records"][0]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "approved"


def test_nkin_search_include_all_expands_public_tokens_without_points(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref, include="all"
        )
    ).json()
    inc = body["request"]["include"]
    for token in ("coefficients", "plog", "source_calculations", "review"):
        assert token in inc, f"include=all dropped public token {token!r}"
    assert "points" not in inc
    assert "internal_ids" not in inc


def test_nkin_search_include_all_does_not_include_points(client, db_session):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref, include="all"
        )
    ).json()
    assert body["records"][0]["points"] is None


def test_nkin_search_include_all_does_not_restore_internal_ids(
    client, db_session
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref, include="all"
        )
    ).json()
    assert "network_kinetics_id" not in body["records"][0]["network_kinetics"]


def test_nkin_search_include_all_internal_ids_obeys_policy(
    client, db_session, allow_internal_ids
):
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref,
            include="all,internal_ids",
        )
    ).json()
    assert body["records"][0]["network_kinetics"]["network_kinetics_id"] == (
        fx["kinetics"].id
    )


def test_nkin_search_include_points_capped_in_search_records(
    client, db_session, monkeypatch
):
    """Search records honor the same point cap + truncation flag as detail."""
    from app.api.config import settings as _settings
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_point,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.tabulated)
    kin = fx["kinetics"]
    for i in range(5):
        attach_network_kinetics_point(
            db_session,
            kinetics=kin,
            temperature_k=500.0 + (i + 1) * 10,
            pressure_bar=1.0 + (i + 1) * 0.1,
            rate_value=1.0 + i,
        )
    # ``public_max_limit`` doubles as the page-limit cap and the
    # points-payload cap — pass ``limit=2`` to satisfy the page-limit
    # guard once the cap is lowered.
    monkeypatch.setattr(_settings, "public_max_limit", 2)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=kin.public_ref,
            include="points",
            limit=2,
        )
    ).json()
    rec = body["records"][0]
    assert len(rec["points"]) == 2
    assert rec["points_truncated"] is True
    assert rec["point_count_total"] == 6


def test_nkin_search_include_coefficients_capped_in_search_records(
    client, db_session, monkeypatch
):
    """Search records cap Chebyshev coefficients the same way detail does."""
    from app.api.config import settings as _settings

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    monkeypatch.setattr(_settings, "public_max_limit", 4)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref,
            include="coefficients",
            limit=4,
        )
    ).json()
    coeffs = body["records"][0]["coefficients"]
    assert len(coeffs["coefficients"]) == 4
    assert coeffs["coefficients_truncated"] is True
    assert coeffs["coefficient_count_total"] == 24


def test_nkin_search_include_plog_capped_in_search_records(
    client, db_session, monkeypatch
):
    """Search records cap PLOG entries the same way detail does."""
    from app.api.config import settings as _settings
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_plog,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.plog)
    kin = fx["kinetics"]
    for i in range(4):
        attach_network_kinetics_plog(
            db_session,
            kinetics=kin,
            pressure_bar=2.0 + i,
            entry_index=1,
            a=1e12,
            n=0.0,
            ea_kj_mol=0.0,
        )
    monkeypatch.setattr(_settings, "public_max_limit", 2)
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=kin.public_ref,
            include="plog",
            limit=2,
        )
    ).json()
    record = body["records"][0]
    plog = record["plog"]
    assert isinstance(plog, list)
    assert len(plog) == 2
    assert record["plog_entries_truncated"] is True
    assert record["plog_entry_count_total"] == 5


def test_nkin_detail_include_all_includes_capped_coefficients_and_plog(
    client, db_session, monkeypatch
):
    """``include=all`` includes capped Chebyshev/PLOG payloads but still
    excludes points (which require explicit opt-in)."""
    from app.api.config import settings as _settings
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_plog,
        attach_network_kinetics_point,
    )

    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    kin = fx["kinetics"]
    # Add an extra PLOG row and a tabulated point so all three payload
    # tables have content. (Mixed-kind kinetics rows are unusual but
    # the schema allows it and the include filter is the right gate.)
    attach_network_kinetics_plog(
        db_session, kinetics=kin, pressure_bar=2.0, entry_index=1,
        a=1e12, n=0.0, ea_kj_mol=0.0,
    )
    attach_network_kinetics_point(
        db_session, kinetics=kin,
        temperature_k=500.0, pressure_bar=1.0, rate_value=1.0,
    )
    monkeypatch.setattr(_settings, "public_max_limit", 3)
    body = client.get(
        _nkin_detail_url(kin.public_ref, include="all")
    ).json()
    rec = body["record"]
    # Coefficients capped + truncation flag set (24 > 3).
    assert rec["coefficients"]["coefficients_truncated"] is True
    assert rec["coefficients"]["coefficient_count_total"] == 24
    assert len(rec["coefficients"]["coefficients"]) == 3
    # PLOG present (bare list) + sibling truncation metadata; only the
    # 1 entry we attached, so not truncated (the Chebyshev factory
    # doesn't seed PLOG rows).
    assert isinstance(rec["plog"], list)
    assert len(rec["plog"]) == 1
    assert rec["plog_entries_truncated"] is False
    assert rec["plog_entry_count_total"] == 1
    # Points excluded from ``include=all``; require explicit opt-in.
    assert rec["points"] is None
    assert rec["points_truncated"] is None
    assert rec["point_count_total"] is None


def test_nkin_search_record_matches_detail_for_same_kinetics(
    client, db_session
):
    """Search and detail return byte-identical per-record payloads for
    the same kinetics record and same include set."""
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    detail = client.get(
        _nkin_detail_url(fx["kinetics"].public_ref, include="all")
    ).json()["record"]
    search = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref, include="all"
        )
    ).json()["records"][0]
    assert detail == search


def test_nkin_search_unknown_ref_short_circuits_to_empty(client, db_session):
    """An unknown kinetics ref returns 200 with an empty result set."""
    body = client.get(
        _nkin_search_url(network_kinetics_ref="nkin_doesnotexist00000000")
    ).json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_nkin_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(
        _nkin_search_url(network_kinetics_ref="nsolve_abcdef0123")
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_nkin_search_no_forbidden_payload_keys(client, db_session):
    """Recursive forbidden-key walk over an ``include=all`` response.

    The model-specific payloads (`coefficients`, `plog`, `points`)
    are this endpoint's purpose under explicit includes — excluded
    from the forbidden list and tested separately for include-gating.
    """
    fx = _make_kinetics(
        db_session,
        NetworkKineticsModelKind.chebyshev,
        with_source_calc=True,
    )
    body = client.get(
        _nkin_search_url(
            network_kinetics_ref=fx["kinetics"].public_ref, include="all"
        )
    ).json()
    forbidden = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"network-kinetics search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_nkin_search_default_does_not_inline_payloads(client, db_session):
    """Without include tokens, payload sections must not appear in records."""
    fx = _make_kinetics(db_session, NetworkKineticsModelKind.chebyshev)
    body = client.get(
        _nkin_search_url(network_kinetics_ref=fx["kinetics"].public_ref)
    ).json()
    rec = body["records"][0]
    for key in ("coefficients", "plog", "points"):
        assert rec[key] is None
