"""A statmech record declares the model it supplies, through the real upload routes.

The block is optional and stored as made: an omitted fact stays omitted (never unity, never projected, never counted),
a record deposited without one makes none, and every reference is a local key or a public ref that the backend checks
against the declaring record's own subject.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.common import StatmechModelScopeKind
from app.db.models.species import ConformerGroup
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.db.models.structure_determination import StructureDetermination
from tests.api.test_api_bundle_extras_622 import _calc_ids, _ethane_bundle
from tests.api.test_api_statmech_upload import _freq_calc_payload, _sp_calc_payload, _statmech_payload
from tests.services.scientific_read._factories import make_conformer_group
from tests.workflows.test_computed_reaction_upload import _minimal_payload

URL = "/api/v1/uploads/statmech"
SPECIES = "/api/v1/uploads/computed-species"
REACTION = "/api/v1/uploads/computed-reaction"
CONFORMER = "/api/v1/uploads/conformers"
WTR = {"name": "ARC", "version": "1.1.0"}
LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _declaration(**changes) -> dict:
    declared = {"version": 1, "scope_kind": "atom", "workflow_tool_release": WTR}
    declared.update(changes)
    return declared


def _water(**overrides) -> dict:
    return _statmech_payload(species_entry={"smiles": "O", "charge": 0, "multiplicity": 1}, **overrides)


def _post(client, payload):
    return client.post(URL, json=payload)


def _declarations(db_session) -> list[StatmechModelDeclaration]:
    return list(db_session.scalars(select(StatmechModelDeclaration).order_by(StatmechModelDeclaration.id)))


def _ref(db_session, statmech_id: int) -> str:
    return db_session.get(Statmech, statmech_id).public_ref


def test_a_record_deposited_without_a_declaration_makes_none(client, db_session):
    assert _post(client, _water()).status_code == 201
    assert _declarations(db_session) == []


def test_an_atom_declaration_is_stored_as_made_and_an_omitted_fact_stays_omitted(client, db_session):
    recipe = {"energy_zero": "e0", "zpe_included_in_energies": True, "vibrational_scheme": "harmonic"}
    resp = _post(client, _water(model_declaration=_declaration(recipe=recipe)))
    assert resp.status_code == 201, resp.text[:600]
    (row,) = _declarations(db_session)
    assert row.statmech_id == resp.json()["id"] and row.scope_kind is StatmechModelScopeKind.atom
    assert row.schema_version == 1 and row.literature_id is None and row.workflow_tool_release_id is not None
    assert row.recipe == recipe  # nothing was added: no scale application, no projection, no symmetry accounting
    assert "scale_application" not in row.recipe and "projected_mode_indices" not in row.recipe
    assert len(row.content_hash) == 64 and row.public_ref.startswith("smd_")


def test_identical_content_has_one_hash_and_different_content_another(client, db_session):
    declared = _declaration(recipe={"energy_zero": "e0"})
    assert _post(client, _water(model_declaration=declared)).status_code == 201
    assert _post(client, _water(model_declaration=declared)).status_code == 201
    other = _declaration(recipe={"energy_zero": "electronic"})
    assert _post(client, _water(model_declaration=other)).status_code == 201
    a, b, c = _declarations(db_session)
    assert a.content_hash == b.content_hash != c.content_hash
    assert a.public_ref != b.public_ref  # two records, two declarations; the hash is a checksum, not the identity


def test_sources_by_key_by_imported_literature_and_by_scale_factor_need_no_fabricated_job(client, db_session):
    scale = {"level_of_theory": LOT, "scale_kind": "fundamental", "value": 0.967, "software": {"name": "Gaussian"}}
    payload = _water(
        calculations=[{"key": "f1", "calculation": _freq_calc_payload()}, {"key": "s1", "calculation": _sp_calc_payload()}],
        source_calculations=[{"calculation_key": "f1", "role": "freq"}, {"calculation_key": "s1", "role": "sp"}],
        model_declaration=_declaration(
            recipe={"scale_application": "applied", "scale_contributions": ["fundamental"]},
            sources=[
                {"source_key": "curv", "role": "curvature", "calculation_key": "f1"},
                {"source_key": "e", "role": "absolute_energy", "calculation_key": "s1"},
                {"source_key": "fsf", "role": "scale_factor", "freq_scale_factor": scale},
                {"source_key": "imp", "role": "parameters", "literature": {"kind": "article", "title": "Imported constants", "year": 2020}},
            ],
        ),
    )
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text[:800]
    sources = {s.source_key: s for s in db_session.scalars(select(StatmechModelSource))}
    assert set(sources) == {"curv", "e", "fsf", "imp"}
    assert sources["curv"].calculation_id is not None and sources["curv"].literature_id is None
    assert sources["fsf"].frequency_scale_factor_id is not None and sources["fsf"].calculation_id is None
    assert sources["imp"].literature_id is not None and sources["imp"].calculation_id is None


def test_an_undeclared_calculation_key_is_refused_with_the_shared_code_and_nothing_is_written(client, db_session):
    declared = _declaration(sources=[{"source_key": "x", "role": "curvature", "calculation_key": "ghost"}])
    resp = _post(client, _water(model_declaration=declared))
    assert resp.status_code == 422, resp.text[:600]
    body = resp.json()
    assert body["code"] == "statmech_calculation_key_undeclared" and body["context"]["key"] == "ghost"
    assert _declarations(db_session) == []


def test_an_unsupported_version_and_a_contradiction_are_refused_before_anything_is_written(client, db_session):
    refused = _post(client, _water(model_declaration=_declaration(version=2)))
    assert refused.status_code == 422 and refused.json()["code"] == "statmech_model_declaration_version_unsupported"
    string_version = _post(client, _water(model_declaration=_declaration(version="1")))
    assert string_version.status_code == 422
    no_attribution = _declaration()
    del no_attribution["workflow_tool_release"]
    assert _post(client, _water(model_declaration=no_attribution)).status_code == 422
    scaled = _declaration(recipe={"scale_application": "unknown", "scale_contributions": ["fundamental"]})
    assert _post(client, _water(model_declaration=scaled)).status_code == 422
    assert _declarations(db_session) == []


def test_a_basin_names_a_group_of_the_records_own_species_entry(client, db_session):
    first = _post(client, _water())
    entry_id = first.json()["species_entry_id"]
    group = make_conformer_group(db_session, db_session.get(Statmech, first.json()["id"]).species_entry)
    ok = _post(client, _water(model_declaration=_declaration(scope_kind="basin", conformer_group_ref=group.public_ref)))
    assert ok.status_code == 201, ok.text[:600]
    (row,) = _declarations(db_session)
    assert row.conformer_group_id == group.id and entry_id == db_session.get(Statmech, ok.json()["id"]).species_entry_id

    other = _statmech_payload(species_entry={"smiles": "N", "charge": 0, "multiplicity": 1})
    foreign = _post(client, other)
    foreign_group = make_conformer_group(db_session, db_session.get(Statmech, foreign.json()["id"]).species_entry)
    crossed = _post(
        client, _water(model_declaration=_declaration(scope_kind="basin", conformer_group_ref=foreign_group.public_ref))
    )
    assert crossed.status_code == 422
    assert crossed.json()["code"] == "statmech_model_declaration_mismatch" and crossed.json()["context"]["reason"] == "basin"

    unstated = _post(client, _water(model_declaration=_declaration(scope_kind="basin")))
    assert unstated.status_code == 422 and unstated.json()["context"]["reason"] == "basin"

    missing = _post(
        client, _water(model_declaration=_declaration(scope_kind="basin", conformer_group_ref="cg_" + "a" * 26))
    )
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_conformer_group_ref"


def test_an_ensemble_cites_existing_records_of_the_same_species_and_a_repeat_is_not_another_state(client, db_session):
    a = _post(client, _water()).json()["id"]
    b = _post(client, _water()).json()["id"]
    foreign = _post(client, _statmech_payload(species_entry={"smiles": "N", "charge": 0, "multiplicity": 1})).json()["id"]
    ref_a, ref_b, ref_foreign = (_ref(db_session, i) for i in (a, b, foreign))

    def ensemble(*members) -> dict:
        return _water(
            model_declaration=_declaration(
                scope_kind="explicit_equilibrium_ensemble",
                members=[
                    {"member_key": f"m{i}", "statmech_ref": ref, "counting_multiplicity": 2 if i == 0 else None}
                    for i, ref in enumerate(members)
                ],
            )
        )

    ok = _post(client, ensemble(ref_a, ref_b))
    assert ok.status_code == 201, ok.text[:600]
    members = list(db_session.scalars(select(StatmechModelMember).order_by(StatmechModelMember.id)))
    assert [m.member_key for m in members] == ["m0", "m1"]
    assert members[0].counting_multiplicity == 2 and members[1].counting_multiplicity is None  # not stated is not one

    twice = _post(client, ensemble(ref_a, ref_a))
    assert twice.status_code == 422  # a repeated record is evidence, not a second state
    crossed = _post(client, ensemble(ref_a, ref_foreign))
    assert crossed.status_code == 422
    assert crossed.json()["code"] == "statmech_model_declaration_mismatch" and crossed.json()["context"]["reason"] == "member_owner"
    ghost = _post(client, ensemble("sm_" + "a" * 26))
    assert ghost.status_code == 404 and ghost.json()["code"] == "unknown_statmech_ref"
    assert len(_declarations(db_session)) == 1  # only the accepted ensemble wrote anything

    no_members = _post(client, _water(model_declaration=_declaration(scope_kind="explicit_equilibrium_ensemble")))
    assert no_members.status_code == 422
    stray = _post(client, _water(model_declaration=_declaration(members=[{"member_key": "m", "statmech_ref": ref_a}])))
    assert stray.status_code == 422


def test_a_member_energy_is_stored_with_its_convention_and_refused_without_one(client, db_session):
    ref = _ref(db_session, _post(client, _water()).json()["id"])

    def ensemble(**member) -> dict:
        return _water(
            model_declaration=_declaration(
                scope_kind="explicit_equilibrium_ensemble",
                members=[{"member_key": "m", "statmech_ref": ref, **member}],
            )
        )

    ok = _post(client, ensemble(relative_energy_kj_mol=2.5, relative_energy_convention="electronic_only"))
    assert ok.status_code == 201, ok.text[:600]
    (member,) = db_session.scalars(select(StatmechModelMember)).all()
    assert member.relative_energy_kj_mol == 2.5 and member.relative_energy_convention.value == "electronic_only"
    (declaration,) = _declarations(db_session)

    refusals = {
        "energy without a convention": {"relative_energy_kj_mol": 2.5},
        "convention without an energy": {"relative_energy_convention": "electronic_plus_zpe"},
        "other": {"relative_energy_kj_mol": 2.5, "relative_energy_convention": "other"},
        "unknown convention": {"relative_energy_kj_mol": 2.5, "relative_energy_convention": "zpe-ish"},
    }
    for label, member_fields in refusals.items():
        refused = _post(client, ensemble(**member_fields))
        assert refused.status_code == 422 and refused.json()["code"] == "request_validation_error", label
        assert "relative_energy_convention" in refused.text, label
    assert [d.id for d in _declarations(db_session)] == [declaration.id]  # nothing was written by a refusal


def test_a_member_without_an_energy_hashes_without_the_convention_key(client, db_session, monkeypatch):
    """A declaration's checksum is unchanged for a member that states no energy: the key is present only when stated."""
    from app.services import statmech_declaration_resolution as resolution

    seen: list[list[dict]] = []
    real = resolution.declaration_content_hash

    def spy(**kw):
        seen.append(kw["members"])
        return real(**kw)

    monkeypatch.setattr(resolution, "declaration_content_hash", spy)
    ref = _ref(db_session, _post(client, _water()).json()["id"])

    def ensemble(**member):
        return _water(
            model_declaration=_declaration(
                scope_kind="explicit_equilibrium_ensemble", members=[{"member_key": "m", "statmech_ref": ref, **member}]
            )
        )

    assert _post(client, ensemble()).status_code == 201
    (row,) = _declarations(db_session)
    db_session.refresh(row)
    assert resolution.recompute_content_hash(db_session, row) == row.content_hash
    assert len(seen) == 2 and all("relative_energy_convention" not in m for members in seen for m in members)
    assert _post(client, ensemble(relative_energy_kj_mol=1.0, relative_energy_convention="electronic_only")).status_code == 201
    assert seen[-1][0]["relative_energy_convention"] == "electronic_only"


