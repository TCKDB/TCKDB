"""R1-R6: geometry/frequency/energy levels of theory, upload validation and reads.

Owner decision, 2026-09 ("statmech-level-roles"): a depositor may run the
optimisation and the single point at two different levels of theory, and
TCKDB must both *display* that split honestly and *catch* the deposit that
forgets half of it -- on every upload shape, including the multi-conformer
ensemble bundles an ARC-style client actually sends
(``/uploads/computed-species``, ``/uploads/computed-reaction``, and the
statmech block nested inside ``/uploads/conformers``), not only the
standalone one-evidence-chain upload. The rules
(``app.services.calculation_levels``):

* R1 -- read-time derivation of geometry/frequency/energy levels from
  ``opt``/``freq``/``sp``/``composite``/``imported`` role links. Never
  blocking; picks the lowest-id calculation per role for display.
* R2' -- ``opt``/``freq`` may repeat (one per conformer). ``sp`` may
  repeat only when each sits on a *distinct* linked ``opt``'s geometry,
  and every linked ``sp`` must share one level of theory (else
  ``*_energy_level_ambiguous``).
* R3' -- every linked ``sp``'s input geometry must be an output geometry
  of *some* linked ``opt``.
* Coverage -- if any ``sp`` is linked, every linked ``opt`` must have one
  on its own geometry ("forgot the SP for one conformer").
* R4'/R5 -- a declared ``energy_level_of_theory`` must equal the linked
  ``sp``s' shared level, or every linked ``opt``'s level when none are
  linked (R5: opt-only is valid exactly when there is nothing to
  contradict).
* R6 -- a thermo record derived from a statmech basis
  (``existing_statmech_id``) inherits that record's levels for R1
  display purposes, but its own R2'/R3'/Coverage still apply to whatever
  it links itself (only R4' is skipped, because the schema refuses
  combining ``energy_level_of_theory`` with ``existing_statmech_id``).

Assertions throughout follow the house style: branch on the envelope's
``code``, never on substring presence in ``detail`` (Pydantic echoes
rejected input back into ``detail``, so a naive ``in`` check can pass on
an echo of the *input* rather than evidence the check actually ran) --
except where this file explicitly checks that a LoT method name and a
public ref DO appear in ``detail`` (the task's own requirement), which is
checked in addition to the coded ``context``, never instead of it. Context
values are asserted by *shape* (``calc_``/``lot_`` ref strings, never a
bare int) rather than by excluding a specific integer's digits from the
response body -- public refs are random base32 whose alphabet includes
digits 2-7, so a small id can coincidentally appear inside an unrelated
ref, which is exactly the flake a shape assertion does not have.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.services.calculation_levels import RoleCalcInfo, derive_levels

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_A = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_B = {"method": "wB97X-D", "basis": "def2-TZVP"}

_ROLE_DUPLICATE_STATMECH = "statmech_role_duplicate"
_ROLE_DUPLICATE_THERMO = "thermo_role_duplicate"
_GEOMETRY_MISMATCH_STATMECH = "statmech_sp_geometry_mismatch"
_REQUIRES_SP_STATMECH = "statmech_energy_level_requires_sp"
_REQUIRES_SP_THERMO = "thermo_energy_level_requires_sp"
_CONTRADICTION_STATMECH = "statmech_energy_level_contradiction"
_AMBIGUOUS_STATMECH = "statmech_energy_level_ambiguous"


def _assert_code(response, expected: str) -> dict:
    """The response is a 422 that names *expected* in its ``code`` field."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) >= {"code", "detail"}, body
    assert body["code"] == expected, (
        f"expected code={expected!r}, got {body['code']!r}. "
        f"detail={body['detail']!r}"
    )
    return body


def _assert_no_ids_disclosed(body: dict) -> None:
    """Every ``context`` key naming a row ends ``_ref``/``_refs``, never ``_id``.

    The house rule (no PKs/FKs in user-facing error detail): a client
    gets a public ref for anything it can act on, never a raw PK. ``role``
    and ``count`` are plain facts, not row references, so they are exempt.
    """
    context = body.get("context", {})
    for key in context:
        if key in ("role", "count"):
            continue
        assert key.endswith("_ref") or key.endswith("_refs"), (
            f"context key {key!r} looks like a disclosed row id: {context}"
        )


