"""Read exported Shadowrocket connection logs and report connection counts."""

import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from urllib.parse import urlsplit

from domain_insights import validate_target
from rule_exporter import target_host
from storage import SHADOWROCKET_TABLES

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 100_000
ROUTES = ("PROXY", "DIRECT", "REJECT", "UNKNOWN")


def connection_host(value):
    if not isinstance(value, str) or len(value) > 16_384:
        return ""
    value = value.strip()
    if host := target_host(value, ""):
        return host
    try:
        host = urlsplit(value if "://" in value else f"//{value}").hostname
    except ValueError:
        return ""
    return target_host(host, "")


def connection_route(result):
    if not isinstance(result, str):
        return "UNKNOWN"
    rule, _, _ = result.partition(" # ")
    policy = rule.rstrip(" ,").rsplit(",", 1)[-1].strip().upper()
    if policy.startswith("REJECT-"):
        return "REJECT"
    return policy if policy in ROUTES else "UNKNOWN"


def import_file(store, path, zone):
    with path.open("rb") as source:
        if source.read(16) != b"SQLite format 3\0":
            raise ValueError("请选择 Shadowrocket「数据 → 代理」导出的 SQLite 日志文件")
    events = []
    skipped = 0
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.execute("PRAGMA trusted_schema = OFF")
            table = db.execute("PRAGMA main.table_list('logging_content')").fetchone()
            if table is None:
                raise ValueError("找不到连接记录表。请打开一份有连接记录的代理日志，再重新导出")
            if table[2] not in {"table", "shadow"}:
                raise ValueError("无法识别此 Shadowrocket 日志格式")
            rows = db.execute(
                "SELECT docid, c0url, c2result, c4created FROM main.logging_content LIMIT ?",
                (MAX_RECORDS + 1,),
            )
            for index, (log_id, url, result, created) in enumerate(rows, 1):
                if index > MAX_RECORDS:
                    raise ValueError("单次最多导入 10 万条连接，请选择较小的日志文件")
                host = connection_host(url)
                try:
                    stamp = datetime.fromisoformat(created)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=zone)
                    observed_at = int(stamp.timestamp())
                except (ValueError, TypeError, OverflowError, OSError):
                    skipped += 1
                    continue
                if not host or not isinstance(log_id, int):
                    skipped += 1
                    continue
                events.append((observed_at, log_id, host, connection_route(result)))
    except sqlite3.Error as exc:
        raise ValueError("无法读取 Shadowrocket 日志，请重新导出代理日志") from exc
    if not events:
        raise ValueError("这份日志没有可读取的连接记录")
    added = store.save_shadowrocket_connections(events)
    return {"added": added, "duplicates": len(events) - added, "skipped": skipped}


def report(store, host, scope, source="file"):
    table = SHADOWROCKET_TABLES[source]
    condition, args = "", ()
    if host:
        host = validate_target(host, scope)
        if scope == "suffix":
            condition, args = "WHERE host = ? OR host LIKE ?", (host, "%." + host)
        else:
            condition, args = "WHERE host = ?", (host,)

    totals = dict.fromkeys(ROUTES, 0)
    points = []
    interval = 60
    with store.connect() as db:
        db.execute("BEGIN")
        first, last, count, host_count = db.execute(
            f"SELECT MIN(observed_at), MAX(observed_at), COUNT(*), COUNT(DISTINCT host) "
            f"FROM {table} {condition}", args,
        ).fetchone()
        hosts = []
        if count:
            span = last - first
            interval = next((step for step in (60, 300, 3600, 86400) if span < 120 * step),
                            (span // (120 * 86400) + 1) * 86400)
            buckets = defaultdict(dict)
            for row in db.execute(
                f"""
                SELECT ((observed_at - ?) / ?) AS bucket, route_type, COUNT(*) AS count
                FROM {table} {condition}
                GROUP BY bucket, route_type
                """,
                (first // interval * interval, interval, *args),
            ):
                buckets[row["bucket"]][row["route_type"]] = row["count"]
                totals[row["route_type"]] += row["count"]
            start = first // interval * interval
            points = [{"start": ts, "routes": buckets.get(index, {})}
                      for index, ts in enumerate(range(start, last + 1, interval))]
            hosts = [dict(row) for row in db.execute(
                f"""
                SELECT host, COUNT(*) AS connections, MAX(observed_at) AS recent,
                       SUM(route_type = 'PROXY') AS proxy,
                       SUM(route_type = 'DIRECT') AS direct,
                       SUM(route_type = 'REJECT') AS reject,
                       SUM(route_type = 'UNKNOWN') AS unknown
                FROM {table} {condition}
                GROUP BY host ORDER BY connections DESC, recent DESC, host LIMIT 100
                """, args,
            )]
    return {"host": host, "first": first, "last": last, "count": count, "host_count": host_count,
            "totals": totals, "interval": interval, "points": points, "hosts": hosts}
