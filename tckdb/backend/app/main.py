from fastapi import FastAPI
from alembic.config import Config
from alembic import command
from tckdb.backend.app.core.config import ENV, FAST_API_PORT
import uvicorn

from tckdb.backend.app.api.api_v1.endpoints import species, bot, np_species, literature, batch


app = FastAPI(
    title="TCKDB API",
    description="Theoretical Chemical Kinetics Database API",
    version="0.1",
)

@app.on_event("startup")
def on_startup():
    print(f'API is running on port {FAST_API_PORT}')

app.include_router(species.router, prefix="/api/v1/species")
app.include_router(np_species.router, prefix="/api/v1/np_species")
app.include_router(bot.router, prefix="/api/v1/bot")
app.include_router(literature.router, prefix="/api/v1/literature")
app.include_router(batch.router, prefix="/api/v1/batch-upload")

def main():
    IS_DEV = ENV=='Development'
    config = uvicorn.Config(
                    "main:app", 
                    port=int(FAST_API_PORT), 
                    log_level= "info" if IS_DEV else "warn", 
                    host="0.0.0.0", 
                    reload=IS_DEV
                )
    server = uvicorn.Server(config)
    server.run()

if __name__ == "__main__":
    main()