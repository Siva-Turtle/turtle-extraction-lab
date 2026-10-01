"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

app = FastAPI(title="Turtle Extraction Lab")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.modules.agents.router import router as agents_router
from app.modules.attributes.router import router as attributes_router
from app.modules.logs.router import router as logs_router
from app.modules.meta.router import router as meta_router
from app.modules.runs.router import router as runs_router

app.include_router(agents_router)
app.include_router(attributes_router)
app.include_router(runs_router)
app.include_router(logs_router)
app.include_router(meta_router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
