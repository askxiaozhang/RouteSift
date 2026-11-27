"""Traffic HTTP endpoints."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from routesift.api import timezone
from routesift.traffic import analyzer, domain_insights

router = APIRouter()


@router.get("/api/status")
async def status(request: Request):
    return request.app.state.collector.status()


@router.get("/api/report")
def report(
    request: Request,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
    include_reviewed: bool = False,
):
    return analyzer.report(request.app.state.store, days, timezone(tz), include_reviewed)


@router.get("/api/domains")
def observed_domains(
    request: Request,
    q: Annotated[str, Query(max_length=253)] = "",
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
):
    return domain_insights.observed_hosts(request.app.state.store, q, timezone(tz))


@router.get("/api/domain-detail")
def domain_detail(
    request: Request,
    host: Annotated[str, Query(min_length=1, max_length=253)],
    start: date,
    end: date,
    scope: Literal["exact", "suffix"] = "exact",
    grain: Literal["5m", "hour", "day"] = "hour",
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
):
    try:
        return domain_insights.insights(
            request.app.state.store, host, scope, start, end, grain, timezone(tz),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
