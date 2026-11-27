"""Rules HTTP endpoints."""

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from routesift.config import HOST, MODULE_PATH, MODULE_TOKEN
from routesift.integrations import shadowrocket
from routesift.rule_exporter import OVERRIDE, PROVIDERS, render_provider

router = APIRouter()


@router.get("/rules/my-{provider}.yaml")
def provider_file(request: Request, provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404)
    return PlainTextResponse(
        render_provider(request.app.state.store.decisions(), provider),
        media_type="application/yaml",
    )


@router.get("/rules/routesift-ios.sgmodule")
@router.api_route("/rules/ios/{module_token}/routesift-ios.sgmodule", methods=["GET", "HEAD"])
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


@router.get("/rules/flclash-override.js")
def override_file(request: Request):
    base_url = f"http://{HOST}:{request.url.port or 80}"
    source = OVERRIDE.read_text(encoding="utf-8").replace("http://127.0.0.1:18765", base_url)
    return PlainTextResponse(source)
