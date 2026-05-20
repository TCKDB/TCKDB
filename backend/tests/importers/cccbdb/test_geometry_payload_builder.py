"""Builder tests for the CCCBDB geometry payload."""

from __future__ import annotations

from app.importers.cccbdb.builders.geometry_payload import (
    build_geometry_payload,
)


class TestH2OGeometry:
    def test_xyz_text_well_formed(self, h2o_record):
        payload = build_geometry_payload(h2o_record)
        assert payload is not None
        assert payload["natoms"] == 3

        lines = payload["xyz_text"].splitlines()
        assert lines[0] == "3"
        assert "CCCBDB experimental" in lines[1]
        # Three atom lines after natoms + comment.
        assert len(lines) == 5
        # Element + 3 numbers per atom line.
        for atom_line in lines[2:]:
            parts = atom_line.split()
            assert len(parts) == 4
            assert parts[0] in {"O", "H"}

    def test_coordinates_in_angstrom(self, h2o_record):
        payload = build_geometry_payload(h2o_record)
        first_atom = payload["xyz_text"].splitlines()[2].split()
        assert first_atom[0] == "O"
        # Oxygen z = 0.1173 from the fixture.
        assert float(first_atom[3]) == 0.1173


class TestH2NoGeometry:
    def test_h2_geometry_is_none(self, h2_record):
        # H2 fixture deliberately omits a geometry section.
        assert build_geometry_payload(h2_record) is None


class TestBenzeneNoGeometry:
    def test_benzene_geometry_is_none(self, benzene_record):
        assert build_geometry_payload(benzene_record) is None
