"""API tests for the lowest-qualifying-SP conformer-observation query.

Exercises ``GET /api/v1/species-entries/{id}/conformer-observations/lowest-sp``.

The query is provenance-qualified: it compares only SP calculations at the
requested level of theory, collapses multiple SPs per observation down to one
canonical candidate, and then ranks observations against each other. Tests use
raw ORM inserts so scenarios can be constructed without relying on the upload
workflow's geometry/reconciliation side effects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.calculation import Calculation, CalculationSPResult
from app.db.models.common import CalculationQuality, CalculationType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import ConformerGroup, ConformerObservation


def _hydrogen_conformer_payload(label: str = "conf-a") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
    }


def _make_lot(db_session, method: str, basis: str | None = None) -> LevelOfTheory:
    """Create a new LevelOfTheory row with a unique hash for tests."""
    lot_hash = (method + (basis or "")).ljust(64, "x")[:64]
    lot = LevelOfTheory(method=method, basis=basis, lot_hash=lot_hash)
    db_session.add(lot)
    db_session.flush()
    return lot


def _make_sp_calc(
    db_session,
    *,
    species_entry_id: int,
    observation_id: int,
    lot_id: int,
    energy: float | None,
    quality: CalculationQuality = CalculationQuality.raw,
    created_at: datetime | None = None,
    calc_type: CalculationType = CalculationType.sp,
    with_sp_result: bool = True,
) -> Calculation:
    """Insert a minimal Calculation plus optional CalculationSPResult row."""
    calc = Calculation(
        type=calc_type,
        quality=quality,
        species_entry_id=species_entry_id,
        conformer_observation_id=observation_id,
        lot_id=lot_id,
    )
    if created_at is not None:
        calc.created_at = created_at
    db_session.add(calc)
    db_session.flush()
    if with_sp_result:
        db_session.add(
            CalculationSPResult(
                calculation_id=calc.id,
                electronic_energy_hartree=energy,
            )
        )
        db_session.flush()
    return calc


@pytest.fixture
def seeded_entry(client, db_session):
    """Upload one conformer; return (species_entry_id, observation_id, lot_id).

    The upload creates a real LoT row (B3LYP/6-31G(d)) and an SP calculation
    with a null SP energy. Tests add their own scenarios on top of this.
    """
    upload = client.post(
        "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
    ).json()
    lot = db_session.query(LevelOfTheory).filter_by(method="B3LYP").one()
    return upload["species_entry_id"], upload["id"], lot.id


class TestLowestSPConformerObservation:
    def _url(self, entry_id: int) -> str:
        return (
            f"/api/v1/species-entries/{entry_id}"
            f"/conformer-observations/lowest-sp"
        )

    def test_returns_lowest_sp_observation(self, client, db_session, seeded_entry):
        entry_id, obs_id, lot_id = seeded_entry
        # Two observations under two groups on the same species entry.
        group2 = ConformerGroup(species_entry_id=entry_id, label="g2")
        db_session.add(group2)
        db_session.flush()
        obs2 = ConformerObservation(conformer_group_id=group2.id)
        db_session.add(obs2)
        db_session.flush()

        # obs1 at -100.0, obs2 at -101.5 (lower = winner)
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-100.0,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs2.id,
            lot_id=lot_id,
            energy=-101.5,
        )

        resp = client.get(self._url(entry_id), params={"lot_id": lot_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["species_entry_id"] == entry_id
        assert body["lot_id"] == lot_id
        assert body["result"] is not None
        result = body["result"]
        assert result["conformer_observation_id"] == obs2.id
        assert result["conformer_group_id"] == group2.id
        assert result["electronic_energy_hartree"] == pytest.approx(-101.5)
        assert result["calculation_quality"] == "raw"

    def test_ignores_other_lots(self, client, db_session, seeded_entry):
        entry_id, obs_id, lot_id = seeded_entry
        other_lot = _make_lot(db_session, "CCSD(T)", "cc-pVTZ")

        # Lower energy exists but at a different LoT — must be ignored.
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=other_lot.id,
            energy=-999.0,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-50.0,
        )

        resp = client.get(self._url(entry_id), params={"lot_id": lot_id})
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result is not None
        assert result["electronic_energy_hartree"] == pytest.approx(-50.0)

    def test_ignores_calculations_without_sp_result(
        self, client, db_session, seeded_entry
    ):
        entry_id, obs_id, lot_id = seeded_entry
        # Calculation without SP-result row (with_sp_result=False) is skipped.
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=None,
            with_sp_result=False,
        )
        # Also: SP result row whose energy is NULL must be skipped.
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=None,
            with_sp_result=True,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-42.0,
        )

        resp = client.get(self._url(entry_id), params={"lot_id": lot_id})
        result = resp.json()["result"]
        assert result is not None
        assert result["electronic_energy_hartree"] == pytest.approx(-42.0)

    def test_ignores_other_species_entries(self, client, db_session):
        # Two distinct uploads → two species entries. The lowest SP should be
        # scoped to the queried species entry only.
        upload_h = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        he_payload = _hydrogen_conformer_payload()
        he_payload["species_entry"] = {
            "smiles": "[He]",
            "charge": 0,
            "multiplicity": 1,
        }
        he_payload["geometry"] = {"xyz_text": "1\nHe atom\nHe 0.0 0.0 0.0"}
        upload_he = client.post(
            "/api/v1/uploads/conformers", json=he_payload
        ).json()
        lot_id = db_session.query(LevelOfTheory).filter_by(method="B3LYP").one().id

        _make_sp_calc(
            db_session,
            species_entry_id=upload_h["species_entry_id"],
            observation_id=upload_h["id"],
            lot_id=lot_id,
            energy=-10.0,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=upload_he["species_entry_id"],
            observation_id=upload_he["id"],
            lot_id=lot_id,
            energy=-999.0,
        )

        body = client.get(
            self._url(upload_h["species_entry_id"]), params={"lot_id": lot_id}
        ).json()
        assert body["result"]["conformer_observation_id"] == upload_h["id"]
        assert body["result"]["electronic_energy_hartree"] == pytest.approx(-10.0)

    def test_returns_null_when_no_qualifying_sp(
        self, client, seeded_entry
    ):
        entry_id, _, lot_id = seeded_entry
        resp = client.get(self._url(entry_id), params={"lot_id": lot_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["species_entry_id"] == entry_id
        assert body["lot_id"] == lot_id
        assert body["result"] is None

    def test_deterministic_tie_break_prefers_earlier_calc(
        self, client, db_session, seeded_entry
    ):
        entry_id, obs_id, lot_id = seeded_entry
        group2 = ConformerGroup(species_entry_id=entry_id, label="g2")
        db_session.add(group2)
        db_session.flush()
        obs2 = ConformerObservation(conformer_group_id=group2.id)
        db_session.add(obs2)
        db_session.flush()

        # Same energy + same quality; earliest created_at wins.
        t0 = datetime(2024, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-10.0,
            created_at=t0,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs2.id,
            lot_id=lot_id,
            energy=-10.0,
            created_at=t0 + timedelta(hours=1),
        )

        body = client.get(self._url(entry_id), params={"lot_id": lot_id}).json()
        assert body["result"]["conformer_observation_id"] == obs_id

    def test_quality_filter_restricts_to_requested_bucket(
        self, client, db_session, seeded_entry
    ):
        entry_id, obs_id, lot_id = seeded_entry
        # Same observation has both a curated and a raw SP at the same LoT, and
        # a second observation has a lower-energy raw SP.
        group2 = ConformerGroup(species_entry_id=entry_id, label="g2")
        db_session.add(group2)
        db_session.flush()
        obs2 = ConformerObservation(conformer_group_id=group2.id)
        db_session.add(obs2)
        db_session.flush()

        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-100.0,
            quality=CalculationQuality.curated,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs2.id,
            lot_id=lot_id,
            energy=-200.0,
            quality=CalculationQuality.raw,
        )

        # No quality filter → curated wins because quality beats energy only
        # during final ranking when energies differ; here the lowest-energy raw
        # row actually wins outright.
        body = client.get(self._url(entry_id), params={"lot_id": lot_id}).json()
        assert body["result"]["conformer_observation_id"] == obs2.id
        assert body["result"]["calculation_quality"] == "raw"

        # With quality=curated the raw -200 row is ineligible and the curated
        # -100 row on obs1 is the only survivor.
        body_curated = client.get(
            self._url(entry_id),
            params={"lot_id": lot_id, "calculation_quality": "curated"},
        ).json()
        assert body_curated["calculation_quality"] == "curated"
        assert body_curated["result"]["conformer_observation_id"] == obs_id
        assert body_curated["result"]["calculation_quality"] == "curated"

    def test_per_observation_collapse(self, client, db_session, seeded_entry):
        entry_id, obs_id, lot_id = seeded_entry
        group2 = ConformerGroup(species_entry_id=entry_id, label="g2")
        db_session.add(group2)
        db_session.flush()
        obs2 = ConformerObservation(conformer_group_id=group2.id)
        db_session.add(obs2)
        db_session.flush()

        # obs1 has two SPs: raw -100 and rejected -200. Under per-observation
        # collapse with quality priority raw > rejected, obs1's canonical
        # candidate is -100 (raw). obs2 has one SP at -150 (raw). After
        # collapse, global min over survivors is obs2 at -150.
        #
        # A naive "global row minimum" would pick obs1's rejected row at -200,
        # which is the exact failure mode collapse prevents.
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-100.0,
            quality=CalculationQuality.raw,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-200.0,
            quality=CalculationQuality.rejected,
        )
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs2.id,
            lot_id=lot_id,
            energy=-150.0,
            quality=CalculationQuality.raw,
        )

        body = client.get(self._url(entry_id), params={"lot_id": lot_id}).json()
        result = body["result"]
        assert result["conformer_observation_id"] == obs2.id
        assert result["electronic_energy_hartree"] == pytest.approx(-150.0)
        assert result["calculation_quality"] == "raw"

    def test_nonexistent_species_entry_returns_404(self, client, db_session):
        lot = _make_lot(db_session, "dummy", "basis")
        resp = client.get(
            "/api/v1/species-entries/999999/conformer-observations/lowest-sp",
            params={"lot_id": lot.id},
        )
        assert resp.status_code == 404
        assert "SpeciesEntry" in resp.json()["detail"]

    def test_nonexistent_lot_returns_404(self, client, seeded_entry):
        entry_id, _, _ = seeded_entry
        resp = client.get(self._url(entry_id), params={"lot_id": 999999})
        assert resp.status_code == 404
        assert "LevelOfTheory" in resp.json()["detail"]

    def test_ignores_non_sp_calculations(self, client, db_session, seeded_entry):
        entry_id, obs_id, lot_id = seeded_entry
        # Opt calculation at the requested LoT — must be ignored.
        _make_sp_calc(
            db_session,
            species_entry_id=entry_id,
            observation_id=obs_id,
            lot_id=lot_id,
            energy=-999.0,
            calc_type=CalculationType.opt,
        )
        resp = client.get(self._url(entry_id), params={"lot_id": lot_id})
        assert resp.json()["result"] is None
