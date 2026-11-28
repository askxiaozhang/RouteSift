// Paste this script into Clash Verge (Global Extend Script or Profile Extend Script).
function main(config) {
  const base = "http://127.0.0.1:18765/rules";
  const names = ["reject", "direct", "keep-proxy", "match-target"];
  const providers = { ...(config["rule-providers"] || {}) };
  for (const name of names) {
    const key = "traffic-auditor-" + name;
    providers[key] = {
      type: "http",
      behavior: "classical",
      format: "yaml",
      url: base + "/my-" + name + ".yaml",
      path: "./ruleset/" + key + ".yaml",
      interval: 300,
      proxy: "DIRECT",
    };
  }

  const originalRules = Array.isArray(config.rules) ? config.rules : [];
  const finalMatch = [...originalRules].reverse().find((rule) => /^\s*MATCH\s*,/i.test(rule));
  if (!finalMatch || !finalMatch.split(",")[1]?.trim()) {
    throw new Error("RouteSift 需要配置包含 MATCH 兜底规则");
  }
  const matchTarget = finalMatch.split(",")[1].trim();
  const local = [
    `RULE-SET,traffic-auditor-match-target,${matchTarget}`,
    "AND,((RULE-SET,traffic-auditor-reject),(NOT,((RULE-SET,traffic-auditor-keep-proxy)))),REJECT",
    "AND,((RULE-SET,traffic-auditor-direct),(NOT,((RULE-SET,traffic-auditor-keep-proxy)))),DIRECT",
  ];
  config["rule-providers"] = providers;
  config.rules = [...local, ...originalRules.filter((rule) =>
    !local.includes(rule) && !/^RULE-SET,traffic-auditor-match-target,/i.test(rule)
  )];
  return config;
}
