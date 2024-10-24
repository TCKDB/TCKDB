import pytest

from tckdb.backend.app.conversions.converter import normalize_coordinates
from tckdb.backend.app.models.species import Species as SpeciesModel

"""
TestClient: Provided by FastAPI, allows you to simulate HTTP requests to your
application
Tests API endpoints by sending requests to the application and receiving
responses

client.get("/species/") or client.post("/species/", json={...})
TestClient simulates an HTTP request to the specified path
FastAPI routes this request to the corresponding endpoint function based on
the path and HTTP method
THe end point function processes the request, interacts with the database, and
returns a response

router = APIRouter(prefix="/species",
                    tags=["species"])
APIRouter: A class that groups related endpoints together
In the setup, you have prefix="/species", so all endpoints in this router will
start with /species

POST /species/: Create a new species
GET /species/{species_id}: Retrieve a species by its ID
GET /species/: Retrieve a list of species
PUT /species/{species_id}: Update a species by its ID
DELETE /species/{species_id}: Delete a species by its ID

VISUALIZATION OF THE REQUEST-RESPONSE FLOW
Client Request (e.g., GET /species/1)
        ↓
FastAPI Routing → Endpoint Function (`read_species`)
        ↓
Database Interaction via `get_db` Dependency → Retrieve Data
        ↓
Response Constructed and Sent Back to Client

"""


import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

API_V1_STR = "/api/v1"


# shared data
h_xyz = {"symbols": ("H",), "isotopes": (1,), "coords": ((0.0, 0.0, 0.0),)}
ch4_xyz = {
    "symbols": ("C", "H", "H", "H", "H"),
    "isotopes": (12, 1, 1, 1, 1),
    "coords": (
        (0.0, 0.0, 0.0),
        (0.6300326, 0.6300326, 0.6300326),
        (-0.6300326, -0.6300326, 0.6300326),
        (-0.6300326, 0.6300326, -0.6300326),
        (0.6300326, -0.6300326, -0.6300326),
    ),
}
ch4_freqs = [3046, 1555, 1555, 3168, 3168, 3168, 1368, 1368, 1368]
ch4_scaled_freqs = [
    3046 * 0.99,
    1555 * 0.99,
    1555 * 0.99,
    3168 * 0.99,
    3168 * 0.99,
    3168 * 0.99,
    1368 * 0.99,
    1368 * 0.99,
    1368 * 0.99,
]
ch4_normal_disp_modes = [[[1, 2, 3]] * 5] * 9
ICdCC_Cl__I_NF_xyz = {
    "symbols": ("I", "I", "Cl", "F", "N", "C", "C", "C", "H", "H", "H"),
    "isotopes": (127, 127, 35, 19, 14, 12, 12, 12, 1, 1, 1),
    "coords": (
        (-0.1752057997244146, 2.579412243969442, -0.0998868241437122),
        (2.982861260942898, -2.085955126671259, -0.45379679871721257),
        (-1.134251085086272, 0.5188212354137014, 2.0757032605524133),
        (-1.8814930376300307, -1.2539895001453651, -0.2064584290134856),
        (-1.5494242545791568, 0.0648586431861974, -0.452378816320353),
        (-0.48883763038476624, 0.5436231953516003, 0.41802210133854123),
        (0.8221194714634887, -0.18495290937918607, 0.3615122853791793),
        (1.1541030666741987, -1.1716982225873687, -0.4863411908291395),
        (1.5573291340285325, 0.13404869476412778, 1.0992045049933756),
        (-2.353154400199601, 0.664646058045691, -0.27667502002715677),
        (0.4586997381222457, -1.5322585343327426, -1.2389102050458707),
    ),
}
ICdCC_Cl__I_NF_freqs = [i + 1 for i in range(27)]
ICdCC_Cl__I_NF_scaled_freqs = [(i + 1) * 0.973 for i in range(27)]
ICdCC_Cl__I_NF_normal_disp_modes = [[[1, 2, 3]] * 11] * 27

heat_capacity_model = {
    "model": "NASA",
    "T min": 100,
    "T max": 5000,
    "coefficients": {
        "low": [
            4.13878818e00,
            -4.69514383e-03,
            2.25730249e-05,
            -2.09849937e-08,
            6.36123283e-12,
            -1.43493283e04,
            3.23827482e00,
        ],
        "high": [
            2.36095410e00,
            7.66804276e-03,
            -3.19770442e-06,
            6.04724833e-10,
            -4.27517878e-14,
            -1.42794809e04,
            1.04457152e01,
        ],
        "T int": 1041.96,
    },
}


