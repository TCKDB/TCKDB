
from difflib import restore
import pytest

from tckdb.backend.app.core.config import API_V1_STR
from tckdb.backend.app.models import NonPhysicalSpecies as NonPhysicalSpeciesModel
from tckdb.backend.app.conversions.converter import normalize_coordinates


# Shared Data
formaldehyde_xyz = {'symbols': ('C', 'O', 'H', 'H'),
                    'isotopes': (12, 16, 1, 1),
                    'coords': ((-0.0122240982, 0.0001804054, -0.00162116),
                               (1.2016481968, -0.0177341701, 0.1593624097),
                               (-0.5971643978, 0.9327281670, 0.0424401022),
                               (-0.5922597008, -0.9151744023, -0.2001813507))}
formaldehyde_adj = """1 C u0 p0 c0 {2,D} {3,S} {4,S}
2 O u0 p2 c0 {1,D}
3 H u0 p0 c0 {1,S}
4 H u0 p0 c0 {1,S}"""

@pytest.mark.usefixtures('setup_database')
class TestNonPhysicalSpeciesEndpoints:
    
    @pytest.fixture(scope='class', autouse=True)
    def setup_np_species(self, request, test_level, test_ess, test_encorr, test_freq, client):
        """
        Class-level fixture to create a non-physical species for testing
        """
        response = client.post(
            f'{API_V1_STR}/np_species/',
            json={
                "label": "TestNonPhysicalSpecies",
                "charge": 0,
                "multiplicity": 1,
                "smiles": "C=O",
                "inchi": "InChI=1S/CH2O/c1-2/h1H2", # What happens when this is wrong
                "electronic_state": "X",
                "coordinates": formaldehyde_xyz,
                "graph": formaldehyde_adj,
                "conformation_method": "CCCBDB",
                "is_well": True,
                "is_global_min": True,
                "is_ts": False,
                "opt_path": "path_opt",
                "freq_path": "path_freq",
                "sp_path": "path_sp",
                "extras":{'reason': 'testing extras'},
                "sp_level_id": test_level.id,
                "sp_ess_id": test_ess.id,
                #"encorr_id": test_encorr.id, # TODO: Why is this not included?
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        print("Response data:", data)
        request.cls.np_species_id = data["id"]
        request.cls.np_species_data = data
        print("Created non-physical species data:", data)
    
    def get_np_species_from_db(self, np_species_id, db):
        """
        Helper method to fetch a non-physical species from the database by ID
        """
        return db.query(NonPhysicalSpeciesModel).filter(NonPhysicalSpeciesModel.id == np_species_id).first()
    
    def test_create_np_species(self):
        """
        Test creating a non-physical species
        """
        assert self.np_species_data["label"] == "TestNonPhysicalSpecies"
        assert self.np_species_data["smiles"] == "C=O"
        assert self.np_species_data["inchi"] == "InChI=1S/CH2O/c1-2/h1H2"
    
    def test_read_np_species(self, client):
        """
        Test retrieving a non-physical species by its ID
        """
        np_species_id = self.np_species_id
        read_response = client.get(f"{API_V1_STR}/np_species/{np_species_id}")
        assert read_response.status_code == 200, read_response.text
        data = read_response.json()
        assert data["label"] == "TestNonPhysicalSpecies"
        assert data["smiles"] == "C=O"
        assert data["graph"] == formaldehyde_adj
        assert normalize_coordinates(data["coordinates"]) == formaldehyde_xyz

    def test_update_np_species_partial(self, client):
        """
        Test partially updating a non-physical species' attributes
        """
        np_species_id = self.np_species_id
        update_response = client.patch(
            f"{API_V1_STR}/np_species/{np_species_id}",
            json={
                "label": "UpdatedNonPhysicalSpecies",
                "charge": 1,
                "multiplicity": 2,
            },
        )
        assert update_response.status_code == 200, update_response.text
        updated_data = update_response.json()
        assert updated_data["label"] == "UpdatedNonPhysicalSpecies"
        assert updated_data["smiles"] == "C=O"
        assert updated_data["charge"] == 1
        assert updated_data["multiplicity"] == 2
    
    def test_soft_delete_np_species(self, client, db_session):
        """
        Test soft deleting a non-physical species
        """
        np_species_id = self.np_species_id
        delete_response = client.delete(f"{API_V1_STR}/np_species/{np_species_id}/soft")
        assert delete_response.status_code == 200, delete_response.text
        # Check that the non-physical species is no longer in the database
        db_np_species = self.get_np_species_from_db(np_species_id, db_session)
        assert db_np_species is not None
        assert db_np_species.deleted_at is not None
        
    def test_restore_species(self, client):
        """
        Test for restoring a soft non-physical species
        """
        species_id = self.np_species_id
        restore_response = client.post(f"{API_V1_STR}/np_species/{species_id}/restore")
        assert restore_response.status_code == 200, restore_response.text
        restored_data = restore_response.json()
        
        assert restored_data["id"] == species_id
        assert restored_data["label"] == "UpdatedNonPhysicalSpecies"
        assert restored_data["smiles"] == "C=O"
        assert restored_data["deleted_at"] is None
