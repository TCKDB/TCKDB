"""Scheme frequency level and the ``composite_delta`` warning, on the wire.

Composite-levels plan P6 (owner decisions 9 and 10):

* ``EnergyCorrectionSchemeRef.frequency_level_of_theory`` is accepted on the
  upload, becomes part of scheme identity, and is read back on the scientific
  scheme read (detail and search) as ``frequency_level_of_theory``.
* A new applied correction with ``application_role = composite_delta`` is stored
  as sent and answered with ``composite_delta_prefer_scheme_terms``. Warning,
  not refusal (ADR 0008): the check states a preference, not a definition.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.energy_correction import AppliedEnergyCorrection, EnergyCorrectionScheme
from app.schemas.workflows.energy_correction_upload import EnergyCorrectionSchemeRef
from app.services.energy_correction_resolution import resolve_or_create_scheme
from tests.workflows.test_computed_reaction_upload import (
    _aec_scheme_ref_rxn,
    _bac_petersson_scheme_ref_rxn,
    _payload_with_aec_carriers,
)

_URL = "/api/v1/uploads/computed-reaction"
_FREQ_A = {"method": "B3LYP", "basis": "6-311G(d,p)"}
_FREQ_B = {"method": "wB97XD", "basis": "def2-TZVP"}
_WARNING = "composite_delta_prefer_scheme_terms"


def _correction(
    role: str = "aec_total", *, bac: bool = False, **scheme_overrides
) -> dict:
    """An applied correction. ``bac`` uses a Petersson BAC scheme (the only
    kind a frequency level is accepted on), under the unconstrained
    ``composite_delta`` role so no BAC component table is needed."""
    scheme = (
        _bac_petersson_scheme_ref_rxn(**scheme_overrides)
        if bac
        else _aec_scheme_ref_rxn(**scheme_overrides)
    )
    return {
        "scheme": scheme,
        "application_role": role,
        "value": -0.1,
        "value_unit": "hartree",
        "source_calculation_key": "ch3-sp",
    }


def _deposit(client, correction: dict):
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [correction]
    return client.post(_URL, json=payload)


def _codes(resp) -> list[str]:
    return [w["code"] for w in resp.json()["warnings"]]


# ---------------------------------------------------------------------------
# frequency level
# ---------------------------------------------------------------------------


def test_a_bundle_scheme_with_a_frequency_level_is_stored_and_keyed_on_it(
    client, db_session: Session
) -> None:
    for freq in (_FREQ_A, _FREQ_B):
        resp = _deposit(
            client, _correction("composite_delta", bac=True, frequency_level_of_theory=freq)
        )
        assert resp.status_code == 201, resp.text[:800]

    schemes = db_session.scalars(
        select(EnergyCorrectionScheme).where(
            EnergyCorrectionScheme.name == "Petersson BAC v1 (rxn)"
        )
    ).all()
    assert len(schemes) == 2
    assert {s.frequency_level_of_theory.method.lower() for s in schemes} == {"b3lyp", "wb97xd"}
    assert len({s.public_ref for s in schemes}) == 2
    assert len({s.level_of_theory_id for s in schemes}) == 1


def test_a_bundle_scheme_without_one_still_lands_on_the_old_identity(
    client, db_session: Session
) -> None:
    for _ in range(2):
        resp = _deposit(client, _correction())
        assert resp.status_code == 201, resp.text[:800]
    schemes = db_session.scalars(
        select(EnergyCorrectionScheme).where(EnergyCorrectionScheme.name == "AEC v1 (rxn)")
    ).all()
    assert len(schemes) == 1
    assert schemes[0].frequency_level_of_theory_id is None


def test_a_frequency_level_on_an_atom_energy_scheme_is_refused_with_a_code(client) -> None:
    resp = _deposit(client, _correction(frequency_level_of_theory=_FREQ_A))
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "energy_correction_scheme_frequency_level_not_applicable", body
    assert body["context"]["scheme_kind"] == "atom_energy", body


def test_a_frequency_level_without_an_energy_level_is_refused_with_a_code(client) -> None:
    correction = _correction("composite_delta", bac=True, frequency_level_of_theory=_FREQ_A)
    del correction["scheme"]["level_of_theory"]
    resp = _deposit(client, correction)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "energy_correction_scheme_frequency_level_without_energy_level", body
    assert body["context"]["requires"] == "level_of_theory", body


def test_the_scheme_read_reports_the_frequency_level(client, db_session: Session) -> None:
    keyed = resolve_or_create_scheme(
        db_session,
        EnergyCorrectionSchemeRef(
            kind="bac_petersson",
            name="Petersson BAC read test",
            level_of_theory={"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
            frequency_level_of_theory=dict(_FREQ_A),
            units="kcal_mol",
            bond_params=[{"bond_key": "C-H", "value": 0.1}],
        ),
    )
    bare = resolve_or_create_scheme(
        db_session,
        EnergyCorrectionSchemeRef(
            kind="bac_petersson",
            name="Petersson BAC read test",
            level_of_theory={"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
            units="kcal_mol",
            bond_params=[{"bond_key": "C-H", "value": 0.1}],
        ),
    )
    assert keyed.id != bare.id

    detail = client.get(f"/api/v1/scientific/energy-correction-schemes/{keyed.public_ref}")
    assert detail.status_code == 200, detail.text
    record = detail.json()["record"]
    assert record["frequency_level_of_theory"]["method"].lower() == "b3lyp"
    assert record["frequency_level_of_theory"]["basis"] == "6-311G(d,p)"
    # The energy half is still reported as the scheme's level of theory.
    assert record["level_of_theory"]["method"] == "CCSD(T)-F12"

    bare_record = client.get(
        f"/api/v1/scientific/energy-correction-schemes/{bare.public_ref}"
    ).json()["record"]
    assert bare_record["frequency_level_of_theory"] is None

    search = client.get(
        "/api/v1/scientific/energy-correction-schemes/search?scheme_kind=bac_petersson"
    )
    assert search.status_code == 200, search.text
    by_ref = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]: r
        for r in search.json()["records"]
    }
    assert by_ref[keyed.public_ref]["frequency_level_of_theory"]["method"].lower() == "b3lyp"
    assert by_ref[bare.public_ref]["frequency_level_of_theory"] is None


# ---------------------------------------------------------------------------
# composite_delta
# ---------------------------------------------------------------------------


def test_a_composite_delta_correction_is_stored_and_warned(
    client, db_session: Session
) -> None:
    resp = _deposit(client, _correction("composite_delta"))
    assert resp.status_code == 201, resp.text[:800]
    assert _codes(resp).count(_WARNING) == 1, resp.json()["warnings"]
    warning = next(w for w in resp.json()["warnings"] if w["code"] == _WARNING)
    assert "composite-scheme terms" in warning["message"]
    # Warned, not refused: the row exists, exactly as sent.
    stored = db_session.scalars(
        select(AppliedEnergyCorrection).where(
            AppliedEnergyCorrection.application_role == "composite_delta"
        )
    ).all()
    assert len(stored) == 1
    assert stored[0].value == -0.1


def test_other_roles_do_not_get_the_composite_delta_warning(client) -> None:
    resp = _deposit(client, _correction("aec_total"))
    assert resp.status_code == 201, resp.text[:800]
    assert _WARNING not in _codes(resp)
