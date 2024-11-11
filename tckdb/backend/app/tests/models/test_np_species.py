"""
TCKDB backend app tests models test_np_species module
"""

from datetime import datetime

import rdkit

from tckdb.backend.app.models.np_species import NonPhysicalSpecies


timestamp = datetime.now(datetime.UTC).timestamp()
formaldehyde_xyz = {
    "symbols": ("C", "O", "H", "H"),
    "isotopes": (12, 16, 1, 1),
    "coords": (
        (-0.0122240982, 0.0001804054, -0.00162116),
        (1.2016481968, -0.0177341701, 0.1593624097),
        (-0.5971643978, 0.9327281670, 0.0424401022),
        (-0.5922597008, -0.9151744023, -0.2001813507),
    ),
}
formaldehyde_adj = """1 C u0 p0 c0 {2,D} {3,S} {4,S}
2 O u0 p2 c0 {1,D}
3 H u0 p0 c0 {1,S}
4 H u0 p0 c0 {1,S}"""


def test_non_physical_species_model():
    """Test creating an instance of NonPhysicalSpecies"""
    np_species_1 = NonPhysicalSpecies(
        label="formaldehyde",
        timestamp=timestamp,
        reviewed=False,
        approved=False,
        smiles="C=O",
        inchi="InChI=1S/CH2O/c1-2/h1H2",
        inchi_key=rdkit.Chem.inchi.InchiToInchiKey("InChI=1S/CH2O/c1-2/h1H2"),
        charge=0,
        multiplicity=1,
        electronic_state="X",
        coordinates=formaldehyde_xyz,
        graph=formaldehyde_adj,
        conformation_method="CCCBDB",
        is_well=True,
        is_global_min=True,
        is_ts=False,
        opt_path="path_opt",
        freq_path="path_freq",
        sp_path="path_sp",
        extras={"reason": "testing extras"},
    )
    assert np_species_1.label == "formaldehyde"
    assert np_species_1.timestamp == timestamp
    assert np_species_1.retracted is None
    assert np_species_1.reviewed is False
    assert np_species_1.approved is False
    assert np_species_1.reviewer_flags is None
    assert np_species_1.smiles == "C=O"
    assert np_species_1.inchi == "InChI=1S/CH2O/c1-2/h1H2"
    assert np_species_1.inchi_key == "WSFSSNUMVMOOMR-UHFFFAOYSA-N"
    assert np_species_1.charge == 0
    assert np_species_1.multiplicity == 1
    assert np_species_1.electronic_state == "X"
    assert np_species_1.coordinates == formaldehyde_xyz
    assert np_species_1.graph == formaldehyde_adj
    assert np_species_1.fragments is None
    assert np_species_1.fragment_orientation is None
    assert np_species_1.conformation_method == "CCCBDB"
    assert np_species_1.is_well is True
    assert np_species_1.is_global_min is True
    assert np_species_1.global_min_geometry is None
    assert np_species_1.is_ts is False
    assert np_species_1.irc_trajectories is None
    assert np_species_1.opt_path == "path_opt"
    assert np_species_1.freq_path == "path_freq"
    assert np_species_1.scan_paths is None
    assert np_species_1.irc_paths is None
    assert np_species_1.sp_path == "path_sp"
    assert np_species_1.unconverged_jobs is None
    assert np_species_1.extras == {"reason": "testing extras"}
    assert (
        str(np_species_1)
        == "<NonPhysicalSpecies(id=None, label=formaldehyde, smiles=C=O)>"
    )
