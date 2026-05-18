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

    sp = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("NA"))
    se = make_species_entry(db_session, sp)
    out["species"] = sp
    out["species_entry"] = se
    if with_species:
        attach_network_species(
            db_session, network=n, species_entry=se, role=NetworkSpeciesRole.well
        )

    if with_reaction:
        sp2 = make_species(
            db_session, smiles="O", inchi_key=next_inchi_key("NB")
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
    fx_b = _make_simple_network(
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
        make_species(db_session, smiles="N", inchi_key=next_inchi_key("NX")),
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
    b = _make_simple_network(db_session)
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
