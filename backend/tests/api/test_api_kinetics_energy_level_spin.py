"""A kinetics energy level names method, basis and qualifiers; spin treatment is judged per participant.

Spin treatment stays in level identity, so restricted and unrestricted B3LYP/def2-TZVP are two level rows.
A kinetics declaration is compared with each linked reactant, product and TS energy as follows:

1. Every link must match the declared level on every field but spin.
2. A closed-shell link (multiplicity 1, computed restricted or restricted-open, or unrestricted with a stored
   <S^2> at or below 0.1) never constrains the spin treatment.
3. The other links (every multiplicity above 1, and unrestricted singlets that are broken-symmetry or have no
   stored <S^2>) form the open-shell reference set. Unknown spin is skipped; the rest must agree
   (``kinetics_energy_level_contradiction`` with ``reason: open_shell_reference_mixed``).
4. A declared spin treatment must equal that shared reference, or, with none, every known link spin.

Each test names the branch it protects.
"""

from __future__ import annotations

import pytest

from tests.api.test_api_bundle_provenance_warnings import (
    _XYZ_H2,
    _kinetics,
    _reaction_bundle,
    _reaction_species,
)

_REACTION = "/api/v1/uploads/computed-reaction"
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_BASE = {"method": "B3LYP", "basis": "def2-TZVP"}
_U = {"spin": "unrestricted"}
_R = {"spin": "restricted"}
_RO = {"spin": "restricted_open"}


def _sp(key: str, geometry_key: str, spec: dict, **level) -> dict:
    spin = spec.get("spin")
    calc = {
        "key": key,
        "type": "sp",
        "geometry_key": geometry_key,
        "software_release": _SOFTWARE,
        "level_of_theory": {**_BASE, **({"spin_treatment": spin} if spin else {}), **level},
        "sp_electronic_energy_hartree": -0.5,
    }
    if "s2" in spec:
        calc["spin_diagnostic"] = {"s_squared": spec["s2"]}
    return calc


def _bundle(declared: dict | None, *, h: list[dict], h2: list[dict], link_level: dict | None = None) -> dict:
    """``H + H -> H2``: the doublet side links ``h`` energies, the singlet side links ``h2`` energies."""
    link_level = link_level or {}
    bundle = _reaction_bundle(
        kinetics=[_kinetics(**({"energy_level_of_theory": declared} if declared is not None else {}))]
    )
    h_species, h2_species = bundle["species"]
    links = []
    for i, spec in enumerate(h):
        h_species["calculations"].append(_sp(f"h-sp{i}", "h-geom", spec, **link_level))
        links.append({"calculation_key": f"h-sp{i}", "role": "reactant_energy"})
    for i, spec in enumerate(h2):
        h2_species["calculations"].append(_sp(f"h2-sp{i}", "h2-geom", spec, **link_level))
        links.append({"calculation_key": f"h2-sp{i}", "role": "product_energy"})
    bundle["kinetics"][0]["source_calculations"] = links
    return bundle


