"use strict";

const analysisActions = {
  DIRECT: "试行直连",
  KEEP_PROXY: "保留原分流",
  REJECT: "考虑拦截",
  OBSERVE: "继续观察",
};
let analysisResultScope = "";
let analysisInProgress = false;

function analysisFilters() {
  return { days: selectedDays(), tz: timezone, mode: state.mode };
}

function analysisMessage(message) {
  text("#analysis-status", message);
  $("#analysis-status").hidden = !message;
}

function syncAnalysisScope() {
  const matches = analysisResultScope === JSON.stringify(analysisFilters());
  $("#analysis-result").hidden = !matches;
  if (!matches && !analysisInProgress) analysisMessage("");
}

function proposalButton(item) {
  return button("带入审核", () => openDecision({
    targets: [{ match_type: "DOMAIN", match_value: item.host }],
    context: item.host + " · DeepSeek 建议，确认后保存",
    proposal: { action: item.action, remark: "DeepSeek 建议：" + item.reason },
  }), "btn secondary");
}

function renderAnalysis(result) {
  const data = result.stats;
  const filter = data.mode === "match" ? "MATCH 兜底" : "全部代理";
  const metrics = [
    ["统计范围", data.start + " 至 " + data.end + " · " + filter],
    ["时段代理总量", bytes(data.totals.PROXY)],
    ["时段直连总量", bytes(data.totals.DIRECT)],
    ["纳入分析", data.sampled_domains + " / " + data.eligible_domains + " 个待审核域名"],
  ];
  $("#analysis-totals").replaceChildren(...metrics.map(([name, value]) => {
    const row = el("div");
    row.append(el("dt", name), el("dd", value));
    return row;
  }));

  text("#analysis-summary", result.summary);
  $("#analysis-summary").hidden = !result.summary;
  const list = $("#analysis-recommendations");
  list.replaceChildren(...result.recommendations.map((item) => {
    const entry = el("li");
    const details = el("div", "", "analysis-target");
    const name = el("strong", "", "analysis-host");
    name.append(domainLink(item.host));
    const badge = tag(analysisActions[item.action]);
    badge.classList.add(item.action);
    details.append(name, badge);
    if (item.action !== "OBSERVE") details.append(proposalButton(item));

    const evidence = [
      "代理 " + bytes(item.proxy_bytes),
      "直连 " + bytes(item.direct_bytes),
      "MATCH " + bytes(item.match_bytes),
      item.active_days + " 天有代理流量",
    ];
    entry.append(details, el("p", evidence.join(" · "), "analysis-evidence"));
    entry.append(el("p", item.reason), el("p", "验证方法 · " + item.verify, "analysis-verify"));
    if (item.rule) entry.append(el("code", item.rule, "mono"));
    return entry;
  }));
  list.hidden = !result.recommendations.length;

  const rules = result.recommendations.map((item) => item.rule).filter(Boolean);
  $("#analysis-config").hidden = !rules.length;
  $("#analysis-rules").value = rules.length ? "rules:\n" + rules.map((rule) => "  - " + rule).join("\n") : "";
  $("#analysis-result").hidden = false;
  analysisMessage(result.message);
}

async function runAnalysis() {
  const filters = analysisFilters();
  const scope = JSON.stringify(filters);
  const trigger = $("#run-analysis");
  trigger.disabled = true;
  analysisInProgress = true;
  analysisResultScope = "";
  $("#analysis-result").hidden = true;
  analysisMessage("正在汇总流量并等待 DeepSeek 返回分析…");
  try {
    const result = await api("/api/ai/analysis", "POST", filters);
    if (scope !== JSON.stringify(analysisFilters())) {
      analysisMessage("统计范围已变更，可以重新分析。");
      return;
    }
    analysisResultScope = scope;
    renderAnalysis(result);
  } catch (error) {
    analysisMessage(error.message);
  } finally {
    trigger.disabled = false;
    analysisInProgress = false;
  }
}

function initAnalysis() {
  $("#run-analysis").addEventListener("click", runAnalysis);
  $("#copy-analysis-rules").addEventListener("click", async () => {
    const rules = $("#analysis-rules");
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(rules.value);
        toast("规则示例已复制");
        return;
      } catch {
        // HTTP LAN clients can copy the selected text manually.
      }
    }
    rules.focus();
    rules.select();
    toast("规则已选中，可直接复制");
  });
}
