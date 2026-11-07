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
