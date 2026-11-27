"""Reviews HTTP endpoints."""

from http.client import HTTPException as ControllerHTTPError
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from routesift.api import timezone
from routesift.integrations.mihomo import MihomoAPI
from routesift.rule_exporter import PROVIDERS, canonical_target
from routesift.traffic import analyzer

router = APIRouter()


class Decision(BaseModel):
    match_type: Literal["DOMAIN", "DOMAIN-SUFFIX", "PROCESS-NAME", "IP-CIDR"]
    match_value: str = Field(min_length=1, max_length=253)
    action: Literal["DIRECT", "KEEP_PROXY", "REJECT", "IGNORE", "MATCH_TARGET"]
    remark: str = Field(default="", max_length=500)


@router.get("/api/candidates")
def candidates(
    request: Request,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
    include_reviewed: bool = False,
    mode: Literal["all", "match"] = "all",
    sort: Literal["bytes", "recent"] = "bytes",
    q: Annotated[str, Query(max_length=253)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
):
    return analyzer.candidates(
        request.app.state.store, days, timezone(tz), include_reviewed, mode, q.strip().casefold(), page, 20, sort=sort,
    )


@router.get("/api/decisions")
def decisions(request: Request):
    return request.app.state.store.decisions()


def refresh_mihomo_rules(request):
    collector = request.app.state.collector
    client = MihomoAPI(collector.url, collector.headers)
    try:
        available = client.request("GET", "/providers/rules").get("providers", {})
    except (OSError, ControllerHTTPError, ValueError, AttributeError):
        return []
    if not isinstance(available, dict):
        return []
    refreshed = []
    for key in PROVIDERS:
        name = f"traffic-auditor-{key}"
        if name in available:
            try:
                client.request("PUT", f"/providers/rules/{name}")
                refreshed.append(PROVIDERS[key])
            except (OSError, ControllerHTTPError, ValueError):
                continue
    return refreshed


@router.put("/api/decisions")
def decide(request: Request, decision: Decision):
    try:
        value = canonical_target(decision.match_type, decision.match_value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    saved = request.app.state.store.save_decision(
        decision.match_type, value, decision.action, decision.remark.strip(),
    )
    return {**saved, "desktop_synced": decision.action in refresh_mihomo_rules(request)}


@router.delete("/api/decisions/{decision_id}", status_code=204)
def undo(request: Request, decision_id: int):
    if not request.app.state.store.remove_decision(decision_id):
        raise HTTPException(404, "这条审核记录已撤销")
    refresh_mihomo_rules(request)
    return Response(status_code=204)
