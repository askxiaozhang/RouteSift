"""Read Mihomo's connection snapshots and aggregate byte deltas."""

import asyncio
import json
import logging
import sqlite3
import time
from collections import Counter, defaultdict
from functools import lru_cache

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, WebSocketException

from routesift.rule_exporter import registered_domain, target_host
from routesift.storage import BUCKET_SECONDS, Store

log = logging.getLogger(__name__)
FLUSH_SECONDS = 15
MAX_COUNTER = 2**63 - 1


@lru_cache(maxsize=8192)
def dimensions(raw_host, destination, process, rule, rule_payload, chain):
    host = target_host(raw_host, destination)
    names = {part.upper() for part in chain}
    route = "PROXY" if names else "UNKNOWN"
    if names & {"REJECT", "REJECT-DROP"}:
        route = "REJECT"
    elif "DIRECT" in names:
        route = "DIRECT"
    process_name = process.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (
        registered_domain(host), host, process_name, rule.strip().upper(), rule_payload.strip(),
        chain[-1] if chain else "", chain[0] if chain else "", route,
    )


def string(data, key):
    value = data.get(key)
    return value[:1024] if isinstance(value, str) else ""


class Collector:
    def __init__(self, store: Store, url: str, secret: str):
        self.store = store
        self.url = url
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else None
        self.active = store.checkpoints()
        self.aggregates = defaultdict(lambda: [0, 0, 0])
        self.samples = Counter()
        self.bucket = None
        self.flushed_at = None
        self.sample_at = None
        self.connected = False
        self.error = None
        self.storage_error = False

    def status(self):
        return {
            "connected": self.connected,
            "last_sample_at": self.sample_at,
            "last_flush_at": self.flushed_at,
            "active_connections": len(self.active) if self.connected else 0,
            "error": self.error,
            "storage_error": self.storage_error,
            "controller": self.url,
        }

    def ingest(self, payload, timestamp):
        if not isinstance(payload, dict) or "connections" not in payload:
            raise ValueError("连接快照缺少 connections")
        connections = payload["connections"]
        if connections is None:
            connections = []
        if not isinstance(connections, list):
            raise ValueError("connections 应为数组")

        bucket = int(timestamp) // BUCKET_SECONDS * BUCKET_SECONDS
        next_active = {}
        for item in connections:
            if not isinstance(item, dict):
                continue
            conn_id = string(item, "id")
            if not conn_id or conn_id in next_active:
                continue
            previous = self.active.get(conn_id)
            start = string(item, "start") or (previous[0] if previous else "")
            current = (start, item.get("upload"), item.get("download"))
            if not all(type(count) is int and 0 <= count <= MAX_COUNTER for count in current[1:]):
                # A malformed row does not erase the last valid checkpoint.
                if previous:
                    next_active[conn_id] = previous
                continue
            next_active[conn_id] = current
            is_new = previous is None or bool(previous[0] and start != previous[0])
            last_up, last_down = (0, 0) if is_new else previous[1:]
            up = current[1] - last_up if current[1] >= last_up else current[1]
            down = current[2] - last_down if current[2] >= last_down else current[2]
            if not (up or down or is_new):
                continue

            metadata = item.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            chain = item.get("chains")
            chain = tuple(part[:1024] for part in chain if isinstance(part, str) and part) if isinstance(chain, list) else ()
            key = dimensions(
                string(metadata, "host"), string(metadata, "destinationIP"),
                string(metadata, "process") or string(metadata, "processPath"),
                string(item, "rule"), string(item, "rulePayload"), chain,
            )
            stats = self.aggregates[(bucket, *key)]
            stats[0] += up
            stats[1] += down
            stats[2] += is_new

        self.active = next_active
        self.samples[bucket] += 1
        self.sample_at = int(timestamp)
        self.bucket = bucket

    async def flush(self):
        if not self.samples:
            return
        # A cancellation must wait for the SQLite transaction to finish before releasing the lock.
        write = asyncio.create_task(
            asyncio.to_thread(self.store.flush, self.aggregates, self.samples, self.active)
        )
        try:
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                raise
        except sqlite3.Error:
            if not self.storage_error:
                log.exception("SQLite 写入失败，保留内存增量等待重试")
            self.storage_error = True
        finally:
            if write.done() and not write.cancelled() and write.exception() is None:
                self.aggregates.clear()
                self.samples.clear()
                self.flushed_at = int(time.time())
                self.storage_error = False

    async def run(self):
        delay = 1
        next_flush = 0
        try:
            while True:
                try:
                    async with connect(
                        self.url, additional_headers=self.headers, proxy=None,
                        max_size=16 * 1024 * 1024, open_timeout=5, close_timeout=2,
                    ) as socket:
                        while True:
                            raw = await asyncio.wait_for(socket.recv(), timeout=12)
                            previous_bucket = self.bucket
                            self.ingest(json.loads(raw), time.time())
                            self.connected, self.error = True, None
                            delay = 1
                            now = time.monotonic()
                            if previous_bucket != self.bucket or now >= next_flush:
                                await self.flush()
                                next_flush = now + FLUSH_SECONDS
                except InvalidStatus as exc:
                    status = exc.response.status_code
                    self.error = f"控制器鉴权失败（HTTP {status}）" if status in {401, 403} else f"控制器返回 HTTP {status}"
                except (OSError, TimeoutError, ValueError, WebSocketException) as exc:
                    self.error = {
                        "ConnectionRefusedError": "控制器尚未监听，请检查 FlClash 外部控制器设置",
                        "TimeoutError": "控制器未在预期时间内发送连接快照",
                    }.get(type(exc).__name__, f"控制器连接中断（{type(exc).__name__}）")
                self.connected = False
                await self.flush()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        finally:
            self.connected = False
            await self.flush()
