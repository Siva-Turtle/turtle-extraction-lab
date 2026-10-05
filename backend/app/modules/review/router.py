"""Server-persisted review row stars (plain JSON file, no DB)."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/review", tags=["review"])


class StarsPayload(BaseModel):
    stars: list[Any]


def _stars_path() -> Path:
    env = os.environ.get("REVIEW_STARS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "review_stars.json"


def _read_stars() -> list[str]:
    try:
        raw = _stars_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return []
        items = data.get("stars")
        if not isinstance(items, list):
            return []
        return sorted({s for s in items if isinstance(s, str)})
    except Exception:
        return []


@router.get("/stars")
def get_stars():
    return {"stars": _read_stars()}


@router.put("/stars")
def put_stars(payload: StarsPayload):
    clean = sorted({s for s in payload.stars if isinstance(s, str)})
    path = _stars_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".review_stars", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"stars": clean}, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"ok": True, "count": len(clean)}
