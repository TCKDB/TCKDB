"""The relative conformer energy methods and their manifest siblings: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted: the verb and path, that the body carries only what the
caller supplied (the client invents no default of any scientific choice), that the level is exactly one of a level of
theory and a composite scheme, that the required choices cannot be omitted, and that a manifest comes back exactly as
the server sent it. Agreement with the live server is covered by the backend route tests.
"""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import make_client
from tckdb_client import TCKDBClient
from tckdb_client._parity import COVERAGE

SPECIES = "spe_abc123def456"
BASE = f"/api/v1/scientific/species-entries/{SPECIES}/conformers/relative-energies"

SINGLE = dict(reference_conformer_ref="cg_ref", energy_kind="electronic_energy", certificate="local_minimum_any_surface")
PROTOCOLS = dict(
    candidate={"level": {"level_of_theory_ref": "lot_cand"}},
    reference={"level": {"composite_scheme_ref": "csch_ref"}},
)
COMPARE = dict(SINGLE, pairing="same_geometry", **PROTOCOLS)


def _recorder(answer=None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer if answer is not None else {"outcome": "relative_energies"})

    return seen, handler


def _client(handler) -> TCKDBClient:
    return make_client(handler)[0]


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def test_each_route_has_a_typed_method_in_the_parity_ledger():
    # Kills: a route without a client method (a row left raw-only or naming a method that does not exist).
    rows = {(e.method, e.path): e for e in COVERAGE}
    for suffix, method in [
        ("", "relative_conformer_energies"),
        ("/manifest", "get_relative_conformer_energy_manifest"),
        ("/compare", "compare_relative_conformer_energies"),
        ("/compare/manifest", "get_relative_conformer_energy_comparison_manifest"),
    ]:
        entry = rows[("POST", "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies" + suffix)]
        assert entry.status == "typed"
        assert entry.client_method == method
        assert callable(getattr(TCKDBClient, method))


def test_single_request_posts_only_what_was_supplied():
    seen, handler = _recorder()
    _client(handler).relative_conformer_energies(SPECIES, level_of_theory_ref="lot_x", **SINGLE)
    request = seen[0]
    assert request.method == "POST" and request.url.path == BASE
    assert _body(request) == {
        "reference_conformer_ref": "cg_ref",
        "energy_kind": "electronic_energy",
        "level": {"level_of_theory_ref": "lot_x"},
        "certificate": "local_minimum_any_surface",
    }


def test_optional_fields_are_sent_only_when_given():
    seen, handler = _recorder()
    _client(handler).relative_conformer_energies(
        SPECIES,
        composite_scheme_ref="csch_x",
        member_refs=("cg_a", "cg_b"),
        coverage_requirement="all_requested_members",
        require_stable_reference=False,
        **SINGLE,
    )
    body = _body(seen[0])
    assert body["level"] == {"composite_scheme_ref": "csch_x"}
    assert body["member_refs"] == ["cg_a", "cg_b"]
    assert body["require_stable_reference"] is False  # an explicit False is a choice, so it is sent
    assert body["coverage_requirement"] == "all_requested_members"
    for key in ("recipe", "repeat_policy", "min_review_status", "permitted_quality", "temperature_k"):
        assert key not in body


@pytest.mark.parametrize("missing", ["energy_kind", "certificate", "reference_conformer_ref"])
def test_a_required_scientific_choice_cannot_be_omitted(missing):
    # Kills: a client-side default for a scientific choice.
    seen, handler = _recorder()
    args = {k: v for k, v in SINGLE.items() if k != missing}
    with pytest.raises(TypeError):
        _client(handler).relative_conformer_energies(SPECIES, level_of_theory_ref="lot_x", **args)
    assert seen == []


def test_both_levels_is_a_value_error_before_any_request():
    seen, handler = _recorder()
    with pytest.raises(ValueError, match="exactly one level"):
        _client(handler).relative_conformer_energies(
            SPECIES, level_of_theory_ref="lot_x", composite_scheme_ref="csch_x", **SINGLE
        )
    assert seen == []


def test_neither_level_is_a_value_error_before_any_request():
    seen, handler = _recorder()
    with pytest.raises(ValueError, match="exactly one level"):
        _client(handler).relative_conformer_energies(SPECIES, **SINGLE)
    assert seen == []


def test_integer_entry_id_is_refused_before_any_request():
    seen, handler = _recorder()
    with pytest.raises(ValueError, match="spe_"):
        _client(handler).relative_conformer_energies(12, level_of_theory_ref="lot_x", **SINGLE)  # type: ignore[arg-type]
    assert seen == []


