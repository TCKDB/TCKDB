from pydantic import BaseModel, Field

class TempBase(BaseModel):
    temp_id: str = Field(..., title='The temporary ID of the object for internal referencing')
    
    class Config:
        orm_mode = True
        extra = 'forbid'