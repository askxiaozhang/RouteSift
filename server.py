"""Local HTTP dashboard and rule-provider endpoints."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import analyzer
import domain_insights
from collector import Collector
from rule_exporter import OVERRIDE, PROVIDERS, canonical_target, render_provider
from storage import Store

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
HOST = "127.0.0.1"


def controller_url():
    value = os.environ.get("MIHOMO_URL", "ws://127.0.0.1:9090/connections")
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate a custom port.
    except ValueError as exc:
        raise RuntimeError("MIHOMO_URL 的端口或地址无效") from exc
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.path.rstrip("/") != "/connections"
        or parsed.username is not None or parsed.fragment or parsed.query
    ):
        raise RuntimeError("MIHOMO_URL 必须是回环地址上的 WebSocket /connections")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(os.environ.get("AUDITOR_DB", "data/traffic.db")).expanduser()
    store = Store((BASE / path).resolve())
    url = controller_url()
    with store.exclusive_collector():
        store.initialize()
        collector = Collector(store, url, os.environ.get("MIHOMO_SECRET", ""))
        app.state.store = store
        app.state.collector = collector
        task = asyncio.create_task(collector.run(), name="mihomo-collector")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="RouteSift", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])


@app.middleware("http")
async def protect_local_dashboard(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if (
            request.headers.get("x-auditor-request") != "1"
            or (origin is not None and origin != expected_origin)
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            return JSONResponse({"detail": "请从本机看板发起审核操作"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


def timezone(value):
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "无效的 IANA 时区") from exc


class Decision(BaseModel):
    match_type: Literal["DOMAIN", "DOMAIN-SUFFIX", "PROCESS-NAME", "IP-CIDR"]
    match_value: str = Field(min_length=1, max_length=253)
    action: Literal["DIRECT", "KEEP_PROXY", "REJECT", "IGNORE"]
    remark: str = Field(default="", max_length=500)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/status")
async def status(request: Request):
    return request.app.state.collector.status()


@app.get("/api/report")
def report(
    request: Request,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
    include_reviewed: bool = False,
):
    return analyzer.report(request.app.state.store, days, timezone(tz), include_reviewed)


@app.get("/api/candidates")
def candidates(
    request: Request,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
    include_reviewed: bool = False,
    mode: Literal["all", "match"] = "all",
    q: Annotated[str, Query(max_length=253)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
):
    return analyzer.candidates(
        request.app.state.store, days, timezone(tz), include_reviewed, mode, q.strip().casefold(), page, 20,
    )


@app.get("/api/domains")
def observed_domains(
    request: Request,
    q: Annotated[str, Query(max_length=253)] = "",
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
):
    return domain_insights.observed_hosts(request.app.state.store, q, timezone(tz))


@app.get("/api/domain-detail")
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


@app.get("/api/decisions")
def decisions(request: Request):
    return request.app.state.store.decisions()


@app.put("/api/decisions")
def decide(request: Request, decision: Decision):
    try:
        value = canonical_target(decision.match_type, decision.match_value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return request.app.state.store.save_decision(
        decision.match_type, value, decision.action, decision.remark.strip(),
    )


@app.delete("/api/decisions/{decision_id}", status_code=204)
def undo(request: Request, decision_id: int):
    if not request.app.state.store.remove_decision(decision_id):
        raise HTTPException(404, "这条审核记录已撤销")
    return Response(status_code=204)


@app.get("/rules/my-{provider}.yaml")
def provider_file(request: Request, provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404)
    return PlainTextResponse(
        render_provider(request.app.state.store.decisions(), provider),
        media_type="application/yaml",
    )


@app.get("/rules/flclash-override.js")
def override_file(request: Request):
    base_url = f"http://{HOST}:{request.url.port or 80}"
    source = OVERRIDE.read_text(encoding="utf-8").replace("http://127.0.0.1:18765", base_url)
    return PlainTextResponse(source)


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        app, host=HOST, port=int(os.environ.get("AUDITOR_PORT", "18765")),
        proxy_headers=False, access_log=False,
    )
