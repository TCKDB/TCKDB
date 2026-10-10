"""An ``electronic_plus_zpe`` state energy is held against sp + scaled freq ZPE (#693).

#678 compared ``electronic_plus_zpe`` only when every source was a composite storing an E0. The
common case is an sp electronic energy plus a frequency calculation's zero-point energy scaled by
a stated factor, so a participant may now cite its freq as ``zpe_calculation_key`` and the state
states ``zpe_scale_factor``. The sum is ``sum(nu * (E_sp + s * ZPE))``.

Hand-written numbers, from the fixture's hartree values: the entrance state is ethyl + O2, with
single points -79.8 and -150.2 Eh, and freq ZPEs of 0.05 (the fixture's ``ethyl_freq``) and 0.01
(``O2_freq``, added here). The scale factor is 0.98, so the sum is
(-79.8 + 0.98 * 0.05) + (-150.2 + 0.98 * 0.01) = -229.9411 Eh.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.api.error_contract import CodedValueError
from app.chemistry.units import HARTREE_TO_KJ_MOL as H
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, EnergyCorrectionConvention, EnergyZeroConvention
from app.db.models.network_pdep import NetworkSolveStateEnergy, NetworkSolveStateEnergySource
from app.services.network_energy_sources import StateEnergyToCompare, compare_state_energy_sums
from app.workflows.network_pdep import _participant_source
from tests.api.test_api_network_pdep_state_energy_sources import (
    _MISMATCH,
    _NOT_COMPARED,
    _codes,
    _payload,
    _post,
    _refused,
)
from tests.workflows.test_network_pdep_upload import _LOT_CC, _LOT_DFT, _SOFTWARE, _XYZ_O2

_PLUS_ZPE = EnergyCorrectionConvention.electronic_plus_zpe
_SCALE = 0.98
_ZPE = {"ethyl": 0.05, "O2": 0.01}
_E = {"ethyl": -79.8, "O2": -150.2}
_SUM_HARTREE = sum(_E[s] + _SCALE * _ZPE[s] for s in _E)  # -229.9411


def _freq(key: str, geometry_key: str, zpe: float) -> dict:
    return {
        "key": key, "type": "freq", "geometry_key": geometry_key,
        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
        "freq_n_imag": 0, "freq_zpe_hartree": zpe,
    }


def _zpe_payload(*, scale: float | None = _SCALE, energy_hartree: float = _SUM_HARTREE) -> dict:
    """The fixture network, its entrance state restated as sp + scaled freq ZPE."""
    payload = deepcopy(_payload())
    o2 = next(s for s in payload["species"] if s["key"] == "O2")
    o2["calculations"].append(_freq("O2_freq", "O2_geom", _ZPE["O2"]))
    entry = payload["solve"]["state_energies"][0]
    entry.update(
        energy_kj_mol=energy_hartree * H,
        correction_convention="electronic_plus_zpe",
        source_calculation_keys=[
            {"species_key": "ethyl", "calculation_key": "ethyl_sp", "zpe_calculation_key": "ethyl_freq"},
            {"species_key": "O2", "calculation_key": "O2_sp", "zpe_calculation_key": "O2_freq"},
        ],
    )
    if scale is not None:
        entry["zpe_scale_factor"] = scale
    return payload


def _entrance_row(db_session) -> NetworkSolveStateEnergy:
    rows = db_session.scalars(select(NetworkSolveStateEnergy)).all()
    (row,) = [r for r in rows if r.correction_convention.value == "electronic_plus_zpe"]
    return row


def test_sp_plus_scaled_freq_zpe_that_equals_the_stated_energy_agrees(client, db_session) -> None:
    resp = _post(client, _zpe_payload())
    assert resp.status_code == 201, resp.text
    row = _entrance_row(db_session)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("agrees", None)
    assert row.zpe_scale_factor == _SCALE
    assert _NOT_COMPARED not in _codes(resp)
    # Both the electronic and the ZPE calculation of every participant are stored.
    sources = db_session.scalars(
        select(NetworkSolveStateEnergySource).where(NetworkSolveStateEnergySource.state_id == row.state_id)
    ).all()
    assert len(sources) == 2 and all(s.zpe_calculation_id is not None for s in sources)
    assert {s.zpe_calculation_id for s in sources}.isdisjoint({s.calculation_id for s in sources})


def test_the_stated_scale_is_used_exactly_not_unity(client) -> None:
    """The unscaled sum is -229.9400 Eh, 1.1 mEh (2.9 kJ/mol) from the scaled one: refused at s=0.98."""
    unscaled = sum(_E[s] + _ZPE[s] for s in _E)
    context = _refused(_post(client, _zpe_payload(energy_hartree=unscaled)), _MISMATCH)
    assert context["field"] == "solve.state_energies[0].energy_kj_mol"
    assert context["correction_convention"] == "electronic_plus_zpe"
    assert context["stored_sum_kj_mol"] == pytest.approx(_SUM_HARTREE * H)
    assert context["zpe_scale_factor"] == _SCALE


def test_a_stated_energy_that_contradicts_the_scaled_sum_is_refused(client) -> None:
    context = _refused(_post(client, _zpe_payload(energy_hartree=_SUM_HARTREE + 0.02)), _MISMATCH)
    assert context["state_key"] == "entrance"
    assert context["stored_sum_kj_mol"] == pytest.approx(_SUM_HARTREE * H)


@pytest.mark.parametrize(
    "energy_hartree",
    [sum(_E[s] + _ZPE[s] for s in _E), _SUM_HARTREE + 0.02],
    ids=["the_unscaled_sum", "a_wrong_number"],
)
def test_an_unstated_scale_is_not_compared_and_never_assumed_to_be_one(
    client, db_session, energy_hartree: float
) -> None:
    """The unscaled sum is what a unity assumption would call agreement; neither case is judged."""
    resp = _post(client, _zpe_payload(scale=None, energy_hartree=energy_hartree))
    assert resp.status_code == 201, resp.text
    row = _entrance_row(db_session)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == (
        "not_compared",
        "zpe_scaling_unstated",
    )
    assert row.zpe_scale_factor is None
    (warning,) = [w for w in resp.json()["warnings"] if w["code"] == _NOT_COMPARED]
    assert "zpe_scale_factor" in warning["message"]


def test_a_zpe_from_another_geometry_is_not_paired(client, db_session) -> None:
    """O2's freq is at a second geometry, so its ZPE is not the ZPE of the sp's geometry: never borrowed."""
    payload = _zpe_payload()
    o2 = next(s for s in payload["species"] if s["key"] == "O2")
    o2["conformers"].append({
        "key": "O2_conf2",
        "geometry": {"key": "O2_geom_b", "xyz_text": _XYZ_O2.replace("1.21", "1.25")},
        "calculation": {"key": "O2_opt_b", "type": "opt", "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT},
    })
    o2["calculations"] = [c for c in o2["calculations"] if c["key"] != "O2_freq"]
    o2["calculations"].append(_freq("O2_freq", "O2_geom_b", _ZPE["O2"]))
    # A number that would disagree if the pairing were assumed.
    payload["solve"]["state_energies"][0]["energy_kj_mol"] = (_SUM_HARTREE + 0.02) * H
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _entrance_row(db_session)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == (
        "not_compared",
        "geometry_not_paired",
    )


def test_a_scale_factor_with_no_cited_zpe_scales_nothing_and_is_refused(client) -> None:
    payload = _zpe_payload()
    for source in payload["solve"]["state_energies"][0]["source_calculation_keys"]:
        source.pop("zpe_calculation_key")
    resp = _post(client, payload)
    assert resp.status_code == 422, resp.text
    assert "would scale nothing" in resp.text


def test_a_zpe_calculation_must_be_a_freq(client) -> None:
    payload = _zpe_payload()
    payload["solve"]["state_energies"][0]["source_calculation_keys"][0]["zpe_calculation_key"] = "ethyl_sp"
    context = _refused(_post(client, payload), "network_energy_source_type_mismatch")
    assert context["accepted_calculation_types"] == ["freq"]
    assert context["field"].endswith("source_calculation_keys[0].zpe_calculation_key")


def test_a_zpe_calculation_must_belong_to_the_participant_it_is_listed_for(client) -> None:
    """O2's slot cites ethyl's freq: another species' ZPE is a wrong source, never borrowed."""
    payload = _zpe_payload()
    payload["solve"]["state_energies"][0]["source_calculation_keys"][1]["zpe_calculation_key"] = "ethyl_freq"
    context = _refused(_post(client, payload), "network_energy_source_subject_mismatch")
    assert context["field"].endswith("source_calculation_keys[1].zpe_calculation_key")