def test_an_ensembles_energies_share_one_convention_that_agrees_with_the_recipe(client, db_session):
    a = _ref(db_session, _post(client, _water()).json()["id"])
    b = _ref(db_session, _post(client, _water()).json()["id"])

    def ensemble(first, second, energy_zero=None):
        recipe = {"energy_zero": energy_zero} if energy_zero else {}
        members = [
            {"member_key": "a", "statmech_ref": a, **first},
            {"member_key": "b", "statmech_ref": b, **second},
        ]
        return _water(model_declaration=_declaration(scope_kind="explicit_equilibrium_ensemble", members=members, recipe=recipe))

    zpe = {"relative_energy_kj_mol": 1.0, "relative_energy_convention": "electronic_plus_zpe"}
    elec = {"relative_energy_kj_mol": 2.0, "relative_energy_convention": "electronic_only"}
    mixed = _post(client, ensemble(zpe, elec))
    contradicting = _post(client, ensemble(zpe, {}, energy_zero="electronic"))
    corrected = _post(client, ensemble(zpe, {}, energy_zero="e0"))
    for refused in (mixed, contradicting):
        assert refused.status_code == 422 and refused.json()["code"] == "request_validation_error"
    assert "one relative_energy_convention" in mixed.text and "contradicts" in contradicting.text
    assert corrected.status_code == 201, corrected.text[:400]  # agreeing; the member with no energy is unaffected
    assert _post(client, ensemble(elec, {}, energy_zero="electronic")).status_code == 201