def _assert_context_values_are_refs(body: dict) -> None:
    """Every ``context`` value is a public ref string (``calc_``/``lot_``),
    or a list of them -- never a bare integer id.

    Public refs are random base32 (alphabet includes digits 2-7), so a
    substring check like ``str(some_id) not in str(body)`` is flaky: a
    small integer id can coincidentally appear inside an unrelated ref's
    digits. Asserting the *shape* of every value is not.
    """
    context = body.get("context", {})
    for key, value in context.items():
        if key in ("role", "count"):
            continue
        values = value if isinstance(value, list) else [value]
        for v in values:
            assert v is None or (
                isinstance(v, str) and (v.startswith("calc_") or v.startswith("lot_"))
            ), f"context[{key!r}] = {value!r} is not a calc_/lot_ ref: {context}"
            assert not isinstance(v, int), (
                f"context[{key!r}] = {value!r} is a raw integer id: {context}"
            )


def _opt_calc_at(lot: dict, **overrides) -> dict:
    calc = {
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "opt_result": {"converged": True},
    }
    calc.update(overrides)
    return calc


def _sp_calc_at(lot: dict, **overrides) -> dict:
    calc = {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }
    calc.update(overrides)
    return calc


def _standalone_statmech_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    base.update(overrides)
    return base


def _thermo_payload(smiles: str, **overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "h298_kj_mol": -83.7,
    }
    base.update(overrides)
    return base


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _methyl_xyz(z_perturbation: float) -> str:
    """A methyl-radical geometry, perturbed on one H so it hashes distinct.

    Mirrors the multi-conformer bundle fixture
    (``test_computed_species_upload.py::_bundle_multi_conformer_thermo_statmech``):
    geometry is content-addressed, so even a tiny coordinate difference is
    a genuinely distinct ``geometry`` row of the same molecule.
    """
    return (
        "4\nmethyl\n"
        "C 0.0 0.0 0.0\n"
        "H 1.0 0.0 0.0\n"
        "H -0.5 0.866 0.0\n"
        f"H -0.5 -0.866 {z_perturbation}"
    )


_METHYL_SPECIES = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}


def _deposit_conformer(
    client,
    *,
    label: str,
    species: dict,
    xyz_text: str,
    primary: dict,
    additional: list[dict] | None = None,
) -> dict:
    """Deposit one conformer with a primary calculation and optional
    additional calculations (all sharing this conformer's own geometry by
    fallback, unless an additional entry declares its own
    ``output_geometries``/``input_geometries``).

    :returns: The parsed ``/uploads/conformers`` response body.
    """
    payload: dict = {
        "species_entry": species,
        "geometry": {"xyz_text": xyz_text},
        "calculation": primary,
        "label": label,
    }
    if additional:
        payload["additional_calculations"] = additional
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# R2' -- sp distinctness: two sps may not claim the same opt's geometry
# ---------------------------------------------------------------------------


def test_statmech_two_sps_on_same_opt_geometry_is_refused(client, db_session):
    """Two 'sp' links both matching the one linked 'opt's geometry -- a
    genuine duplicate under R2', even though opt/freq repeats are fine."""
    before = _count(db_session, Statmech)
    species = dict(_METHYL_SPECIES)
    conf = _deposit_conformer(
        client,
        label="dup-sp",
        species=species,
        xyz_text=_methyl_xyz(0.0),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_a"},
        additional=[
            {**_sp_calc_at(_LOT_A), "key": "sp_x"},
            {**_sp_calc_at(_LOT_A), "key": "sp_y"},
        ],
    )
    opt_id = conf["primary_calculation"]["calculation_id"]
    all_sp_ids = [c["calculation_id"] for c in conf["additional_calculations"]]

    payload = _standalone_statmech_payload(
        species_entry=species,
        source_calculations=[
            {"existing_calculation_id": opt_id, "role": "opt"},
        ]
        + [
            {"existing_calculation_id": sid, "role": "sp"} for sid in all_sp_ids
        ],
    )

    resp = client.post("/api/v1/uploads/statmech", json=payload)
    body = _assert_code(resp, _ROLE_DUPLICATE_STATMECH)
    assert set(body["context"]) == {"opt_calculation_ref", "sp_calculation_refs"}
    assert len(body["context"]["sp_calculation_refs"]) == 2
    _assert_context_values_are_refs(body)
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Statmech) == before


