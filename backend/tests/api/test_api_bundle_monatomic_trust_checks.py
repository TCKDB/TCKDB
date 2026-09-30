"""The trust checks do not ask a single atom for an optimisation it cannot have (#610).

Three optional evidence checks grade a missing ``opt`` as ``missing``:
``calculation_dependencies_present_when_expected`` on an ``sp``, and
``opt_source_present`` on thermo and on statmech. A relabelled ``opt`` used to
satisfy the last two by accident. With an honest ``sp`` primary they would
grade the atom down for the absence of something that cannot exist, so each
is ``not_applicable`` when every calculation involved is a one-atom
calculation, and unchanged otherwise.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationDependency
from app.db.models.common import CalculationType
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.services.trust import (
    EvidenceOutcome,
    evaluate_computed_calculation,
    evaluate_computed_statmech,
    evaluate_computed_thermo,
)
from tests.api.test_api_bundle_monatomic_sp_primary import (
    _H2_XYZ,
    _SPECIES_URL,
    _reaction_payload,
    _species_bundle_from_reaction_atom,
)


def _evidence(db_session):
    """Outcomes for the one ``sp`` calculation, statmech and thermo stored."""
    (sp_calc,) = [
        c for c in db_session.scalars(select(Calculation)).all() if c.type is CalculationType.sp
    ]
    (statmech,) = db_session.scalars(select(Statmech)).all()
    (thermo,) = db_session.scalars(select(Thermo)).all()
    return (
        evaluate_computed_calculation(db_session, sp_calc.id).checks,
        evaluate_computed_statmech(db_session, statmech.id).checks,
        evaluate_computed_thermo(db_session, thermo.id).checks,
    )


def test_an_atoms_sp_is_asked_for_no_optimisation(client, db_session):
    resp = client.post(
        _SPECIES_URL, json=_species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"))
    )
    assert resp.status_code == 201, resp.text[:1500]
    calc, statmech, thermo = _evidence(db_session)
    assert calc["calculation_dependencies_present_when_expected"] is EvidenceOutcome.not_applicable
    assert statmech["opt_source_present"] is EvidenceOutcome.not_applicable
    assert thermo["opt_source_present"] is EvidenceOutcome.not_applicable


def test_a_polyatomic_is_still_asked_for_its_optimisation(client, db_session):
    """The exemption is for one-atom geometries, not for any record lacking an ``opt``.

    Hydrogen molecule, deposited with an ``opt`` primary but with its statmech
    and thermo linked to the single point alone, and the single point's
    inferred parent edge removed: each check still asks for what is missing.
    """
    bundle = _species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"), sp_only=False)
    bundle["species_entry"] = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
    bundle["conformers"][0]["geometry"] = {"xyz_text": _H2_XYZ}
    bundle.pop("applied_energy_corrections")
    (conformer,) = bundle["conformers"]
    conformer["primary_calculation"].pop("input_geometries", None)
    (additional,) = conformer["additional_calculations"]
    additional["input_geometries"] = [{"xyz_text": _H2_XYZ}]
    only_sp = [{"calculation_key": additional["key"], "role": "sp"}]
    bundle["statmech"]["source_calculations"] = only_sp
    bundle["thermo"]["source_calculations"] = only_sp
    resp = client.post(_SPECIES_URL, json=bundle)
    assert resp.status_code == 201, resp.text[:1500]
    for edge in db_session.scalars(select(CalculationDependency)).all():
        db_session.delete(edge)
    db_session.flush()
    db_session.expire_all()
    calc, statmech, thermo = _evidence(db_session)
    assert calc["calculation_dependencies_present_when_expected"] is EvidenceOutcome.missing
    assert statmech["opt_source_present"] is EvidenceOutcome.missing
    assert thermo["opt_source_present"] is EvidenceOutcome.missing
