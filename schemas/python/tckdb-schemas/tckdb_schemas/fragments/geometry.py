from pydantic import Field, field_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.utils import normalize_required_text


class GeometryPayload(SchemaBase):
    """Upload-facing geometry payload.

    :param xyz_text: Raw XYZ text block for the uploaded geometry.
    :param isotopes: Optional atom-resolved isotope labelling, mapping a
        1-based XYZ atom index to that atom's isotope mass number (``2`` for
        deuterium, ``13`` for carbon-13, ...). Only substituted atoms need an
        entry; every unlisted atom is taken to be at its most abundant
        natural isotope. Omit the field entirely for an ordinary geometry.
    """

    xyz_text: str = Field(min_length=1)
    isotopes: dict[int, int] | None = Field(default=None)

    @field_validator("xyz_text")
    @classmethod
    def normalize_xyz_text(cls, value: str) -> str:
        return normalize_required_text(value)

    @field_validator("isotopes")
    @classmethod
    def validate_isotopes(cls, value: dict[int, int] | None) -> dict[int, int] | None:
        """Reject structurally impossible isotope maps.

        Element-aware validation (does this isotope exist for this element?)
        needs the parsed XYZ and therefore lives in ``parse_xyz``; this only
        enforces what can be checked from the mapping alone.
        """

        if value is None:
            return None
        for atom_index, mass_number in value.items():
            if atom_index < 1:
                raise ValueError(
                    "geometry.isotopes keys are 1-based XYZ atom indices; "
                    f"got {atom_index}"
                )
            if mass_number < 1:
                raise ValueError(
                    "geometry.isotopes values are isotope mass numbers >= 1; "
                    f"got {mass_number}"
                )
        return value or None
