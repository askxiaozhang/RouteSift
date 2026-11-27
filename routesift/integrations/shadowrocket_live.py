"""Read Shadowrocket diagnostic logs over a local HTTP connection."""

import re
import sqlite3
from datetime import datetime, time, timedelta
from hashlib import blake2b
from http.client import HTTPConnection, HTTPException
from ipaddress import IPv4Address, ip_network
from threading import Event
from urllib.parse import urlsplit

from routesift.integrations.shadowrocket_logs import connection_host, connection_route

LAN_NETWORKS = tuple(map(ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
RULE_START = re.compile(r"^\[([^\]]+)\]\s+((?:tcp|udp|proxy) stream <\d+>).*?\brule\s*=>\s*\{\s*$", re.I)
FIELD = re.compile(r"^\s*(url|type|result)\s*=\s*(.*?)\s*$", re.I)
MAX_LINE = 32_768


def log_endpoint(url):
    try:
        parsed = urlsplit(url)
        host = IPv4Address(parsed.hostname or "")
        if (parsed.scheme == "http" and parsed.path == "/api/log" and parsed.port
                and not (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment)
                and (str(host) == "127.0.0.1" or any(host in network for network in LAN_NETWORKS))):
            return str(host), parsed.port
    except ValueError:
        pass
    raise ValueError("诊断地址须为 http://<局域网 IP>:<端口>/api/log")


def event_time(value, now):
    try:
        clock = time.fromisoformat(value)
    except ValueError:
        instant = datetime.fromisoformat(value)
        return instant if instant.tzinfo else instant.replace(tzinfo=now.tzinfo)
    instant = datetime.combine(now.date(), clock, clock.tzinfo or now.tzinfo)
    if instant - now > timedelta(hours=12):
        return instant - timedelta(days=1)
    if now - instant > timedelta(hours=12):
        return instant + timedelta(days=1)
    return instant


class RuleLogs:
    def __init__(self, zone):
        self.zone = zone
        self.header = None
        self.fields = {}

    def feed(self, line):
        if line.startswith("["):
            self.header = RULE_START.match(line)
            self.fields.clear()
            return None
        if self.header is None:
            return None
        if field := FIELD.match(line):
            self.fields[field[1].lower()] = field[2].rstrip(",;").strip().strip('"')
        if line.strip() != "}":
            return None
        stamp, stream = self.header.groups()
        self.header = None
        host = connection_host(self.fields.get("url"))
        if not host:
            return None
        route = connection_route(self.fields.get("type"))
        if route == "UNKNOWN":
            route = connection_route(self.fields.get("result"))
        try:
            instant = event_time(stamp, datetime.now(self.zone))
            seconds = instant.timestamp()
        except (ValueError, OverflowError):
            return None
        identity = blake2b(f"{seconds:.6f} {stream.lower()}".encode(), digest_size=8).digest()
        return int(seconds), int.from_bytes(identity, "big", signed=True), host, route


class LiveCollector:
    def __init__(self, store, url, zone):
        self.store = store
        self.host, self.port = log_endpoint(url)
        self.zone = zone
        self.stop = Event()
        self.pending = []
        self.state = {"configured": True, "connected": False, "last_event_at": None, "error": ""}

    def flush(self):
        if self.pending:
            self.store.save_shadowrocket_connections(self.pending, source="live")
            last = max(self.pending)[0]
            self.pending.clear()
            self.state = {**self.state, "last_event_at": max(last, self.state["last_event_at"] or last)}

    def read_stream(self):
        connection = HTTPConnection(self.host, self.port, timeout=8)
        logs = RuleLogs(self.zone)
        tail = b""
        try:
            connection.request("GET", "/api/log", headers={"Accept": "text/plain"})
            with connection.getresponse() as response:
                if response.status != 200:
                    raise HTTPException(f"HTTP {response.status}")
                self.state = {**self.state, "connected": True, "error": ""}
                while not self.stop.is_set() and (chunk := response.read1(8192)):
                    *lines, tail = (tail + chunk).split(b"\n")
                    if len(tail) > MAX_LINE:
                        raise HTTPException("诊断日志行过长")
                    for line in lines:
                        event = logs.feed(line.decode("utf-8", "replace"))
                        if event:
                            self.pending.append(event)
                    self.flush()
        finally:
            connection.close()
            self.flush()

    def run(self):
        while not self.stop.is_set():
            error = "手机诊断连接已结束，等待重连"
            try:
                self.flush()
                self.read_stream()
            except (OSError, HTTPException):
                error = "暂未收到手机诊断日志，请检查手机的连接和「允许访问」"
            except sqlite3.Error:
                error = "暂时无法保存手机日志，稍后重试"
            self.state = {**self.state, "connected": False, "error": error}
            self.stop.wait(3)
