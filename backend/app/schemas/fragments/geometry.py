from pydantic import Field, field_validator

from app.schemas.common import SchemaBase
from app.schemas.utils import normalize_required_text


class GeometryPayload(SchemaBase):
    """Upload-facing geometry payload.

    :param xyz_text: Raw XYZ text block for the uploaded geometry.
    """

    xyz_text: str = Field(min_length=1)

    @field_validator("xyz_text")
    @classmethod
    def normalize_xyz_text(cls, value: str) -> str:
        return normalize_required_text(value)
