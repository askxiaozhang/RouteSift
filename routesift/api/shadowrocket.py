"""Shadowrocket HTTP endpoints."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from routesift.api import timezone
from routesift.config import LAN_HOST, MODULE_PATH
from routesift.integrations import shadowrocket, shadowrocket_logs

router = APIRouter()


class ShadowrocketDraft(BaseModel):
    rules: str = Field(max_length=shadowrocket.MAX_RULE_TEXT)


class ShadowrocketConfig(ShadowrocketDraft):
    version: int = Field(ge=0, le=2**63 - 1)


@router.get("/api/shadowrocket")
def get_shadowrocket(request: Request):
    config = request.app.state.store.shadowrocket_config()
    if MODULE_PATH:
        config["subscription_url"] = f"http://{LAN_HOST}:{request.url.port or 80}{MODULE_PATH}"
    return config


@router.post("/api/shadowrocket/import-review")
def import_shadowrocket_reviews(request: Request, draft: ShadowrocketDraft):
    try:
        rules = shadowrocket.parse_rules(draft.rules)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    merged, added = shadowrocket.merge_reviews(rules, request.app.state.store.decisions())
    if len(merged) > shadowrocket.MAX_RULE_TEXT:
        raise HTTPException(422, "带入后超过规则长度上限，请删减部分规则")
    return {"rules": merged, "added": added}


@router.put("/api/shadowrocket")
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


@router.get("/api/shadowrocket/connections")
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


@router.post("/api/shadowrocket/connections")
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
