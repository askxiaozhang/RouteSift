import { api } from "./api.js";
import { clearTrendPoint, initCharts } from "./charts.js";
import { initDecisions } from "./decisions.js";
import { adoptDomainHash, domainHash, initDomainView, loadDomainView } from "./domains.js";
import { renderOverview } from "./overview.js";
import { initPhoneLogs, loadPhoneLogs } from "./phone-logs.js";
import { initReview, renderQueue, setMode } from "./review.js";
import { initRules, renderDecisions } from "./rules.js";
import { state } from "./state.js";
import { $, $$, clock, selectedDays, showError, text, timezone } from "./ui.js";

const views = {
  overview: ["流量概览", "查看本机采集的代理用量、流量归因和突发变化。"],
  domains: ["域名详情", "按时间查看单个域名的用量和实际走向，结合命中规则做审核。"],
  review: ["审核队列", "按代理用量或最近有流量的时间查看目的地址与进程。"],
  "phone-logs": ["手机日志", "查看 Shadowrocket 导入记录，按域名核对连接次数和分流去向。"],
  rules: ["规则管理", "管理分流决策，导出到 FlClash 或 Shadowrocket。"],
};

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    text("#status-label", status.connected ? "采集中" : "等待控制器");
    text("#status-address", new URL(status.controller).host);
    text("#last-sample", status.last_sample_at ? `最近采样 ${clock.format(status.last_sample_at * 1000)}` : "");
    $("#status-dot").classList.toggle("online", status.connected && !status.storage_error);
    const message = status.storage_error
      ? "数据库暂时无法写入，采集器将重试。"
      : (status.error || "");
    if ($("#collector-notice").textContent !== message) text("#collector-notice", message);
    $("#collector-notice").hidden = !message || state.view === "phone-logs";
  } catch {
    text("#status-label", "审计服务不可达");
    $("#status-dot").classList.remove("online");
  }
}

function query(reviewed) {
  return new URLSearchParams({ days: selectedDays(), tz: timezone, include_reviewed: reviewed });
}

async function loadView() {
  const id = ++state.requestId;
  $("#load-error").hidden = true;
  $("#refresh").disabled = true;
  const view = state.view;
  try {
    if (view === "overview") {
      const report = await api(`/api/report?${query($("#overview-reviewed").checked)}`);
      if (id === state.requestId) renderOverview(report);
    } else if (view === "domains") {
      await loadDomainView(id);
    } else if (view === "review") {
      const params = query($("#review-reviewed").checked);
      params.set("mode", state.mode);
      params.set("sort", $("#review-sort").value);
      params.set("q", $("#search").value);
      params.set("page", state.page);
      const result = await api(`/api/candidates?${params}`);
      if (id === state.requestId) renderQueue(result);
    } else if (view === "phone-logs") {
      await loadPhoneLogs(id);
    } else {
      const decisions = await api("/api/decisions");
      if (id === state.requestId) renderDecisions(decisions);
    }
  } catch (error) {
    if (id === state.requestId) showError(error);
  } finally {
    if (id === state.requestId) $("#refresh").disabled = false;
  }
}

function setView(view, mode) {
  if (!Object.hasOwn(views, view)) view = "overview";
  if (mode) setMode(mode);
  state.view = view;
  for (const element of $$(".view")) element.hidden = element.id !== view;
  for (const tab of $$("[data-view]")) {
    if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  text("#page-title", views[view][0]);
  text("#page-description", views[view][1]);
  $("#range-tools").hidden = view === "rules" || view === "domains" || view === "phone-logs";
  $("#collector-notice").hidden = view === "phone-logs" || !$("#collector-notice").textContent;
  updateViewLocation();
  window.scrollTo(0, 0);
  loadView();
}

function updateViewLocation() {
  const hash = state.view === "domains" ? domainHash() : `#${state.view}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

function followViewLocation() {
  const [view, filters] = location.hash.slice(1).split("?", 2);
  if (view === "domains") adoptDomainHash(filters);
  setView(view);
}

for (const tab of $$("[data-view]")) tab.addEventListener("click", () => setView(tab.dataset.view));
for (const tab of $$("[data-mode]")) tab.addEventListener("click", () => { setMode(tab.dataset.mode); loadView(); });
for (const link of $$("[data-go]")) {
  link.addEventListener("click", () => {
    $("#search").value = "";
    setView("review", link.dataset.go === "match" ? "match" : "all");
  });
}
$("#refresh").addEventListener("click", () => { refreshStatus(); clearTrendPoint(); loadView(); });
$("#days").addEventListener("change", () => { state.page = 1; clearTrendPoint(); loadView(); });
$("#overview-reviewed").addEventListener("change", loadView);

window.addEventListener("hashchange", followViewLocation);
initDecisions(loadView);
initReview(loadView);
initRules();
initCharts();
initDomainView({ reload: loadView, updateLocation: updateViewLocation });
initPhoneLogs(loadView);
followViewLocation();
refreshStatus();
setInterval(refreshStatus, 5000);
