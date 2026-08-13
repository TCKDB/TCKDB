"""What a bundle upload can say about where its numbers came from.

Two gaps, both invisible from the payload a depositor writes, both closed by
widening a model rather than by changing what the database can hold.

``scf_stability`` existed only on ``CalculationWithResultsPayload``, so the
primitive routes (``/uploads/conformers``, ``/thermo``, ``/statmech``, ...)
could record whether an SCF solution had been tested for stability and the
two bundle roots could not. ``SchemaBase`` is ``extra="forbid"``, so a bundle
that tried got a 422 rather than a silent drop — the depositor was told no,
without being told why or where else to put it.

``BundleThermoIn`` carried eight fields against ``ThermoInBundle``'s fifteen,
so a reaction bundle's per-species thermo could not record which calculations
produced it. The two models were written three days apart in April 2026 —
the narrow one first — and never reconciled; the shared ``persist_thermo``
helper the species and standalone routes use has always written every one of
the missing columns, and the reaction workflow is the only one of the three
that hand-rolled its own lossy ORM construction instead.

Both are asserted against the database, not the response body: a 201 proves
the payload was accepted, not that anything was stored.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationSCFStability
from app.db.models.common import SCFStabilityStatus, ThermoCalculationRole
from app.db.models.thermo import Thermo, ThermoSourceCalculation

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"

#: A stability job that found one instability and then resolved it. Chosen
#: over the bare ``stable`` because it exercises the cross-field validator
#: and the two table check constraints rather than the zero case.
_SCF_STABILITY = {
    "status": "stabilized",
    "instability_count": 1,
    "lowest_eigenvalue": -0.0123,
    "reoptimized_wavefunction": True,
    "note": "internal instability, reoptimized",
}


# ---------------------------------------------------------------------------
# scf_stability, through the species bundle
# ---------------------------------------------------------------------------


def _species_bundle(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _XYZ_H},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }
    base.update(overrides)
    return base


def _stability_rows(db_session, calc_key_to_id: dict[str, int]):
    return {
        key: db_session.get(CalculationSCFStability, calc_id)
        for key, calc_id in calc_key_to_id.items()
    }


def test_computed_species_bundle_records_scf_stability(client, db_session):
    """The species bundle can now say the SCF was checked, and it is stored."""
    payload = _species_bundle()
    payload["conformers"][0]["primary_calculation"]["scf_stability"] = dict(
        _SCF_STABILITY
    )

    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]

    calc_id = resp.json()["conformers"][0]["primary_calculation"]["calculation_id"]
    row = db_session.get(CalculationSCFStability, calc_id)
    assert row is not None, "no calc_scf_stability row was written"
    assert row.status is SCFStabilityStatus.stabilized
    assert row.instability_count == 1
    assert row.reoptimized_wavefunction is True
    assert row.lowest_eigenvalue == -0.0123


def test_computed_species_bundle_without_scf_stability_writes_no_row(
    client, db_session
):
    """Absence stays absence.

    ``not_checked`` is deliberately not a storable status — no row is the
    encoding — so a bundle that says nothing must not acquire a default.
    """
    resp = client.post("/api/v1/uploads/computed-species", json=_species_bundle())
    assert resp.status_code == 201, resp.text[:800]

    calc_id = resp.json()["conformers"][0]["primary_calculation"]["calculation_id"]
    assert db_session.get(CalculationSCFStability, calc_id) is None


def test_an_inconsistent_scf_stability_block_is_refused_at_the_seam(client):
    """``stable`` and ``reoptimized_wavefunction`` contradict each other.

    The validator that says so has existed since the field was written; what
    is new is that a bundle payload can now reach it. Reaching it as a 422
    is the point — the same contradiction reaches the table as a check
    constraint, and an ``IntegrityError`` cannot name the field.
    """
    payload = _species_bundle()
    payload["conformers"][0]["primary_calculation"]["scf_stability"] = {
        "status": "stable",
        "reoptimized_wavefunction": True,
    }
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 422, resp.text[:800]

    errors = resp.json()["detail"]
    assert isinstance(errors, list), resp.text[:800]
    assert any(
        e.get("type") == "value_error"
        and "inconsistent with reoptimized_wavefunction" in e.get("msg", "")
        for e in errors
    ), errors


# ---------------------------------------------------------------------------
# scf_stability and thermo provenance, through the reaction bundle
# ---------------------------------------------------------------------------


def _reaction_species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
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
    """``H + H -> H2``. Balanced, no transition state, no atom map needed."""
    return {
        "species": [
            _reaction_species("h", "[H]", 2, _XYZ_H),
            _reaction_species("h2", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }


def test_computed_reaction_bundle_records_scf_stability(client, db_session):
    """The reaction bundle reaches the same seam through the shared adapter."""
    bundle = _reaction_bundle()
    bundle["species"][0]["calculations"][1]["scf_stability"] = dict(_SCF_STABILITY)

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    calc_id = resp.json()["calculation_keys"]["h-sp"]
    row = db_session.get(CalculationSCFStability, calc_id)
    assert row is not None, "no calc_scf_stability row was written"
    assert row.status is SCFStabilityStatus.stabilized
    assert row.instability_count == 1

    # Only the calculation that declared it got a row.
    other_id = resp.json()["calculation_keys"]["h-opt"]
    assert db_session.get(CalculationSCFStability, other_id) is None


def test_reaction_bundle_thermo_records_its_source_calculations(client, db_session):
    """The headline gap: which calculations produced this thermo.

    Before, ``BundleThermoIn`` had no ``source_calculations`` field at all
    and the reaction workflow wrote no ``thermo_source_calculation`` rows, so
    the provenance the species route keeps was dropped on this path with
    nothing in the payload to suggest it had been.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "s298_j_mol_k": 114.7,
        "h298_uncertainty_kj_mol": 0.006,
        "s298_uncertainty_j_mol_k": 0.02,
        "source_calculations": [
            {"calculation_key": "h-opt", "role": "opt"},
            {"calculation_key": "h-freq", "role": "freq"},
            {"calculation_key": "h-sp", "role": "sp"},
        ],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    thermo_ids = resp.json()["thermo_ids"]
    assert len(thermo_ids) == 1, thermo_ids
    thermo = db_session.get(Thermo, thermo_ids[0])

    assert thermo.h298_uncertainty_kj_mol == 0.006
    assert thermo.s298_uncertainty_j_mol_k == 0.02

    links = db_session.scalars(
        select(ThermoSourceCalculation).where(
            ThermoSourceCalculation.thermo_id == thermo.id
        )
    ).all()
    calc_keys = resp.json()["calculation_keys"]
    assert {(link.calculation_id, link.role) for link in links} == {
        (calc_keys["h-opt"], ThermoCalculationRole.opt),
        (calc_keys["h-freq"], ThermoCalculationRole.freq),
        (calc_keys["h-sp"], ThermoCalculationRole.sp),
    }