def test_a_conformer_deposit_declares_its_own_basin_without_naming_it(client, db_session):
    from tests.api.test_api_provenance_warnings import _statmech_payload as _conformer_statmech  # noqa: F401

    payload = {
        "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": _ethane_bundle()["conformers"][0]["geometry"]["xyz_text"]},
        "calculation": {"type": "opt", "software_release": {"name": "Gaussian", "version": "16"}, "level_of_theory": LOT, "opt_result": {"converged": True}},
        "statmech": {
            "scientific_origin": "computed",
            "external_symmetry": 6,
            "model_declaration": _declaration(scope_kind="basin"),
        },
    }
    resp = client.post(CONFORMER, json=payload)
    assert resp.status_code == 201, resp.text[:800]
    (row,) = _declarations(db_session)
    group = db_session.get(ConformerGroup, row.conformer_group_id)
    assert row.scope_kind is StatmechModelScopeKind.basin
    assert group.species_entry_id == db_session.get(Statmech, row.statmech_id).species_entry_id


def test_a_species_bundle_statmech_declares_a_model_over_the_bundles_own_calculations(client, db_session):
    payload = _ethane_bundle()
    payload["statmech"] = {
        "scientific_origin": "computed",
        "source_calculations": [{"calculation_key": "freq0", "role": "freq"}],
        "model_declaration": _declaration(
            sources=[{"source_key": "curv", "role": "curvature", "calculation_key": "freq0"}]
        ),
    }
    resp = client.post(SPECIES, json=payload)
    assert resp.status_code == 201, resp.text[:800]
    (source,) = list(db_session.scalars(select(StatmechModelSource)))
    assert source.calculation_id == _calc_ids(resp)["freq0"]

    bad = _ethane_bundle()
    bad["statmech"] = {"model_declaration": _declaration(sources=[{"source_key": "c", "role": "curvature", "calculation_key": "nope"}])}
    refused = client.post(SPECIES, json=bad)
    assert refused.status_code == 422 and refused.json()["code"] == "statmech_calculation_key_undeclared"


