from fastapi import FastAPI
from alembic.config import Config
from alembic import command

from tckdb.backend.app.api.api_v1.endpoints import species, bot, np_species


app = FastAPI(
    title="TCKDB API",
    description="Theoretical Chemical Kinetics Database API",
    version="0.1",
)

@app.on_event("startup")
def on_startup():
    alembic_config = Config("/code/tckdb/backend/alembic.ini")
    command.upgrade(alembic_config, "head")

app.include_router(species.router, prefix="/api/v1/species")
app.include_router(np_species.router, prefix="/api/v1/np_species")
app.include_router(bot.router, prefix="/api/v1/bot")
