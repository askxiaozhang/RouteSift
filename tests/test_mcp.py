import importlib.util
import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HAS_MCP = importlib.util.find_spec("mcp") is not None
if HAS_MCP:
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters
    from routesift.mcp_server import create_server


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        self.server.seen.append((self.command, parsed.path, params))
        status = 200
        if params.get("days") == ["13"]:
            status, payload = 503, {"detail": "fixture unavailable"}
        elif params.get("days") == ["11"]:
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()
            return
        elif parsed.path == "/api/review-context":
            payload = {"candidates": [{"host": "example.com", "proxy_bytes": 1024}], "timezone": params["tz"][0], "byte_unit": "bytes"}
        elif parsed.path == "/api/candidates":
            payload = {"items": [{"host": "example.com"}], "total": 1, "page": int(params["page"][0]), "size": 20}
        elif parsed.path == "/api/domain-detail":
            payload = {"host": params["host"][0], "series": [], "totals": {}}
        elif parsed.path == "/api/decisions":
            payload = [{"id": 1, "match_value": "example.com"}, {"id": 2, "match_value": "example.org"}]
        else:
            status, payload = 404, {"detail": "not found"}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_MCP, "Install requirements-mcp.txt to run MCP protocol tests")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.http.seen = []
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        self.http.seen.clear()

    async def test_stdio_discovery_and_all_read_tools_from_another_directory(self):
        script = Path(__file__).resolve().parents[1] / "routesift/mcp_server.py"
        with tempfile.TemporaryDirectory() as cwd:
            transport = StdioServerParameters(command=sys.executable, args=[str(script), "--url", self.url], cwd=cwd)
            async with Client(transport) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools}, {"get_review_context", "list_review_candidates", "get_domain_detail", "list_decisions"})
                self.assertTrue(all(tool.annotations.read_only_hint and not tool.annotations.destructive_hint for tool in tools))
                context = await client.call_tool("get_review_context", {"mode": "match"})
                self.assertFalse(context.is_error)
                self.assertEqual(context.structured_content["candidates"][0]["proxy_bytes"], 1024)
                candidates = await client.call_tool("list_review_candidates", {"query": "example.com", "sort": "recent", "page": 2, "include_reviewed": True})
                self.assertFalse(candidates.is_error)
                self.assertEqual(candidates.structured_content["page"], 2)
                domain = await client.call_tool("get_domain_detail", {"host": "example.com", "start": "2026-11-26", "end": "2026-11-27"})
                self.assertFalse(domain.is_error)
                self.assertEqual(domain.structured_content["host"], "example.com")
                decisions = await client.call_tool("list_decisions", {"offset": 1, "limit": 1})
                self.assertFalse(decisions.is_error)
                self.assertEqual(decisions.structured_content["total"], 2)
                self.assertEqual(decisions.structured_content["items"][0]["id"], 2)
        self.assertEqual(len(self.http.seen), 4)
        self.assertTrue(all(method == "GET" for method, _, _ in self.http.seen))
        self.assertEqual(self.http.seen[1][2]["q"], ["example.com"])
        self.assertEqual(self.http.seen[1][2]["include_reviewed"], ["true"])

    async def test_argument_bounds_are_enforced_before_http_access(self):
        async with Client(create_server(self.url)) as client:
            for arguments in [{"days": 0}, {"days": 31}, {"limit": 101}, {"mode": "invalid"}]:
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("get_review_context", arguments)
                    self.assertTrue(result.is_error)
        self.assertEqual(self.http.seen, [])

    async def test_stdio_legacy_client_can_query(self):
        script = Path(__file__).resolve().parents[1] / "routesift/mcp_server.py"
        transport = StdioServerParameters(command=sys.executable, args=[str(script), "--url", self.url])
        async with Client(transport, mode="legacy") as client:
            tools = await client.list_tools()
            self.assertEqual(len(tools.tools), 4)
            result = await client.call_tool("get_review_context")
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["byte_unit"], "bytes")

    async def test_http_errors_are_returned_as_tool_errors(self):
        async with Client(create_server(self.url)) as client:
            result = await client.call_tool("get_review_context", {"days": 13})
            self.assertTrue(result.is_error)
            self.assertIn("503", result.content[0].text)

    async def test_redirects_are_not_followed(self):
        async with Client(create_server(self.url)) as client:
            result = await client.call_tool("get_review_context", {"days": 11})
            self.assertTrue(result.is_error)
        self.assertEqual([path for _, path, _ in self.http.seen], ["/api/review-context"])

    async def test_stopped_service_returns_actionable_error(self):
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{bound.getsockname()[1]}"
            async with Client(create_server(url)) as client:
                result = await client.call_tool("get_review_context")
                self.assertTrue(result.is_error)
                self.assertIn("请先启动", result.content[0].text)

    def test_server_url_must_be_loopback_without_credentials_or_paths(self):
        for url in ["https://example.com", "http://192.168.1.10:18765", "file:///tmp/test", "http://user:pass@localhost", "http://localhost/path", "http://localhost:bad"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                create_server(url)