def _ts_with_statmech(**declaration) -> dict:
    payload = _minimal_payload()
    ts = payload["transition_state"]
    ts["structure_determinations"] = [
        {
            "key": "saddle",
            "target_kind": "saddle_point",
            "evaluated_geometry": {"calculation_key": "ts-opt"},
            "sources": [
                {"role": "geometry_optimization", "calculation_key": "ts-opt"},
                {"role": "curvature", "calculation_key": "ts-freq"},
            ],
            "workflow_tool_release": WTR,
        }
    ]
    ts["statmech"] = {
        "scientific_origin": "computed",
        "source_calculations": [{"calculation_key": "ts-freq", "role": "freq"}],
        "model_declaration": _declaration(scope_kind="ts_determination", determination_key="saddle", **declaration),
    }
    return payload


def test_a_ts_bundle_declares_a_transverse_model_on_a_determination_it_declares_in_the_same_deposit(client, db_session):
    resp = client.post(REACTION, json=_ts_with_statmech(sources=[{"source_key": "c", "role": "curvature", "calculation_key": "ts-freq"}]))
    assert resp.status_code == 201, resp.text[:800]
    (row,) = _declarations(db_session)
    assert row.scope_kind is StatmechModelScopeKind.ts_determination
    assert row.structure_determination_id is not None and row.conformer_group_id is None
    host = db_session.get(Statmech, row.statmech_id)
    assert host.transition_state_entry_id is not None and host.species_entry_id is None


def test_a_ts_scope_names_a_determination_the_deposit_declares_and_only_a_saddle_scope_names_one(client, db_session):
    payload = _ts_with_statmech()
    payload["transition_state"]["statmech"]["model_declaration"]["determination_key"] = "unknown-key"
    refused = client.post(REACTION, json=payload)
    assert refused.status_code == 422, refused.text[:600]
    assert refused.json()["context"]["key"] == "unknown-key"
    assert _declarations(db_session) == []
    both = _ts_with_statmech()
    both["transition_state"]["statmech"]["model_declaration"]["determination_ref"] = "sdet_" + "a" * 26
    assert client.post(REACTION, json=both).status_code == 422
    atom_on_ts = _ts_with_statmech()
    atom_on_ts["transition_state"]["statmech"]["model_declaration"].update(scope_kind="atom")
    atom_on_ts["transition_state"]["statmech"]["model_declaration"].pop("determination_key")
    mismatch = client.post(REACTION, json=atom_on_ts)
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "statmech_model_declaration_mismatch" and mismatch.json()["context"]["reason"] == "owner"


