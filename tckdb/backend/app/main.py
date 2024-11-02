from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tckdb.backend.app.api.api_v1.endpoints import (
    batch,
    bot,
    literature,
    np_species,
    species,
)
from tckdb.backend.app.core.config import ENV, FAST_API_PORT

if FAST_API_PORT is None:
    raise ValueError("FAST_API_PORT is not set in the environment variables.")
try:
    port = int(FAST_API_PORT)
except ValueError as e:
    raise ValueError("FAST_API_PORT must be an integer.") from e


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting application on port: ", port)
    yield


app = FastAPI(
    title="TCKDB API",
    description="Theoretical Chemical Kinetics Database API",
    version="0.1",
    lifespan=lifespan,
)

app.include_router(species.router, prefix="/api/v1/species")
app.include_router(np_species.router, prefix="/api/v1/np_species")
app.include_router(bot.router, prefix="/api/v1/bot")
app.include_router(literature.router, prefix="/api/v1/literature")
app.include_router(batch.router, prefix="/api/v1/batch-upload")


def main():
    IS_DEV = ENV == "Development"

    config = uvicorn.Config(
        "main:app",
        port=int(FAST_API_PORT),
        log_level="info" if IS_DEV else "warning",
        host="0.0.0.0",
        reload=IS_DEV,
    )
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()