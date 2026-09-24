from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import ask, auth, destinations, places, plan, system
from app.config import APP_ENV, APP_HOST, APP_PORT, AUTO_SEED_PACKS, CORS_ORIGINS
from app.core.logging import configure_logging
from app.db.seed import seed_if_empty
from app.db.session import init_db
from app.retrieval.indexer import reindex_in_background

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if AUTO_SEED_PACKS:
        seed_if_empty()
    reindex_in_background()  # embeds knowledge when the embedding model is available
    yield


app = FastAPI(title="GeoGuide API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=CORS_ORIGINS != ["*"], allow_methods=["*"], allow_headers=["*"])
for module in (system, auth, places, ask, plan, destinations):
    app.include_router(module.router)


@app.get("/")
def root() -> dict:
    return {"message": "GeoGuide backend is running.", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=APP_ENV == "development")
