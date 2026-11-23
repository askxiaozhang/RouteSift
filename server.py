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
from http.client import HTTPException as ControllerHTTPError
from ipaddress import IPv4Address, ip_network
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

import analyzer
import deepseek_analysis
import domain_insights
import shadowrocket
import shadowrocket_live
import shadowrocket_logs
from collector import Collector
from mihomo_api import MihomoAPI
from rule_exporter import OVERRIDE, PROVIDERS, canonical_target, render_provider
from storage import Store

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
HOST = "127.0.0.1"
PORT = int(os.environ.get("AUDITOR_PORT", "18765"))
BIND_HOST = os.environ.get("AUDITOR_BIND_HOST", "").strip()
if BIND_HOST and BIND_HOST != "0.0.0.0":
    raise RuntimeError("AUDITOR_BIND_HOST 仅支持留空或设为 0.0.0.0")
LAN_IP = os.environ.get("AUDITOR_LAN_IP", "").strip()
LAN_PORT = int(os.environ.get("AUDITOR_LAN_PORT", str(PORT + 1)))
LAN_PASSWORD = os.environ.get("AUDITOR_LAN_PASSWORD", "")
LAN_USER = "routesift"
MODULE_TOKEN = os.environ.get("SHADOWROCKET_MODULE_TOKEN", "").strip()
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
    allowed_hosts = {"127.0.0.1", "::1", "localhost"}
    if BIND_HOST:
        allowed_hosts.add("host.docker.internal")
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.hostname not in allowed_hosts
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
        phone_url = os.environ.get("SHADOWROCKET_LOG_URL", "").strip()
        phone = None
        app.state.phone_status = {"configured": bool(phone_url), "connected": False, "last_event_at": None, "error": ""}
        if phone_url:
            try:
                phone_zone = ZoneInfo(os.environ.get("SHADOWROCKET_LOG_TZ", "Asia/Singapore"))
                phone = shadowrocket_live.LiveCollector(store, phone_url, phone_zone)
            except (ValueError, ZoneInfoNotFoundError):
                app.state.phone_status["error"] = "请检查 Shadowrocket 的诊断地址和时区"
        app.state.phone_collector = phone
        phone_task = asyncio.create_task(asyncio.to_thread(phone.run), name="shadowrocket-collector") if phone else None
        task = asyncio.create_task(collector.run(), name="mihomo-collector")
        try:
            yield
        finally:
            if phone:
                phone.stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            if phone_task:
                await phone_task


