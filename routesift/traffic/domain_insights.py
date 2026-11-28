"""Time series and policy attribution for a domain or individual host."""

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from ipaddress import ip_address

from routesift.rule_exporter import ReviewMatcher, suggested_targets, target_host
from routesift.storage import BUCKET_SECONDS

INTERVALS = {"5m": BUCKET_SECONDS, "hour": 3600, "day": None}
MAX_DAYS = {"5m": 2, "hour": 31, "day": 90}
TRAFFIC_SUMS = "SUM(up_bytes) AS up, SUM(down_bytes) AS down, SUM(conn_count) AS connections"


def local_midnight(day, zone):
    return int(datetime.combine(day, time.min, zone).timestamp())


def time_bins(start, end, zone, interval, now):
    """Build ordered bins between local midnights, including repeated DST hours."""
    stop = int(now.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS + BUCKET_SECONDS
    starts = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        first = local_midnight(day, zone)
        after = min(local_midnight(day + timedelta(days=1), zone), stop)
        if first >= after:
            continue
        starts.extend(range(first, after, interval) if interval else (first,))
    return starts, min(local_midnight(end + timedelta(days=1), zone), stop)


def validate_target(value, scope):
    host = target_host(value, "")
    if not host:
        raise ValueError("请输入有效的域名或 IP，例如 binance.com")
    if scope == "suffix":
        try:
            ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP 地址只支持精确匹配")
    return host


def insights(store, value, scope, start, end, grain, zone, now=None):
    host = validate_target(value, scope)
    now = now or datetime.now(zone)
    if start > end or end > now.date():
        raise ValueError("请选择有效的日期范围，结束日期最晚为今天")
    if (end - start).days >= MAX_DAYS[grain]:
        raise ValueError(f"当前时间粒度最多查询 {MAX_DAYS[grain]} 天")

    starts, stop = time_bins(start, end, zone, INTERVALS[grain], now)
    series = [
        {"start_ts": first, "end_ts": after, "sample_buckets": 0, "traffic": {}}
        for first, after in zip(starts, starts[1:] + [stop])
    ]
    # Bucket times stay UTC in SQLite. The bin boundaries follow the viewer's timezone.
    bounds = (starts[0] if starts else stop, stop)
    condition = "bucket_ts >= ? AND bucket_ts < ? AND host = ?"
    args = (*bounds, host)
    if scope == "suffix":
        condition = "bucket_ts >= ? AND bucket_ts < ? AND (host = ? OR host LIKE ?)"
        args = (*args, "%." + host)
    where = f"FROM traffic_stats WHERE {condition}"
    totals = defaultdict(lambda: {"up": 0, "down": 0, "connections": 0})
    proxy_match_bytes = 0

    with store.connect() as db:
        db.execute("BEGIN")
        for sample in db.execute(
            "SELECT bucket_ts FROM sample_buckets WHERE bucket_ts >= ? AND bucket_ts < ?", bounds
        ):
            index = bisect_right(starts, sample["bucket_ts"]) - 1
            series[index]["sample_buckets"] += 1

        for row in db.execute(
            f"""
            SELECT bucket_ts, route_type, {TRAFFIC_SUMS},
                   SUM(CASE WHEN rule_type = 'MATCH' THEN up_bytes + down_bytes ELSE 0 END) AS match_bytes
            {where}
            GROUP BY bucket_ts, route_type
            """,
            args,
        ):
            index = bisect_right(starts, row["bucket_ts"]) - 1
            route = row["route_type"]
            point = series[index]["traffic"].setdefault(route, {"up": 0, "down": 0, "connections": 0})
            for field in point:
                point[field] += row[field]
                totals[route][field] += row[field]
            if route == "PROXY":
                proxy_match_bytes += row["match_bytes"]

        policies = [
            dict(row)
            for row in db.execute(
                f"""
                SELECT route_type, rule_type, rule_payload, proxy_group, proxy_node, {TRAFFIC_SUMS}
                {where}
                GROUP BY route_type, rule_type, rule_payload, proxy_group, proxy_node
                ORDER BY up + down DESC, route_type, rule_type, rule_payload, proxy_group, proxy_node
                LIMIT 50
                """,
                args,
            )
        ]
        hosts = [
            dict(row)
            for row in db.execute(
                f"""
                SELECT host,
                       SUM(CASE route_type WHEN 'PROXY' THEN up_bytes + down_bytes ELSE 0 END) AS proxy_bytes,
                       SUM(CASE route_type WHEN 'DIRECT' THEN up_bytes + down_bytes ELSE 0 END) AS direct_bytes,
                       SUM(up_bytes + down_bytes) AS bytes
                {where}
                GROUP BY host
                ORDER BY bytes DESC, host
                LIMIT 25
                """,
                args,
            )
        ] if scope == "suffix" else []

    return {
        "host": host,
        "scope": scope,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "grain": grain,
        "series": series,
        "totals": dict(totals),
        "proxy_match_bytes": proxy_match_bytes,
        "policies": policies,
        "hosts": hosts,
        "reviews": ReviewMatcher(store.decisions()).matching(host, ""),
        "targets": suggested_targets(host, host, ""),
    }


def observed_hosts(store, query, zone):
    recent = local_midnight(datetime.now(zone).date() - timedelta(days=29), zone)
    with store.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                """
                SELECT host, SUM(up_bytes + down_bytes) AS bytes
                FROM traffic_stats
                WHERE bucket_ts >= ? AND host <> '' AND instr(host, ?) > 0
                GROUP BY host
                ORDER BY bytes DESC, host
                LIMIT 10
                """,
                (recent, query.strip().lower()),
            )
        ]
