"""API tests for GET /api/v1/scientific/meta/* (Phase 7 vocabulary reads).

``/meta/software`` and ``/meta/workflow-tools`` are derived from
*calculations that actually cite them*, not from the ``software`` /
``workflow_tool`` registry tables. A row registered but never attributed
to any calculation (the ``Arkane`` bug this module guards against) must
not appear. ``count`` on both is the number of attributing calculations,
and an optional ``record_kind`` narrows to calculations owned by a
``species`` or ``transition_state`` record.
"""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_lot,
    make_reaction_entry,
    make_software,
    make_software_release,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    make_workflow_tool_release,
    next_inchi_key,
    unique_smiles,
)


def _species_entry(db_session, *, prefix: str):
    return make_species_entry(
        db_session,
        make_species(
            db_session,
            smiles=unique_smiles(),
            inchi_key=next_inchi_key(prefix),
        ),
    )


def _transition_state_entry(db_session, *, prefix: str):
    """Build the minimal reaction chain needed for a TransitionStateEntry.

    A ``calculation.transition_state_entry_id`` FK needs a real row, which
    needs a ``TransitionState`` on a ``ReactionEntry`` on a
    ``ChemReaction`` with at least one reactant and one product — there is
    no shortcut past that chain.
    """
    reactant = _species_entry(db_session, prefix=f"{prefix}R")
    product = _species_entry(db_session, prefix=f"{prefix}P")
    reaction = make_chem_reaction(
        db_session,
        reactants=[reactant.species],
        products=[product.species],
        reversible=False,
    )
    entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[reactant],
        product_entries=[product],
    )
    ts = make_transition_state(db_session, reaction_entry=entry)
    return make_transition_state_entry(db_session, transition_state=ts)


def test_methods_lists_distinct_with_counts(client, db_session):
    make_lot(db_session, method="CCSD(T)", basis="cc-pVTZ")
    make_lot(db_session, method="B3LYP", basis="6-31G(d)")

    body = client.get("/api/v1/scientific/meta/methods").json()

    methods = {r["value"] for r in body["results"]}
    assert {"CCSD(T)", "B3LYP"} <= methods
    for r in body["results"]:
        assert r["count"] >= 1


def test_basis_sets_lists_distinct(client, db_session):
    make_lot(db_session, method="B3LYP", basis="6-311+G(3df,2p)")
    body = client.get("/api/v1/scientific/meta/basis-sets").json()
    assert "6-311+G(3df,2p)" in {r["value"] for r in body["results"]}


def test_software_absent_when_no_calculation_uses_it(client, db_session):
    """The Arkane case: a registered software with zero attributing
    calculations must not appear. A registry-backed implementation (the
    prior behavior) would show it regardless."""
    make_software(db_session, name="UnusedPackageXYZ")

    body = client.get("/api/v1/scientific/meta/software").json()

    assert "UnusedPackageXYZ" not in {r["value"] for r in body["results"]}


def test_software_present_with_calculation_count(client, db_session):
    release = make_software_release(db_session, name="UsedPackageXYZ", version="1.0")
    se = _species_entry(db_session, prefix="SWUC")
    make_calculation(
        db_session,
        species_entry_id=se.id,
        software_release_id=release.id,
    )

    body = client.get("/api/v1/scientific/meta/software").json()

    by_value = {r["value"]: r["count"] for r in body["results"]}
    assert by_value["UsedPackageXYZ"] == 1


def test_software_counts_distinguish_different_usage_counts(client, db_session):
    """Two software with genuinely different counts — equal counts across
    the board cannot tell a correct count apart from a constant."""
    heavy = make_software_release(db_session, name="HeavyUseXYZ", version="1.0")
    light = make_software_release(db_session, name="LightUseXYZ", version="1.0")
    for _ in range(3):
        se = _species_entry(db_session, prefix="SWHV")
        make_calculation(
            db_session, species_entry_id=se.id, software_release_id=heavy.id
        )
    se = _species_entry(db_session, prefix="SWLT")
    make_calculation(db_session, species_entry_id=se.id, software_release_id=light.id)

    body = client.get("/api/v1/scientific/meta/software").json()
    by_value = {r["value"]: r["count"] for r in body["results"]}

    assert by_value["HeavyUseXYZ"] == 3
    assert by_value["LightUseXYZ"] == 1


def test_software_record_kind_scopes_species_and_transition_state(client, db_session):
    """A package used ONLY on species calculations and one used ONLY on
    transition-state calculations, distinguished both directions — a
    fixture where both kinds share every package could not tell a working
    scope apart from an ignored parameter."""
    species_only = make_software_release(
        db_session, name="SpeciesOnlyXYZ", version="1.0"
    )
    ts_only = make_software_release(db_session, name="TsOnlyXYZ", version="1.0")

    se = _species_entry(db_session, prefix="SWSK")
    make_calculation(
        db_session, species_entry_id=se.id, software_release_id=species_only.id
    )
    tse = _transition_state_entry(db_session, prefix="SWTK")
    make_calculation(
        db_session,
        transition_state_entry_id=tse.id,
        software_release_id=ts_only.id,
    )

    species_scoped = client.get(
        "/api/v1/scientific/meta/software", params={"record_kind": "species"}
    ).json()
    ts_scoped = client.get(
        "/api/v1/scientific/meta/software",
        params={"record_kind": "transition_state"},
    ).json()

    species_values = {r["value"] for r in species_scoped["results"]}
    ts_values = {r["value"] for r in ts_scoped["results"]}

    assert "SpeciesOnlyXYZ" in species_values
    assert "TsOnlyXYZ" not in species_values

    assert "TsOnlyXYZ" in ts_values
    assert "SpeciesOnlyXYZ" not in ts_values


