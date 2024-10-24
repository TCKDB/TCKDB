from pydantic import BaseModel, ConfigDict, Field


class TempBase(BaseModel):
    temp_id: str = Field(
        ..., title="The temporary ID of the object for internal referencing"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")
