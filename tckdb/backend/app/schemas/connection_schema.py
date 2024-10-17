from pydantic import BaseModel, Field

class ConnectionBase(BaseModel):
    """
    A ConnectionBase class (shared properties) for batch uploading data and need to connect
    the uploaded data to each other via API
    """
    connection_id: str = Field(..., title='The connection ID of the object for internal referencing')
    
    class Config:
        orm_mode = True
        extra = 'forbid'