"""WP4: run the LLM auto-classifier over the backlog."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...enrich import classifier
from ..auth import require_session
from ..config import settings
from ..deps import get_conn

router = APIRouter(prefix="/api")


class ClassifyBody(BaseModel):
    threshold: float = 0.85
    allow_search: bool = True
    budget: int = 200


@router.get("/classify/status")
def classify_status(_: None = Depends(require_session)) -> dict:
    return {"configured": settings.anthropic_api_key is not None, "model": settings.classifier_model}


@router.post("/classify/run")
def classify_run(body: ClassifyBody, _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    return classifier.run(
        conn, api_key=settings.anthropic_api_key, model=settings.classifier_model,
        threshold=body.threshold, allow_search=body.allow_search, budget=body.budget,
    )
