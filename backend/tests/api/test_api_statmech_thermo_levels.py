"""R1-R6: geometry/frequency/energy levels of theory, upload validation and reads.

Owner decision, 2026-09 ("statmech-level-roles"): a depositor may run the
optimisation and the single point at two different levels of theory, and
TCKDB must both *display* that split honestly and *catch* the deposit that
forgets half of it. The rules (``app.services.calculation_levels``):

* R1 -- read-time derivation of geometry/frequency/energy levels from
  ``opt``/``freq``/``sp``/``composite``/``imported`` role links. Never
  blocking.
* R2 -- at most one ``opt``, one ``freq``, one ``sp`` per record.
* R3 -- a linked ``sp`` must share the linked ``opt``'s own geometry.
* R4/R5 -- a declared ``energy_level_of_theory`` must agree with what is
  actually linked (the ``sp``'s level when an ``sp`` is linked, else the
  ``opt``'s own level).
* R6 -- a thermo record derived from a statmech basis
  (``existing_statmech_id``) inherits that record's levels and skips
  R2-R4 entirely.

Assertions throughout follow the house style: branch on the envelope's
``code``, never on substring presence in ``detail`` (Pydantic echoes
rejected input back into ``detail``, so a naive ``in`` check can pass on
an echo of the *input* rather than evidence the check actually ran) --
except where this file explicitly checks that a LoT method name and a
public ref DO appear in ``detail`` (the task's own requirement), which is
checked in addition to the coded ``context``, never instead of it.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_A = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_B = {"method": "wB97X-D", "basis": "def2-TZVP"}

_ROLE_DUPLICATE_STATMECH = "statmech_role_duplicate"
_ROLE_DUPLICATE_THERMO = "thermo_role_duplicate"
_GEOMETRY_MISMATCH_STATMECH = "statmech_sp_geometry_mismatch"
_REQUIRES_SP_STATMECH = "statmech_energy_level_requires_sp"
_CONTRADICTION_STATMECH = "statmech_energy_level_contradiction"


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
    """Every ``context`` key naming a row ends ``_ref``, never ``_id``.

    The house rule (no PKs/FKs in user-facing error detail): a client
    gets a public ref for anything it can act on, never a raw PK. ``role``
    and ``count`` are plain facts, not row references, so they are exempt.
    """
    context = body.get("context", {})
    for key in context:
        if key in ("role", "count", "calculation_refs"):
            continue
        assert key.endswith("_ref"), (
            f"context key {key!r} looks like a disclosed row id: {context}"
        )


def _opt_calc_at(lot: dict) -> dict:
    return {
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "opt_result": {"converged": True},
    }


def _sp_calc_at(lot: dict) -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }


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


def _deposit_conformer(
    client, *, label: str, species: dict, calc_type: str, lot: dict, xyz_text: str, key: str
) -> dict:
    """Deposit one conformer with a single primary calculation.

    :returns: The parsed ``/uploads/conformers`` response body.
    """
    calc = (
        _opt_calc_at(lot) if calc_type == "opt" else _sp_calc_at(lot)
    )
    calc["key"] = key
    payload = {
        "species_entry": species,
        "geometry": {"xyz_text": xyz_text},
        "calculation": calc,
        "label": label,
    }
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# R2 -- at most one opt/freq/sp
# ---------------------------------------------------------------------------


def test_statmech_duplicate_opt_role_is_refused(client, db_session):
    """Two 'opt' links on one standalone statmech record -- refused (R2)."""
    before = _count(db_session, Statmech)
    payload = _standalone_statmech_payload(
        species_entry={"smiles": "CCCCCCCC", "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "opt_a", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "opt_b", "calculation": _opt_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt_a", "role": "opt"},
            {"calculation_key": "opt_b", "role": "opt"},
        ],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    body = _assert_code(resp, _ROLE_DUPLICATE_STATMECH)
    assert body["context"]["role"] == "opt"
    assert body["context"]["count"] == 2
    _assert_no_ids_disclosed(body)
    # Nothing persisted: the whole submission fails as a unit.
    assert _count(client._db_session, Statmech) == before


def test_thermo_duplicate_opt_role_is_refused(client, db_session):
    """Thermo's own copy of R2, own-roles path (no statmech basis linked)."""
    before = _count(db_session, Thermo)
    payload = _thermo_payload(
        "CCCCCCCCC",
        calculations=[
            {"key": "opt_a", "calculation": _opt_calc_at(_LOT_A)},
            {"key": "opt_b", "calculation": _opt_calc_at(_LOT_B)},
        ],
        source_calculations=[
            {"calculation_key": "opt_a", "role": "opt"},
            {"calculation_key": "opt_b", "role": "opt"},
        ],
    )
    resp = client.post("/api/v1/uploads/thermo", json=payload)
    body = _assert_code(resp, _ROLE_DUPLICATE_THERMO)
    assert body["context"]["role"] == "opt"
    assert body["context"]["count"] == 2
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Thermo) == before


# ---------------------------------------------------------------------------
# R3 -- a linked sp must share the linked opt's own geometry
# ---------------------------------------------------------------------------


def test_statmech_sp_geometry_mismatch_is_refused(client, db_session):
    species = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}
    before = _count(db_session, Statmech)

    conf_opt = _deposit_conformer(
        client,
        label="geo-opt",
        species=species,
        calc_type="opt",
        lot=_LOT_A,
        xyz_text=_methyl_xyz(0.0),
        key="opt_a",
    )
    conf_sp = _deposit_conformer(
        client,
        label="geo-sp",
        species=species,
        calc_type="sp",
        lot=_LOT_A,
        xyz_text=_methyl_xyz(0.37),
        key="sp_b",
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
    assert set(body["context"]) == {"opt_calculation_ref", "sp_calculation_ref"}
    for ref in body["context"].values():
        assert ref.startswith("calc_"), ref
    _assert_no_ids_disclosed(body)
    assert str(opt_id) not in str(body)
    assert str(sp_id) not in str(body)
    # Nothing new: the two conformer deposits above are unaffected, but no
    # statmech row was created for the failed request.
    assert _count(client._db_session, Statmech) == before


# ---------------------------------------------------------------------------
# R4 -- a declared energy_level_of_theory must agree with what is linked
# ---------------------------------------------------------------------------


def test_statmech_energy_level_requires_sp(client, db_session):
    """Declared level != opt level, no sp linked -- refused (R4)."""
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
        "opt_calculation_ref",
        "opt_level_of_theory_ref",
    }
    for ref in body["context"].values():
        assert ref is None or (
            ref.startswith("calc_") or ref.startswith("lot_")
        ), ref
    _assert_no_ids_disclosed(body)
    assert _count(client._db_session, Statmech) == before


def test_statmech_energy_level_contradiction(client, db_session):
    """Declared level disagrees with the linked sp's own level -- refused (R4)."""
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
        "sp_calculation_ref",
        "sp_level_of_theory_ref",
    }
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


# ---------------------------------------------------------------------------
# R6 -- thermo derived from a statmech basis inherits its levels
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
