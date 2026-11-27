"""Build a local Shadowrocket module from ordered routing rules."""

from ipaddress import ip_network

from routesift.rule_exporter import canonical_target, domain

DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX"}
NETWORK_TYPES = {"IP-CIDR": 4, "IP-CIDR6": 6}
POLICIES = {"DIRECT", "PROXY", "REJECT"}
MAX_RULE_TEXT = 64_000


def parse_rules(source):
    rules = {}
    for line_number, raw_line in enumerate(source.splitlines(), 1):
        line = raw_line.partition("#")[0].strip()
        if not line or line == "[Rule]":
            continue
        try:
            kind, target, policy, *options = (part.strip() for part in line.split(","))
            kind, policy = kind.upper(), policy.upper()
            if policy not in POLICIES:
                raise ValueError("策略须为 DIRECT、PROXY 或 REJECT")
            if kind in DOMAIN_TYPES and not options:
                target = domain(target)
            elif kind in NETWORK_TYPES and options in ([], ["no-resolve"]):
                target = canonical_target("IP-CIDR", target)
                if ip_network(target).version != NETWORK_TYPES[kind]:
                    raise ValueError("IPv4 使用 IP-CIDR，IPv6 使用 IP-CIDR6")
            else:
                raise ValueError("请使用 DOMAIN、DOMAIN-SUFFIX、IP-CIDR 或 IP-CIDR6")
            key = (kind, target)
            if key in rules:
                raise ValueError(f"{kind},{target} 已出现过，请只保留一条")
            rules[key] = ",".join((policy, *options))
        except ValueError as exc:
            message = str(exc) if line.count(",") >= 2 else "请填写匹配方式、目标和策略，用英文逗号分隔"
            raise ValueError(f"第 {line_number} 行：{message}") from exc
    return rules


def render_rules(rules):
    return "\n".join(f"{kind},{target},{policy}" for (kind, target), policy in rules.items())


def merge_reviews(rules, decisions):
    initial_count = len(rules)
    # Review decisions are sorted newest first. Prefer exact targets to broader ones.
    priority = {"DOMAIN": 0, "DOMAIN-SUFFIX": 1, "IP-CIDR": 2}
    compatible = (
        decision for decision in decisions
        if decision["action"] in {"DIRECT", "REJECT"} and decision["match_type"] in priority
    )
    for decision in sorted(compatible, key=lambda item: (priority[item["match_type"]], -len(item["match_value"]))):
        kind, target = decision["match_type"], decision["match_value"]
        policy = decision["action"]
        if kind == "IP-CIDR":
            kind = "IP-CIDR6" if ip_network(target).version == 6 else kind
            policy += ",no-resolve"
        rules.setdefault((kind, target), policy)
    return render_rules(rules), len(rules) - initial_count


def render_module(rules):
    return f"#!name=RouteSift iOS\n#!desc=Routing rules from RouteSift\n\n[Rule]\n{rules}\n"
