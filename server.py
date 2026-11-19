"""Local HTTP dashboard and rule-provider endpoints."""

import asyncio
import base64
import binascii
import hmac
import logging
import os
import socket
from contextlib import ExitStack, asynccontextmanager, suppress
from datetime import date
from ipaddress import IPv4Address, ip_network
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
import deepseek_analysis
import domain_insights
from collector import Collector
from rule_exporter import OVERRIDE, PROVIDERS, canonical_target, render_provider
from storage import Store

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
HOST = "127.0.0.1"
LAN_IP = os.environ.get("AUDITOR_LAN_IP", "").strip()
LAN_PASSWORD = os.environ.get("AUDITOR_LAN_PASSWORD", "")
LAN_USER = "routesift"
PRIVATE_NETWORKS = tuple(map(ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def lan_host():
    if not LAN_IP:
        return None
    try:
        address = IPv4Address(LAN_IP)
    except ValueError as exc:
        raise RuntimeError("AUDITOR_LAN_IP 必须是本机的局域网 IPv4 地址") from exc
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise RuntimeError("AUDITOR_LAN_IP 必须位于私有 IPv4 网段")
    if not LAN_PASSWORD:
        raise RuntimeError("启用局域网访问时请设置 AUDITOR_LAN_PASSWORD")
    return str(address)


def authorized_lan_request(request):
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if not LAN_PASSWORD or scheme.lower() != "basic":
        return False
    try:
        supplied = base64.b64decode(credentials, validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(supplied, f"{LAN_USER}:{LAN_PASSWORD}".encode())


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
LAN_HOST = lan_host()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost", *([LAN_HOST] if LAN_HOST else [])])


@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    local = request.client is not None and request.client.host == HOST
    if not local and not (LAN_HOST and authorized_lan_request(request)):
        return PlainTextResponse(
            "请使用 RouteSift 局域网账号登录",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="RouteSift", charset="UTF-8"', "Cache-Control": "no-store"},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if (
            request.headers.get("x-auditor-request") != "1"
            or (origin is not None and origin != expected_origin)
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            return JSONResponse({"detail": "请从 RouteSift 看板发起审核操作"}, status_code=403)
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


class AnalysisRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    tz: str = Field(default="Asia/Singapore", max_length=100)
    mode: Literal["all", "match"] = "all"


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
    sort: Literal["bytes", "recent"] = "bytes",
    q: Annotated[str, Query(max_length=253)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
):
    return analyzer.candidates(
        request.app.state.store, days, timezone(tz), include_reviewed, mode, q.strip().casefold(), page, 20, sort=sort,
    )


@app.get("/api/domains")
def observed_domains(
    request: Request,
    q: Annotated[str, Query(max_length=253)] = "",
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
):
    return domain_insights.observed_hosts(request.app.state.store, q, timezone(tz))


@app.post("/api/ai/analysis")
def run_analysis(request: Request, filters: AnalysisRequest):
    zone = timezone(filters.tz)
    if not deepseek_analysis.ACTIVE_ANALYSIS.acquire(blocking=False):
        raise HTTPException(409, "已有分析正在运行，请稍后再试。")
    try:
        return deepseek_analysis.analyze(request.app.state.store, filters.days, zone, filters.mode)
    finally:
        deepseek_analysis.ACTIVE_ANALYSIS.release()


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
    port = int(os.environ.get("AUDITOR_PORT", "18765"))
    if LAN_HOST:
        with ExitStack() as stack:
            listeners = [stack.enter_context(socket.create_server((address, port))) for address in (HOST, LAN_HOST)]
            logging.info("RouteSift · http://%s:%s · http://%s:%s", HOST, port, LAN_HOST, port)
            uvicorn.Server(uvicorn.Config(app, access_log=False, proxy_headers=False)).run(sockets=listeners)
    else:
        uvicorn.run(app, host=HOST, port=port, proxy_headers=False, access_log=False)
