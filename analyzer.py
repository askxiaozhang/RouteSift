"""Traffic analytics over local calendar days."""

import math
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import mean, median, pstdev

from rule_exporter import ReviewMatcher, suggested_targets

MIB = 1024**2


def calendar_days(zone, count, now=None):
    today = datetime.fromtimestamp(now, zone).date() if now is not None else datetime.now(zone).date()
    days = []
    for offset in reversed(range(count)):
        day = today - timedelta(days=offset)
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        days.append((day.isoformat(), int(start.timestamp()), int(end.timestamp())))
    return days


def day_query(days):
    return "WITH days(day, start, end) AS (VALUES " + ",".join("(?,?,?)" for _ in days) + ")\n"


def traffic_rows(db, days):
    return db.execute(
        day_query(days)
        + """
        SELECT day, root_domain, host, process, rule_type, route_type,
               SUM(up_bytes) AS up, SUM(down_bytes) AS down, SUM(conn_count) AS connections
        FROM days JOIN traffic_stats ON bucket_ts >= start AND bucket_ts < end
        GROUP BY day, root_domain, host, process, rule_type, route_type
        """,
        tuple(value for day in days for value in day),
    )


def coverage(db, days):
    return dict(
        db.execute(
            day_query(days)
            + """
            SELECT day, COUNT(bucket_ts) FROM days
            LEFT JOIN sample_buckets ON bucket_ts >= start AND bucket_ts < end
            GROUP BY day
            """,
            tuple(value for day in days for value in day),
        ).fetchall()
    )


def top(counter, limit=30):
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]


def pareto(counter):
    total = sum(counter.values())
    cumulative = 0
    items = []
    for key, count in top(counter):
        cumulative += count
        items.append({
            "name": key, "bytes": count, "share": count / total,
            "cumulative_share": cumulative / total,
        })
    return items


def quadrants(domain_bytes, active_days, connections):
    if not domain_bytes:
        return {"days_threshold": None, "bytes_threshold": None, "points": []}
    days_cut = median(len(days) for days in active_days.values())
    bytes_cut = median(domain_bytes.values())
    points = []
    for name, count in top(domain_bytes, 100):
        days = len(active_days[name])
        points.append({
            "name": name, "bytes": count, "days": days, "connections": connections[name],
            "high_volume": count >= bytes_cut, "high_frequency": days >= days_cut,
        })
    return {"days_threshold": days_cut, "bytes_threshold": bytes_cut, "points": points}


def anomalies(daily_pairs, covered, days):
    target = days[-2]
    target_coverage = covered[target]
    # Compare yesterday only with observed days of comparable sampling coverage.
    baseline = [
        day for day in days[:-2]
        if covered[day] >= 12 and 0.8 <= covered[day] / max(1, target_coverage) <= 1.25
    ]
    results = []
    if target_coverage >= 12 and len(baseline) >= 7:
        for (process, domain), series in daily_pairs.items():
            current = series[target]
            if current < 10 * MIB:
                continue
            samples = [series[day] for day in baseline]
            average = mean(samples)
            z_score = (current - average) / max(MIB, pstdev(samples))
            if current - average >= 10 * MIB and z_score >= 3:
                results.append({
                    "process": process, "domain": domain, "bytes": current,
                    "baseline_bytes": round(average), "z_score": round(z_score, 1),
                    "previous_bytes": series[days[-3]] if days[-3] in baseline else None,
                })
    results.sort(key=lambda item: (-item["bytes"], item["domain"], item["process"]))
    return {"day": target, "baseline_days": len(baseline), "items": results[:30]}


def report(store, days, zone, include_reviewed, now=None):
    windows = calendar_days(zone, max(days, 16), now)
    all_days = [day for day, _, _ in windows]
    anomaly_days = all_days[-16:]
    selected = all_days[-days:]
    daily = {day: Counter() for day in selected}
    totals = Counter()
    uploads = Counter()
    downloads = Counter()
    match_host = Counter()
    domain_bytes = Counter()
    process_bytes = Counter()
    process_domains = defaultdict(Counter)
    daily_pairs = defaultdict(Counter)
    active_days = defaultdict(set)
    connections = Counter()
    decisions = ReviewMatcher(store.decisions())
    reviewed = {}
    total_connections = 0

    with store.connect() as db:
        db.execute("BEGIN")
        covered = coverage(db, windows)
        for row in traffic_rows(db, windows):
            day = row["day"]
            route = row["route_type"]
            count = row["up"] + row["down"]
            if day in daily:
                daily[day][route] += count
                totals[route] += count
                uploads[route] += row["up"]
                downloads[route] += row["down"]
                total_connections += row["connections"]

            if route != "PROXY" or not count:
                continue
            host, process, root = row["host"], row["process"], row["root_domain"]
            key = (host, process)
            if not include_reviewed:
                if key not in reviewed:
                    reviewed[key] = bool(decisions.matching(host, process))
                if reviewed[key]:
                    continue
            if day in anomaly_days:
                daily_pairs[(process, root)][day] += count
            if day not in daily:
                continue
            domain_bytes[root] += count
            process_bytes[process] += count
            process_domains[process][root] += count
            active_days[root].add(day)
            connections[root] += row["connections"]
            if row["rule_type"] == "MATCH":
                match_host[host] += count

    processes = pareto(process_bytes)
    for item in processes:
        item["domains"] = [
            {"name": name, "bytes": count} for name, count in top(process_domains[item["name"]], 10)
        ]
    return {
        "days": selected,
        "totals": totals,
        "uploads": uploads,
        "downloads": downloads,
        "connections": total_connections,
        "trend": [{"day": day, "traffic": daily[day], "sample_buckets": covered[day]} for day in selected],
        "domains": pareto(domain_bytes),
        "processes": processes,
        "match": [{"host": host, "bytes": count} for host, count in top(match_host)],
        "quadrants": quadrants(domain_bytes, active_days, connections),
        "anomalies": anomalies(daily_pairs, covered, anomaly_days),
    }


def candidates(store, days, zone, include_reviewed, mode, query, page, size):
    matcher = ReviewMatcher(store.decisions())
    windows = calendar_days(zone, days)
    entries = defaultdict(Counter)
    with store.connect() as db:
        for row in traffic_rows(db, windows):
            count = row["up"] + row["down"]
            if row["route_type"] != "PROXY" or not count:
                continue
            key = row["host"], row["root_domain"], row["process"]
            rule = row["rule_type"]
            if mode == "match" and rule != "MATCH":
                continue
            if query and not any(query in value.casefold() for value in (*key, rule)):
                continue
            entries[key][rule] += count

    matches = []
    for (host, root, process), rules in entries.items():
        reviews = matcher.matching(host, process)
        if reviews and not include_reviewed:
            continue
        matches.append({
            "host": host, "root_domain": root, "process": process,
            "bytes": sum(rules.values()), "match_bytes": rules["MATCH"],
            "rules": [rule for rule, _ in top(rules, 3)],
            "reviews": reviews,
        })
    matches.sort(key=lambda item: (-item["bytes"], item["host"], item["process"]))
    count = len(matches)
    page = min(page, max(1, math.ceil(count / size)))
    items = matches[(page - 1) * size:page * size]
    for item in items:
        item["targets"] = suggested_targets(item["host"], item["root_domain"], item["process"])
    return {"items": items, "total": count, "page": page, "size": size}
