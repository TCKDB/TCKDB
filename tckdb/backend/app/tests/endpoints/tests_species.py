from urllib import response
import pytest
from fastapi.testclient import TestClient
from tckdb.backend.app.main import app
from tckdb.backend.app.models import species
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
h_xyz = {'symbols': ('H',),
            'isotopes': (1,),
            'coords': ((0.0, 0.0, 0.0),)}
ch4_xyz = {'symbols': ('C', 'H', 'H', 'H', 'H'),
            'isotopes': (12, 1, 1, 1, 1),
            'coords': ((0.0, 0.0, 0.0),
                        (0.6300326, 0.6300326, 0.6300326),
                        (-0.6300326, -0.6300326, 0.6300326),
                        (-0.6300326, 0.6300326, -0.6300326),
                        (0.6300326, -0.6300326, -0.6300326))}
ch4_freqs = [3046, 1555, 1555, 3168, 3168, 3168, 1368, 1368, 1368]
ch4_scaled_freqs = [3046 * .99, 1555 * .99, 1555 * .99, 3168 * .99, 3168 * .99, 3168 * .99, 1368 * .99, 1368 * .99,
                    1368 * .99]
ch4_normal_disp_modes = [[[1, 2, 3]] * 5] * 9
ICdCC_Cl__I_NF_xyz = {'symbols': ('I', 'I', 'Cl', 'F', 'N', 'C', 'C', 'C', 'H', 'H', 'H'),
                        'isotopes': (127, 127, 35, 19, 14, 12, 12, 12, 1, 1, 1),
                        'coords': ((-0.1752057997244146, 2.579412243969442, -0.0998868241437122),
                                    (2.982861260942898, -2.085955126671259, -0.45379679871721257),
                                    (-1.134251085086272, 0.5188212354137014, 2.0757032605524133),
                                    (-1.8814930376300307, -1.2539895001453651, -0.2064584290134856),
                                    (-1.5494242545791568, 0.0648586431861974, -0.452378816320353),
                                    (-0.48883763038476624, 0.5436231953516003, 0.41802210133854123),
                                    (0.8221194714634887, -0.18495290937918607, 0.3615122853791793),
                                    (1.1541030666741987, -1.1716982225873687, -0.4863411908291395),
                                    (1.5573291340285325, 0.13404869476412778, 1.0992045049933756),
                                    (-2.353154400199601, 0.664646058045691, -0.27667502002715677),
                                    (0.4586997381222457, -1.5322585343327426, -1.2389102050458707))}
ICdCC_Cl__I_NF_freqs = [i + 1 for i in range(27)]
ICdCC_Cl__I_NF_scaled_freqs = [(i + 1) * 0.973 for i in range(27)]
ICdCC_Cl__I_NF_normal_disp_modes = [[[1, 2, 3]] * 11] * 27

heat_capacity_model = {'model': 'NASA',
                        'T min': 100,
                        'T max': 5000,
                        'coefficients': {'low': [4.13878818E+00, -4.69514383E-03,
                                                2.25730249E-05, -2.09849937E-08,
                                                6.36123283E-12, -1.43493283E+04,
                                                3.23827482E+00],
                                        'high': [2.36095410E+00, 7.66804276E-03,
                                                    -3.19770442E-06, 6.04724833E-10,
                                                    -4.27517878E-14, -1.42794809E+04,
                                                    1.04457152E+01],
                                        'T int': 1041.96}}

@pytest.mark.usefixtures("setup_database")
class TestSpeciesEndpoints:

    @pytest.fixture(scope="class", autouse=True)
    def setup_species(self, request, test_level, test_ess, test_encorr, test_freq, client):
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
                #"freq_id": test_freq.id,
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

    def test_soft_delete_species(self, client):
        """
        Test soft deleting the species
        """
        species_id = self.species_id
        response = client.delete(f"{API_V1_STR}/species/{species_id}/soft")
        assert response.status_code == 200, response.text
        assert response.json() == {"detail": "Species soft deleted"}
    


    # Verify AuditLog entry using the provided db_session_fixture
    # audit_logs = db_session_fixture.query(AuditLog).filter(
    #     AuditLog.model == "species",
    #     AuditLog.model_id == data["id"],
    #     AuditLog.action == "create",
    # ).all()
    # assert len(audit_logs) == 1
    # assert audit_logs[0].changes["label"] == "TestSpecies"