def _singlet_pair_bundle(declared: dict | None, *, reactant: list[dict], product: list[dict]) -> dict:
    """Two singlet species (same constitution, two keys): every link is closed-shell by multiplicity."""
    bundle = {
        "species": [
            _reaction_species("a", "[H][H]", 1, _XYZ_H2),
            _reaction_species("b", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["a"],
        "product_keys": ["b"],
        "kinetics": [
            _kinetics(
                reactant_keys=["a"],
                product_keys=["b"],
                a_units="per_s",
                **({"energy_level_of_theory": declared} if declared is not None else {}),
            )
        ],
    }
    links = []
    for key, specs, role in (("a", reactant, "reactant_energy"), ("b", product, "product_energy")):
        species = next(s for s in bundle["species"] if s["key"] == key)
        for i, spec in enumerate(specs):
            species["calculations"].append(_sp(f"{key}-sp{i}", f"{key}-geom", spec))
            links.append({"calculation_key": f"{key}-sp{i}", "role": role})
    bundle["kinetics"][0]["source_calculations"] = links
    return bundle


def _codes(resp) -> set[str]:
    return {w["code"] for w in resp.json()["warnings"]}


def _assert_refused(resp, reason: str | None = None) -> dict:
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "kinetics_energy_level_contradiction"
    if reason is not None:
        assert body["context"]["reason"] == reason
    return body


_FOUR_U = [_U, _U, _U, _U]


# ---------------------------------------------------------------------------
# Spin: accepted
# ---------------------------------------------------------------------------


def test_four_unrestricted_and_one_restricted_with_no_spin_stated_is_accepted(client):
    """Branch: no declared spin; the closed-shell restricted link is exempt."""
    resp = client.post(_REACTION, json=_bundle(dict(_BASE), h=_FOUR_U, h2=[_R]))
    assert resp.status_code == 201, resp.text[:800]
    assert "missing_level_of_theory_provenance" not in _codes(resp)


def test_declaring_the_open_shell_reference_is_accepted(client):
    """Branch: declared spin is compared with the open-shell references only, never with exempt links."""
    resp = client.post(_REACTION, json=_bundle({**_BASE, "spin_treatment": "unrestricted"}, h=_FOUR_U, h2=[_R]))
    assert resp.status_code == 201, resp.text[:800]


def test_a_collapsed_unrestricted_singlet_is_exempt(client):
    """Branch: m=1 unrestricted with <S^2> <= 0.1 never constrains spin (here against restricted open-shell links)."""
    resp = client.post(
        _REACTION,
        json=_bundle({**_BASE, "spin_treatment": "restricted_open"}, h=[_RO, _RO], h2=[{**_U, "s2": 0.0}]),
    )
    assert resp.status_code == 201, resp.text[:800]


def test_a_broken_symmetry_singlet_with_closed_shell_partners_is_accepted(client):
    """Branch: a broken-symmetry singlet is the only open-shell reference; restricted singlets are exempt."""
    resp = client.post(
        _REACTION,
        json=_singlet_pair_bundle({**_BASE, "spin_treatment": "unrestricted"}, reactant=[_R], product=[{**_U, "s2": 1.0}]),
    )
    assert resp.status_code == 201, resp.text[:800]


def test_a_link_with_unknown_spin_is_skipped(client):
    """Branch: unknown spin is skipped, never refused, and never makes the set 'mixed'."""
    resp = client.post(_REACTION, json=_bundle({**_BASE, "spin_treatment": "unrestricted"}, h=[_U, {}, _U], h2=[_R]))
    assert resp.status_code == 201, resp.text[:800]


def test_all_closed_shell_links_declare_their_own_spin(client):
    resp = client.post(
        _REACTION, json=_singlet_pair_bundle({**_BASE, "spin_treatment": "restricted"}, reactant=[_R], product=[_R])
    )
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# Spin: refused
# ---------------------------------------------------------------------------


def test_declaring_a_spin_the_open_shell_links_do_not_share_is_refused(client):
    """Branch: declared spin compared with the open-shell reference (4U + 1R declared restricted)."""
    body = _assert_refused(
        client.post(_REACTION, json=_bundle({**_BASE, "spin_treatment": "restricted"}, h=_FOUR_U, h2=[_R])),
        "declared_spin_differs",
    )
    assert len(body["context"]["energy_calculation_refs"]) == 4


def test_restricted_open_mixed_with_unrestricted_open_shell_links_is_refused(client):
    """Branch: RO vs U is a real level difference (ROCBS-QB3 vs CBS-QB3)."""
    _assert_refused(
        client.post(_REACTION, json=_bundle(dict(_BASE), h=[_U, _U, _U, _RO], h2=[_R])), "open_shell_reference_mixed"
    )


def test_restricted_on_a_doublet_mixed_with_unrestricted_partners_is_refused(client):
    """Branch: 'restricted' on m > 1 is not exempt and takes part like any other value."""
    _assert_refused(
        client.post(_REACTION, json=_bundle(dict(_BASE), h=[_U, _U, _U, _R], h2=[_R])), "open_shell_reference_mixed"
    )


def test_a_broken_symmetry_singlet_mixed_with_restricted_open_links_is_refused(client):
    """Branch: <S^2> above 0.1 puts an m=1 unrestricted link in the open-shell set."""
    _assert_refused(
        client.post(_REACTION, json=_bundle(dict(_BASE), h=[_RO, _RO], h2=[{**_U, "s2": 1.0}])),
        "open_shell_reference_mixed",
    )


def test_an_unrestricted_singlet_with_no_stored_s_squared_counts_as_unrestricted(client):
    """Branch: no <S^2> means not exempt (counted as unrestricted), so it conflicts with RO partners."""
    _assert_refused(
        client.post(_REACTION, json=_bundle(dict(_BASE), h=[_RO, _RO], h2=[_U])), "open_shell_reference_mixed"
    )


def test_the_threshold_is_inclusive_at_the_conventional_value(client):
    """Branch: <S^2> of exactly 0.1 is still collapsed (exempt); the strict side is covered above."""
    resp = client.post(_REACTION, json=_bundle(dict(_BASE), h=[_RO, _RO], h2=[{**_U, "s2": 0.1}]))
    assert resp.status_code == 201, resp.text[:800]


def test_a_declared_spin_that_every_closed_shell_link_lacks_is_refused(client):
    """Branch: no open-shell link, so the declared spin is compared with every known link spin."""
    _assert_refused(
        client.post(
            _REACTION,
            json=_singlet_pair_bundle({**_BASE, "spin_treatment": "unrestricted"}, reactant=[_R], product=[_R]),
        ),
        "declared_spin_differs",
    )


# ---------------------------------------------------------------------------
# Non-spin fields (rule 1)
# ---------------------------------------------------------------------------


def test_a_declaration_at_another_basis_is_refused(client):
    _assert_refused(client.post(_REACTION, json=_bundle({"method": "B3LYP", "basis": "def2-QZVP"}, h=_FOUR_U, h2=[_R])))


def test_a_declaration_at_another_method_is_refused(client):
    _assert_refused(
        client.post(_REACTION, json=_bundle({"method": "wB97X-D", "basis": "def2-TZVP"}, h=_FOUR_U, h2=[_R]))
    )


@pytest.mark.parametrize(
    ("link_extra", "declared_extra"),
    [
        ({"core_treatment": "frozen_core"}, {"core_treatment": "all_electron"}),
        ({"core_treatment": "frozen_core"}, {}),
        ({}, {"core_treatment": "all_electron"}),
        ({"pno_truncation": "tight"}, {"pno_truncation": "normal"}),
        ({"ri_approximation": "rijcosx"}, {}),
        ({"relativistic_hamiltonian": "none"}, {"relativistic_hamiltonian": "dkh2"}),
        ({"dispersion": "d3bj"}, {}),
    ],
    ids=["core", "core_link_only", "core_declared_only", "pno", "ri", "relativistic", "dispersion"],
)
def test_a_declaration_differing_in_another_identity_field_is_refused(client, link_extra, declared_extra):
    """Kills: any field other than spin_treatment dropped from the spin-agnostic key."""
    _assert_refused(
        client.post(_REACTION, json=_bundle({**_BASE, **declared_extra}, h=_FOUR_U, h2=[_R], link_level=link_extra))
    )


def test_name_spellings_of_one_level_still_agree(client):
    """The comparison normalises names the way the hash does (def2-tzvp is Def2TZVP)."""
    resp = client.post(_REACTION, json=_bundle({"method": "b3lyp", "basis": "Def2TZVP"}, h=_FOUR_U, h2=[_R]))
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# Loading, standalone route, key derivation
# ---------------------------------------------------------------------------


def test_the_stored_s_squared_is_what_decides_the_exemption(client):
    """The same payload flips on <S^2> alone: it is read from rows written earlier in this request."""
    collapsed = client.post(_REACTION, json=_bundle(dict(_BASE), h=[_RO, _RO], h2=[{**_U, "s2": 0.0}]))
    assert collapsed.status_code == 201, collapsed.text[:800]
    broken = client.post(_REACTION, json=_bundle(dict(_BASE), h=[_RO, _RO], h2=[{**_U, "s2": 0.5}]))
    _assert_refused(broken, "open_shell_reference_mixed")


def test_the_context_reason_is_a_closed_value_and_names_only_refs(client):
    body = _assert_refused(
        client.post(_REACTION, json=_bundle(dict(_BASE), h=[_U, _RO], h2=[_R])), "open_shell_reference_mixed"
    )
    assert set(body["context"]) == {"declared_level_of_theory_ref", "energy_calculation_refs", "reason"}
    assert all(ref.startswith("calc_") for ref in body["context"]["energy_calculation_refs"])
    assert "unrestricted" in body["detail"] and "restricted_open" in body["detail"]


def test_the_standalone_route_finds_source_calculations_modulo_spin(client):
    """The lookup of a declared level's calculations uses the same field rule as the consistency check."""
    from tests.api.test_api_composite_kinetics_energy_level import (
        _H_ATOM,
        _METHANE,
        _METHYL,
        _deposit,
        _sp_calc,
    )
    from tests.api.test_api_composite_kinetics_energy_level import _kinetics as _standalone

    level = {"method": "B3LYP", "basis": "6-31G(d)"}
    _deposit(client, _METHYL, primary=_sp_calc({**level, "spin_treatment": "unrestricted"}))
    _deposit(client, _H_ATOM, primary=_sp_calc({**level, "spin_treatment": "unrestricted"}))
    _deposit(client, _METHANE, primary=_sp_calc({**level, "spin_treatment": "restricted"}))
    resp = _standalone(client, energy_level=level)
    assert resp.status_code == 201, resp.text[:600]


def test_the_spin_agnostic_key_is_the_hash_payload_minus_spin():
    """One source of truth: a field added to the hash payload is compared automatically."""
    from tckdb_schemas.fragments.calculation import LevelOfTheoryRef

    from app.services.calculation_resolution import _level_of_theory_payload, spin_agnostic_level_key

    ref = LevelOfTheoryRef(
        method="DLPNO-CCSD(T)",
        basis="cc-pVTZ",
        core_treatment="window",
        relativistic_hamiltonian="none",
        pno_truncation="tight",
        ri_approximation="rijcosx",
        spin_treatment="unrestricted",
    )
    full = _level_of_theory_payload(ref)
    key = spin_agnostic_level_key(ref)
    assert set(full) - set(key) == {"spin_treatment"}
    assert {k: v for k, v in full.items() if k != "spin_treatment"} == key


# ---------------------------------------------------------------------------
# Standalone (/uploads/kinetics) and bundle import: the same rule, through the lookup
# ---------------------------------------------------------------------------

_STANDALONE = "/api/v1/uploads/kinetics"
_LEVEL = {"method": "B3LYP", "basis": "6-31G(d)"}


def _calc(spin: str | None, *, s2: float | None = None) -> dict:
    from tests.api.test_api_composite_kinetics_energy_level import _sp_calc

    calc = _sp_calc({**_LEVEL, **({"spin_treatment": spin} if spin else {})})
    if s2 is not None:
        calc["spin_diagnostic"] = {"s_squared": s2}
    return calc


def _deposit_ch3_h_ch4(client, *, ch3, h, ch4) -> None:
    """Each argument is a primary calculation, or a (primary, [additional]) pair."""
    from tests.api.test_api_composite_kinetics_energy_level import _H_ATOM, _METHANE, _METHYL, _deposit

    for species, given in ((_METHYL, ch3), (_H_ATOM, h), (_METHANE, ch4)):
        primary, additional = given if isinstance(given, tuple) else (given, None)
        _deposit(client, species, primary=primary, additional=additional)


def _standalone_payload(level: dict) -> dict:
    return {
        "reaction": {
            "reversible": False,
            "reactants": [
                {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [{"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}}],
        },
        "scientific_origin": "computed",
        "energy_level_of_theory": level,
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 0.0,
        "reported_ea": 0.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "software_release": {"name": "Arkane", "version": "3.0"},
    }


def _bundle_mode(db_session, level: dict):
    """The same payload through the bundle-import policy: returns (kinetics row, warnings)."""
    from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
    from app.workflows.kinetics import persist_kinetics_upload

    warnings: list = []
    row = persist_kinetics_upload(
        db_session,
        KineticsUploadRequest.model_validate(_standalone_payload(level)),
        warnings=warnings,
        require_energy_sources=False,
    )
    return row, warnings


def _linked_calcs(db_session, kinetics_id: int):
    from sqlalchemy import select

    from app.db.models.calculation import Calculation
    from app.db.models.kinetics import KineticsSourceCalculation

    return db_session.scalars(
        select(Calculation)
        .join(KineticsSourceCalculation, KineticsSourceCalculation.calculation_id == Calculation.id)
        .where(KineticsSourceCalculation.kinetics_id == kinetics_id)
    ).all()


def _assert_stored_without_sources(db_session, row, warnings) -> None:
    assert [w.code for w in warnings] == ["energy_level_stored_without_source_calculations"]
    assert row.energy_level_of_theory_id is not None
    assert _linked_calcs(db_session, row.id) == []


def test_standalone_a_radical_with_only_a_restricted_open_sp_does_not_serve_a_declared_unrestricted(client):
    """Lookup, eligibility: a radical with no calculation at the declared spin finds none (as on main)."""
    _deposit_ch3_h_ch4(client, ch3=_calc("restricted_open"), h=_calc("unrestricted"), ch4=_calc("restricted"))
    resp = client.post(_STANDALONE, json=_standalone_payload({**_LEVEL, "spin_treatment": "unrestricted"}))
    assert resp.status_code == 422, resp.text[:600]
    assert "No SP calculation found" in resp.text


def test_standalone_mixed_open_shell_references_are_refused_when_no_spin_is_declared(client):
    """Check (b): CH3 restricted-open with H unrestricted, no spin declared."""
    _deposit_ch3_h_ch4(client, ch3=_calc("restricted_open"), h=_calc("unrestricted"), ch4=_calc("restricted"))
    body = _assert_refused(client.post(_STANDALONE, json=_standalone_payload(dict(_LEVEL))), "open_shell_reference_mixed")
    assert len(body["context"]["energy_calculation_refs"]) == 2


def test_bundle_mode_mixed_open_shell_references_store_the_level_without_sources(client, db_session):
    """Bundle mode downgrades a violation exactly like 'no sources found'."""
    _deposit_ch3_h_ch4(client, ch3=_calc("restricted_open"), h=_calc("unrestricted"), ch4=_calc("restricted"))
    row, warnings = _bundle_mode(db_session, dict(_LEVEL))
    _assert_stored_without_sources(db_session, row, warnings)


def test_bundle_mode_a_radical_without_the_declared_spin_stores_the_level_without_sources(client, db_session):
    _deposit_ch3_h_ch4(client, ch3=_calc("restricted_open"), h=_calc("unrestricted"), ch4=_calc("restricted"))
    row, warnings = _bundle_mode(db_session, {**_LEVEL, "spin_treatment": "unrestricted"})
    _assert_stored_without_sources(db_session, row, warnings)


def test_standalone_a_consistent_deposit_links_and_a_declared_open_shell_reference_serves_closed_shell(
    client, db_session
):
    """The closed-shell restricted methane is eligible under a declared unrestricted: three links."""
    _deposit_ch3_h_ch4(client, ch3=_calc("unrestricted"), h=_calc("unrestricted"), ch4=_calc("restricted"))
    resp = client.post(_STANDALONE, json=_standalone_payload({**_LEVEL, "spin_treatment": "unrestricted"}))
    assert resp.status_code == 201, resp.text[:600]
    assert len(_linked_calcs(db_session, resp.json()["id"])) == 3


def test_bundle_mode_a_consistent_deposit_links_normally(client, db_session):
    _deposit_ch3_h_ch4(client, ch3=_calc("unrestricted"), h=_calc("unrestricted"), ch4=_calc("restricted"))
    row, warnings = _bundle_mode(db_session, dict(_LEVEL))
    assert warnings == []
    assert len(_linked_calcs(db_session, row.id)) == 3


def test_the_calculation_on_the_declared_levels_own_row_is_preferred_when_no_spin_is_declared(client, db_session):
    """Regression guard: one SP at exactly the declared level (spin unstated) plus another at unrestricted."""
    _deposit_ch3_h_ch4(client, ch3=(_calc(None), [_calc("unrestricted")]), h=_calc(None), ch4=_calc(None))
    resp = client.post(_STANDALONE, json=_standalone_payload(dict(_LEVEL)))
    assert resp.status_code == 201, resp.text[:600]
    assert {c.lot.spin_treatment for c in _linked_calcs(db_session, resp.json()["id"])} == {None}


def test_with_several_single_points_the_declared_spin_is_preferred(client, db_session):
    """Methane has restricted and unrestricted single points; both are eligible, the declared spin wins."""
    _deposit_ch3_h_ch4(
        client,
        ch3=_calc("unrestricted"),
        h=_calc("unrestricted"),
        ch4=(_calc("restricted"), [_calc("unrestricted")]),
    )
    resp = client.post(_STANDALONE, json=_standalone_payload({**_LEVEL, "spin_treatment": "unrestricted"}))
    assert resp.status_code == 201, resp.text[:600]
    spins = {c.lot.spin_treatment.value for c in _linked_calcs(db_session, resp.json()["id"])}
    assert spins == {"unrestricted"}


# ---------------------------------------------------------------------------
# Transition-state links, restricted-open singlets, declared composite schemes
# ---------------------------------------------------------------------------


def _ts_payload(*, ts_spin: str, declared: dict | None = None) -> dict:
    import copy

    from tests.api.test_api_bundle_monatomic_sp_primary import _reaction_payload

    payload = copy.deepcopy(_reaction_payload("rotor_scan_1"))
    spins = {"r0_sp": "restricted", "r1_sp": "unrestricted", "p0_sp": "unrestricted", "p1_sp": "restricted"}
    calcs = [c for s in payload["species"] for c in s["calculations"]] + payload["transition_state"]["calculations"]
    for calc in calcs:
        if calc["key"] in spins:
            calc["level_of_theory"]["spin_treatment"] = spins[calc["key"]]
        if calc["key"] == "ts_sp":
            calc["level_of_theory"]["spin_treatment"] = ts_spin
            level = {k: v for k, v in calc["level_of_theory"].items() if k != "spin_treatment"}
    payload["kinetics"][0]["energy_level_of_theory"] = declared if declared is not None else level
    return payload


def test_a_restricted_open_transition_state_against_unrestricted_radicals_is_refused(client):
    """The TS link's multiplicity is read from the transition-state entry, so its energy joins the open-shell set."""
    resp = client.post(_REACTION, json=_ts_payload(ts_spin="restricted_open"))
    _assert_refused(resp, "open_shell_reference_mixed")


def test_an_unrestricted_transition_state_with_unrestricted_radicals_is_accepted(client):
    resp = client.post(_REACTION, json=_ts_payload(ts_spin="unrestricted"))
    assert resp.status_code == 201, resp.text[:800]


def test_a_restricted_open_singlet_is_closed_shell_exempt(client):
    """Kills: restricted_open dropped from the singlet exemption."""
    resp = client.post(_REACTION, json=_bundle(dict(_BASE), h=[_U, _U], h2=[_RO]))
    assert resp.status_code == 201, resp.text[:800]
    declared = client.post(_REACTION, json=_bundle({**_BASE, "spin_treatment": "unrestricted"}, h=[_U, _U], h2=[_RO]))
    assert declared.status_code == 201, declared.text[:800]


def _scheme_level(db_session, *, method: str, lot_hash: str, source, scheme_name: str, definition_hash: str):
    from app.db.models.common import CompositeSchemeKind
    from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
    from app.db.models.level_of_theory import LevelOfTheory

    scheme = CompositeScheme(kind=CompositeSchemeKind.extrapolation, name=scheme_name, definition_hash=definition_hash)
    level = LevelOfTheory(method=method, lot_hash=lot_hash)
    db_session.add_all([scheme, level])
    db_session.flush()
    db_session.add(LevelOfTheoryComposite(level_of_theory_id=level.id, scheme_id=scheme.id, binding_source=source))
    db_session.flush()
    return level


def test_declared_composite_schemes_keep_the_row_comparison(db_session):
    """Kills: the declared-scheme guard removed. Their columns are only a label, so equal labels are not equal levels."""
    from app.db.models.common import CompositeBindingSource
    from app.db.models.level_of_theory import LevelOfTheory
    from app.services.calculation_levels import declared_scheme_level_ids, kinetics_level_fields_match

    declared = CompositeBindingSource.declared
    one = _scheme_level(db_session, method="Label", lot_hash="1" * 64, source=declared, scheme_name="Label", definition_hash="a" * 64)
    two = _scheme_level(db_session, method="Label", lot_hash="2" * 64, source=declared, scheme_name="Label", definition_hash="b" * 64)
    named = _scheme_level(db_session, method="CBS-QB3", lot_hash="3" * 64, source=declared, scheme_name="CBS-QB3", definition_hash="c" * 64)
    plain = LevelOfTheory(method="CBS-QB3", lot_hash="4" * 64)
    db_session.add(plain)
    db_session.flush()
    ids = declared_scheme_level_ids(db_session, [one.id, two.id, named.id, plain.id])
    assert ids == {one.id, two.id, named.id}

    # Two declared schemes with one name are two levels; a declared scheme named like a method is not that method.
    assert kinetics_level_fields_match(one, two, scheme_ids=ids) is False
    assert kinetics_level_fields_match(named, plain, scheme_ids=ids) is False
    assert kinetics_level_fields_match(plain, named, scheme_ids=ids) is False
    assert kinetics_level_fields_match(one, one, scheme_ids=ids) is True


def test_a_catalogued_composite_binding_follows_the_ordinary_rule(db_session):
    from app.db.models.common import CompositeBindingSource
    from app.db.models.level_of_theory import LevelOfTheory
    from app.services.calculation_levels import declared_scheme_level_ids, kinetics_level_fields_match

    catalogued = _scheme_level(
        db_session,
        method="CBS-QB3",
        lot_hash="5" * 64,
        source=CompositeBindingSource.named_method_catalogue,
        scheme_name="CBS-QB3",
        definition_hash="d" * 64,
    )
    other = LevelOfTheory(method="cbs-qb3", lot_hash="6" * 64)
    db_session.add(other)
    db_session.flush()
    ids = declared_scheme_level_ids(db_session, [catalogued.id, other.id])
    assert ids == set()
    assert kinetics_level_fields_match(catalogued, other, scheme_ids=ids) is True


# ---------------------------------------------------------------------------
# A repeated species (H + H -> H2) links its single point once per role
# ---------------------------------------------------------------------------


def _h_plus_h_payload(level: dict) -> dict:
    payload = _standalone_payload(level)
    h = {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}
    payload["reaction"]["reactants"] = [h, dict(h)]
    payload["reaction"]["products"] = [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}]
    return payload


def _deposit_h_h2(client) -> None:
    from tests.api.test_api_composite_kinetics_energy_level import _H_ATOM, _deposit

    _deposit(client, _H_ATOM, primary=_calc("unrestricted"))
    _deposit(client, ("[H][H]", 1, "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"), primary=_calc("restricted"))


def _roles(db_session, kinetics_id: int):
    from sqlalchemy import select

    from app.db.models.kinetics import KineticsSourceCalculation

    rows = db_session.scalars(
        select(KineticsSourceCalculation).where(KineticsSourceCalculation.kinetics_id == kinetics_id)
    ).all()
    return sorted(((r.calculation_id, r.role.value) for r in rows), key=lambda link: link[1])


def test_standalone_a_repeated_reactant_links_its_single_point_once(client, db_session):
    """Reproduced on the previous head: the second H hit pk_kinetics_source_calculation."""
    _deposit_h_h2(client)
    resp = client.post(_STANDALONE, json=_h_plus_h_payload(dict(_LEVEL)))
    assert resp.status_code == 201, resp.text[:600]
    links = _roles(db_session, resp.json()["id"])
    assert [role for _id, role in links] == ["product_energy", "reactant_energy"]
    assert len({calc_id for calc_id, _role in links}) == 2


def test_bundle_mode_a_repeated_reactant_links_its_single_point_once(client, db_session):
    from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
    from app.workflows.kinetics import persist_kinetics_upload

    _deposit_h_h2(client)
    warnings: list = []
    row = persist_kinetics_upload(
        db_session,
        KineticsUploadRequest.model_validate(_h_plus_h_payload(dict(_LEVEL))),
        warnings=warnings,
        require_energy_sources=False,
    )
    assert warnings == []
    assert [role for _id, role in _roles(db_session, row.id)] == ["product_energy", "reactant_energy"]


def test_the_check_runs_on_the_deduplicated_links(client):
    """Hydrogen restricted-open, H2 an unrestricted singlet with no stored <S^2> (counted as unrestricted): mixed.

    The hydrogen single point is named once, not once per occurrence in the reaction.
    """
    from tests.api.test_api_composite_kinetics_energy_level import _H_ATOM, _deposit

    _deposit(client, _H_ATOM, primary=_calc("restricted_open"))
    _deposit(client, ("[H][H]", 1, "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"), primary=_calc("unrestricted"))
    body = _assert_refused(
        client.post(_STANDALONE, json=_h_plus_h_payload(dict(_LEVEL))), "open_shell_reference_mixed"
    )
    assert len(body["context"]["energy_calculation_refs"]) == 2
