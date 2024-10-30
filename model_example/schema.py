from typing import Optional

from pydantic import BaseModel, Field


class ModelSchema(BaseModel):
    """
    A Model class (shared properties)
    """

    name: Optional[str] = Field(None, max_length=100, title="The Model's name")

    """
    Here we should insert function to validate fields and data
    ***
    ***  NOT FUNCTIOLLITY! ***
    """


class ModelCreateSchema(ModelSchema):
    """Create a Model item: Properties to receive on item creation"""

    pass


class ModelCreateBatchSchema(ModelSchema):
    """Create a batch of Bot items: Properties to receive on item creation"""

    pass


class ModelUpdateSchema(ModelSchema):
    """Update a Bot item: Properties to receive on item update"""

    pass

class ModelPatchSchema(ModelSchema):
    """Patch a Bot item: Properties to receive on item update"""

    pass
