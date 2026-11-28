import { openDecision } from "./decisions.js";
import { domainLink } from "./domains.js";
import { state } from "./state.js";
import { $, $$, button, bytes, el, integer, table, tag, text, trafficTime } from "./ui.js";

let searchTimer;
let reloadView;

function setMode(mode) {
  state.mode = mode;
  state.page = 1;
  for (const tab of $$("[data-mode]")) tab.setAttribute("aria-pressed", String(tab.dataset.mode === mode));
}

function latestTrafficCell(timestamp) {
  if (timestamp == null) return "—";
  const date = new Date(timestamp * 1000);
  const time = el("time", trafficTime.format(date));
  time.dateTime = date.toISOString();
  time.title = "最近一个有代理流量的 5 分钟窗口的开始时间";
  return time;
}

function renderQueue(result) {
  state.page = result.page;
  table("#queue-table", [
    { label: "目标" }, { label: "进程" }, { label: "命中规则" },
    { label: "代理流量", numeric: true }, { label: "最近有流量", numeric: true },
    { label: "审核" }, { label: "操作" },
  ], result.items.map((item) => {
    const rules = el("div");
    for (const name of item.rules) rules.append(tag(name || "未知"), " ");
    const review = item.reviews[0];
    const action = item.targets.length
      ? button(review ? "调整" : "审核", () => openDecision(item, review))
      : "—";
    return [
      domainLink(item.host), item.process || "未知进程", rules, bytes(item.bytes), latestTrafficCell(item.last_traffic_at),
      review ? tag(review.action) : "待审核", action,
    ];
  }), "当前筛选范围暂无候选。");
  const last = Math.max(1, Math.ceil(result.total / result.size));
  text("#queue-count", `${integer.format(result.total)} 条 · 第 ${result.page} / ${last} 页`);
  $("#prev").disabled = result.page <= 1;
  $("#next").disabled = result.page >= last;
}

function initReview(reload) {
  reloadView = reload;
  for (const selector of ["#review-reviewed", "#review-sort"]) {
    $(selector).addEventListener("change", () => { state.page = 1; reloadView(); });
  }
  $("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.page = 1; reloadView(); }, 180);
  });
  $("#prev").addEventListener("click", () => { state.page -= 1; reloadView(); });
  $("#next").addEventListener("click", () => { state.page += 1; reloadView(); });
}

export { initReview, setMode, renderQueue };