app = FastAPI(title="RouteSift", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
LAN_HOST = lan_host()
if BIND_HOST and LAN_HOST and LAN_PORT == PORT:
    raise RuntimeError("容器模式下 AUDITOR_LAN_PORT 不能与 AUDITOR_PORT 相同")
MODULE_PATH = (
    f"/rules/ios/{MODULE_TOKEN}/routesift-ios.sgmodule"
    if LAN_HOST and len(MODULE_TOKEN) >= 32 and all(c.isascii() and (c.isalnum() or c in "-_") for c in MODULE_TOKEN)
    else ""
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost", *([LAN_HOST] if LAN_HOST else [])])


@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    server_port = (request.scope.get("server") or (None, None))[1]
    local = (
        server_port == PORT
        if BIND_HOST
        else request.client is not None and request.client.host == HOST
    )
    module_request = request.method in {"GET", "HEAD"} and MODULE_PATH and hmac.compare_digest(
        request.url.path.encode(), MODULE_PATH.encode(),
    )
    if not local and not module_request and not (LAN_HOST and authorized_lan_request(request)):
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
    action: Literal["DIRECT", "KEEP_PROXY", "REJECT", "IGNORE", "MATCH_TARGET"]
    remark: str = Field(default="", max_length=500)


class AnalysisRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    tz: str = Field(default="Asia/Singapore", max_length=100)
    mode: Literal["all", "match"] = "all"


class ShadowrocketDraft(BaseModel):
    rules: str = Field(max_length=shadowrocket.MAX_RULE_TEXT)


class ShadowrocketConfig(ShadowrocketDraft):
    version: int = Field(ge=0, le=2**63 - 1)


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


@app.put("/api/decisions")
def decide(request: Request, decision: Decision):
    try:
        value = canonical_target(decision.match_type, decision.match_value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    saved = request.app.state.store.save_decision(
        decision.match_type, value, decision.action, decision.remark.strip(),
    )
    return {**saved, "desktop_synced": decision.action in refresh_mihomo_rules(request)}


@app.delete("/api/decisions/{decision_id}", status_code=204)
def undo(request: Request, decision_id: int):
    if not request.app.state.store.remove_decision(decision_id):
        raise HTTPException(404, "这条审核记录已撤销")
    refresh_mihomo_rules(request)
    return Response(status_code=204)


@app.get("/rules/my-{provider}.yaml")
def provider_file(request: Request, provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404)
    return PlainTextResponse(
        render_provider(request.app.state.store.decisions(), provider),
        media_type="application/yaml",
    )


@app.get("/api/shadowrocket")
def get_shadowrocket(request: Request):
    config = request.app.state.store.shadowrocket_config()
    if MODULE_PATH:
        config["subscription_url"] = f"http://{LAN_HOST}:{request.url.port or 80}{MODULE_PATH}"
    return config


@app.post("/api/shadowrocket/import-review")
def import_shadowrocket_reviews(request: Request, draft: ShadowrocketDraft):
    try:
        rules = shadowrocket.parse_rules(draft.rules)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    merged, added = shadowrocket.merge_reviews(rules, request.app.state.store.decisions())
    if len(merged) > shadowrocket.MAX_RULE_TEXT:
        raise HTTPException(422, "带入后超过规则长度上限，请删减部分规则")
    return {"rules": merged, "added": added}


@app.put("/api/shadowrocket")
def save_shadowrocket(request: Request, config: ShadowrocketConfig):
    try:
        rules = shadowrocket.render_rules(shadowrocket.parse_rules(config.rules))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(rules) > shadowrocket.MAX_RULE_TEXT:
        raise HTTPException(422, "规范化后超过规则长度上限，请删减部分规则")
    saved = request.app.state.store.save_shadowrocket_config(rules, config.version)
    if saved is None:
        raise HTTPException(409, "另一台设备已保存。请复制当前内容，刷新后合并再保存")
    return saved


@app.get("/rules/routesift-ios.sgmodule")
@app.api_route("/rules/ios/{module_token}/routesift-ios.sgmodule", methods=["GET", "HEAD"])
def shadowrocket_module(request: Request, module_token: str = ""):
    if module_token and not (MODULE_PATH and hmac.compare_digest(module_token.encode(), MODULE_TOKEN.encode())):
        raise HTTPException(404)
    store = request.app.state.store
    config = store.shadowrocket_config()
    rules, _ = shadowrocket.merge_reviews(shadowrocket.parse_rules(config["rules"]), store.decisions())
    return PlainTextResponse(
        shadowrocket.render_module(rules),
        headers={"Content-Disposition": 'attachment; filename="routesift-ios.sgmodule"'},
    )


@app.get("/api/shadowrocket/connections")
def shadowrocket_connections(
    request: Request,
    host: Annotated[str, Query(max_length=253)] = "",
    scope: Literal["exact", "suffix"] = "exact",
    source: Literal["file", "live"] | None = None,
):
    phone = request.app.state.phone_collector
    status = phone.state if phone else request.app.state.phone_status
    source = source or ("live" if status["configured"] else "file")
    try:
        report = shadowrocket_logs.report(request.app.state.store, host.strip(), scope, source)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**report, "source": source, "live_capture": status}


@app.post("/api/shadowrocket/connections")
async def import_shadowrocket_connections(
    request: Request,
    tz: Annotated[str, Query(max_length=100)] = "Asia/Singapore",
):
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "请选择 SQLite 日志文件")
    zone = timezone(tz)
    with TemporaryDirectory(prefix="routesift-ios-") as directory:
        path = Path(directory) / "log.db"
        size = 0
        with path.open("wb") as upload:
            async for chunk in request.stream():
                size += len(chunk)
                if size > shadowrocket_logs.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "单个日志文件最多 32 MiB")
                upload.write(chunk)
        try:
            return await run_in_threadpool(shadowrocket_logs.import_file, request.app.state.store, path, zone)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@app.get("/rules/flclash-override.js")
def override_file(request: Request):
    base_url = f"http://{HOST}:{request.url.port or 80}"
    source = OVERRIDE.read_text(encoding="utf-8").replace("http://127.0.0.1:18765", base_url)
    return PlainTextResponse(source)


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if BIND_HOST:
        with ExitStack() as stack:
            binds = [(BIND_HOST, PORT)]
            if LAN_HOST:
                binds.append((BIND_HOST, LAN_PORT))
            listeners = [stack.enter_context(socket.create_server(addr)) for addr in binds]
            if LAN_HOST:
                logging.info(
                    "RouteSift · http://%s:%s · http://%s:%s (container ports %s/%s)",
                    HOST, PORT, LAN_HOST, PORT, PORT, LAN_PORT,
                )
            else:
                logging.info("RouteSift · http://%s:%s (container port %s)", HOST, PORT, PORT)
            uvicorn.Server(uvicorn.Config(app, access_log=False, proxy_headers=False)).run(sockets=listeners)
    elif LAN_HOST:
        with ExitStack() as stack:
            listeners = [stack.enter_context(socket.create_server((address, PORT))) for address in (HOST, LAN_HOST)]
            logging.info("RouteSift · http://%s:%s · http://%s:%s", HOST, PORT, LAN_HOST, PORT)
            uvicorn.Server(uvicorn.Config(app, access_log=False, proxy_headers=False)).run(sockets=listeners)
    else:
        uvicorn.run(app, host=HOST, port=PORT, proxy_headers=False, access_log=False)
