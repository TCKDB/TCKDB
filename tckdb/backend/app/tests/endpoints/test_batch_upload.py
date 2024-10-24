import pytest

from tckdb.backend.app.core.config import API_V1_STR
from tckdb.backend.app.models.species import Species as SpeciesModel


@pytest.mark.usefixtures("setup_database")
class TestBatchEndpoint:
    """
    A class to test the batch upload endpoint
    """

    @pytest.fixture(scope="class", autouse=True)
    def setup_payload(self, request, client):
        """
        A function to setup a payload
        """
        payload = {
            # "authors": [
            #     {
            #     "connection_id": "temp_author_1",
            #     "first_name": "Calvin",
            #     "last_name": "Pieters",
            #     "orcid": "0000-0001-6377-2161"},
            #     {
            #     "connection_id": "temp_author_2",
            #     "first_name": "Florian",
            #     "last_name": "Solbach",
            #     "orcid": "0000-0003-1923-3747"}
            # ],
            "literature": [
                {
                    "connection_id": "temp_literature_1",
                    # "author_connection_ids": ["temp_author_1", "temp_author_2"],
                    "authors": [
                        {
                            "first_name": "Calvin",
                            "last_name": "Pieters",
                            "orcid": "0000-0001-6377-2161",
                        },
                        {
                            "first_name": "Florian",
                            "last_name": "Solbach",
                            "orcid": "0000-0003-1923-3747",
                        },
                    ],
                    "type": "book",
                    "title": "Quantum Chemistry and Computing for the Curious",
                    "year": 2022,
                    "publisher": "Springer",
                    "editors": "Calvin Pieters",
                    "chapter_title": "Chapter 1",
                    "publication_place": "Berlin",
                    "isbn": "9781803238593",
                }
            ],
            "freq_scales": [
                {
                    "connection_id": "temp_freq_scale_1",
                    "factor": 1.0,
                    "source": "J.A. Montgomery, M.J. Frisch, J. Chem. Phys. 1999, 110, 2822–2827",
                    "level_connection_id": "temp_level_1",
                }
            ],
            "encorr": [
                {
                    "connection_id": "temp_encorr_1",
                    "supported_elements": ["H", "C", "N", "O", "S", "P"],
                    "energy_unit": "kJ/mol",
                    # "aec": {'H': -0.502155915123, 'C': -37.8574709934,
                    #         'N': -54.6007233609, 'O': -75.0909131284,
                    #         'P': -341.281730319, 'S': -398.134489850
                    #         },
                    # "bac": {'C-H': 0.25, 'C-C': -1.89, 'C=C': -0.40, 'C#C': -1.50,
                    #         'O-H': -1.09, 'C-O': -1.18, 'C=O': -0.01, 'N-H': 1.36,
                    #         'C-N': -0.44, 'C#N': 0.22, 'C-S': -2.35, 'O=S': -5.19,
                    #         'S-H': -0.52
                    #         },
                    "isodesmic_reactions": [
                        {
                            "reactants": ["[CH2]CCCC", "[CH]"],
                            "products": ["[C]C", "[CH2]C(C)C"],
                            "stoichiometry": [1, 1, 1, 1],
                            "DHrxn298": 16.809,
                        }
                    ],
                    "primary_level_connection_id": "temp_level_encorr",
                    "isodesmic_level_connection_id": "temp_level_isodesmic",
                }
            ],
            "bots": [
                {
                    "connection_id": "temp_bot_1",
                    "name": "ARC",
                    "version": "1.0",
                    "url": "https://arc.github.io",
                }
            ],
            "levels": [
                {
                    "connection_id": "temp_level_1",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
                {
                    "connection_id": "temp_level_encorr",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
                {
                    "connection_id": "temp_level_isodesmic",
                    "method": "M062X",
                    "basis": "cc-pVTZ",
                },
                {
                    "connection_id": "temp_level_irc",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
                {
                    "connection_id": "temp_level_freq",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
                {
                    "connection_id": "temp_level_sp",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
                {
                    "connection_id": "temp_level_opt",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
                {
                    "connection_id": "temp_level_freq_scan",
                    "method": "B3LYP",
                    "basis": "6-31G(d,p)",
                    "dispersion": "gd3bj",
                },
            ],
            "ess": [
                {
                    "connection_id": "temp_ess_irc_sp_scan",
                    "name": "Gaussian",
                    "version": "16",
                    "revision": "A",
                    "url": "https://gaussian.com",
                },
                {
                    "connection_id": "temp_ess_opt_freq",
                    "name": "Gaussian",
                    "version": "09",
                    "revision": "D",
                    "url": "https://gaussian.com",
                },
            ],
            "species": [
                {
                    "connection_id": "temp_species_1",
                    "label": "CH4",
                    "smiles": "C",
                    "charge": 0,
                    "multiplicity": 1,
                    "coordinates": {
                        "symbols": ("C", "H", "H", "H", "H"),
                        "isotopes": (12, 1, 1, 1, 1),
                        "coords": (
                            (0.0, 0.0, 0.0),
                            (0.6300326, 0.6300326, 0.6300326),
                            (-0.6300326, -0.6300326, 0.6300326),
                            (-0.6300326, 0.6300326, -0.6300326),
                            (0.6300326, -0.6300326, -0.6300326),
                        ),
                    },
                    "external_symmetry": 4,
                    "point_group": "Td",
                    "conformation_method": "ARC v1.1.0",
                    "is_well": True,
                    "electronic_energy": -365.544,
                    "E0": -370.240,
                    "hessian": [
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                    ],
                    "frequencies": [
                        3046,
                        1555,
                        1555,
                        3168,
                        3168,
                        3168,
                        1368,
                        1368,
                        1368,
                    ],
                    "scaled_projected_frequencies": [
                        3015.54,
                        1539.45,
                        1539.45,
                        3136.32,
                        3136.32,
                        3136.32,
                        1354.32,
                        1354.32,
                        1354.32,
                    ],
                    "normal_displacement_modes": [
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                        [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
                    ],
                    "rigid_rotor": "spherical top",
                    "statmech_treatment": "RRHO",
                    "rotational_constants": [1, 2, 3],
                    "H298": -74.52,
                    "S298": 186.06,
                    "Cp_values": [36.07, 40.38, 45.77, 51.63, 62.30, 71.00, 85.94],
                    "Cp_T_list": [300, 400, 500, 600, 800, 1000, 1500],
                    "bot_connection_id": "temp_bot_1",
                    "literature_connection_id": "temp_literature_1",
                    "encorr_connection_id": "temp_encorr_1",
                    "freq_scale_connection_id": "temp_freq_scale_1",
                    "level_connections": {
                        "irc": "temp_level_irc",
                        "opt": "temp_level_opt",
                        "scan": "temp_level_freq_scan",
                        "sp": "temp_level_sp",
                        "freq": "temp_level_freq",
                    },
                    "ess_connections": {
                        "irc": "temp_ess_irc_sp_scan",
                        "opt": "temp_ess_opt_freq",
                        "scan": "temp_ess_irc_sp_scan",
                        "sp": "temp_ess_irc_sp_scan",
                        "freq": "temp_ess_opt_freq",
                    },
                    "opt_path": "path/to/log",
                    "freq_path": "path/to/log",
                    "sp_path": "path/to/log",
                }
            ],
        }

        response = client.post(f"{API_V1_STR}/batch-upload", json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        print("Response data:", data)
        request.cls.data = data
        print("Created data: ", data)
        request.cls.species_id = data["species"][0]["id"]

    def get_species_from_db(self, species_id, db):
        """
        Helper method to fetch a species from the database by ID.
        """
        return db.query(SpeciesModel).filter(SpeciesModel.id == species_id).first()

    def test_create_batch(self, db_session):
        """
        Test the initial creation of the batch
        """
        species = self.get_species_from_db(self.species_id, db_session)
        assert species.label == "CH4"
        assert species.smiles == "C"
        assert species.charge == 0

    # def test_missing_required_fields(self, client):
    #     """
    #     Test that the endpoint returns an error when required fields are missing.
    #     """
    #     incomplete_payload = {
    #         "species": [
    #             {
    #                 "connection_id": "temp_species_2",
    #                 # "label": "H2O",  # Missing label
    #                 "smiles": "O",
    #                 "charge": 0,
    #                 "multiplicity": 1,
    #                 # ... other fields ...
    #             }
    #         ]
    #     }

    #     response = client.post(f"{API_V1_STR}/batch-upload", json=incomplete_payload)
    #     assert response.status_code == 422, "Expected 422 Unprocessable Entity for missing required fields."
    #     assert "label" in response.text, "Error message should indicate missing 'label' field."