def test_thermo_two_sps_on_same_opt_geometry_is_refused(client, db_session):
    """Thermo's own copy of the R2' distinctness rule, own-roles path."""
    before = _count(db_session, Thermo)
    species = dict(_METHYL_SPECIES)
    conf = _deposit_conformer(
        client,
        label="dup-sp-thermo",
        species=species,
        xyz_text=_methyl_xyz(0.0),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_a"},
        additional=[
            {**_sp_calc_at(_LOT_A), "key": "sp_x"},
            {**_sp_calc_at(_LOT_A), "key": "sp_y"},
        ],
    )
    opt_id = conf["primary_calculation"]["calculation_id"]
    all_sp_ids = [c["calculation_id"] for c in conf["additional_calculations"]]

    payload = _thermo_payload(species["smiles"])
    payload["species_entry"] = species
    payload["source_calculations"] = [
        {"existing_calculation_id": opt_id, "role": "opt"},
    ] + [
        {"existing_calculation_id": sid, "role": "sp"} for sid in all_sp_ids
    ]

    resp = client.post("/api/v1/uploads/thermo", json=payload)
    body = _assert_code(resp, _ROLE_DUPLICATE_THERMO)
    assert set(body["context"]) == {"opt_calculation_ref", "sp_calculation_refs"}
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Thermo) == before


# ---------------------------------------------------------------------------
# R3' -- a linked sp must sit on some linked opt's geometry
# ---------------------------------------------------------------------------