def test_a_species_record_cannot_declare_a_ts_scope(client, db_session):
    refused = _post(
        client, _water(model_declaration=_declaration(scope_kind="ts_determination", determination_ref="sdet_" + "a" * 26))
    )
    assert refused.status_code in (404, 422)
    assert _declarations(db_session) == []


def test_the_stored_hash_is_a_recomputable_checksum_of_what_was_stored(client, db_session):
    from app.services.statmech_declaration_resolution import recompute_content_hash

    scale = {"level_of_theory": LOT, "scale_kind": "fundamental", "value": 0.967, "software": {"name": "Gaussian"}}
    member_ref = _ref(db_session, _post(client, _water()).json()["id"])
    declared = _declaration(
        scope_kind="explicit_equilibrium_ensemble",
        members=[{"member_key": "m", "statmech_ref": member_ref, "counting_multiplicity": 2, "relative_energy_kj_mol": 1.0, "relative_energy_convention": "electronic_plus_zpe"}],
        recipe={"energy_zero": "e0", "scale_application": "applied", "scale_contributions": ["fundamental"]},
        sources=[
            {"source_key": "imp", "role": "parameters", "literature": {"kind": "article", "title": "P", "year": 2021}},
            {"source_key": "fsf", "role": "scale_factor", "freq_scale_factor": scale},
        ],
    )
    assert _post(client, _water(model_declaration=declared)).status_code == 201
    (row,) = _declarations(db_session)
    db_session.refresh(row)
    assert recompute_content_hash(db_session, row) == row.content_hash
    # Every part of what was stored is in the checksum: a row edited behind the write path would be detectable. The
    # database refuses such an edit, so it is made in memory only and never flushed.
    original = row.content_hash
    with db_session.no_autoflush:
        row.recipe = {**row.recipe, "energy_zero": "electronic"}
        assert recompute_content_hash(db_session, row) != original
        row.recipe = {**row.recipe, "energy_zero": "e0"}
        assert recompute_content_hash(db_session, row) == original
        row.members[0].counting_multiplicity = 3
        assert recompute_content_hash(db_session, row) != original
        row.members[0].counting_multiplicity = 2
        row.sources[0].member_key = None
        row.sources[0].source_key = "renamed"
        assert recompute_content_hash(db_session, row) != original
    db_session.expire_all()  # discard the in-memory edits
    assert recompute_content_hash(db_session, row) == original


def test_the_service_refuses_a_ts_scope_whose_determination_it_was_not_given(client, db_session):
    """A key that passed request validation but names nothing the workflow persisted, and a ref that names nothing."""
    from tckdb_schemas.statmech_declarations import StatmechModelDeclaration as Wire

    from app.api.error_contract import CodedValueError
    from app.api.errors import NotFoundError
    from app.services.statmech_declaration_resolution import persist_statmech_model_declaration

    assert client.post(REACTION, json=_ts_with_statmech()).status_code == 201
    (existing,) = _declarations(db_session)
    host_ts = db_session.get(Statmech, existing.statmech_id).transition_state_entry_id
    ts_statmech = Statmech(transition_state_entry_id=host_ts, scientific_origin="computed")
    db_session.add(ts_statmech)
    db_session.flush()

    by_key = Wire.model_validate(_declaration(scope_kind="ts_determination", determination_key="saddle"))
    with pytest.raises(CodedValueError) as caught:
        persist_statmech_model_declaration(db_session, ts_statmech, by_key, determinations_by_key={})
    assert caught.value.code == "statmech_model_declaration_mismatch" and caught.value.context["reason"] == "determination"

    ghost = Wire.model_validate(_declaration(scope_kind="ts_determination", determination_ref="sdet_" + "a" * 26))
    with pytest.raises(NotFoundError) as missing:
        persist_statmech_model_declaration(db_session, ts_statmech, ghost)
    assert missing.value.code == "unknown_structure_determination_ref"

    own_ref = db_session.get(StructureDetermination, existing.structure_determination_id).public_ref
    by_ref = Wire.model_validate(_declaration(scope_kind="ts_determination", determination_ref=own_ref))
    assert persist_statmech_model_declaration(db_session, ts_statmech, by_ref).structure_determination_id == existing.structure_determination_id
    assert _declarations(db_session)[-1].statmech_id == ts_statmech.id
