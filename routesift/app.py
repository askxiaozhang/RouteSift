"""FastAPI assembly and background collector lifecycle."""

import asyncio
from contextlib import asynccontextmanager, suppress
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from routesift.api import reviews, rules, shadowrocket, traffic
from routesift.config import DATABASE_PATH, HOST, LAN_HOST, MIHOMO_SECRET, PHONE_LOG_TZ, PHONE_LOG_URL, STATIC_DIR, controller_url
from routesift.integrations import shadowrocket_live
from routesift.security import protect_dashboard
from routesift.storage import Store
from routesift.traffic.collector import Collector


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store(DATABASE_PATH)
    url = controller_url()
    with store.exclusive_collector():
        store.initialize()
        collector = Collector(store, url, MIHOMO_SECRET)
        app.state.store = store
        app.state.collector = collector
        phone_url = PHONE_LOG_URL
        phone = None
        app.state.phone_status = {"configured": bool(phone_url), "connected": False, "last_event_at": None, "error": ""}
        if phone_url:
            try:
                phone_zone = ZoneInfo(PHONE_LOG_TZ)
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


def index():
    return FileResponse(STATIC_DIR / "index.html")


def create_app():
    app = FastAPI(title="RouteSift", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost", *([LAN_HOST] if LAN_HOST else [])])
    app.middleware("http")(protect_dashboard)
    app.add_api_route("/", index, methods=["GET"])
    for module in (traffic, reviews, rules, shadowrocket):
        app.include_router(module.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
