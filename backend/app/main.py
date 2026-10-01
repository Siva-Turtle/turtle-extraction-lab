"""FastAPI application entrypoint."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core.config import settings

app = FastAPI(title="Turtle Extraction Lab")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(OperationalError)
async def db_operational_error_handler(request: Request, exc: OperationalError):
    # Wrong DATABASE_URL credentials surface here (e.g. stale postgres
    # password in backend/.env). Return a concise 503 instead of a
    # traceback wall; never include the URL or password.
    return JSONResponse(
        status_code=503,
        content={
            "detail": "database unavailable: check DATABASE_URL credentials "
            "(re-run scripts/db-init.ps1)."
        },
    )

from app.modules.agents.router import router as agents_router
from app.modules.attributes.router import router as attributes_router
from app.modules.logs.router import router as logs_router
from app.modules.meetings.router import router as meetings_router
from app.modules.meta.router import router as meta_router
from app.modules.runs.router import router as runs_router

app.include_router(agents_router)
app.include_router(attributes_router)
app.include_router(runs_router)
app.include_router(logs_router)
app.include_router(meetings_router)
app.include_router(meta_router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