@pytest.mark.usefixtures("setup_database")
class TestSpeciesEndpoints:

    @pytest.fixture(scope="class", autouse=True)
    def setup_species(
        self, request, test_level, test_ess, test_encorr, test_freq, client
    ):
        """
        Class-level ficture to create a species for testing
        """
        response = client.post(
            f"{API_V1_STR}/species/",
            json={
                "label": "TestSpecies",
                "charge": 0,
                "multiplicity": 1,
                "smiles": "C",
                "coordinates": ch4_xyz,
                "external_symmetry": 4,
                "point_group": "Td",
                "conformation_method": "ARC v1.1.0",
                "is_well": True,
                "electronic_energy": -365.544,
                "E0": -370.240,
                "hessian": [[1] * 15] * 15,
                "frequencies": ch4_freqs,
                "scaled_projected_frequencies": ch4_scaled_freqs,
                "normal_displacement_modes": ch4_normal_disp_modes,
                # "freq_id": test_freq.id,
                "freq_id": 1,
                "rigid_rotor": "spherical top",
                "statmech_treatment": "RRHO",
                "rotational_constants": [1, 2, 3],
                "H298": -74.52,
                "S298": 186.06,
                "Cp_values": [36.07, 40.38, 45.77, 51.63, 62.30, 71.00, 85.94],
                "Cp_T_list": [300, 400, 500, 600, 800, 1000, 1500],
                "encorr_id": test_encorr.id,
                "sp_level_id": test_level.id,  # Use the created Level's ID
                "sp_ess_id": test_ess.id,
                "opt_path": "path/to/opt/job.log",
                "freq_path": "path/to/freq/job.log",
                "sp_path": "path/to/sp/job.log",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        print("Response data:", data)  # Add this line for debugging
        request.cls.species_id = data["id"]
        request.cls.species_data = data
        print("Created species data: ", data)

    def get_species_from_db(self, species_id, db):
        """
        Helper method to fetch a species from the database by ID.
        """
        return db.query(SpeciesModel).filter(SpeciesModel.id == species_id).first()

    def test_create_species(self):
        """
        Test the initial creation of the species
        """
        assert self.species_data["label"] == "TestSpecies"
        assert self.species_data["smiles"] == "C"
        assert self.species_data["inchi"] == "InChI=1S/CH4/h1H4"

    def test_read_species(self, client):
        """
        Test retrieving a species by its ID
        """
        species_id = self.species_id
        response = client.get(f"{API_V1_STR}/species/{species_id}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["label"] == "TestSpecies"
        assert data["smiles"] == "C"
        assert normalize_coordinates(data["coordinates"]) == ch4_xyz

    def test_update_species_partial(self, client):
        """
        Test partial updating the species' attributes
        """
        species_id = self.species_id
        response = client.patch(
            f"{API_V1_STR}/species/{species_id}",
            json={
                "label": "UpdatedSpecies",
                "charge": 1,
                "multiplicity": 2,
            },
        )
        assert response.status_code == 200, response.text
        updated_data = response.json()
        assert updated_data["label"] == "UpdatedSpecies"
        assert updated_data["smiles"] == "C"
        assert updated_data["charge"] == 1
        assert updated_data["multiplicity"] == 2

    def test_soft_delete_species(self, client, db_session):
        """
        Test soft deleting the species
        """
        species_id = self.species_id
        response = client.delete(f"{API_V1_STR}/species/{species_id}/soft")
        assert response.status_code == 200, response.text
        assert response.json() == {"detail": "Species soft deleted"}

        db_species = self.get_species_from_db(species_id, db_session)
        assert db_species is not None, "Species should exist before soft deletion."
        assert (
            db_species.deleted_at is not None
        ), "Species should be soft-deleted (deleted_at should be set)."

    def test_restore_species(self, client):
        """
        Test for restoring a soft deleted species
        """
        species_id = self.species_id
        restore_response = client.post(f"{API_V1_STR}/species/{species_id}/restore")
        assert restore_response.status_code == 200, restore_response.text
        restored_data = restore_response.json()

        assert restored_data["id"] == species_id
        assert restored_data["label"] == "UpdatedSpecies"
        assert restored_data["smiles"] == "C"
        assert (
            restored_data["deleted_at"] is None
        ), "Species should be restored (deleted_at should be None)."