def test_workflow_tools_absent_when_no_calculation_uses_it(client, db_session):
    make_workflow_tool_release(db_session, name="unused-tool-xyz", version="1.0.0")

    body = client.get("/api/v1/scientific/meta/workflow-tools").json()

    assert "unused-tool-xyz" not in {r["value"] for r in body["results"]}


def test_workflow_tools_lists_only_used_with_counts(client, db_session):
    """The full result set restricted to used tools, not a contains-check.

    A query that returned every registered row (or a hard-coded static
    list) would also satisfy a mere ``in`` assertion; asserting the exact
    set is what actually exercises the join through ``calculation``.
    """
    arc = make_workflow_tool_release(db_session, name="arc-used-xyz", version="1.2.3")
    make_workflow_tool_release(db_session, name="rmg-unused-xyz", version="3.1.0")

    se = _species_entry(db_session, prefix="WTUC")
    make_calculation(
        db_session, species_entry_id=se.id, workflow_tool_release_id=arc.id
    )

    body = client.get("/api/v1/scientific/meta/workflow-tools").json()
    ours = {
        (r["value"], r["count"])
        for r in body["results"]
        if r["value"] in {"arc-used-xyz", "rmg-unused-xyz"}
    }

    assert ours == {("arc-used-xyz", 1)}


def test_workflow_tools_record_kind_scopes_species_and_transition_state(
    client, db_session
):
    species_only = make_workflow_tool_release(
        db_session, name="species-only-tool-xyz", version="1.0"
    )
    ts_only = make_workflow_tool_release(
        db_session, name="ts-only-tool-xyz", version="1.0"
    )

    se = _species_entry(db_session, prefix="WTSK")
    make_calculation(
        db_session, species_entry_id=se.id, workflow_tool_release_id=species_only.id
    )
    tse = _transition_state_entry(db_session, prefix="WTTK")
    make_calculation(
        db_session,
        transition_state_entry_id=tse.id,
        workflow_tool_release_id=ts_only.id,
    )

    species_scoped = client.get(
        "/api/v1/scientific/meta/workflow-tools", params={"record_kind": "species"}
    ).json()
    ts_scoped = client.get(
        "/api/v1/scientific/meta/workflow-tools",
        params={"record_kind": "transition_state"},
    ).json()

    species_values = {r["value"] for r in species_scoped["results"]}
    ts_values = {r["value"] for r in ts_scoped["results"]}

    assert "species-only-tool-xyz" in species_values
    assert "ts-only-tool-xyz" not in species_values

    assert "ts-only-tool-xyz" in ts_values
    assert "species-only-tool-xyz" not in ts_values


def test_reaction_families_lists_canonical_vocabulary(client, db_session):
    # The seeded canonical families are always present (count >= 0).
    body = client.get("/api/v1/scientific/meta/reaction-families").json()
    assert isinstance(body["results"], list)
    assert len(body["results"]) >= 1
    assert all("value" in r and "count" in r for r in body["results"])


def test_software_versions_returns_only_that_softwares_versions(client, db_session):
    """A ``software=`` filter that is accepted and ignored is the classic
    vacuous pass this endpoint exists to prevent: seed two software rows
    with different versions and confirm the other software's version is
    absent from a scoped response, not merely that the requested one is
    present."""
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    orca = make_software_release(db_session, name="orca", version="5.0")
    se1 = _species_entry(db_session, prefix="SVG1")
    se2 = _species_entry(db_session, prefix="SVG2")
    make_calculation(
        db_session, species_entry_id=se1.id, software_release_id=gaussian.id
    )
    make_calculation(
        db_session, species_entry_id=se2.id, software_release_id=orca.id
    )

    body = client.get(
        "/api/v1/scientific/meta/software-versions", params={"software": "gaussian"}
    ).json()

    assert {r["value"] for r in body["results"]} == {"16"}


def test_software_versions_excludes_release_with_no_calculations(client, db_session):
    """A version with no attributing calculation must not be offered —
    same rule as the top-level ``/meta/software`` list, one level down."""
    used = make_software_release(db_session, name="gaussian", version="16")
    make_software_release(db_session, name="gaussian", version="09")
    se = _species_entry(db_session, prefix="SVUN")
    make_calculation(db_session, species_entry_id=se.id, software_release_id=used.id)

    body = client.get(
        "/api/v1/scientific/meta/software-versions", params={"software": "gaussian"}
    ).json()

    assert {r["value"] for r in body["results"]} == {"16"}


