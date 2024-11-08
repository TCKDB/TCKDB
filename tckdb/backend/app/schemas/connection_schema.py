from pydantic import BaseModel, ConfigDict, Field


class ConnectionBase(BaseModel):
    """
    ConnectionBase serves as a foundational schema that provides a unique `connection_id` to each
    entity involved in a batch upload process. This temporary identifier facilitates the mapping
    and relationship establishment between different entities before they are persisted to the
    database and assigned permanent IDs.

    Attributes:
        connection_id (str): A unique temporary identifier used to reference entities within
            the same batch upload. It allows entities to establish relationships without
            relying on database-generated IDs.
    """

    connection_id: str = Field(
        ..., title="The connection ID of the object for internal referencing"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")
