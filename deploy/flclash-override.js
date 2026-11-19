// Bind this script to the active FlClash profile in script extension mode.
const main = (config) => {
  const base = "http://127.0.0.1:18765/rules";
  const names = ["reject", "direct", "keep-proxy"];
  const providers = { ...config["rule-providers"] };
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

  const local = [
    "AND,((RULE-SET,traffic-auditor-reject),(NOT,((RULE-SET,traffic-auditor-keep-proxy)))),REJECT",
    "AND,((RULE-SET,traffic-auditor-direct),(NOT,((RULE-SET,traffic-auditor-keep-proxy)))),DIRECT",
  ];
  config["rule-providers"] = providers;
  config.rules = [...local, ...(config.rules || []).filter((rule) => !local.includes(rule))];
  return config;
};