# def test_read_species(setup_database):
#     """
#     Test retrieving a species by its ID
#     """
#     # Create a species
#     create_response = client.post(
#         "/species/",
#         json={
#             "label": "ReadTestSpecies",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "CO",
#             "inchi": "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3",
#             "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
#     2 O u0 p2 c0 {1,S} {6,S}
#     3 H u0 p0 c0 {1,S}
#     4 H u0 p0 c0 {1,S}
#     5 H u0 p0 c0 {1,S}
#     6 H u0 p0 c0 {2,S}""",
#             "external_symmetry": 2,
#             "point_group": "C2v",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -80.0,
#             "E0": -80.0,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [1500, 1600, 1700],
#             "scaled_projected_frequencies": [1500 * 0.99, 1600 * 0.99, 1700 * 0.99],
#             "normal_displacement_modes": [[[1, 2, 3]] * 2] * 3,
#             "freq_id": 2,
#             "rigid_rotor": "linear",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -80.00,
#             "S298": 190.00,
#             "Cp_values": [40.00, 45.00, 50.00],
#             "Cp_T_list": [300, 400, 500],
#             "encorr_id": 3,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     assert create_response.status_code == 201, create_response.text
#     species = create_response.json()
#     species_id = species["id"]

#     # Retrieve the species
#     response = client.get(f"/species/{species_id}")
#     assert response.status_code == 200, response.text
#     data = response.json()
#     assert data["label"] == "ReadTestSpecies"
#     assert data["smiles"] == "CO"


# def test_read_species_list(setup_database):
#     """
#     Test retrieving species with SMILES filter
#     """
#     # Create multiple species
#     client.post(
#         "/species/",
#         json={
#             "label": "FilterTestSpecies1",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "CC",
#             "inchi": "InChI=1S/C2H6/c1-2/h1-2H3",
#             "inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
# 2 C u0 p0 c0 {1,S} {6,S} {7,S} {8,S}
# 3 H u0 p0 c0 {1,S}
# 4 H u0 p0 c0 {1,S}
# 5 H u0 p0 c0 {1,S}
# 6 H u0 p0 c0 {2,S}
# 7 H u0 p0 c0 {2,S}
# 8 H u0 p0 c0 {2,S}""",
#             "external_symmetry": 2,
#             "point_group": "C2v",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -80.0,
#             "E0": -80.0,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [1500, 1600, 1700],
#             "scaled_projected_frequencies": [1500 * 0.99, 1600 * 0.99, 1700 * 0.99],
#             "normal_displacement_modes": [[[1, 2, 3]] * 2] * 3,
#             "freq_id": 3,
#             "rigid_rotor": "linear",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -80.00,
#             "S298": 190.00,
#             "Cp_values": [40.00, 45.00, 50.00],
#             "Cp_T_list": [300, 400, 500],
#             "encorr_id": 3,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     client.post(
#         "/species/",
#         json={
#             "label": "FilterTestSpecies2",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "CO",
#             "inchi": "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3",
#             "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
# 2 O u0 p2 c0 {1,S} {6,S}
# 3 H u0 p0 c0 {1,S}
# 4 H u0 p0 c0 {1,S}
# 5 H u0 p0 c0 {1,S}
# 6 H u0 p0 c0 {2,S}""",
#             "external_symmetry": 2,
#             "point_group": "C2v",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -80.0,
#             "E0": -80.0,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [1500, 1600, 1700],
#             "scaled_projected_frequencies": [1500 * 0.99, 1600 * 0.99, 1700 * 0.99],
#             "normal_displacement_modes": [[[1, 2, 3]] * 2] * 3,
#             "freq_id": 4,
#             "rigid_rotor": "linear",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -80.00,
#             "S298": 190.00,
#             "Cp_values": [40.00, 45.00, 50.00],
#             "Cp_T_list": [300, 400, 500],
#             "encorr_id": 4,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     # Retrieve species with SMILES 'CO'
#     response = client.get("/species/?smiles=CO")
#     assert response.status_code == 200, response.text
#     data = response.json()
#     assert len(data) == 1
#     assert data[0]["label"] == "FilterTestSpecies2"
#     assert data[0]["smiles"] == "CO"


