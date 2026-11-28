import base64
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from routesift import security
from routesift.app import create_app
from routesift.config import ROOT
from routesift.storage import Store


class APIContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "traffic.db")
        self.store.initialize()
        self.app = create_app()
        self.app.state.store = self.store
        self.app.state.collector = SimpleNamespace(status=lambda: {"connected": True})
        self.app.state.phone_collector = None
        self.app.state.phone_status = {"configured": False, "connected": False, "last_event_at": None, "error": ""}

    async def request(self, url, method="GET", body=None, headers=None, client="127.0.0.1"):
        parsed = urlsplit(url)
        payload = json.dumps(body).encode() if body is not None else b""
        request_headers = {"host": "127.0.0.1:18765", **(headers or {})}
        if body is not None:
            request_headers.setdefault("content-type", "application/json")
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": parsed.path, "raw_path": parsed.path.encode(), "root_path": "",
            "query_string": parsed.query.encode(), "headers": [(k.encode(), v.encode()) for k, v in request_headers.items()],
            "client": (client, 12345), "server": ("127.0.0.1", 18765),
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            messages.append(message)

        with patch.object(security, "BIND_HOST", ""):
            await self.app(scope, receive, send)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return status, content

    async def test_dashboard_and_all_static_modules_are_served(self):
        status, html = await self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'type="module" src="/static/js/app.js"', html)
        for file in (ROOT / "static").rglob("*"):
            if file.is_file():
                with self.subTest(file=file.name):
                    status, content = await self.request("/static/" + file.relative_to(ROOT / "static").as_posix())
                    self.assertEqual(status, 200)
                    self.assertTrue(content)

    async def test_traffic_and_domain_routes_keep_their_contracts(self):
        for url in ["/api/status", "/api/report?days=1", "/api/candidates", "/api/domains"]:
            with self.subTest(url=url):
                status, body = await self.request(url)
                self.assertEqual(status, 200)
                json.loads(body)
        today = date.today().isoformat()
        status, body = await self.request(f"/api/domain-detail?host=example.com&start={today}&end={today}&tz=UTC")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["host"], "example.com")
        status, _ = await self.request("/api/report?tz=Invalid/Timezone")
        self.assertEqual(status, 422)

    async def test_review_save_and_undo_use_the_same_database(self):
        body = {"match_type": "DOMAIN", "match_value": "example.com", "action": "DIRECT", "remark": "test"}
        headers = {"x-auditor-request": "1"}
        with patch("routesift.api.reviews.refresh_mihomo_rules", return_value=["DIRECT"]):
            status, content = await self.request("/api/decisions", "PUT", body, headers)
            self.assertEqual(status, 200)
            saved = json.loads(content)
            self.assertTrue(saved["desktop_synced"])
            status, content = await self.request("/api/decisions")
            self.assertEqual(json.loads(content)[0]["match_value"], "example.com")
            status, _ = await self.request(f"/api/decisions/{saved['id']}", "DELETE", headers=headers)
            self.assertEqual(status, 204)
            self.assertEqual(self.store.decisions(), [])

    async def test_shadowrocket_configuration_and_exports(self):
        headers = {"x-auditor-request": "1"}
        status, content = await self.request("/api/shadowrocket")
        self.assertEqual(status, 200)
        version = json.loads(content)["version"]
        rules = "DOMAIN,example.com,DIRECT"
        status, _ = await self.request("/api/shadowrocket", "PUT", {"version": version, "rules": rules}, headers)
        self.assertEqual(status, 200)
        status, _ = await self.request("/api/shadowrocket/import-review", "POST", {"rules": rules}, headers)
        self.assertEqual(status, 200)
        for url in [
            "/rules/routesift-ios.sgmodule",
            "/rules/my-direct.yaml",
            "/rules/flclash-override.js",
            "/rules/clash-verge-override.js",
            "/rules/clash-verge-merge.yaml",
        ]:
            status, content = await self.request(url)
            self.assertEqual(status, 200)
            self.assertTrue(content)
            if url.endswith("clash-verge-override.js"):
                self.assertIn(b"function main(config)", content)
            if url.endswith("clash-verge-merge.yaml"):
                self.assertNotIn(b"prepend-rules:", content)
                self.assertIn(b"\nrule-providers:\n", content)
                self.assertIn(b"\n# RULE-SET,traffic-auditor-match-target,PROXY", content)
                for provider, action in ((b"reject", b"REJECT"), (b"direct", b"DIRECT")):
                    self.assertIn(
                        b"\n# AND,((RULE-SET,traffic-auditor-" + provider
                        + b"),(NOT,((RULE-SET,traffic-auditor-keep-proxy))))," + action,
                        content,
                    )
        status, _ = await self.request("/rules/ios/invalid/routesift-ios.sgmodule")
        self.assertEqual(status, 404)

    async def test_phone_logs_and_upload_validation(self):
        status, content = await self.request("/api/shadowrocket/connections")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["count"], 0)
        status, _ = await self.request("/api/shadowrocket/connections", "POST", headers={"x-auditor-request": "1"})
        self.assertEqual(status, 415)

    async def test_review_context_is_read_only_and_includes_sampling_metadata(self):
        status, body = await self.request("/api/review-context?days=7&tz=Asia%2FShanghai&mode=match&limit=10")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertEqual(result["byte_unit"], "bytes")
        self.assertEqual(result["mode"], "match")
        self.assertEqual(result["sampling"]["bucket_seconds"], 300)
        self.assertEqual(result["candidates"], [])
        for query in ["days=0", "days=31", "limit=0", "limit=101", "mode=invalid", "tz=Invalid/Timezone"]:
            with self.subTest(query=query):
                status, _ = await self.request("/api/review-context?" + query)
                self.assertEqual(status, 422)

    async def test_provider_specific_analysis_entrypoint_is_removed(self):
        status, _ = await self.request("/api/ai/analysis", "POST", {}, {"x-auditor-request": "1"})
        self.assertEqual(status, 404)
        status, html = await self.request("/")
        self.assertNotIn(b'run-analysis', html)

    async def test_write_requests_keep_origin_and_header_checks(self):
        for headers in [{}, {"x-auditor-request": "1", "origin": "https://other.example"}]:
            status, _ = await self.request("/api/decisions", "PUT", {}, headers)
            self.assertEqual(status, 403)
        status, _ = await self.request("/", headers={"host": "other.example"})
        self.assertEqual(status, 400)

    async def test_lan_access_still_requires_authentication(self):
        with patch.multiple(security, LAN_HOST="192.168.1.10", LAN_USER="routesift", LAN_PASSWORD="test-password"):
            status, _ = await self.request("/api/status", client="192.168.1.20")
            self.assertEqual(status, 401)
            credentials = base64.b64encode(b"routesift:test-password").decode()
            status, _ = await self.request("/api/status", headers={"authorization": "Basic " + credentials}, client="192.168.1.20")
            self.assertEqual(status, 200)