@pytest.mark.parametrize("which", ["zpe_scale_factor", "zpe_calculation_key"])
def test_the_zpe_fields_are_only_for_electronic_plus_zpe(client, which: str) -> None:
    payload = _zpe_payload()
    entry = payload["solve"]["state_energies"][0]
    entry["correction_convention"] = "electronic_only"
    if which == "zpe_scale_factor":
        for source in entry["source_calculation_keys"]:
            source.pop("zpe_calculation_key")
    else:
        entry.pop("zpe_scale_factor")
    resp = _post(client, payload)
    assert resp.status_code == 422, resp.text
    assert "electronic_plus_zpe" in resp.text


# ---------------------------------------------------------------------------
# An atom's sp is its E0: the workflow reads "atom" from the stored geometry
# ---------------------------------------------------------------------------

_AR_SP = {
    "key": "Ar_sp", "type": "sp", "geometry_key": "Ar_geom", "software_release": _SOFTWARE,
    "level_of_theory": _LOT_CC, "sp_electronic_energy_hartree": -0.5,
}


def _sp_of(db_session, smiles: str) -> Calculation:
    rows = db_session.scalars(select(Calculation).where(Calculation.type == CalculationType.sp)).all()
    matching = [r for r in rows if r.species_entry is not None and r.species_entry.species.smiles == smiles]
    assert len(matching) == 1, smiles
    return matching[0]