# def test_update_species(setup_database):
#     """
#     Test updating an existing species
#     """
#     # Create a species
#     create_response = client.post(
#         "/species/",
#         json={
#             "label": "UpdateTestSpecies",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "C",
#             "inchi": "InChI=1S/CH4/h1H4",
#             "inchi_key": "VNWKTOKETHGBQD-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
# 2 H u0 p0 c0 {1,S}
# 3 H u0 p0 c0 {1,S}
# 4 H u0 p0 c0 {1,S}
# 5 H u0 p0 c0 {1,S}""",
#             "external_symmetry": 4,
#             "point_group": "Td",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -365.544,
#             "E0": -370.240,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [3046, 1555, 1555, 3168, 3168, 3168, 1368, 1368, 1368],
#             "scaled_projected_frequencies": [
#                 3046 * 0.99,
#                 1555 * 0.99,
#                 1555 * 0.99,
#                 3168 * 0.99,
#                 3168 * 0.99,
#                 3168 * 0.99,
#                 1368 * 0.99,
#                 1368 * 0.99,
#                 1368 * 0.99,
#             ],
#             "normal_displacement_modes": [[[1, 2, 3]] * 5] * 9,
#             "freq_id": 5,
#             "rigid_rotor": "spherical top",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -74.52,
#             "S298": 186.06,
#             "Cp_values": [36.07, 40.38, 45.77, 51.63, 62.30, 71.00, 85.94],
#             "Cp_T_list": [300, 400, 500, 600, 800, 1000, 1500],
#             "encorr_id": 5,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     assert create_response.status_code == 201, create_response.text
#     species = create_response.json()
#     species_id = species["id"]

#     # Update the species
#     update_response = client.put(
#         f"/species/{species_id}",
#         json={
#             "label": "UpdatedSpecies",
#             "charge": 1,
#             "multiplicity": 2,
#             "smiles": "CC",
#             "inchi": "InChI=1S/C2H6/c1-2/h1-2H3",
#             "inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
# 2 C u0 p0 c0 {1,S} {6,S} {7,S} {8,S}
# 3 H u0 p0 c0 {1,S}
# 4 H u0 p0 c0 {1,S}
# 5 H u0 p0 c0 {1,S}
# 6 H u0 p0 c0 {2,S}
# 7 H u0 p0 c0 {2,S}
# 8 H u0 p0 c0 {2,S}""",
#             "external_symmetry": 2,
#             "point_group": "C2v",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": False,
#             "electronic_energy": -80.0,
#             "E0": -80.0,
#             "hessian": [[2] * 15] * 15,
#             "frequencies": [3100, 1600, 1600, 3200, 3200, 3200, 1400, 1400, 1400],
#             "scaled_projected_frequencies": [
#                 3100 * 0.99,
#                 1600 * 0.99,
#                 1600 * 0.99,
#                 3200 * 0.99,
#                 3200 * 0.99,
#                 3200 * 0.99,
#                 1400 * 0.99,
#                 1400 * 0.99,
#                 1400 * 0.99,
#             ],
#             "normal_displacement_modes": [[[2, 3, 4]] * 5] * 9,
#             "freq_id": 6,
#             "rigid_rotor": "linear",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [2, 3, 4],
#             "H298": -80.00,
#             "S298": 190.00,
#             "Cp_values": [40.00, 45.00, 50.00, 55.00, 65.00, 75.00, 90.00],
#             "Cp_T_list": [350, 450, 550, 650, 850, 1050, 1550],
#             "encorr_id": 6,
#             "opt_path": "path/to/opt/job_updated.log",
#             "freq_path": "path/to/freq/job_updated.log",
#             "sp_path": "path/to/sp/job_updated.log",
#         },
#     )
#     assert update_response.status_code == 200, update_response.text
#     updated_species = update_response.json()
#     assert updated_species["label"] == "UpdatedSpecies"
#     assert updated_species["smiles"] == "CC"
#     assert updated_species["charge"] == 1
#     assert updated_species["multiplicity"] == 2