def test_statmech_sp_geometry_mismatch_is_refused(client, db_session):
    species = dict(_METHYL_SPECIES)
    before = _count(db_session, Statmech)

    conf_opt = _deposit_conformer(
        client,
        label="geo-opt",
        species=species,
        xyz_text=_methyl_xyz(0.0),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_a"},
    )
    conf_sp = _deposit_conformer(
        client,
        label="geo-sp",
        species=species,
        xyz_text=_methyl_xyz(0.37),
        primary={**_sp_calc_at(_LOT_A), "key": "sp_b"},
    )
    opt_id = conf_opt["primary_calculation"]["calculation_id"]
    sp_id = conf_sp["primary_calculation"]["calculation_id"]

    payload = _standalone_statmech_payload(
        species_entry=species,
        source_calculations=[
            {"existing_calculation_id": opt_id, "role": "opt"},
            {"existing_calculation_id": sp_id, "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    body = _assert_code(resp, _GEOMETRY_MISMATCH_STATMECH)
    assert set(body["context"]) == {"sp_calculation_ref"}
    _assert_context_values_are_refs(body)
    _assert_no_ids_disclosed(body)
    # Nothing new: the two conformer deposits above are unaffected, but no
    # statmech row was created for the failed request.
    assert _count(client._db_session, Statmech) == before


# ---------------------------------------------------------------------------
# R2' -- linked sps must share one level of theory ("ambiguous")
# ---------------------------------------------------------------------------


def test_statmech_two_level_sps_ambiguous_is_refused(client, db_session):
    """Two conformers, each with its own correctly-matched sp, but the two
    sps run at different levels of theory -- no single energy level."""
    before = _count(db_session, Statmech)
    species = dict(_METHYL_SPECIES)

    conf_a = _deposit_conformer(
        client,
        label="amb-a",
        species=species,
        xyz_text=_methyl_xyz(0.0),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_a"},
        additional=[{**_sp_calc_at(_LOT_A), "key": "sp_a"}],
    )
    conf_b = _deposit_conformer(
        client,
        label="amb-b",
        species=species,
        xyz_text=_methyl_xyz(0.55),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_b"},
        additional=[{**_sp_calc_at(_LOT_B), "key": "sp_b"}],
    )
    opt_a_id = conf_a["primary_calculation"]["calculation_id"]
    sp_a_id = conf_a["additional_calculations"][0]["calculation_id"]
    opt_b_id = conf_b["primary_calculation"]["calculation_id"]
    sp_b_id = conf_b["additional_calculations"][0]["calculation_id"]

    payload = _standalone_statmech_payload(
        species_entry=species,
        source_calculations=[
            {"existing_calculation_id": opt_a_id, "role": "opt"},
            {"existing_calculation_id": sp_a_id, "role": "sp"},
            {"existing_calculation_id": opt_b_id, "role": "opt"},
            {"existing_calculation_id": sp_b_id, "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    body = _assert_code(resp, _AMBIGUOUS_STATMECH)
    assert set(body["context"]) == {"sp_calculation_refs"}
    assert len(body["context"]["sp_calculation_refs"]) == 2
    _assert_context_values_are_refs(body)
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Statmech) == before


def test_statmech_valid_ensemble_with_per_conformer_sps_is_accepted(client):
    """The mirror of the ambiguous case: same shape, same level of theory
    on both sps -- accepted, and reads back with a single unambiguous
    energy level."""
    species = dict(_METHYL_SPECIES)

    conf_a = _deposit_conformer(
        client,
        label="ens-a",
        species=species,
        xyz_text=_methyl_xyz(0.0),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_a"},
        additional=[{**_sp_calc_at(_LOT_B), "key": "sp_a"}],
    )
    conf_b = _deposit_conformer(
        client,
        label="ens-b",
        species=species,
        xyz_text=_methyl_xyz(0.55),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_b"},
        additional=[{**_sp_calc_at(_LOT_B), "key": "sp_b"}],
    )
    opt_a_id = conf_a["primary_calculation"]["calculation_id"]
    sp_a_id = conf_a["additional_calculations"][0]["calculation_id"]
    opt_b_id = conf_b["primary_calculation"]["calculation_id"]
    sp_b_id = conf_b["additional_calculations"][0]["calculation_id"]

    payload = _standalone_statmech_payload(
        species_entry=species,
        source_calculations=[
            {"existing_calculation_id": opt_a_id, "role": "opt"},
            {"existing_calculation_id": sp_a_id, "role": "sp"},
            {"existing_calculation_id": opt_b_id, "role": "opt"},
            {"existing_calculation_id": sp_b_id, "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}")
    levels = detail.json()["record"]["levels"]
    assert levels["energy_source"] == "sp"
    assert levels["energy"]["method"] == "wB97X-D"
    assert levels["geometry"]["method"] == "B3LYP"


# ---------------------------------------------------------------------------
# R4' -- a declared energy_level_of_theory must agree with what is linked
# ---------------------------------------------------------------------------


def test_statmech_energy_level_requires_sp(client, db_session):
    """Declared level != opt level, no sp linked -- refused (R4')."""
    before = _count(db_session, Statmech)
    payload = _standalone_statmech_payload(
        species_entry={"smiles": "CCCCCCCCCC", "charge": 0, "multiplicity": 1},
        calculations=[{"key": "opt0", "calculation": _opt_calc_at(_LOT_A)}],
        source_calculations=[{"calculation_key": "opt0", "role": "opt"}],
        energy_level_of_theory=_LOT_B,
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    body = _assert_code(resp, _REQUIRES_SP_STATMECH)
    # The owner's exact phrasing: "energy level of theory X differs from
    # the optimisation level Y but no single-point calculation at X is
    # linked" -- both LoT strings must be present.
    assert "wB97X-D/def2-TZVP" in body["detail"]
    assert "B3LYP/6-31G(d)" in body["detail"]
    assert set(body["context"]) == {
        "declared_level_of_theory_ref",
        "opt_calculation_refs",
    }
    _assert_context_values_are_refs(body)
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Statmech) == before


def test_statmech_energy_level_contradiction(client, db_session):
    """Declared level disagrees with the linked sp's own level -- refused (R4')."""
    before = _count(db_session, Statmech)
    payload = _standalone_statmech_payload(
        species_entry={"smiles": "CCCCCCCCCCC", "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "opt0", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "sp0", "calculation": _sp_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
        energy_level_of_theory=_LOT_A,
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    body = _assert_code(resp, _CONTRADICTION_STATMECH)
    assert "B3LYP/6-31G(d)" in body["detail"]
    assert "wB97X-D/def2-TZVP" in body["detail"]
    assert set(body["context"]) == {
        "declared_level_of_theory_ref",
        "sp_calculation_refs",
        "sp_level_of_theory_ref",
    }
    _assert_context_values_are_refs(body)
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Statmech) == before


# ---------------------------------------------------------------------------
# R5 / valid deposits, and the read-time levels they produce
# ---------------------------------------------------------------------------


def test_statmech_opt_only_valid_with_no_declared_level(client):
    """Opt-only, no declared energy level, is valid (R5) and reports
    ``energy_source == 'opt'`` — the optimisation's own energy stands in."""
    payload = _standalone_statmech_payload(
        species_entry={"smiles": "CCCCCCCCCCCC", "charge": 0, "multiplicity": 1},
        calculations=[{"key": "opt0", "calculation": _opt_calc_at(_LOT_A)}],
        source_calculations=[{"calculation_key": "opt0", "role": "opt"}],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}")
    assert detail.status_code == 200, detail.text
    levels = detail.json()["record"]["levels"]
    assert levels["energy_source"] == "opt"
    assert levels["geometry"]["method"] == "B3LYP"
    assert levels["geometry"]["basis"] == "6-31G(d)"
    assert levels["energy"]["method"] == "B3LYP"
    assert levels["frequency"] is None


def test_statmech_opt_sp_different_levels_valid(client):
    """Opt and sp at two different levels of theory is exactly the shape
    the owner asked TCKDB to display honestly: ``energy_source == 'sp'``,
    and the energy level differs from the geometry level."""
    payload = _standalone_statmech_payload(
        species_entry={"smiles": "CCCCCCCCCCCCC", "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "opt0", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "sp0", "calculation": _sp_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}")
    levels = detail.json()["record"]["levels"]
    assert levels["energy_source"] == "sp"
    assert levels["geometry"]["method"] == "B3LYP"
    assert levels["energy"]["method"] == "wB97X-D"
    assert levels["geometry"]["level_of_theory_ref"] != levels["energy"]["level_of_theory_ref"]


def test_statmech_declared_energy_level_equals_opt_is_valid(client):
    """R5's named asymmetry: opt-only is valid when the declared energy
    level *equals* the opt level, with no sp required."""
    payload = _standalone_statmech_payload(
        species_entry={"smiles": "CCCCCCCCCCCCCC", "charge": 0, "multiplicity": 1},
        calculations=[{"key": "opt0", "calculation": _opt_calc_at(_LOT_A)}],
        source_calculations=[{"calculation_key": "opt0", "role": "opt"}],
        energy_level_of_theory=_LOT_A,
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}")
    levels = detail.json()["record"]["levels"]
    assert levels["energy_source"] == "opt"
    assert levels["energy"]["method"] == "B3LYP"


def test_opt_carrying_freq_result_supplies_the_frequency_level():
    """R1's other fallback, unit-tested directly against ``derive_levels``:
    no separate 'freq' link, but the 'opt' calculation itself carries
    frequency results (a combined opt+freq job) -- the frequency level
    comes from that same calculation.

    Not exercised through an upload: ``CalculationWithResultsPayload``
    cross-validates that a calc's result block matches its declared
    ``type`` (a ``type='opt'`` calc may carry ``opt_result`` and nothing
    else), so a single uploaded calculation cannot carry both
    ``opt_result`` and ``freq_result`` today. The *derivation* logic this
    fallback exercises is still real (a future combined-job parser could
    populate both typed result rows on one ``calculation`` id, which is
    all ``derive_levels`` looks at), so it is tested directly against the
    pure function rather than skipped.
    """
    opt_with_freq = RoleCalcInfo(lot_id=101, carries_frequencies=True)
    opt_without_freq = RoleCalcInfo(lot_id=101, carries_frequencies=False)

    with_freq = derive_levels(opts=[opt_with_freq])
    assert with_freq.frequency_lot_id == 101
    assert with_freq.geometry_lot_id == 101

    without_freq = derive_levels(opts=[opt_without_freq])
    assert without_freq.frequency_lot_id is None
    assert without_freq.geometry_lot_id == 101

    # A separate freq link always wins over the opt's own, even when the
    # opt also carries frequencies.
    with_separate_freq = derive_levels(
        opts=[opt_with_freq], freqs=[RoleCalcInfo(lot_id=202)]
    )
    assert with_separate_freq.frequency_lot_id == 202


# ---------------------------------------------------------------------------
# R6 -- thermo derived from a statmech basis
# ---------------------------------------------------------------------------


def test_thermo_inherits_statmech_levels(client):
    species_smiles = "CCCCCCCCCCCCCCC"
    sm_payload = _standalone_statmech_payload(
        species_entry={"smiles": species_smiles, "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "opt0", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "sp0", "calculation": _sp_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
    )
    sm_resp = client.post("/api/v1/uploads/statmech", json=sm_payload)
    assert sm_resp.status_code == 201, sm_resp.text
    sm_id = sm_resp.json()["id"]

    thermo_payload = _thermo_payload(species_smiles, existing_statmech_id=sm_id)
    t_resp = client.post("/api/v1/uploads/thermo", json=thermo_payload)
    assert t_resp.status_code == 201, t_resp.text
    species_entry_id = t_resp.json()["species_entry_id"]

    read = client.get(
        f"/api/v1/scientific/species-entries/{species_entry_id}/thermo"
    )
    assert read.status_code == 200, read.text
    records = read.json()["records"]
    assert len(records) == 1
    levels = records[0]["levels"]
    # Inherited wholesale from the statmech basis: same shape as the
    # statmech record's own levels above (opt=A, sp=B, energy_source=sp).
    assert levels["energy_source"] == "sp"
    assert levels["geometry"]["method"] == "B3LYP"
    assert levels["energy"]["method"] == "wB97X-D"


def test_thermo_own_roles_path_valid_different_levels(client):
    """A thermo that links its own opt/freq/sp (no statmech basis) runs the
    same R1-R5 rules directly against those links."""
    species_smiles = "CCCCCCCCCCCCCCCC"
    payload = _thermo_payload(
        species_smiles,
        calculations=[
            {"key": "opt0", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "sp0", "calculation": _sp_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/thermo", json=payload)
    assert resp.status_code == 201, resp.text
    species_entry_id = resp.json()["species_entry_id"]

    read = client.get(
        f"/api/v1/scientific/species-entries/{species_entry_id}/thermo"
    )
    assert read.status_code == 200, read.text
    levels = read.json()["records"][0]["levels"]
    assert levels["energy_source"] == "sp"
    assert levels["geometry"]["method"] == "B3LYP"
    assert levels["energy"]["method"] == "wB97X-D"


def test_thermo_energy_level_declared_with_statmech_link_is_refused_at_schema(
    client,
):
    """A schema-level guard (not one of R1-R6's coded refusals): declaring
    ``energy_level_of_theory`` together with ``existing_statmech_id`` is
    ambiguous (levels are then the statmech's, wholesale), so it is
    refused before either reaches the workflow."""
    payload = _thermo_payload(
        "CCCCCCCCCCCCCCCCC",
        existing_statmech_id=1,
        energy_level_of_theory=_LOT_A,
    )
    resp = client.post("/api/v1/uploads/thermo", json=payload)
    assert resp.status_code == 422, resp.text
    assert "energy_level_of_theory" in resp.text
    assert "existing_statmech_id" in resp.text


def test_thermo_existing_statmech_link_still_enforces_r2_prime(client, db_session):
    """R6 skips only R4' (the declared-level check) when a statmech basis
    is linked -- R2'/R3'/Coverage still apply to whatever the thermo
    links itself, on its *own* role_links, independent of the basis."""
    species_smiles = "CCCCCCCCCCCCCCCCCC"
    sm_payload = _standalone_statmech_payload(
        species_entry={"smiles": species_smiles, "charge": 0, "multiplicity": 1},
        calculations=[{"key": "opt0", "calculation": _opt_calc_at(_LOT_A)}],
        source_calculations=[{"calculation_key": "opt0", "role": "opt"}],
    )
    sm_resp = client.post("/api/v1/uploads/statmech", json=sm_payload)
    assert sm_resp.status_code == 201, sm_resp.text
    sm_id = sm_resp.json()["id"]

    before = _count(db_session, Thermo)
    thermo_payload = _thermo_payload(
        species_smiles,
        existing_statmech_id=sm_id,
        calculations=[
            {"key": "opt_a", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "opt_b", "calculation": _opt_calc_at(_LOT_B)},
            {"key": "sp_a", "calculation": _sp_calc_at(_LOT_A)},
        ],
        source_calculations=[
            {"calculation_key": "opt_a", "role": "opt"},
            {"calculation_key": "opt_b", "role": "opt"},
            {"calculation_key": "sp_a", "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/thermo", json=thermo_payload)
    # Neither opt declares geometry (inline calcs), so with two opts
    # linked, sp_a is "not comparable" to either -- Coverage sees both
    # opts uncovered and refuses, exactly as it would with no statmech
    # basis linked at all.
    body = _assert_code(resp, _REQUIRES_SP_THERMO)
    assert "uncovered_opt_calculation_refs" in body["context"]
    assert len(body["context"]["uncovered_opt_calculation_refs"]) == 2
    assert _count(client._db_session, Thermo) == before


# ---------------------------------------------------------------------------
# Ensemble bundle paths: computed-species, computed-reaction, nested conformers
# ---------------------------------------------------------------------------


def _bundle_conformer(
    index: int,
    *,
    opt_lot: dict,
    xyz_z: float,
    sp_lot: dict | None = None,
    sp_xyz_z: float | None = None,
) -> dict:
    """One conformer block for a computed-species bundle: an opt-typed
    primary calculation, and optionally an sp-typed additional
    calculation. The sp's input geometry defaults to this same
    conformer's own geometry (fallback, so it matches this conformer's
    opt); ``sp_xyz_z`` overrides it explicitly to build a genuine R3'
    mismatch."""
    conf: dict = {
        "key": f"c{index}",
        "geometry": {"xyz_text": _methyl_xyz(xyz_z)},
        "primary_calculation": {
            "key": f"opt{index}",
            "type": "opt",
            "level_of_theory": opt_lot,
            "software_release": _SOFTWARE,
            "opt_result": {"converged": True},
        },
    }
    if sp_lot is not None:
        sp_calc: dict = {
            "key": f"sp{index}",
            "type": "sp",
            "level_of_theory": sp_lot,
            "software_release": _SOFTWARE,
            "sp_result": {"electronic_energy_hartree": -76.437},
        }
        if sp_xyz_z is not None:
            sp_calc["input_geometries"] = [{"xyz_text": _methyl_xyz(sp_xyz_z)}]
        conf["additional_calculations"] = [sp_calc]
    return conf


def _computed_species_bundle_payload(
    *, conformers: list[dict], statmech_source_calculations: list[dict]
) -> dict:
    return {
        "species_entry": dict(_METHYL_SPECIES),
        "conformers": conformers,
        "statmech": {
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
            "source_calculations": statmech_source_calculations,
        },
    }


def test_computed_species_statmech_forgot_sp_is_refused(client):
    """Two conformers; only one has a covering sp. Coverage fires with no
    declared energy level at all -- this is the 'forgot the SP for one
    conformer' shape the owner named directly."""
    payload = _computed_species_bundle_payload(
        conformers=[
            _bundle_conformer(0, opt_lot=_LOT_A, xyz_z=0.0, sp_lot=_LOT_A),
            _bundle_conformer(1, opt_lot=_LOT_A, xyz_z=0.55),
        ],
        statmech_source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
            {"calculation_key": "opt1", "role": "opt"},
        ],
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    body = _assert_code(resp, _REQUIRES_SP_STATMECH)
    assert "uncovered_opt_calculation_refs" in body["context"]
    assert len(body["context"]["uncovered_opt_calculation_refs"]) == 1
    _assert_context_values_are_refs(body)


def test_computed_species_statmech_sp_on_other_geometry_is_refused(client):
    """One conformer; its sp explicitly declares a different input
    geometry than the opt it is supposed to support -- R3'."""
    payload = _computed_species_bundle_payload(
        conformers=[
            _bundle_conformer(
                0, opt_lot=_LOT_A, xyz_z=0.0, sp_lot=_LOT_A, sp_xyz_z=0.91
            ),
        ],
        statmech_source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    body = _assert_code(resp, _GEOMETRY_MISMATCH_STATMECH)
    _assert_context_values_are_refs(body)


def test_computed_species_statmech_two_level_sps_is_refused(client):
    """Two conformers, each correctly covered by its own sp, but the two
    sps disagree on level of theory."""
    payload = _computed_species_bundle_payload(
        conformers=[
            _bundle_conformer(0, opt_lot=_LOT_A, xyz_z=0.0, sp_lot=_LOT_A),
            _bundle_conformer(1, opt_lot=_LOT_A, xyz_z=0.55, sp_lot=_LOT_B),
        ],
        statmech_source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
            {"calculation_key": "opt1", "role": "opt"},
            {"calculation_key": "sp1", "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    body = _assert_code(resp, _AMBIGUOUS_STATMECH)
    assert len(body["context"]["sp_calculation_refs"]) == 2


def test_computed_species_statmech_ensemble_with_per_conformer_sps_accepted(client):
    """The valid ensemble shape: two conformers, each covered by its own
    sp, both sps at the same level of theory -- accepted, and reads back
    unambiguously."""
    payload = _computed_species_bundle_payload(
        conformers=[
            _bundle_conformer(0, opt_lot=_LOT_A, xyz_z=0.0, sp_lot=_LOT_B),
            _bundle_conformer(1, opt_lot=_LOT_A, xyz_z=0.55, sp_lot=_LOT_B),
        ],
        statmech_source_calculations=[
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "sp0", "role": "sp"},
            {"calculation_key": "opt1", "role": "opt"},
            {"calculation_key": "sp1", "role": "sp"},
        ],
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text
    statmech_id = resp.json()["statmech"]["statmech_id"]

    detail = client.get(f"/api/v1/scientific/statmech/{statmech_id}")
    assert detail.status_code == 200, detail.text
    levels = detail.json()["record"]["levels"]
    assert levels["energy_source"] == "sp"
    assert levels["geometry"]["method"] == "B3LYP"
    assert levels["energy"]["method"] == "wB97X-D"


def test_computed_reaction_statmech_forgot_sp_is_refused(client):
    """The fourth statmech write path -- the one ARC actually deposits
    through. Two conformers on the same species; only one has a covering
    sp."""
    payload = {
        "species": [
            {
                "key": "ch3",
                "species_entry": _METHYL_SPECIES,
                "conformers": [
                    {
                        "key": "c0",
                        "geometry": {"key": "g0", "xyz_text": _methyl_xyz(0.0)},
                        "calculation": {
                            "key": "opt0",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_A,
                            "opt_converged": True,
                        },
                    },
                    {
                        "key": "c1",
                        "geometry": {"key": "g1", "xyz_text": _methyl_xyz(0.55)},
                        "calculation": {
                            "key": "opt1",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_A,
                            "opt_converged": True,
                        },
                    },
                ],
                "calculations": [
                    {
                        "key": "sp0",
                        "type": "sp",
                        "geometry_key": "g0",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT_A,
                        "sp_electronic_energy_hartree": -40.5,
                    },
                ],
                "statmech": {
                    "statmech_treatment": "rrho",
                    "external_symmetry": 1,
                    "source_calculations": [
                        {"calculation_key": "opt0", "role": "opt"},
                        {"calculation_key": "sp0", "role": "sp"},
                        {"calculation_key": "opt1", "role": "opt"},
                    ],
                },
            },
            {
                "key": "h",
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "conformers": [
                    {
                        "key": "hc",
                        "geometry": {"key": "hg", "xyz_text": "1\nH\nH 0.0 0.0 0.0"},
                        "calculation": {
                            "key": "hopt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_A,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch3", "h"],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    body = _assert_code(resp, _REQUIRES_SP_STATMECH)
    assert "uncovered_opt_calculation_refs" in body["context"]


def test_computed_reaction_statmech_declared_energy_level_is_honored(client):
    """``BundleStatmechIn.energy_level_of_theory`` (added post-review to
    keep the two bundle roots' statmech models symmetric, per
    ``tests/schemas/test_bundle_root_model_symmetry.py``) is actually
    threaded through and checked, the same as the species-root bundle's:
    a single opt, a declared level that disagrees with it, no sp linked
    -- refused (R4')."""
    payload = {
        "species": [
            {
                "key": "ch3",
                "species_entry": _METHYL_SPECIES,
                "conformers": [
                    {
                        "key": "c0",
                        "geometry": {"key": "g0", "xyz_text": _methyl_xyz(0.0)},
                        "calculation": {
                            "key": "opt0",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_A,
                            "opt_converged": True,
                        },
                    },
                ],
                "calculations": [],
                "statmech": {
                    "statmech_treatment": "rrho",
                    "external_symmetry": 1,
                    "source_calculations": [
                        {"calculation_key": "opt0", "role": "opt"},
                    ],
                    "energy_level_of_theory": _LOT_B,
                },
            },
            {
                "key": "h",
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "conformers": [
                    {
                        "key": "hc",
                        "geometry": {"key": "hg", "xyz_text": "1\nH\nH 0.0 0.0 0.0"},
                        "calculation": {
                            "key": "hopt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_A,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch3", "h"],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    body = _assert_code(resp, _REQUIRES_SP_STATMECH)
    assert "declared_level_of_theory_ref" in body["context"]
    assert "wB97X-D/def2-TZVP" in body["detail"]
    assert "B3LYP/6-31G(d)" in body["detail"]


def test_nested_conformer_statmech_forgot_sp_is_refused(client):
    """The statmech block nested under ``/uploads/conformers``, R4': the
    depositor declares an intended energy level but links no 'sp' at all
    -- the "forgot the SP" mistake in the one shape this path can
    actually carry.

    Not the multi-conformer Coverage shape the other three probes use:
    ``additional_calculations`` on a conformer upload only accepts
    ``freq``/``sp`` (never a second ``opt``, confirmed by
    ``ConformerUploadRequest``'s own validator), and this payload's
    nested ``statmech.source_calculations`` cannot chain in an ``opt``
    from a *different* request the way the standalone upload's
    ``existing_calculation_id`` can. So within one request there is
    structurally only ever one linked ``opt`` here -- which is exactly
    why R4' (declared level vs. the single opt, still generalised to
    "every opt" in ``calculation_levels.py``) is the reachable probe for
    this path, not Coverage.
    """
    species = dict(_METHYL_SPECIES)
    payload = {
        "species_entry": species,
        "geometry": {"xyz_text": _methyl_xyz(0.0)},
        "calculation": {
            "key": "opt_primary",
            "type": "opt",
            "level_of_theory": _LOT_A,
            "software_release": _SOFTWARE,
            "opt_result": {"converged": True},
        },
        "label": "nested-forgot-sp",
        "statmech": {
            "external_symmetry": 1,
            "statmech_treatment": "rrho",
            "uploaded_calculation_role": "opt",
            "energy_level_of_theory": _LOT_B,
        },
    }
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    body = _assert_code(resp, _REQUIRES_SP_STATMECH)
    assert "opt_calculation_refs" in body["context"]