def test_compare_posts_both_protocols_and_the_pairing():
    seen, handler = _recorder({"outcome": "compared"})
    result = _client(handler).compare_relative_conformer_energies(SPECIES, **COMPARE)
    assert seen[0].url.path == BASE + "/compare"
    assert _body(seen[0]) == {
        "reference_conformer_ref": "cg_ref",
        "energy_kind": "electronic_energy",
        "certificate": "local_minimum_any_surface",
        "candidate": PROTOCOLS["candidate"],
        "reference": PROTOCOLS["reference"],
        "pairing": "same_geometry",
    }
    assert result == {"outcome": "compared"}


@pytest.mark.parametrize("missing", ["pairing", "candidate", "reference", "energy_kind", "certificate"])
def test_compare_requires_its_choices(missing):
    # Kills: defaulting pairing (or any other required choice).
    seen, handler = _recorder()
    args = {k: v for k, v in COMPARE.items() if k != missing}
    with pytest.raises(TypeError):
        _client(handler).compare_relative_conformer_energies(SPECIES, **args)
    assert seen == []


def test_compare_protocol_without_a_level_is_refused_locally():
    seen, handler = _recorder()
    with pytest.raises(ValueError, match="candidate"):
        _client(handler).compare_relative_conformer_energies(SPECIES, **dict(COMPARE, candidate={"recipe": {}}))
    assert seen == []


def test_manifest_routes_and_paths():
    seen, handler = _recorder({"manifest_format": "x"})
    client = _client(handler)
    client.get_relative_conformer_energy_manifest(SPECIES, level_of_theory_ref="lot_x", **SINGLE)
    client.get_relative_conformer_energy_comparison_manifest(SPECIES, **COMPARE)
    assert [r.url.path for r in seen] == [BASE + "/manifest", BASE + "/compare/manifest"]
    assert all(r.method == "POST" for r in seen)


def test_manifests_come_back_exactly_as_served():
    # Kills: normalising the manifest client-side (key order, dropped or added keys, rounded numbers).
    served = (
        '{"outcome": "relative_energies", "zeta": [3, 2, 1], "a": {"y": 1.0000000000000002, "x": null}, '
        '"manifest_format": "tckdb.relative_conformer_energy.manifest", "extra_key": true, "integrity": {"manifest_sha256": "ab"}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=served.encode(), headers={"content-type": "application/json"})

    client = _client(handler)
    expected = json.loads(served)
    for got in (
        client.get_relative_conformer_energy_manifest(SPECIES, level_of_theory_ref="lot_x", **SINGLE),
        client.get_relative_conformer_energy_comparison_manifest(SPECIES, **COMPARE),
    ):
        assert got == expected
        assert list(got) == list(expected)
        assert list(got["a"]) == ["y", "x"]
        assert json.dumps(got) == json.dumps(expected)


@pytest.mark.parametrize(
    "bad_level",
    [{}, {"level_of_theory_ref": "lot_x", "composite_scheme_ref": "csch_x"}, "lot_x", {"bogus": "lot_x"}],
)
@pytest.mark.parametrize("method", ["compare_relative_conformer_energies", "get_relative_conformer_energy_comparison_manifest"])
@pytest.mark.parametrize("which", ["candidate", "reference"])
def test_compare_protocol_level_must_name_exactly_one_ref(method, which, bad_level):
    # Kills: skipping the level check on a compare protocol.
    seen, handler = _recorder()
    with pytest.raises(ValueError):
        getattr(_client(handler), method)(SPECIES, **dict(COMPARE, **{which: {"level": bad_level}}))
    assert seen == []


def test_client_documents_the_backend_vocabularies_it_lists():
    # The client cannot import the backend, so read the backend source when the checkout has it (skipped otherwise).
    import re
    from pathlib import Path

    import tckdb_client.scientific_types as types

    source = Path(__file__).resolve().parents[3] / "backend/app/services/relative_conformer_energy/compare.py"
    if not source.exists():
        pytest.skip("backend source is not beside the client package")
    codes = dict(re.findall(r'^([A-Z_]+) = "([a-z_]+)"', source.read_text(), re.M))
    reasons = {v for k, v in codes.items() if k not in {"COMPARISON_VERSION", "CODE_IDENTICAL_PROTOCOLS", "SIGN_CONVENTION"}}
    problems = {v for k, v in codes.items() if k.startswith("REFERENCE_CONFORMER_")}
    assert len(reasons) == 8 and len(problems) == 3

    def words(cls) -> set[str]:
        return set(re.findall(r"``([a-z_]+)``", cls.__doc__ or ""))

    assert reasons <= words(types.RelativeEnergyComparePair)
    assert problems <= words(types.RelativeEnergyCompareReferenceConformer)
