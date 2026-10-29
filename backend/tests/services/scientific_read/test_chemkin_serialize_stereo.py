"""CHEMKIN export prints an entry's stereo beside the stereo-blind species SMILES (DR-0038).

The species SMILES is the constitution, so ``E`` and ``Z`` 2-butene both carry ``CC=CC``. Without
the entry's stereo in the ``! SMILES=`` comment two different entries would read as one structure,
and an unresolved or unrepresentable element would read as "not distinguished". Built without a
database: the stand-ins carry the descriptor columns exactly as the resolver computes them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.chemistry.stereo import ResolvedStereo
from tests.services.scientific_read.test_chemkin_serialize_kinetics import _serialize, _species
from tests.services.test_stereo_descriptor import embed_xyz, resolve, species_key

_CIS = embed_xyz("C/C=C\\C")


def _record(sid: int, smiles: str, *, xyz: str | None = None):
    """A species-export stand-in whose stereo columns are what resolution stores for the deposit."""
    resolved: ResolvedStereo = resolve(smiles, xyz=xyz)
    record = _species(sid, species_key(smiles)[0], f"S{sid}")
    record.species_entry.stereo_descriptor = resolved.descriptor
    record.species_entry.stereo_resolution = resolved.resolution
    record.species_entry.stereo_smiles = resolved.stereo_smiles
    return record


def _species_block(chem_inp: str) -> list[str]:
    lines = chem_inp.splitlines()
    start = lines.index("SPECIES")
    end = lines.index("END", start)
    return [line.split("!", 1)[1].strip() for line in lines[start + 1 : end]]


def _export(*records):
    export, _names = _serialize(list(records), [])
    return export


def test_resolved_entries_print_their_isomeric_smiles_and_descriptor() -> None:
    export = _export(_record(1, "C/C=C/C"), _record(2, "CC=CC", xyz=_CIS))
    assert _species_block(export.files["chem.inp"]) == [
        "SMILES=C/C=C/C  STEREO=E ref=S1",
        "SMILES=C/C=C\\C  STEREO=Z ref=S2",
    ]


def test_therm_dat_carries_the_same_text() -> None:
    export = _export(_record(1, "C/C=C/C"))
    assert "SMILES=C/C=C/C  STEREO=E  ref=S1" in export.files["therm.dat"]


def test_an_entry_without_a_smiles_form_prints_the_constitution_and_the_descriptor() -> None:
    record = _record(1, "C/C=C/C")
    record.species_entry.stereo_smiles = None  # a resolved entry whose SMILES cannot be written
    export = _export(record)
    assert _species_block(export.files["chem.inp"]) == ["SMILES=CC=CC  STEREO=E ref=S1"]


def test_partial_and_unresolved_entries_are_marked() -> None:
    export = _export(_record(1, "CC=CC"), _record(2, "C/C=C/C=CC"))
    assert _species_block(export.files["chem.inp"]) == [
        "SMILES=CC=CC  STEREO=?  STEREO_RESOLUTION=unresolved ref=S1",
        "SMILES=CC=CC=CC  STEREO=E,?  STEREO_RESOLUTION=partial ref=S2",
    ]


def test_a_small_ring_allene_axis_is_marked_not_represented() -> None:
    export = _export(_record(1, "C1=C=C(C)CCC1"))
    assert _species_block(export.files["chem.inp"]) == [
        "SMILES=CC1=C=CCCC1  STEREO=?  STEREO_RESOLUTION=unresolved  STEREO_NOT_REPRESENTED=axial_small_ring ref=S1"
    ]


def test_an_open_allene_with_no_data_is_just_unresolved() -> None:
    export = _export(_record(1, "CC=C=CC"))
    assert _species_block(export.files["chem.inp"]) == [
        "SMILES=CC=C=CC  STEREO=?  STEREO_RESOLUTION=unresolved ref=S1"
    ]


def test_a_resolved_allene_prints_its_axis_token_and_no_isomeric_smiles() -> None:
    record = _record(1, "CC=C=CC")
    record.species_entry.stereo_descriptor = "Ra"
    record.species_entry.stereo_resolution = "resolved"
    record.species_entry.stereo_smiles = None
    assert _species_block(_export(record).files["chem.inp"]) == ["SMILES=CC=C=CC  STEREO=Ra ref=S1"]


def test_an_achiral_species_prints_no_stereo() -> None:
    export = _export(_record(1, "CCO"))
    assert _species_block(export.files["chem.inp"]) == ["SMILES=CCO ref=S1"]


# ---------------------------------------------------------------------------
# The CHEMKIN importer reads the structure and ignores the stereo text
# ---------------------------------------------------------------------------

_ADAPTER = Path(__file__).resolve().parents[4] / "clients" / "python" / "adapters" / "chemkin"


@pytest.mark.skipif(not _ADAPTER.is_dir(), reason="CHEMKIN adapter not present")
def test_the_importer_resolves_the_structure_and_ignores_the_stereo_comment() -> None:
    if str(_ADAPTER) not in sys.path:
        sys.path.append(str(_ADAPTER))
    from tckdb_chemkin.identity import IdentityResolver
    from tckdb_chemkin.parser import parse_mechanism

    export = _export(
        _record(1, "C/C=C/C"),
        _record(2, "CC=CC"),
        _record(3, "C/C=C/C=CC"),
        _record(4, "CC=C=CC"),
    )
    mechanism = parse_mechanism(export.files["chem.inp"], thermo_text=export.files["therm.dat"])
    resolved = IdentityResolver().resolve_mechanism(mechanism)

    smiles = sorted(species.smiles for species in resolved.values())
    assert smiles == sorted(["C/C=C/C", "CC=CC", "CC=CC=CC", "CC=C=CC"])
    assert not any("STEREO" in species.smiles for species in resolved.values())
