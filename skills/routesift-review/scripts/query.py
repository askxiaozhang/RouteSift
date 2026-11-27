#!/usr/bin/env python3
"""Query a running local RouteSift using only the Python standard library."""

import argparse
import json
import sys
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(value):
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("无效的服务地址或端口") from exc
    if (
        parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.username is not None or parts.password is not None
        or parts.path not in {"", "/"} or parts.query or parts.fragment
    ):
        raise argparse.ArgumentTypeError("仅支持本机回环 HTTP 地址，如 http://127.0.0.1:18765")
    return value.rstrip("/")


def bounded_int(low, high=None):
    def parse(value):
        number = int(value)
        if number < low or (high is not None and number > high):
            raise argparse.ArgumentTypeError(f"数值须 >= {low}" + (f" 且 <= {high}" if high is not None else ""))
        return number
    return parse


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--url", type=local_url, default="http://127.0.0.1:18765")
    commands = root.add_subparsers(dest="command", required=True)
    context = commands.add_parser("context", help="流量总量、采样覆盖和优先审核域名")
    candidates = commands.add_parser("candidates", help="搜索和分页查询候选")
    for command in (context, candidates):
        command.add_argument("--days", type=bounded_int(1, 30), default=7)
        command.add_argument("--tz", default="Asia/Shanghai")
        command.add_argument("--mode", choices=("all", "match"), default="all")
    context.add_argument("--limit", type=bounded_int(1, 100), default=24)
    candidates.add_argument("--query", dest="q", default="")
    candidates.add_argument("--sort", choices=("bytes", "recent"), default="bytes")
    candidates.add_argument("--page", type=bounded_int(1), default=1)
    candidates.add_argument("--include-reviewed", action="store_true")
    domain = commands.add_parser("domain", help="具体域名/IP 的时间序列及分流归因")
    domain.add_argument("--host", required=True)
    domain.add_argument("--start", type=date.fromisoformat, required=True)
    domain.add_argument("--end", type=date.fromisoformat, required=True)
    domain.add_argument("--tz", default="Asia/Shanghai")
    domain.add_argument("--scope", choices=("exact", "suffix"), default="exact")
    domain.add_argument("--grain", choices=("5m", "hour", "day"), default="hour")
    decisions = commands.add_parser("decisions", help="分页读取已有审核结论")
    decisions.add_argument("--offset", type=bounded_int(0), default=0)
    decisions.add_argument("--limit", type=bounded_int(1, 100), default=50)
    return root


def query(args):
    params = vars(args).copy()
    base = params.pop("url")
    command = params.pop("command")
    endpoints = {
        "context": "/api/review-context", "candidates": "/api/candidates",
        "domain": "/api/domain-detail", "decisions": "/api/decisions",
    }
    query_params = {} if command == "decisions" else params
    encoded = urlencode({key: str(value).lower() if isinstance(value, bool) else value for key, value in query_params.items()})
    target = base + endpoints[command] + ("?" + encoded if encoded else "")
    with build_opener(ProxyHandler({}), NoRedirect()).open(target, timeout=15) as response:
        result = json.load(response)
    if command == "decisions":
        offset, limit = params["offset"], params["limit"]
        return {"items": result[offset:offset + limit], "total": len(result), "offset": offset, "limit": limit}
    return {**result, "timezone": params["tz"], "byte_unit": "bytes"}


def main():
    args = parser().parse_args()
    try:
        result = query(args)
    except HTTPError as exc:
        print(f"RouteSift 返回 HTTP {exc.code}；请检查查询参数、服务版本或重定向配置。", file=sys.stderr)
        return 1
    except (URLError, OSError):
        print("无法连接本机 RouteSift；请检查服务状态和 --url 端口。", file=sys.stderr)
        return 1
    except ValueError:
        print("RouteSift 未返回有效 JSON；请检查 --url 是否指向看板服务。", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
