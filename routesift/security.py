"""Local and LAN dashboard request protection."""

import base64
import binascii
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from routesift.config import BIND_HOST, HOST, LAN_HOST, LAN_PASSWORD, LAN_USER, MODULE_PATH, PORT


def authorized_lan_request(request):
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if not LAN_PASSWORD or scheme.lower() != "basic":
        return False
    try:
        supplied = base64.b64decode(credentials, validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(supplied, f"{LAN_USER}:{LAN_PASSWORD}".encode())


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
