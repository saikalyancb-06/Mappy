from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import knowledge, router
from app.config import APP_ENV, APP_HOST, APP_PORT
from app.config import GROQ_API_KEY, SERPAPI_KEY
from app.db.session import init_db

init_db()
app = FastAPI(title='GeoGuide API', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(router)


@app.on_event('startup')
def startup_event() -> None:
    init_db()


@app.get('/api/health')
def health() -> dict:
    return {
        'status': 'ok',
        'environment': APP_ENV,
        'services': {
            'sqlite': 'ready',
            'qdrant': 'ready' if knowledge.store.client is not None else 'degraded',
            'groq': 'configured' if GROQ_API_KEY else 'unconfigured',
            'serpapi': 'configured' if SERPAPI_KEY else 'unconfigured',
        },
    }


@app.get('/')
def root() -> dict:
    return {'message': 'GeoGuide backend is running.'}


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('app.main:app', host=APP_HOST, port=APP_PORT, reload=APP_ENV == 'development')