#     db = next(override_get_db())
#     audit_logs = db.query(AuditLog).filter(
#         AuditLog.model == "species",
#         AuditLog.model_id == species_id,
#         AuditLog.action == "update",
#     ).all()
#     assert len(audit_logs) == 1
#     assert audit_logs[0].changes["label"]["old"] == "UpdateTestSpecies"
#     assert audit_logs[0].changes["label"]["new"] == "UpdatedSpecies"
#     assert audit_logs[0].changes["charge"]["old"] == 0
#     assert audit_logs[0].changes["charge"]["new"] == 1

# def test_delete_species_soft(setup_database):
#     """
#     Test deleting a species by its ID
#     """
#     # Create a species
#     create_response = client.post(
#         "/species/",
#         json={
#             "label": "DeleteTestSpecies",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "C",
#             "inchi": "InChI=1S/CH4/h1H4",
#             "inchi_key": "VNWKTOKETHGBQD-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
# 2 H u0 p0 c0 {1,S}
# 3 H u0 p0 c0 {1,S}
# 4 H u0 p0 c0 {1,S}
# 5 H u0 p0 c0 {1,S}""",
#             "external_symmetry": 4,
#             "point_group": "Td",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -365.544,
#             "E0": -370.240,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [3046, 1555, 1555, 3168, 3168, 3168, 1368, 1368, 1368],
#             "scaled_projected_frequencies": [
#                 3046 * 0.99,
#                 1555 * 0.99,
#                 1555 * 0.99,
#                 3168 * 0.99,
#                 3168 * 0.99,
#                 3168 * 0.99,
#                 1368 * 0.99,
#                 1368 * 0.99,
#                 1368 * 0.99,
#             ],
#             "normal_displacement_modes": [[[1, 2, 3]] * 5] * 9,
#             "freq_id": 7,
#             "rigid_rotor": "spherical top",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -74.52,
#             "S298": 186.06,
#             "Cp_values": [36.07, 40.38, 45.77, 51.63, 62.30, 71.00, 85.94],
#             "Cp_T_list": [300, 400, 500, 600, 800, 1000, 1500],
#             "encorr_id": 7,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     assert create_response.status_code == 201, create_response.text
#     species = create_response.json()
#     species_id = species["id"]
    
#     # Soft delete the species
#     delete_response = client.delete(f"/species/{species_id}/soft")
#     assert delete_response.status_code == 200, delete_response.text
#     assert delete_response.json() == {"message": "Species soft deleted"}
    
#     # Attempt to retrieve the species
#     get_response = client.get(f"/species/{species_id}")
#     assert get_response.status_code == 404, get_response.text
#     assert get_response.json() == {"detail": "Species not found"}
    
#     # Verify AuditLog entry
#     db = next(override_get_db())
#     audit_logs = db.query(AuditLog).filter(
#         AuditLog.model == "species",
#         AuditLog.model_id == species_id,
#         AuditLog.action == "soft_delete",
#     ).all()
#     assert len(audit_logs) == 1
#     assert audit_logs[0].changes["deleted_at"] is not None
    
# def test_delete_species_hard(setup_database):
#     """
#     Test permanently deleting a species by its ID
#     """
#     # Create a species
#     create_response = client.post(
#         "/species/",
#         json={
#             "label": "DeleteTestSpecies",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "C",
#             "inchi": "InChI=1S/CH4/h1H4",
#             "inchi_key": "VNWKTOKETHGBQD-UHFFFAOYSA-N",
#             "graph": """1 C u0 p0 c0 {2,S} {3,S} {4,S} {5,S}
# 2 H u0 p0 c0 {1,S}
# 3 H u0 p0 c0 {1,S}
# 4 H u0 p0 c0 {1,S}
# 5 H u0 p0 c0 {1,S}""",
#             "external_symmetry": 4,
#             "point_group": "Td",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -365.544,
#             "E0": -370.240,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [3046, 1555, 1555, 3168, 3168, 3168, 1368, 1368, 1368],
#             "scaled_projected_frequencies": [
#                 3046 * 0.99,
#                 1555 * 0.99,
#                 1555 * 0.99,
#                 3168 * 0.99,
#                 3168 * 0.99,
#                 3168 * 0.99,
#                 1368 * 0.99,
#                 1368 * 0.99,
#                 1368 * 0.99,
#             ],
#             "normal_displacement_modes": [[[1, 2, 3]] * 5] * 9,
#             "freq_id": 8,
#             "rigid_rotor": "spherical top",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -74.52,
#             "S298": 186.06,
#             "Cp_values": [36.07, 40.38, 45.77, 51.63, 62.30, 71.00, 85.94],
#             "Cp_T_list": [300, 400, 500, 600, 800, 1000, 1500],
#             "encorr_id": 8,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     assert create_response.status_code == 201, create_response.text
#     species = create_response.json()
#     species_id = species["id"]
    
