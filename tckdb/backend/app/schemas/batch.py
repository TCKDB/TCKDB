
from typing import Optional, List
from pydantic import BaseModel, Field


from tckdb.backend.app.schemas.bot import BotCreateBatch
from tckdb.backend.app.schemas.encorr import EnCorrCreateBatch
from tckdb.backend.app.schemas.ess import ESSCreateBatch
from tckdb.backend.app.schemas.freq_scale import FreqScaleCreate, FreqScaleCreateBatch
from tckdb.backend.app.schemas.level import LevelCreateBatch
from tckdb.backend.app.schemas.literature import LiteratureCreateBatch
from tckdb.backend.app.schemas.author import AuthorCreateBatch
from tckdb.backend.app.schemas.species import SpeciesCreate, SpeciesCreateBatch


class BatchUploadPayload(BaseModel):
    
    species: Optional[List[SpeciesCreateBatch]] = Field(None, title='A list of species to be uploaded')
    literature: Optional[List[LiteratureCreateBatch]] = Field(None, title='A list of literature references to be uploaded')
    # Authors
    authors: Optional[List[AuthorCreateBatch]] = Field(None, title='A list of authors to be uploaded')
    levels: Optional[List[LevelCreateBatch]] = Field(None, title='A list of levels to be uploaded')
    encorr: Optional[List[EnCorrCreateBatch]] = Field(None, title='A list of EnCorr entries to be uploaded')
    bots: Optional[List[BotCreateBatch]] = Field(None, title='A list of bot entries to be uploaded')
    ess: Optional[List[ESSCreateBatch]] = Field(None, title='A list of ESS entries to be uploaded')
    freq_scales: Optional[List[FreqScaleCreateBatch]] = Field(None, title='A list of frequency scaling entries to be uploaded')
    
    class Config:
        orm_mode = True
        extra = "forbid"