def test_the_workflow_reads_an_atom_from_the_calculations_stored_geometry(client, db_session) -> None:
    """The geometry -> ``is_atom`` step: an Ar atom's sp is an atom's, ethene's is not, and it feeds a real comparison."""
    payload = deepcopy(_payload())
    argon = next(sp for sp in payload["species"] if sp["key"] == "Ar")  # the bath gas: one atom
    argon["calculations"] = [deepcopy(_AR_SP)]
    assert _post(client, payload).status_code == 201

    atom = _participant_source(
        db_session, species_key="Ar", stoichiometry=1, calculation=_sp_of(db_session, "[Ar]"),
        zpe_calculation=None, looks_up_geometry=True,
    )
    ethene = _participant_source(
        db_session, species_key="ethene", stoichiometry=1, calculation=_sp_of(db_session, "C=C"),
        zpe_calculation=None, looks_up_geometry=True,
    )
    assert atom.is_atom is True and ethene.is_atom is False

    def state(participant, kj: float) -> StateEnergyToCompare:
        return StateEnergyToCompare(
            index=0, state_key="s", energy_kj_mol=kj, energy_zero_convention=EnergyZeroConvention.absolute,
            correction_convention=_PLUS_ZPE, participants=(participant,), zpe_scale_factor=None,
        )

    assert compare_state_energy_sums([state(atom, -0.5 * H)])[0].status == "agrees"
    with pytest.raises(CodedValueError):  # the atom branch really compares: +0.01 Eh is a contradiction
        compare_state_energy_sums([state(atom, -0.49 * H)])
    assert compare_state_energy_sums([state(ethene, -78.4 * H)])[0].reason == "zpe_not_in_source"
