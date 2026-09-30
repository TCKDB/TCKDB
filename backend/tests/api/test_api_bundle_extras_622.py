"""What the two bundles can now say: transport, rejected rotors, who measured stability.

Issue #622. Four things a depositor could say through a standalone route or
the conformer route and could not say through ``/uploads/computed-species`` or
``/uploads/computed-reaction``:

* a **transport** record for the species (Lennard-Jones parameters and the
  rest), attached to the same species entry as the bundle's thermo and
  statmech;
* an ``invalidated_reason`` on a statmech torsion, so a rotor the producer
  rejected can be said to be rejected;
* a local ``source_calculation_key`` on ``scf_stability``, so a stability
  verdict can name the job that measured it;
* the contract prose (``SoftwareReleaseRef``, path-search ``converged``).

Every assertion is against the database or a read endpoint, not the 201 body:
a 201 proves the payload was accepted, not that anything was stored. The
mutation that falsifies each test is named in its docstring.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationSCFStability
from app.db.models.common import TransportCalculationRole
from app.db.models.statmech import Statmech, StatmechTorsion
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport, TransportSourceCalculation
from app.db.models.workflow import WorkflowToolRelease

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_TOOL = {"name": "ARC", "version": "1.1.0"}

_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
_XYZ_ETHANE = (
    "8\nethane\n"
    "C 0.000000 0.000000 0.765000\n"
    "C 0.000000 0.000000 -0.765000\n"
    "H 1.018000 0.000000 1.157000\n"
    "H -0.509000 0.881600 1.157000\n"
    "H -0.509000 -0.881600 1.157000\n"
    "H -1.018000 0.000000 -1.157000\n"
    "H 0.509000 -0.881600 -1.157000\n"
    "H 0.509000 0.881600 -1.157000"
)

_LJ = {"sigma_angstrom": 2.05, "epsilon_over_k_k": 145.0}


def _ethane_bundle(**overrides) -> dict:
    """One ethane conformer: an opt, a freq and an sp, so keys have somewhere to point."""
    base: dict = {
        "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _XYZ_ETHANE},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
                "additional_calculations": [
                    {
                        "key": "freq0",
                        "type": "freq",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT,
                        "freq_result": {"n_imag": 0},
                    },
                    {
                        "key": "sp0",
                        "type": "sp",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT,
                        "sp_result": {"electronic_energy_hartree": -79.8},
                    },
                ],
            }
        ],
    }
    base.update(overrides)
    return base


def _transport_rows(db_session, species_entry_id: int) -> list[Transport]:
    return list(
        db_session.scalars(
            select(Transport)
            .where(Transport.species_entry_id == species_entry_id)
            .order_by(Transport.id)
        ).all()
    )


def _transport_sources(db_session, transport_id: int) -> set[tuple[int, TransportCalculationRole]]:
    return {
        (link.calculation_id, link.role)
        for link in db_session.scalars(
            select(TransportSourceCalculation).where(
                TransportSourceCalculation.transport_id == transport_id
            )
        ).all()
    }


def _calc_ids(resp) -> dict[str, int]:
    """Bundle-local calculation key -> assigned id, from a species-bundle response."""
    out: dict[str, int] = {}
    for conf in resp.json()["conformers"]:
        for calc in (conf["primary_calculation"], *conf["additional_calculations"]):
            out[calc["key"]] = calc["calculation_id"]
    return out


# ---------------------------------------------------------------------------
# Transport on the species bundle
# ---------------------------------------------------------------------------


def test_species_bundle_stores_transport_on_the_bundles_species_entry(client, db_session):
    """The record lands on the entry the bundle resolved, with its content and links.

    Mutation: drop the ``persist_bundle_transport`` call in
    ``persist_computed_species_upload`` and every assertion below fails,
    starting with the 201's ``transport`` being ``null``.
    """
    payload = _ethane_bundle(
        transport={
            **_LJ,
            "dipole_debye": 0.0,
            "polarizability_angstrom3": 4.47,
            "rotational_relaxation": 1.0,
            "note": "from the Stockmayer fit",
            "source_calculations": [
                {"calculation_key": "opt0", "role": "supporting_geometry"},
                {"calculation_key": "sp0", "role": "full_transport"},
            ],
        }
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]

    body = resp.json()
    rows = _transport_rows(db_session, body["species_entry_id"])
    assert len(rows) == 1, "one transport row per bundle"
    (row,) = rows
    assert body["transport"] == {"transport_id": row.id}
    assert (row.sigma_angstrom, row.epsilon_over_k_k) == (2.05, 145.0)
    assert row.dipole_debye == 0.0
    assert row.polarizability_angstrom3 == 4.47
    assert row.rotational_relaxation == 1.0
    assert row.note == "from the Stockmayer fit"

    calc_ids = _calc_ids(resp)
    assert _transport_sources(db_session, row.id) == {
        (calc_ids["opt0"], TransportCalculationRole.supporting_geometry),
        (calc_ids["sp0"], TransportCalculationRole.full_transport),
    }


def test_species_bundle_transport_reads_back_through_the_read_api(client, db_session):
    """Round trip: what the bundle wrote is what ``/scientific/transport`` reports.

    Mutation: write the source links with the roles swapped, or skip them;
    the ``source_calculations`` comparison fails.
    """
    payload = _ethane_bundle(
        transport={
            **_LJ,
            "source_calculations": [{"calculation_key": "sp0", "role": "full_transport"}],
        }
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]
    row = db_session.get(Transport, resp.json()["transport"]["transport_id"])

    read = client.get(
        f"/api/v1/scientific/transport/{row.public_ref}?include=source_calculations"
    )
    assert read.status_code == 200, read.text[:800]
    record = read.json()["record"]
    assert record["transport"]["transport_ref"] == row.public_ref
    assert record["transport"]["sigma_angstrom"] == 2.05
    sources = record["source_calculations"]
    assert [s["role"] for s in sources] == ["full_transport"]
    sp_ref = db_session.get(Calculation, _calc_ids(resp)["sp0"]).public_ref
    assert sources[0]["calculation_ref"] == sp_ref


def test_transport_shares_the_species_entry_with_thermo_and_statmech(client, db_session):
    """One bundle, one species entry, three products on it.

    Mutation: resolve the transport's entry separately (for example with a
    fresh ``resolve_species_entry`` call that got a different charge); the
    equality below fails.
    """
    payload = _ethane_bundle(
        thermo={
            "enthalpy_reference_kind": "formation_298k",
            "h298_kj_mol": -84.0,
            "s298_j_mol_k": 229.0,
        },
        statmech={"external_symmetry": 6, "statmech_treatment": "rrho"},
        transport={**_LJ},
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]
    body = resp.json()

    thermo = db_session.get(Thermo, body["thermo"]["thermo_id"])
    statmech = db_session.get(Statmech, body["statmech"]["statmech_id"])
    transport = db_session.get(Transport, body["transport"]["transport_id"])
    assert (
        thermo.species_entry_id
        == statmech.species_entry_id
        == transport.species_entry_id
        == body["species_entry_id"]
    )


def test_a_bundle_without_transport_writes_none(client, db_session):
    """Absence stays absence: no row, no ``transport`` in the response.

    Mutation: make the persistence unconditional; a row with no properties
    would be attempted and the 201 would not come back.
    """
    resp = client.post("/api/v1/uploads/computed-species", json=_ethane_bundle())
    assert resp.status_code == 201, resp.text[:800]
    assert resp.json()["transport"] is None
    assert _transport_rows(db_session, resp.json()["species_entry_id"]) == []


def test_transport_is_append_only_across_bundle_uploads(client, db_session):
    """A second deposit adds a record rather than replacing the first.

    Mutation: dedupe on species entry inside the bundle path; one row remains.
    """
    payload = _ethane_bundle(transport={**_LJ})
    first = client.post("/api/v1/uploads/computed-species", json=payload)
    second = client.post("/api/v1/uploads/computed-species", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["species_entry_id"] == second.json()["species_entry_id"]
    rows = _transport_rows(db_session, first.json()["species_entry_id"])
    assert len(rows) == 2


def test_transport_provenance_block_wins_and_bundle_default_fills_in(client, db_session):
    """Thermo's rule: a block-level tool overrides the bundle's; silence inherits it.

    Mutation: ignore ``request.workflow_tool_release`` for transport and the
    inherited assertion fails; always prefer the bundle's and the override
    assertion fails.
    """
    inherits = client.post(
        "/api/v1/uploads/computed-species",
        json=_ethane_bundle(workflow_tool_release=_TOOL, transport={**_LJ}),
    )
    assert inherits.status_code == 201, inherits.text[:800]
    row = db_session.get(Transport, inherits.json()["transport"]["transport_id"])
    tool = db_session.get(WorkflowToolRelease, row.workflow_tool_release_id)
    assert tool.version == "1.1.0"

    overrides = client.post(
        "/api/v1/uploads/computed-species",
        json=_ethane_bundle(
            workflow_tool_release=_TOOL,
            transport={**_LJ, "workflow_tool_release": {"name": "ARC", "version": "9.9.9"}},
        ),
    )
    assert overrides.status_code == 201, overrides.text[:800]
    row = db_session.get(Transport, overrides.json()["transport"]["transport_id"])
    assert db_session.get(WorkflowToolRelease, row.workflow_tool_release_id).version == "9.9.9"


def test_transport_provenance_gaps_are_annotated_under_the_transport_prefix(client):
    """The same provenance warnings the standalone route returns, pointed at ``transport.``.

    Mutation: drop the ``collect_provenance_warnings`` call for transport; the
    set below is empty.
    """
    resp = client.post(
        "/api/v1/uploads/computed-species", json=_ethane_bundle(transport={**_LJ})
    )
    assert resp.status_code == 201, resp.text[:800]
    codes = {
        w["code"] for w in resp.json()["warnings"] if w["field"].startswith("transport.")
    }
    assert "missing_workflow_tool_provenance" in codes
    assert "missing_software_release_provenance" in codes


def test_transport_source_key_must_be_declared(client):
    """A typo is a 422 that names the key, before anything is written.

    The schema and the workflow answer identically (ADR 0017), so this pins
    the wire behaviour, not the layer. Mutation: resolve the key with a plain
    subscript instead of ``resolve_calculation_key`` in both layers; a
    ``KeyError`` surfaces as a 500.
    """
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_ethane_bundle(
            transport={**_LJ, "source_calculations": [
                {"calculation_key": "sp-typo", "role": "full_transport"}
            ]}
        ),
    )
    assert resp.status_code == 422, resp.text[:800]
    assert "transport.source_calculations[0]" in resp.text
    assert "sp-typo" in resp.text


def test_transport_inherits_the_standalone_content_rules(client):
    """An empty block and an unpaired Lennard-Jones value are refused, exactly as standalone.

    Mutation: give ``TransportInBundle`` its own fields instead of subclassing
    ``TransportUploadPayload``; both requests then return 201.
    """
    empty = client.post(
        "/api/v1/uploads/computed-species", json=_ethane_bundle(transport={"note": "nothing"})
    )
    assert empty.status_code == 422, empty.text[:800]
    assert "at least one transport property" in empty.text

    unpaired = client.post(
        "/api/v1/uploads/computed-species",
        json=_ethane_bundle(transport={"sigma_angstrom": 2.0}),
    )
    assert unpaired.status_code == 422, unpaired.text[:800]
    assert "provided together" in unpaired.text


# ---------------------------------------------------------------------------
# Transport on the reaction bundle
# ---------------------------------------------------------------------------


def _reaction_species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [
            {
                "key": f"{key}-freq",
                "type": "freq",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": 0,
                "freq_zpe_hartree": 0.01,
            },
            {
                "key": f"{key}-sp",
                "type": "sp",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "sp_electronic_energy_hartree": -1.5,
            },
        ],
    }


def _reaction_bundle() -> dict:
    """``H + H -> H2``: balanced, no transition state, no atom map."""
    return {
        "species": [
            _reaction_species("h", "[H]", 2, _XYZ_H),
            _reaction_species("h2", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }


def test_reaction_bundle_stores_transport_per_species(client, db_session):
    """Each species' transport lands on its own entry, with its own links.

    Mutation: attach every transport to the first species' entry; the
    ``h2`` comparison fails.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["transport"] = {
        **_LJ,
        "source_calculations": [{"calculation_key": "h-sp", "role": "full_transport"}],
    }
    bundle["species"][1]["transport"] = {
        "sigma_angstrom": 2.83,
        "epsilon_over_k_k": 59.7,
        "source_calculations": [{"calculation_key": "h2-opt", "role": "supporting_geometry"}],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    body = resp.json()

    assert len(body["transport_ids"]) == 2
    by_sigma = {
        db_session.get(Transport, tid).sigma_angstrom: db_session.get(Transport, tid)
        for tid in body["transport_ids"]
    }
    h_entry, h2_entry = body["species_entry_ids"][0], body["species_entry_ids"][1]
    assert by_sigma[2.05].species_entry_id == h_entry
    assert by_sigma[2.83].species_entry_id == h2_entry
    keys = body["calculation_keys"]
    assert _transport_sources(db_session, by_sigma[2.05].id) == {
        (keys["h-sp"], TransportCalculationRole.full_transport)
    }
    assert _transport_sources(db_session, by_sigma[2.83].id) == {
        (keys["h2-opt"], TransportCalculationRole.supporting_geometry)
    }


def test_reaction_bundle_transport_takes_the_bundle_provenance_defaults(client, db_session):
    """``analysis_software_release`` and ``workflow_tool_release`` fill in as thermo's do.

    Mutation: skip the defaults for transport; both columns are ``NULL``.
    """
    bundle = _reaction_bundle()
    bundle["analysis_software_release"] = {"name": "Arkane", "version": "3.3.0"}
    bundle["workflow_tool_release"] = _TOOL
    bundle["species"][0]["transport"] = {**_LJ}
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    row = db_session.get(Transport, resp.json()["transport_ids"][0])
    assert row.software_release_id is not None
    assert row.workflow_tool_release_id is not None


def test_reaction_bundle_transport_source_must_belong_to_its_own_species(client):
    """H2's calculation cannot be what produced H's transport.

    The key resolves, so only the owner rule can catch it. Mutation: drop the
    owner check from ``validate_species_key_refs``; the workflow's guard still
    refuses, so this stays a 422 -- the schema check exists to refuse it with a
    message that names the field before any row is written.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["transport"] = {
        **_LJ,
        "source_calculations": [{"calculation_key": "h2-sp", "role": "full_transport"}],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert "transport.source_calculations[0]" in resp.text
    assert "one of this species' own" in resp.text


def test_reaction_bundle_transport_source_key_must_exist(client):
    bundle = _reaction_bundle()
    bundle["species"][0]["transport"] = {
        **_LJ,
        "source_calculations": [{"calculation_key": "h-spp", "role": "full_transport"}],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert "'h-spp'" in resp.text


# ---------------------------------------------------------------------------
# invalidated_reason on a bundle torsion
# ---------------------------------------------------------------------------

_TORSION = {"torsion_index": 1, "symmetry_number": 3, "treatment_kind": "hindered_rotor"}


def _torsion_rows(db_session, statmech_id: int) -> list[StatmechTorsion]:
    return list(
        db_session.scalars(
            select(StatmechTorsion)
            .where(StatmechTorsion.statmech_id == statmech_id)
            .order_by(StatmechTorsion.torsion_index)
        ).all()
    )


def test_species_bundle_torsion_keeps_its_invalidated_reason(client, db_session):
    """The reason is stored and read back; an unrejected rotor stays ``null``.

    Mutation: delete the ``invalidated_reason=`` line from
    ``_persist_statmech_block``; the stored and read-back values are ``None``.
    """
    payload = _ethane_bundle(
        statmech={
            "external_symmetry": 6,
            "statmech_treatment": "rrho_1d",
            "torsions": [
                {**_TORSION, "invalidated_reason": "scan not periodic"},
                {**_TORSION, "torsion_index": 2},
            ],
        }
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]
    statmech_id = resp.json()["statmech"]["statmech_id"]

    first, second = _torsion_rows(db_session, statmech_id)
    assert first.invalidated_reason == "scan not periodic"
    assert second.invalidated_reason is None

    ref = db_session.get(Statmech, statmech_id).public_ref
    read = client.get(f"/api/v1/scientific/statmech/{ref}?include=torsions")
    assert read.status_code == 200, read.text[:800]
    torsions = {t["torsion_index"]: t for t in read.json()["record"]["torsions"]}
    assert torsions[1]["invalidated_reason"] == "scan not periodic"
    assert torsions[2]["invalidated_reason"] is None


def test_reaction_bundle_torsion_keeps_its_invalidated_reason(client, db_session):
    """The reaction bundle's torsion model is a separate class and needs the same field.

    Mutation: delete the ``invalidated_reason=`` line from the inline torsion
    construction in ``persist_computed_reaction_upload``.
    """
    bundle = _reaction_bundle()
    bundle["species"][1]["statmech"] = {
        "external_symmetry": 2,
        "statmech_treatment": "rrho_1d",
        "torsions": [{**_TORSION, "invalidated_reason": "rotor is a vibration"}],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    (statmech_id,) = resp.json()["statmech_ids"]
    (torsion,) = _torsion_rows(db_session, statmech_id)
    assert torsion.invalidated_reason == "rotor is a vibration"

    ref = db_session.get(Statmech, statmech_id).public_ref
    read = client.get(f"/api/v1/scientific/statmech/{ref}?include=torsions")
    assert read.json()["record"]["torsions"][0]["invalidated_reason"] == "rotor is a vibration"


# ---------------------------------------------------------------------------
# scf_stability.source_calculation_key
# ---------------------------------------------------------------------------

_STABILITY = {"status": "stable", "lowest_eigenvalue": 0.021}


def _with_stability(payload: dict, *, carrier: str, **extra) -> dict:
    """Attach a stability block to the species-bundle calculation keyed ``carrier``."""
    conf = payload["conformers"][0]
    for calc in (conf["primary_calculation"], *conf["additional_calculations"]):
        if calc["key"] == carrier:
            calc["scf_stability"] = {**_STABILITY, **extra}
    return payload


def test_stability_verdict_names_the_job_that_measured_it(client, db_session):
    """The block hangs off the opt and cites the sp job, which is declared *later*.

    Mutation: delete the ``link_scf_stability_sources`` call in
    ``persist_computed_species_upload``; ``source_calculation_id`` stays
    ``NULL`` and the read returns no ``source_calculation_ref``.
    """
    payload = _with_stability(_ethane_bundle(), carrier="opt0", source_calculation_key="sp0")
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]
    calc_ids = _calc_ids(resp)

    row = db_session.get(CalculationSCFStability, calc_ids["opt0"])
    assert row is not None
    assert row.source_calculation_id == calc_ids["sp0"]
    assert db_session.get(CalculationSCFStability, calc_ids["sp0"]) is None

    opt_ref = db_session.get(Calculation, calc_ids["opt0"]).public_ref
    sp_ref = db_session.get(Calculation, calc_ids["sp0"]).public_ref
    read = client.get(f"/api/v1/scientific/calculations/{opt_ref}?include=scf_stability")
    assert read.status_code == 200, read.text[:800]
    (stability,) = read.json()["record"]["scf_stability"]
    assert stability["status"] == "stable"
    assert stability["source_calculation_ref"] == sp_ref


def test_stability_without_a_key_keeps_no_source(client, db_session):
    """Omitting the key is the old behaviour: the carrying calculation measured it.

    Mutation: default the source to the first additional calculation; the
    ``None`` assertion fails.
    """
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_with_stability(_ethane_bundle(), carrier="opt0"),
    )
    assert resp.status_code == 201, resp.text[:800]
    row = db_session.get(CalculationSCFStability, _calc_ids(resp)["opt0"])
    assert row is not None and row.source_calculation_id is None


def test_stability_source_key_must_be_declared(client):
    """An unknown key is a coded 422 naming the field.

    The schema and the workflow answer identically (ADR 0017), so dropping
    the schema validator does not change this test; the workflow layer is
    pinned on its own in ``tests/services/test_scf_stability_sources.py``.
    Mutation: drop the lookup from both layers and the block is stored with
    no source and this returns 201.
    """
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_with_stability(_ethane_bundle(), carrier="opt0", source_calculation_key="nope"),
    )
    assert resp.status_code == 422, resp.text[:800]
    assert "scf_stability.source_calculation_key" in resp.text
    assert "'nope'" in resp.text


def test_stability_source_key_may_not_name_the_carrier_itself(client):
    """Naming yourself says nothing the omitted key does not.

    Mutation: delete the self-reference check; this returns 201.
    """
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_with_stability(_ethane_bundle(), carrier="opt0", source_calculation_key="opt0"),
    )
    assert resp.status_code == 422, resp.text[:800]
    assert "names the calculation itself" in resp.text


def test_reaction_bundle_stability_source_links_within_one_species(client, db_session):
    """On the reaction bundle the cited job is one of the same species' own.

    Mutation: delete the ``link_scf_stability_sources`` call in
    ``persist_computed_reaction_upload``.
    """
    bundle = _reaction_bundle()
    bundle["species"][1]["conformers"][0]["calculation"]["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "h2-sp",
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    keys = resp.json()["calculation_keys"]
    row = db_session.get(CalculationSCFStability, keys["h2-opt"])
    assert row.source_calculation_id == keys["h2-sp"]


def test_reaction_bundle_stability_source_may_not_be_another_species_job(client):
    """H2's job cannot have measured H's stability.

    Mutation: delete the owner rule from ``validate_species_key_refs``; the
    workflow's check still answers 422, so this keeps passing -- the schema
    rule exists for the message and for clients that validate offline.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["conformers"][0]["calculation"]["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "h2-sp",
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert "must be one of the same subject's own" in resp.text


def test_primitive_route_refuses_the_local_key(client):
    """``/uploads/conformers`` names rows by id; a local key has nothing to resolve against.

    Mutation: delete ``SCFStabilityPayload.refuse_local_key``; the route would
    accept the block and silently store it with no source.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json={
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "geometry": {"xyz_text": _XYZ_H},
            "calculation": {
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_result": {"converged": True},
                "scf_stability": {**_STABILITY, "source_calculation_key": "anything"},
            },
        },
    )
    assert resp.status_code == 422, resp.text[:800]
    assert "only valid inside a" in resp.text


# ---------------------------------------------------------------------------
# Contract prose
# ---------------------------------------------------------------------------


def test_software_release_ref_fields_are_documented_in_the_schema():
    """The contract table showed ``version``/``revision``/``build`` blank.

    Mutation: remove the ``Field(description=...)`` from any one; it is blank again.
    """
    from tckdb_schemas.fragments.refs import SoftwareReleaseRef

    props = SoftwareReleaseRef.model_json_schema()["properties"]
    for name in ("version", "revision", "build"):
        assert props[name].get("description"), f"{name} has no description"
    assert "C.02" in props["revision"]["description"]
    assert "commit" in props["revision"]["description"]
    assert "mpi" in props["build"]["description"]


def test_path_search_converged_says_what_it_means_per_method():
    """``converged`` is defined for every method, and not by the output file existing.

    Mutation: revert the docstring; the method names vanish from the schema.
    """
    from tckdb_schemas.fragments.calculation import PathSearchResultPayload

    doc = PathSearchResultPayload.__doc__
    for method in ("neb", "gsm", "growing_string", "freezing_string", "other"):
        assert f"``{method}``" in doc, method
    assert "output file" in doc
