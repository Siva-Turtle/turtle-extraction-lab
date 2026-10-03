"""Instance status + OpenRouter model list (no database needed)."""

from fastapi import APIRouter
from sqlalchemy import text

from app.core import openrouter
from app.db.session import engine

router = APIRouter(prefix="/api/v1", tags=["meta"])


@router.get("/config")
def get_config():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"openrouter_configured": openrouter.is_configured(), "db_ok": db_ok}


@router.get("/models")
async def list_models():
    models, live = await openrouter.fetch_models()
    return {"live": live, "models": models}


@router.get("/models/endpoints")
async def list_model_endpoints(model: str = ""):
    endpoints = await openrouter.fetch_model_endpoints(model)
    return {"model": model, "endpoints": endpoints}
