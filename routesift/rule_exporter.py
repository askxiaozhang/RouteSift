"""Validate review targets and build Mihomo classical rule providers."""

import ipaddress
import json
import re
from functools import lru_cache

import tldextract

from routesift.config import RESOURCES_DIR

EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)
DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
PROVIDERS = {"direct": "DIRECT", "reject": "REJECT", "keep-proxy": "KEEP_PROXY", "match-target": "MATCH_TARGET"}
OVERRIDE = RESOURCES_DIR / "flclash-override.js"


@lru_cache(maxsize=8192)
def domain(value):
    clean = value.strip().rstrip(".")
    try:
        ipaddress.ip_address(clean)
    except ValueError:
        pass
    else:
        raise ValueError("IP 地址请使用 IP-CIDR")

    try:
        hostname = clean.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("请输入有效的域名") from exc
    if len(hostname) > 253 or not all(DOMAIN_LABEL.fullmatch(label) for label in hostname.split(".")):
        raise ValueError("请输入有效的域名")
    return hostname


@lru_cache(maxsize=8192)
def registered_domain(host):
    if not host:
        return ""
    extracted = EXTRACT(host)
    return extracted.top_domain_under_public_suffix or host


def target_host(host, destination_ip):
    if isinstance(host, str) and host:
        try:
            return domain(host)
        except ValueError:
            pass
    for value in (host, destination_ip):
        if isinstance(value, str):
            try:
                return str(ipaddress.ip_address(value.strip()))
            except ValueError:
                pass
    return ""


def canonical_target(match_type, value):
    value = value.strip()
    if match_type in {"DOMAIN", "DOMAIN-SUFFIX"}:
        value = domain(value)
        if match_type == "DOMAIN-SUFFIX" and not EXTRACT(value).top_domain_under_public_suffix:
            raise ValueError("后缀需包含公共后缀和可注册部分")
        return value
    if match_type == "PROCESS-NAME":
        if not value or any(char in ",/\\()" or not char.isprintable() for char in value):
            raise ValueError("请输入可用于 Mihomo 的进程名")
        return value.lower()
    if match_type == "IP-CIDR":
        try:
            if "%" in value:
                raise ValueError("IPv6 zone identifiers are not valid CIDR targets")
            return str(ipaddress.ip_network(value, strict=True))
        except ValueError as exc:
            raise ValueError("请输入 IP 或规范的 CIDR 网段") from exc
    raise ValueError("不支持此匹配方式")


def suggested_targets(host, root, process):
    targets = []
    for kind, value in (
        ("DOMAIN", host),
        ("DOMAIN-SUFFIX", root),
        ("IP-CIDR", host),
        ("PROCESS-NAME", process),
    ):
        try:
            targets.append({"match_type": kind, "match_value": canonical_target(kind, value)})
        except ValueError:
            continue
    return targets


class ReviewMatcher:
    """Mirror rule precedence for observed domain, IP and process targets."""

    def __init__(self, decisions):
        self.exact = {}
        self.networks = []
        for decision in decisions:
            kind, value = decision["match_type"], decision["match_value"]
            if kind == "IP-CIDR":
                self.networks.append((ipaddress.ip_network(value), decision))
            else:
                self.exact[kind, value] = decision

    def matching(self, host, process):
        matches = [self.exact.get(("DOMAIN", host)), self.exact.get(("PROCESS-NAME", process.lower()))]
        if host:
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                labels = host.split(".")
                matches.extend(
                    self.exact.get(("DOMAIN-SUFFIX", ".".join(labels[index:])))
                    for index in range(len(labels) - 1)
                )
            else:
                matches.extend(decision for subnet, decision in self.networks if address in subnet)
        priority = {"MATCH_TARGET": 0, "KEEP_PROXY": 1, "REJECT": 2, "DIRECT": 3, "IGNORE": 4}
        return sorted(
            (match for match in matches if match is not None),
            key=lambda match: (priority[match["action"]], -match["id"]),
        )


def render_provider(decisions, provider):
    rules = []
    for decision in decisions:
        if decision["action"] == PROVIDERS[provider]:
            kind, value = decision["match_type"], decision["match_value"]
            params = ",no-resolve" if kind == "IP-CIDR" else ""
            rules.append(f"{kind},{value}{params}")
    if not rules:
        # Mihomo's line-by-line YAML reader skips empty strings in a block sequence.
        return 'payload:\n  - ""\n'
    return "payload:\n" + "".join(
        f"  - {json.dumps(rule, ensure_ascii=False)}\n" for rule in sorted(rules)
    )