def test_reaction_bundle_thermo_source_role_must_match_calculation_type(client):
    """A freq calculation is not the ``sp`` the thermo says it is.

    Same rule the species route and the standalone thermo route apply — now
    owned once by ``app.workflows.thermo`` rather than copied per route,
    because three copies of one rule can disagree about one deposit.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "source_calculations": [{"calculation_key": "h-freq", "role": "sp"}],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code != 201, resp.text[:800]
    assert "incompatible with the resolved calculation type" in resp.text


def test_reaction_bundle_thermo_source_must_belong_to_its_own_species(client):
    """H2's calculation cannot be what produced H's enthalpy.

    The key resolves — it is in the bundle's global calc namespace — so the
    schema cannot catch this one; the workflow is the layer that knows which
    species entry each calculation landed on.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "source_calculations": [{"calculation_key": "h2-sp", "role": "sp"}],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    # #158: the same rule the standalone thermo route enforces, reported
    # under the same code -- the reaction bundle is not a second contract.
    assert body["code"] == "thermo_source_calculation_owner_mismatch", body
    assert "another species entry" in body["detail"]


def test_reaction_bundle_thermo_source_key_must_exist(client):
    """A typo is refused at the schema seam, where it can name the key."""
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "source_calculations": [{"calculation_key": "h-spp", "role": "sp"}],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]

    errors = resp.json()["detail"]
    assert any(
        e.get("type") == "value_error"
        and "thermo.source_calculations[0]" in e.get("msg", "")
        and "'h-spp'" in e.get("msg", "")
        for e in errors
    ), errors


def test_reaction_bundle_thermo_provenance_overrides_the_bundle_default(
    client, db_session
):
    """Per-thermo provenance wins; silence still falls back to the bundle.

    Both halves matter. Without the override a species whose thermo came out
    of a different code cannot say so; without the fallback every deposit
    written before these fields existed would start reading differently.
    """
    bundle = _reaction_bundle()
    bundle["analysis_software_release"] = {"name": "Arkane", "version": "3.1.0"}
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "software_release": {"name": "MultiWell", "version": "2023"},
    }
    bundle["species"][1]["thermo"] = {"h298_kj_mol": 0.0}

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    thermo_ids = resp.json()["thermo_ids"]
    assert len(thermo_ids) == 2, thermo_ids
    releases = {
        db_session.get(Thermo, tid).software_release.software.name
        for tid in thermo_ids
    }
    assert releases == {"MultiWell", "Arkane"}, releases


def test_reaction_bundle_thermo_records_its_literature(client, db_session):
    """A thermo value taken from a paper can name the paper on this route."""
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "scientific_origin": "experimental",
        "h298_kj_mol": 217.998,
        "literature": {
            "kind": "article",
            "title": "Enthalpy of formation of the hydrogen atom",
            "year": 2020,
            "journal": "J. Chem. Phys.",
            "doi": "10.1000/tckdb.bundle.thermo.lit",
        },
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    thermo = db_session.get(Thermo, resp.json()["thermo_ids"][0])
    assert thermo.literature_id is not None
    assert thermo.literature.doi == "10.1000/tckdb.bundle.thermo.lit"


def test_a_calculation_still_owns_one_thermo_role_once(client):
    """The same (calculation, role) pair may be claimed once.

    Mirrors ``ThermoInBundle``'s rule so the two routes cannot disagree
    about the table constraint they both write into.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "source_calculations": [
            {"calculation_key": "h-sp", "role": "sp"},
            {"calculation_key": "h-sp", "role": "sp"},
        ],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert any(
        "must not repeat the same" in e.get("msg", "")
        for e in resp.json()["detail"]
    ), resp.text[:800]


def test_calculation_rows_are_shared_not_duplicated_by_thermo_links(
    client, db_session
):
    """A source link points at the calculation the bundle already created.

    Guards the shape of the fix: resolving a local key must reuse the row,
    not mint a second calculation to hang the link off.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["thermo"] = {
        "h298_kj_mol": 217.998,
        "source_calculations": [{"calculation_key": "h-sp", "role": "sp"}],
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    calc_keys = resp.json()["calculation_keys"]
    total = db_session.scalar(
        select(Calculation.id).where(Calculation.id == calc_keys["h-sp"])
    )
    link = db_session.scalars(
        select(ThermoSourceCalculation).where(
            ThermoSourceCalculation.thermo_id == resp.json()["thermo_ids"][0]
        )
    ).one()
    assert link.calculation_id == total
