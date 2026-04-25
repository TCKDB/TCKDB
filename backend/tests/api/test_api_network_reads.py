"""Tests for the network read API.

The network upload workflow (``/api/v1/uploads/networks/pdep``) creates
the full identity + provenance graph (network, states, channels, solve,
species, calculations).  ``network_kinetics`` rows, however, are not
written by any existing workflow — so tests that exercise kinetics read
endpoints insert those rows via raw ORM inserts on the shared test
session, matching the pattern used in ``test_calculation_phase2_reads``.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.common import (
    ArrheniusAUnits,
    NetworkKineticsModelKind,
    PressureUnit,
    TemperatureUnit,
)
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
    NetworkSolveEnergyTransfer,
)


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


_XYZ_ETHYL = "3\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nH 2.0 1.0 0.0"
_XYZ_O2 = "2\n\nO 0.0 0.0 0.0\nO 1.21 0.0 0.0"
_XYZ_ETOO = "4\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nO 2.5 0.0 0.0\nO 3.7 0.0 0.0"
_XYZ_TS = "4\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nO 2.2 0.0 0.0\nO 3.4 0.0 0.0"
_XYZ_AR = "1\n\nAr 0.0 0.0 0.0"

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_CC = {"method": "CCSD(T)", "basis": "cc-pVTZ"}


def _pdep_payload(name: str = "ethyl + O2") -> dict:
    """A compact PDep upload payload with a single association channel."""
    return {
        "name": name,
        "species": [
            {
                "key": "ethyl",
                "species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2},
                "conformers": [{
                    "key": "ethyl_conf",
                    "geometry": {"key": "ethyl_geom", "xyz_text": _XYZ_ETHYL},
                    "calculation": {
                        "key": "ethyl_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
                "calculations": [
                    {
                        "key": "ethyl_sp", "type": "sp", "geometry_key": "ethyl_geom",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -79.8,
                    },
                ],
            },
            {
                "key": "O2",
                "species_entry": {"smiles": "[O][O]", "charge": 0, "multiplicity": 3},
                "conformers": [{
                    "key": "O2_conf",
                    "geometry": {"key": "O2_geom", "xyz_text": _XYZ_O2},
                    "calculation": {
                        "key": "O2_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
                "calculations": [
                    {
                        "key": "O2_sp", "type": "sp", "geometry_key": "O2_geom",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -150.2,
                    },
                ],
            },
            {
                "key": "ethylperoxy",
                "species_entry": {"smiles": "CCO[O]", "charge": 0, "multiplicity": 2},
                "conformers": [{
                    "key": "etoo_conf",
                    "geometry": {"key": "etoo_geom", "xyz_text": _XYZ_ETOO},
                    "calculation": {
                        "key": "etoo_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
                "calculations": [
                    {
                        "key": "etoo_sp", "type": "sp", "geometry_key": "etoo_geom",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -229.1,
                    },
                ],
            },
            {
                "key": "Ar",
                "species_entry": {"smiles": "[Ar]", "charge": 0, "multiplicity": 1},
                "conformers": [{
                    "key": "Ar_conf",
                    "geometry": {"key": "Ar_geom", "xyz_text": _XYZ_AR},
                    "calculation": {
                        "key": "Ar_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
            },
        ],
        "transition_states": [{
            "key": "ts_assoc",
            "micro_reaction_key": "rxn_assoc",
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts_assoc_geom", "xyz_text": _XYZ_TS},
            "calculation": {
                "key": "ts_assoc_opt", "type": "opt",
                "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts_assoc_sp", "type": "sp",
                    "geometry_key": "ts_assoc_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                    "sp_electronic_energy_hartree": -229.5,
                },
            ],
        }],
        "micro_reactions": [{
            "key": "rxn_assoc",
            "reversible": True,
            "reactants": [{"species_key": "ethyl"}, {"species_key": "O2"}],
            "products": [{"species_key": "ethylperoxy"}],
        }],
        "states": [
            {
                "key": "entrance",
                "kind": "bimolecular",
                "participants": [
                    {"species_key": "ethyl"},
                    {"species_key": "O2"},
                ],
            },
            {
                "key": "well_RO2",
                "kind": "well",
                "participants": [{"species_key": "ethylperoxy"}],
            },
        ],
        "channels": [
            {
                "source_state_key": "entrance",
                "sink_state_key": "well_RO2",
                "kind": "association",
            },
        ],
        "solve": {
            "me_method": "reservoir_state",
            "tmin_k": 300,
            "tmax_k": 2000,
            "pmin_bar": 0.01,
            "pmax_bar": 100,
            "grain_count": 250,
            "bath_gas": [{"species_key": "Ar", "mole_fraction": 1.0}],
            "energy_transfer": {
                "model": "single_exponential_down",
                "alpha0_cm_inv": 300,
                "t_ref_k": 300,
            },
            "source_calculations": [
                {"calculation_key": "ethyl_sp", "role": "well_energy"},
                {"calculation_key": "ts_assoc_sp", "role": "barrier_energy"},
            ],
        },
    }


def _upload_pdep_network(client, name: str = "ethyl + O2") -> tuple[int, int]:
    """Upload a PDep network and return ``(network_id, solve_id)``."""
    resp = client.post("/api/v1/uploads/networks/pdep", json=_pdep_payload(name))
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return body["id"], body["solve_id"]


def _first_channel_id(db_session, network_id: int) -> int:
    channels = db_session.scalars(
        select(NetworkChannel).where(NetworkChannel.network_id == network_id)
    ).all()
    assert channels
    return channels[0].id


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


class TestNetworkList:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/networks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["skip"] == 0

    def test_list_after_upload(self, client):
        network_id, _ = _upload_pdep_network(client, name="net-a")
        resp = client.get("/api/v1/networks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

        item = data["items"][0]
        assert item["id"] == network_id
        assert item["name"] == "net-a"
        assert item["state_count"] == 2
        assert item["channel_count"] == 1
        assert item["solve_count"] == 1
        # Four species were uploaded (ethyl, O2, ethylperoxy, Ar).
        assert item["species_count"] == 4
        assert item["reaction_count"] == 1

    def test_list_pagination_smoke(self, client):
        for i in range(3):
            _upload_pdep_network(client, name=f"net-{i}")

        resp = client.get("/api/v1/networks?skip=1&limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 1
        assert data["skip"] == 1
        assert data["limit"] == 1


class TestNetworkDetail:
    def test_detail(self, client):
        network_id, _ = _upload_pdep_network(client, name="detail-net")
        resp = client.get(f"/api/v1/networks/{network_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["id"] == network_id
        assert data["name"] == "detail-net"
        assert data["solve_count"] == 1

        # Linked entities are embedded and graph-shaped
        assert len(data["species"]) == 4
        assert all("species_entry" in s and s["species_entry"] is not None
                   for s in data["species"])
        assert len(data["reactions"]) == 1
        assert data["reactions"][0]["reaction_entry"] is not None

        assert len(data["states"]) == 2
        # One state is bimolecular (2 participants), the other is a well.
        participant_counts = sorted(len(s["participants"]) for s in data["states"])
        assert participant_counts == [1, 2]
        # Participants carry the embedded species entry
        for state in data["states"]:
            for p in state["participants"]:
                assert p["species_entry"] is not None

        assert len(data["channels"]) == 1
        assert data["channels"][0]["kind"] == "association"

    def test_detail_404(self, client):
        resp = client.get("/api/v1/networks/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Solves
# ---------------------------------------------------------------------------


class TestNetworkSolves:
    def test_list_solves(self, client):
        network_id, solve_id = _upload_pdep_network(client)
        resp = client.get(f"/api/v1/networks/{network_id}/solves")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 1
        row = data[0]
        assert row["id"] == solve_id
        assert row["network_id"] == network_id
        assert row["me_method"] == "reservoir_state"
        assert row["bath_gas_count"] == 1
        assert row["source_calculation_count"] == 2
        assert row["kinetics_count"] == 0  # none inserted yet

    def test_list_solves_parent_404(self, client):
        resp = client.get("/api/v1/networks/999999/solves")
        assert resp.status_code == 404

    def test_solve_detail(self, client):
        network_id, solve_id = _upload_pdep_network(client)
        resp = client.get(f"/api/v1/networks/{network_id}/solves/{solve_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["id"] == solve_id
        assert data["network_id"] == network_id
        assert data["me_method"] == "reservoir_state"
        # Bath gas with embedded species entry
        assert len(data["bath_gases"]) == 1
        assert data["bath_gases"][0]["mole_fraction"] == 1.0
        assert data["bath_gases"][0]["species_entry"] is not None
        # Energy transfer
        assert data["energy_transfer"] is not None
        assert data["energy_transfer"]["model"] == "single_exponential_down"
        # Source calcs with embedded calculation
        assert len(data["source_calculations"]) == 2
        assert all(sc["calculation"] is not None for sc in data["source_calculations"])
        # Kinetics: empty in this fixture
        assert data["kinetics"] == []

    def test_solve_detail_404_when_solve_missing(self, client):
        network_id, _ = _upload_pdep_network(client)
        resp = client.get(f"/api/v1/networks/{network_id}/solves/999999")
        assert resp.status_code == 404

    def test_solve_detail_404_when_solve_belongs_to_other_network(
        self, client, db_session
    ):
        # Two networks with their own solves
        net_a, solve_a = _upload_pdep_network(client, name="A")
        net_b, solve_b = _upload_pdep_network(client, name="B")
        assert net_a != net_b
        assert solve_a != solve_b

        # solve_b under net_a must 404
        resp = client.get(f"/api/v1/networks/{net_a}/solves/{solve_b}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Channel kinetics  (requires raw ORM inserts of NetworkKinetics)
# ---------------------------------------------------------------------------


class TestChannelKinetics:
    def test_chebyshev_serialization(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id,
            solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.chebyshev,
            tmin_k=300.0, tmax_k=2000.0,
            pmin_bar=0.01, pmax_bar=100.0,
            rate_units=ArrheniusAUnits.cm3_mol_s,
            pressure_units=PressureUnit.bar,
            temperature_units=TemperatureUnit.kelvin,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add(NetworkKineticsChebyshev(
            network_kinetics_id=kin.id,
            n_temperature=3,
            n_pressure=2,
            coefficients={"matrix": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]},
        ))
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["model_kind"] == "chebyshev"
        # Enum round-trip — serialized as canonical string values
        assert row["rate_units"] == "cm3_mol_s"
        assert row["pressure_units"] == "bar"
        assert row["temperature_units"] == "kelvin"
        assert row["chebyshev"] is not None
        assert row["chebyshev"]["n_temperature"] == 3
        assert row["chebyshev"]["n_pressure"] == 2
        assert row["chebyshev"]["coefficients"] == {
            "matrix": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        }
        assert row["plog_entries"] == []
        assert row["points"] == []

    def test_plog_serialization(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id,
            solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.plog,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add_all([
            NetworkKineticsPlog(
                network_kinetics_id=kin.id,
                pressure_bar=1.0, entry_index=1,
                a=1.0e12, n=0.0, ea_kj_mol=50.0,
            ),
            NetworkKineticsPlog(
                network_kinetics_id=kin.id,
                pressure_bar=10.0, entry_index=1,
                a=2.0e12, n=0.5, ea_kj_mol=45.0,
            ),
        ])
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["model_kind"] == "plog"
        assert len(row["plog_entries"]) == 2
        pressures = [p["pressure_bar"] for p in row["plog_entries"]]
        assert pressures == sorted(pressures)  # sorted by pressure
        assert row["chebyshev"] is None
        assert row["points"] == []

    def test_tabulated_serialization(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id,
            solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.tabulated,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add_all([
            NetworkKineticsPoint(
                network_kinetics_id=kin.id,
                temperature_k=500.0, pressure_bar=1.0, rate_value=1.0e8,
            ),
            NetworkKineticsPoint(
                network_kinetics_id=kin.id,
                temperature_k=1000.0, pressure_bar=1.0, rate_value=5.0e9,
            ),
        ])
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["model_kind"] == "tabulated"
        assert len(row["points"]) == 2
        assert row["chebyshev"] is None
        assert row["plog_entries"] == []

    def test_channel_404_when_channel_belongs_to_other_network(
        self, client, db_session
    ):
        net_a, _ = _upload_pdep_network(client, name="A")
        net_b, _ = _upload_pdep_network(client, name="B")

        channel_b_id = _first_channel_id(db_session, net_b)
        resp = client.get(
            f"/api/v1/networks/{net_a}/channels/{channel_b_id}/kinetics"
        )
        assert resp.status_code == 404

    def test_channel_404_when_missing(self, client):
        network_id, _ = _upload_pdep_network(client)
        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/999999/kinetics"
        )
        assert resp.status_code == 404

    def test_kinetics_filter_by_solve_id(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        # Insert a second solve on the same network directly.
        other_solve = NetworkSolve(network_id=network_id, me_method="alt")
        db_session.add(other_solve)
        db_session.flush()

        # One kinetics per solve
        k1 = NetworkKinetics(
            channel_id=channel_id, solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.plog,
        )
        k2 = NetworkKinetics(
            channel_id=channel_id, solve_id=other_solve.id,
            model_kind=NetworkKineticsModelKind.plog,
        )
        db_session.add_all([k1, k2])
        db_session.flush()
        db_session.add_all([
            NetworkKineticsPlog(
                network_kinetics_id=k1.id, pressure_bar=1.0, entry_index=1,
                a=1.0, n=0.0, ea_kj_mol=10.0,
            ),
            NetworkKineticsPlog(
                network_kinetics_id=k2.id, pressure_bar=1.0, entry_index=1,
                a=2.0, n=0.0, ea_kj_mol=20.0,
            ),
        ])
        db_session.flush()

        # No filter — both
        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        # Filter by solve_id
        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
            f"?solve_id={solve_id}"
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["solve_id"] == solve_id

        # Filter by solve_id that belongs to a different network → 404
        net_b, solve_b = _upload_pdep_network(client, name="foreign")
        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
            f"?solve_id={solve_b}"
        )
        assert resp.status_code == 404


class TestSolveKineticsInclusion:
    def test_solve_detail_includes_kinetics(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id,
            solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.plog,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add(NetworkKineticsPlog(
            network_kinetics_id=kin.id, pressure_bar=1.0, entry_index=1,
            a=1.0e12, n=0.5, ea_kj_mol=40.0,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/networks/{network_id}/solves/{solve_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["kinetics"]) == 1
        assert data["kinetics"][0]["model_kind"] == "plog"
        assert len(data["kinetics"][0]["plog_entries"]) == 1


# ---------------------------------------------------------------------------
# Enum round-trip on kinetics list and solve detail
# ---------------------------------------------------------------------------


class TestKineticsEnumRoundTrip:
    def test_plog_carries_all_unit_enums(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id, solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.plog,
            rate_units=ArrheniusAUnits.per_s,
            pressure_units=PressureUnit.bar,
            temperature_units=TemperatureUnit.kelvin,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add(NetworkKineticsPlog(
            network_kinetics_id=kin.id, pressure_bar=1.0, entry_index=1,
            a=1.0e12, n=0.0, ea_kj_mol=50.0,
        ))
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 200
        row = resp.json()[0]
        assert row["rate_units"] == "per_s"
        assert row["pressure_units"] == "bar"
        assert row["temperature_units"] == "kelvin"

    def test_solve_detail_exposes_unit_enums(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id, solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.tabulated,
            rate_units=ArrheniusAUnits.cm3_mol_s,
            pressure_units=PressureUnit.bar,
            temperature_units=TemperatureUnit.kelvin,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add(NetworkKineticsPoint(
            network_kinetics_id=kin.id,
            temperature_k=500.0, pressure_bar=1.0, rate_value=1e8,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/networks/{network_id}/solves/{solve_id}")
        assert resp.status_code == 200
        k = resp.json()["kinetics"][0]
        assert k["rate_units"] == "cm3_mol_s"
        assert k["pressure_units"] == "bar"
        assert k["temperature_units"] == "kelvin"


# ---------------------------------------------------------------------------
# Structural empty cases — networks with no solves / no channels / no kinetics
# ---------------------------------------------------------------------------


def _bare_network(db_session, name: str = "bare") -> int:
    """Insert a minimal network row directly (no states/channels/solves)."""
    from app.db.models.network import Network as _Network

    net = _Network(name=name)
    db_session.add(net)
    db_session.flush()
    return net.id


class TestStructuralEmpty:
    def test_network_detail_without_solves_or_channels(self, client, db_session):
        net_id = _bare_network(db_session, "empty")
        resp = client.get(f"/api/v1/networks/{net_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == net_id
        assert data["solve_count"] == 0
        assert data["species"] == []
        assert data["reactions"] == []
        assert data["states"] == []
        assert data["channels"] == []

    def test_network_detail_without_channels_only(self, client, db_session):
        """A network that has solves but no channels should still serialize cleanly.

        This shape is uncommon but structurally valid (the upload workflow
        always co-creates channels; here we simulate a curated-only record).
        """
        net_id = _bare_network(db_session, "no-channels")
        # Add a solve directly so solve_count > 0 but channels remain empty.
        db_session.add(NetworkSolve(network_id=net_id, me_method="curated"))
        db_session.flush()

        resp = client.get(f"/api/v1/networks/{net_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"] == []
        assert data["solve_count"] == 1

    def test_network_solves_empty_list(self, client, db_session):
        net_id = _bare_network(db_session, "no-solves")
        resp = client.get(f"/api/v1/networks/{net_id}/solves")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_channel_kinetics_empty_list(self, client, db_session):
        network_id, _ = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)
        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Malformed polymorphic kinetics data  — must 500 with clear message
# ---------------------------------------------------------------------------


class TestMalformedKinetics:
    def test_no_subtype_payload_raises_500(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        # Insert a kinetics row with NO child payload (malformed state).
        kin = NetworkKinetics(
            channel_id=channel_id, solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.plog,
        )
        db_session.add(kin)
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        # Row id must NOT leak into the user-facing payload.
        assert str(kin.id) not in detail
        assert "Invalid network_kinetics row" in detail
        assert "model_kind='plog'" in detail
        assert "no matching subtype payload was found" in detail

    def test_multiple_subtype_families_raises_500(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        kin = NetworkKinetics(
            channel_id=channel_id, solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.plog,
        )
        db_session.add(kin)
        db_session.flush()

        # Both a plog entry AND a chebyshev payload — contradictory.
        db_session.add(NetworkKineticsPlog(
            network_kinetics_id=kin.id, pressure_bar=1.0, entry_index=1,
            a=1.0e12, n=0.0, ea_kj_mol=50.0,
        ))
        db_session.add(NetworkKineticsChebyshev(
            network_kinetics_id=kin.id,
            n_temperature=2, n_pressure=2,
            coefficients={"matrix": [[1, 2], [3, 4]]},
        ))
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert str(kin.id) not in detail
        assert "Invalid network_kinetics row" in detail
        assert "model_kind='plog'" in detail
        # Mismatch summary shows every family's populated flag.
        assert "chebyshev=True" in detail
        assert "plog=True" in detail
        assert "tabulated=False" in detail

    def test_payload_does_not_match_model_kind_raises_500(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        channel_id = _first_channel_id(db_session, network_id)

        # model_kind says chebyshev, but the payload is a plog row.
        kin = NetworkKinetics(
            channel_id=channel_id, solve_id=solve_id,
            model_kind=NetworkKineticsModelKind.chebyshev,
        )
        db_session.add(kin)
        db_session.flush()
        db_session.add(NetworkKineticsPlog(
            network_kinetics_id=kin.id, pressure_bar=1.0, entry_index=1,
            a=1.0e12, n=0.0, ea_kj_mol=50.0,
        ))
        db_session.flush()

        resp = client.get(
            f"/api/v1/networks/{network_id}/channels/{channel_id}/kinetics"
        )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert str(kin.id) not in detail
        assert "Invalid network_kinetics row" in detail
        assert "model_kind='chebyshev'" in detail
        assert "chebyshev=False" in detail
        assert "plog=True" in detail


# ---------------------------------------------------------------------------
# Energy transfer validity
# ---------------------------------------------------------------------------


class TestEnergyTransferValidity:
    def test_no_energy_transfer_serialises_null(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        # Delete the single energy-transfer row the upload created.
        db_session.query(NetworkSolveEnergyTransfer).filter_by(
            solve_id=solve_id
        ).delete()
        db_session.flush()

        resp = client.get(f"/api/v1/networks/{network_id}/solves/{solve_id}")
        assert resp.status_code == 200
        assert resp.json()["energy_transfer"] is None

    def test_single_energy_transfer_serialises_object(self, client):
        network_id, solve_id = _upload_pdep_network(client)
        resp = client.get(f"/api/v1/networks/{network_id}/solves/{solve_id}")
        assert resp.status_code == 200
        et = resp.json()["energy_transfer"]
        assert et is not None
        assert et["model"] == "single_exponential_down"

    def test_multiple_energy_transfer_rows_raises_500(self, client, db_session):
        network_id, solve_id = _upload_pdep_network(client)
        # Add a second row, making the solve malformed.
        db_session.add(NetworkSolveEnergyTransfer(
            solve_id=solve_id,
            model="exponential_down",
            alpha0_cm_inv=250.0,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/networks/{network_id}/solves/{solve_id}")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert str(solve_id) not in detail
        assert "Invalid network_solve" in detail
        assert "expected at most one energy transfer row" in detail
        assert "found 2" in detail