#     # Hard delete the species
#     delete_response = client.delete(f"/species/{species_id}/hard")
#     assert delete_response.status_code == 200, delete_response.text
#     assert delete_response.json() == {"message": "Species permanently deleted"}
    
#     # Attempt to retrieve the species
#     get_response = client.get(f"/species/{species_id}")
#     assert get_response.status_code == 404, get_response.text
    
#     # Verify AuditLog entry
#     db = next(override_get_db())
#     audit_logs = db.query(AuditLog).filter(
#         AuditLog.model == "species",
#         AuditLog.model_id == species_id,
#         AuditLog.action == "hard_delete",
#     ).all()
#     assert len(audit_logs) == 1
#     assert audit_logs[0].changes["deleted_at"] is not None

# def test_restore_species(setup_database):
#     """
#     Test restoring a species by its ID
#     """
#     # Create a species
#     # Create a species
#     create_response = client.post(
#         "/species/",
#         json={
#             "label": "RestoreTestSpecies",
#             "charge": 0,
#             "multiplicity": 1,
#             "smiles": "O",
#             "inchi": "InChI=1S/O2/c1-2",
#             "inchi_key": "WQZGKKKJIJNGIO-UHFFFAOYSA-N",
#             "graph": """1 O u0 p2 c0 {2,S}
#     2 O u0 p2 c0 {1,S}""",
#             "external_symmetry": 2,
#             "point_group": "C2v",
#             "conformation_method": "ARC v1.1.0",
#             "is_well": True,
#             "electronic_energy": -80.0,
#             "E0": -80.0,
#             "hessian": [[1] * 15] * 15,
#             "frequencies": [1500, 1600, 1700],
#             "scaled_projected_frequencies": [1500 * 0.99, 1600 * 0.99, 1700 * 0.99],
#             "normal_displacement_modes": [[[1, 2, 3]] * 2] * 3,
#             "freq_id": 8,
#             "rigid_rotor": "linear",
#             "statmech_treatment": "RRHO",
#             "rotational_constants": [1, 2, 3],
#             "H298": -80.00,
#             "S298": 190.00,
#             "Cp_values": [40.00, 45.00, 50.00],
#             "Cp_T_list": [300, 400, 500],
#             "encorr_id": 8,
#             "opt_path": "path/to/opt/job.log",
#             "freq_path": "path/to/freq/job.log",
#             "sp_path": "path/to/sp/job.log",
#         },
#     )
#     assert create_response.status_code == 201, create_response.text
#     species = create_response.json()
#     species_id = species["id"]
    
#     # Soft delete the species
#     delete_response = client.delete(f"/species/{species_id}/soft")
#     assert delete_response.status_code == 200, delete_response.text
#     assert delete_response.json() == {"message": "Species soft deleted"}
    
#     # Restore the species
#     restore_response = client.post(f"/species/{species_id}/restore")
#     assert restore_response.status_code == 200, restore_response.text
#     restored_species = restore_response.json()
#     assert restored_species["label"] == "RestoreTestSpecies"
    
#     # Verify audit log entry
#     db = next(override_get_db())
#     audit_logs = db.query(AuditLog).filter(
#         AuditLog.model == "species",
#         AuditLog.model_id == species_id,
#         AuditLog.action == "restore",
#     ).all()
#     assert len(audit_logs) == 1
#     assert audit_logs[0].changes["deleted_at"] is None

# def test_create_species_invalid_data(setup_database):
#     """
#     Test creating a new species with invalid data
#     """
#     response = client.post("/species/", json={"invalid_field": "value"})
#     assert response.status_code == 422  # Unprocessable Entity


# def test_get_nonexistent_species(setup_database):
#     """
#     Test retrieving a species that does not exist
#     """
#     response = client.get("/species/999")
#     assert response.status_code == 404  # Not Found
#     assert response.json() == {"detail": "Species not found"}
