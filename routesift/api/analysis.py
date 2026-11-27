"""Analysis HTTP endpoints."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from routesift.api import timezone
from routesift.integrations import deepseek

router = APIRouter()


class AnalysisRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    tz: str = Field(default="Asia/Singapore", max_length=100)
    mode: Literal["all", "match"] = "all"


@router.post("/api/ai/analysis")
def run_analysis(request: Request, filters: AnalysisRequest):
    zone = timezone(filters.tz)
    if not deepseek.ACTIVE_ANALYSIS.acquire(blocking=False):
        raise HTTPException(409, "已有分析正在运行，请稍后再试。")
    try:
        return deepseek.analyze(request.app.state.store, filters.days, zone, filters.mode)
    finally:
        deepseek.ACTIVE_ANALYSIS.release()