def test_software_versions_missing_parent_is_a_coded_refusal(client, db_session):
    """No ``software=`` is refused with a coded 422, not a bare FastAPI
    validation error and not an unscoped dump of every package's
    versions."""
    resp = client.get("/api/v1/scientific/meta/software-versions")

    assert resp.status_code == 422
    assert resp.json()["code"] == "missing_version_parent"


def test_software_versions_unknown_parent_is_empty_not_an_error(client, db_session):
    release = make_software_release(db_session, name="gaussian", version="16")
    se = _species_entry(db_session, prefix="SVKP")
    make_calculation(db_session, species_entry_id=se.id, software_release_id=release.id)

    resp = client.get(
        "/api/v1/scientific/meta/software-versions",
        params={"software": "no-such-package"},
    )

    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_software_versions_counts_distinguish_release_row_counts(client, db_session):
    """Two values with genuinely different counts — equal counts across
    the board cannot tell a correct count apart from a constant."""
    r16a = make_software_release(
        db_session, name="gaussian", version="16", revision="a"
    )
    r16b = make_software_release(
        db_session, name="gaussian", version="16", revision="b"
    )
    r17 = make_software_release(db_session, name="gaussian", version="17")
    for release in (r16a, r16b, r17):
        se = _species_entry(db_session, prefix="SVCR")
        make_calculation(
            db_session, species_entry_id=se.id, software_release_id=release.id
        )

    body = client.get(
        "/api/v1/scientific/meta/software-versions", params={"software": "gaussian"}
    ).json()

    assert {(r["value"], r["count"]) for r in body["results"]} == {
        ("16", 2),
        ("17", 1),
    }


def test_workflow_tool_versions_returns_only_that_tools_versions(client, db_session):
    arc = make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    rmg = make_workflow_tool_release(db_session, name="rmg", version="3.1.0")
    se1 = _species_entry(db_session, prefix="WVR1")
    se2 = _species_entry(db_session, prefix="WVR2")
    make_calculation(
        db_session, species_entry_id=se1.id, workflow_tool_release_id=arc.id
    )
    make_calculation(
        db_session, species_entry_id=se2.id, workflow_tool_release_id=rmg.id
    )

    body = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "arc"},
    ).json()

    assert {r["value"] for r in body["results"]} == {"1.2.3"}


def test_workflow_tool_versions_excludes_release_with_no_calculations(
    client, db_session
):
    used = make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    make_workflow_tool_release(db_session, name="arc", version="1.3.0")
    se = _species_entry(db_session, prefix="WVUN")
    make_calculation(
        db_session, species_entry_id=se.id, workflow_tool_release_id=used.id
    )

    body = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "arc"},
    ).json()

    assert {r["value"] for r in body["results"]} == {"1.2.3"}


def test_workflow_tool_versions_missing_parent_is_a_coded_refusal(client, db_session):
    resp = client.get("/api/v1/scientific/meta/workflow-tool-versions")

    assert resp.status_code == 422
    assert resp.json()["code"] == "missing_version_parent"


def test_workflow_tool_versions_unknown_parent_is_empty_not_an_error(
    client, db_session
):
    release = make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    se = _species_entry(db_session, prefix="WVKP")
    make_calculation(
        db_session, species_entry_id=se.id, workflow_tool_release_id=release.id
    )

    resp = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "no-such-tool"},
    )

    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_workflow_tool_versions_counts_distinguish_release_row_counts(
    client, db_session
):
    r1a = make_workflow_tool_release(
        db_session, name="arc", version="1.2.3", git_commit="a" * 40
    )
    r1b = make_workflow_tool_release(
        db_session, name="arc", version="1.2.3", git_commit="b" * 40
    )
    r2 = make_workflow_tool_release(db_session, name="arc", version="1.3.0")
    for release in (r1a, r1b, r2):
        se = _species_entry(db_session, prefix="WVCR")
        make_calculation(
            db_session, species_entry_id=se.id, workflow_tool_release_id=release.id
        )

    body = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "arc"},
    ).json()

    assert {(r["value"], r["count"]) for r in body["results"]} == {
        ("1.2.3", 2),
        ("1.3.0", 1),
    }


def test_reaction_families_carry_a_readable_display_name(client, db_session):
    """``value`` stays the filter token; ``display_name`` is the readable form.

    A family whose meaning is unresolved keeps its identifier as its display
    name rather than being shown half-translated.
    """
    body = client.get("/api/v1/scientific/meta/reaction-families").json()
    by_value = {r["value"]: r["display_name"] for r in body["results"]}

    assert by_value["H_Abstraction"] == "Hydrogen Abstraction"
    assert by_value["R_Addition_MultipleBond"] == "Radical Addition Multiple Bond"
    assert by_value["Surface_Adsorption_Bidentate"] == "Surface Adsorption Bidentate"
    assert (
        by_value["Surface_Carbonate_2F_Decomposition"]
        == "Surface_Carbonate_2F_Decomposition"
    )
    assert all(r["display_name"] for r in body["results"])
