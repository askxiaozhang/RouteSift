"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const text = (selector, value) => { $(selector).textContent = value; };
const integer = new Intl.NumberFormat("zh-CN");
const clock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Singapore";
const trafficTime = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone,
});
const routes = { PROXY: "代理", DIRECT: "直连", REJECT: "拦截", UNKNOWN: "去向未知" };
const kinds = { DOMAIN: "精确域名", "DOMAIN-SUFFIX": "域名后缀", "PROCESS-NAME": "进程名", "IP-CIDR": "IP / CIDR" };
const views = {
  overview: ["流量概览", "查看本机采集的代理用量、流量归因和突发变化。"],
  domains: ["域名详情", "按时间查看单个域名的用量和实际走向，结合命中规则做审核。"],
  review: ["审核队列", "按代理用量或最近有流量的时间查看目的地址与进程。"],
  rules: ["规则管理", "管理分流决策，并将本机规则集接入 FlClash。"],
};
const state = { view: "overview", mode: "all", page: 1, report: null, decisions: [], requestId: 0 };
let editing = null;
let toastTimer;
let searchTimer;

function el(tag, value = "", className = "") {
  const node = document.createElement(tag);
  if (value) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function button(label, handler, className = "text-btn") {
  const node = el("button", label, className);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function bytes(value = 0) {
  if (value < 1024) return `${integer.format(value)} B`;
  const units = ["KiB", "MiB", "GiB", "TiB", "PiB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)) - 1);
  const amount = value / (1024 ** (index + 1));
  return `${amount.toFixed(amount < 10 ? 2 : 1)} ${units[index]}`;
}

function percent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function tag(value) {
  const node = el("span", value, "tag");
  if (/^[A-Z][A-Z_]*$/.test(value)) node.classList.add(value);
  return node;
}

function table(selector, columns, rows, empty) {
  const wrap = el("div", "", "table-wrap");
  const tableEl = el("table");
  const head = tableEl.createTHead().insertRow();
  columns.forEach((column) => {
    const cell = el("th", column.label, column.numeric ? "num" : "");
    cell.scope = "col";
    head.append(cell);
  });
  const body = tableEl.createTBody();
  if (!rows.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = columns.length;
    cell.className = "empty";
    cell.textContent = empty;
  }
  rows.forEach((values) => {
    const row = body.insertRow();
    values.forEach((value, index) => {
      const cell = row.insertCell();
      if (columns[index].numeric) cell.className = "num";
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value;
    });
  });
  wrap.append(tableEl);
  $(selector).replaceChildren(wrap);
}

async function api(path, method = "GET", body) {
  const options = { method };
  if (method !== "GET") options.headers = { "X-Auditor-Request": "1" };
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const message = typeof result.detail === "string" ? result.detail : `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

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
    $("#collector-notice").hidden = !message;
  } catch {
    text("#status-label", "审计服务不可达");
    $("#status-dot").classList.remove("online");
  }
}

function query(reviewed) {
  return new URLSearchParams({ days: $("#days").value, tz: timezone, include_reviewed: reviewed });
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
      syncAnalysisScope();
      const params = query($("#review-reviewed").checked);
      params.set("mode", state.mode);
      params.set("sort", $("#review-sort").value);
      params.set("q", $("#search").value);
      params.set("page", state.page);
      const result = await api(`/api/candidates?${params}`);
      if (id === state.requestId) renderQueue(result);
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

function showError(error) {
  text("#load-error", error.message);
  $("#load-error").hidden = false;
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
  $("#range-tools").hidden = view === "rules" || view === "domains";
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

function setMode(mode) {
  state.mode = mode;
  state.page = 1;
  for (const tab of $$("[data-mode]")) tab.setAttribute("aria-pressed", String(tab.dataset.mode === mode));
}

function breakdown(totals) {
  const total = Object.values(totals).reduce((sum, value) => sum + value, 0);
  const bar = $("#route-bar");
  const list = $("#route-list");
  bar.replaceChildren();
  list.replaceChildren();
  for (const [route, label] of Object.entries(routes)) {
    const amount = totals[route] || 0;
    const share = total ? amount / total : 0;
    const segment = el("span", "", route.toLowerCase());
    segment.style.width = percent(share);
    bar.append(segment);
    const row = el("div");
    const name = el("dt");
    name.append(el("i", "", route.toLowerCase()), label);
    row.append(name, el("dd", `${bytes(amount)} · ${percent(share)}`));
    list.append(row);
  }
}

function ranking(selector, items, type) {
  if (!items.length) {
    $(selector).replaceChildren(el("p", "当前筛选范围暂无代理流量。", "empty-block"));
    return;
  }
  const list = el("div", "", "rank-scroll");
  for (const item of items) {
    const name = item.name || item.host || (type === "process" ? "未知进程" : "未知目标");
    const row = el("div", "", "rank-row");
    if (type === "process") {
      const details = el("details");
      const domains = el("ul");
      for (const domain of item.domains) {
        const entry = el("li");
        entry.append(domainLink(domain.name, "suffix"), ` · ${bytes(domain.bytes)}`);
        domains.append(entry);
      }
      details.append(el("summary", name, "rank-name"), domains);
      row.append(details);
    } else {
      const title = el("span", "", "rank-name");
      title.append(domainLink(item.name || item.host, type === "domain" ? "suffix" : "exact"));
      row.append(title);
    }
    row.append(el("span", bytes(item.bytes), "mono"));
    if (item.cumulative_share !== undefined) {
      const meta = el("small");
      const track = el("span", "", "rank-track");
      const fill = el("i");
      fill.style.width = percent(item.cumulative_share);
      track.setAttribute("aria-hidden", "true");
      track.append(fill);
      meta.append(track, `占 ${percent(item.share)} · 累计 ${percent(item.cumulative_share)}`);
      row.append(meta);
    }
    list.append(row);
  }
  $(selector).replaceChildren(list);
}

function renderOverview(report) {
  state.report = report;
  const { PROXY = 0, DIRECT = 0 } = report.totals;
  text("#total-proxy", bytes(PROXY));
  text("#total-direct", bytes(DIRECT));
  text("#proxy-share", PROXY + DIRECT ? percent(PROXY / (PROXY + DIRECT)) : "—");
  text("#total-connections", integer.format(report.connections));
  text("#proxy-io", `↑ ${bytes(report.uploads.PROXY)} / ↓ ${bytes(report.downloads.PROXY)}`);
  text("#direct-io", `↑ ${bytes(report.uploads.DIRECT)} / ↓ ${bytes(report.downloads.DIRECT)}`);
  const covered = report.trend.filter((item) => item.sample_buckets).length;
  text("#coverage", `覆盖 ${covered} / ${report.days.length} 个自然日`);
  text("#trend-range", `${report.days[0]} 至 ${report.days.at(-1)} · ${timezone}`);
  breakdown(report.totals);

  table("#trend-table", [
    { label: "日期" }, { label: "代理", numeric: true }, { label: "直连", numeric: true }, { label: "采样窗口", numeric: true },
  ], report.trend.map((item) => [
    item.day, bytes(item.traffic.PROXY), bytes(item.traffic.DIRECT), item.sample_buckets,
  ]), "暂无采样数据。");

  ranking("#domain-rank", report.domains, "domain");
  ranking("#process-rank", report.processes, "process");
  ranking("#match-rank", report.match, "match");
  const limits = report.quadrants;
  text("#quad-threshold", limits.bytes_threshold === null
    ? "按中位数分隔 · 流量轴为对数"
    : `中位数 ${limits.days_threshold} 天 / ${bytes(limits.bytes_threshold)} · 流量轴为对数`);
  table("#quad-table", [
    { label: "主域名" }, { label: "象限" }, { label: "活跃天数", numeric: true },
    { label: "新增连接", numeric: true }, { label: "代理流量", numeric: true },
  ], limits.points.map((item) => [
    domainLink(item.name, "suffix"),
    `${item.high_frequency ? "高频" : "低频"}${item.high_volume ? "高耗" : "低耗"}`,
    item.days, item.connections, bytes(item.bytes),
  ]), "暂无代理流量。");

  const spikes = report.anomalies;
  text("#spike-period", `${spikes.day} · 可比较的历史日期 ${spikes.baseline_days} 天`);
  table("#spike-table", [
    { label: "进程" }, { label: "主域名" }, { label: "当日代理流量", numeric: true },
    { label: "历史日均", numeric: true }, { label: "前日", numeric: true }, { label: "Z 分数", numeric: true },
  ], spikes.items.map((item) => [
    item.process || "未知进程", domainLink(item.domain, "suffix"), bytes(item.bytes),
    bytes(item.baseline_bytes), item.previous_bytes === null ? "—" : bytes(item.previous_bytes), item.z_score,
  ]), spikes.baseline_days < 7 ? "需要至少 7 个采样覆盖相近的历史日期才能判断突发。" : "未发现符合阈值的流量突发。");
  drawCharts();
}

function canvas(selector) {
  const node = $(`${selector} canvas`);
  const width = node.clientWidth;
  const height = node.clientHeight;
  if (!width || !height) return null;
  const scale = window.devicePixelRatio || 1;
  node.width = Math.round(width * scale);
  node.height = Math.round(height * scale);
  const ctx = node.getContext("2d");
  ctx.scale(scale, scale);
  ctx.font = "10px ui-monospace, monospace";
  return { ctx, width, height };
}

function trendChart(items) {
  const area = canvas("#trend-chart");
  if (!area) return;
  const { ctx, width, height } = area;
  const left = 62;
  const top = 12;
  const bottom = height - 25;
  const span = width - left - 7;
  const gap = span / items.length;
  const max = Math.max(1, ...items.map(({ traffic }) => (traffic.PROXY || 0) + (traffic.DIRECT || 0)));
  const step = 1024 ** Math.floor(Math.log(max) / Math.log(1024));
  const ceiling = Math.max(4, Math.ceil(max / (step * 4)) * step * 4);
  const barWidth = Math.min(30, gap * .63);
  $("#trend-chart .chart-empty").hidden = items.some((item) => item.sample_buckets);
  for (let level = 0; level <= 4; level += 1) {
    const y = bottom - ((bottom - top) * level / 4);
    ctx.fillStyle = "#536462";
    ctx.textAlign = "right";
    ctx.fillText(bytes(Math.round(ceiling * level / 4)), left - 7, y + 3);
    ctx.fillStyle = "#e1e7df";
    ctx.fillRect(left, y, span, 1);
  }
  const every = Math.max(1, Math.ceil(items.length / Math.max(1, Math.floor(span / 55))));
  items.forEach((item, index) => {
    const x = left + gap * (index + .5);
    let y = bottom;
    for (const [route, color] of [["PROXY", "#187b60"], ["DIRECT", "#8caeb0"]]) {
      const bar = (item.traffic[route] || 0) / ceiling * (bottom - top);
      ctx.fillStyle = color;
      if (bar) ctx.fillRect(x - barWidth / 2, y - bar, barWidth, bar);
      y -= bar;
    }
    if (!item.sample_buckets) {
      ctx.fillStyle = "#c9d1c9";
      ctx.fillRect(x - barWidth / 2, bottom - 2, barWidth, 2);
    }
    if (index % every === 0 || index === items.length - 1) {
      if (index !== items.length - 1 && items.length - 1 - index < every) return;
      ctx.fillStyle = "#536462";
      ctx.textAlign = "center";
      ctx.fillText(item.day.slice(5), x, height - 8);
    }
  });
}

function quadChart(data) {
  const area = canvas("#quad-chart");
  if (!area) return;
  const { ctx, width, height } = area;
  $("#quad-chart .chart-empty").hidden = data.points.length > 0;
  const left = 62;
  const right = width - 19;
  const top = 12;
  const bottom = height - 27;
  const maxDays = Math.max(2, ...data.points.map((item) => item.days));
  const values = data.points.map((item) => Math.log10(Math.max(1, item.bytes)));
  const minimum = Math.max(0, Math.floor(Math.min(1 + (values[0] || 0), ...values)) - 1);
  const maximum = Math.max(minimum + 2, Math.ceil(Math.max(2, ...values)) + 1);
  const x = (days) => left + (right - left) * days / maxDays;
  const y = (amount) => bottom - (bottom - top) * (Math.log10(amount) - minimum) / (maximum - minimum);
  for (let tick = minimum; tick <= maximum; tick += Math.ceil((maximum - minimum) / 4)) {
    ctx.fillStyle = "#e1e7df";
    ctx.fillRect(left, y(10 ** tick), right - left, 1);
    ctx.fillStyle = "#536462";
    ctx.textAlign = "right";
    ctx.fillText(bytes(10 ** tick), left - 6, y(10 ** tick) + 3);
  }
  for (let tick = 0; tick <= maxDays; tick += Math.ceil(maxDays / 5)) {
    ctx.textAlign = "center";
    ctx.fillText(`${tick}天`, x(tick), height - 8);
  }
  if (data.days_threshold === null) return;
  ctx.fillStyle = "#e9f3e9";
  ctx.fillRect(x(data.days_threshold), top, right - x(data.days_threshold), y(data.bytes_threshold) - top);
  ctx.strokeStyle = "#738e81";
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(left, y(data.bytes_threshold));
  ctx.lineTo(right, y(data.bytes_threshold));
  ctx.moveTo(x(data.days_threshold), top);
  ctx.lineTo(x(data.days_threshold), bottom);
  ctx.stroke();
  ctx.setLineDash([]);
  for (const item of data.points) {
    ctx.beginPath();
    ctx.fillStyle = item.high_volume && item.high_frequency ? "#187b60bb" : "#6e8996bb";
    ctx.arc(x(item.days), y(item.bytes), 4.5, 0, 2 * Math.PI);
    ctx.fill();
  }
}

function drawCharts() {
  if (!state.report || state.view !== "overview") return;
  trendChart(state.report.trend);
  quadChart(state.report.quadrants);
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

function renderDecisions(decisions) {
  state.decisions = decisions;
  table("#decision-table", [
    { label: "匹配方式" }, { label: "目标" }, { label: "决策" }, { label: "备注" }, { label: "操作" },
  ], decisions.map((decision) => {
    const actions = el("div", "", "row-actions");
    actions.append(
      button("修改", () => openDecision(null, decision)),
      button("撤销规则", () => removeDecision(decision)),
    );
    return [kinds[decision.match_type], decision.match_value, tag(decision.action), decision.remark || "—", actions];
  }), "暂无审核记录。");
}

function openDecision(item, existing) {
  const targets = item ? [...item.targets] : [];
  if (item && existing && !targets.some((target) => sameTarget(target, existing))) {
    targets.unshift({ match_type: existing.match_type, match_value: existing.match_value });
  }
  editing = { targets, existing, locked: Boolean(existing && !item) };
  text("#dialog-title", existing ? "调整审核" : "添加审核");
  text("#dialog-context", item ? (item.context || `${item.host || "未知目标"} · ${item.process || "未知进程"} · ${bytes(item.bytes)}`) : "");
  $("#decision-error").hidden = true;
  $("#suggested").hidden = !item;
  $("#suggested-label").hidden = !item;
  $("#edit-target").hidden = Boolean(item);
  $("#match-type").disabled = editing.locked;
  $("#match-value").readOnly = editing.locked;
  $("#match-type").value = existing?.match_type || "DOMAIN";
  $("#match-value").value = existing?.match_value || "";
  $("#suggested").replaceChildren(...targets.map((option, index) => {
    const node = el("option", `${kinds[option.match_type]} · ${option.match_value}`);
    node.value = index;
    return node;
  }));
  $("#suggested").value = String(existing ? Math.max(0, targets.findIndex((target) => sameTarget(target, existing))) : 0);
  $("#action").value = existing?.action ?? item?.proposal?.action ?? "";
  $("#remark").value = existing?.remark ?? item?.proposal?.remark ?? "";
  $("#save").disabled = false;
  $("#review-dialog").showModal();
}

function sameTarget(a, b) {
  return a.match_type === b.match_type && a.match_value === b.match_value;
}

async function saveDecision(event) {
  event.preventDefault();
  const selected = editing.targets[Number($("#suggested").value)];
  const target = selected || { match_type: $("#match-type").value, match_value: $("#match-value").value.trim() };
  if (!target.match_value || !$("#action").value) {
    text("#decision-error", "请填写目标并选择审核结论。");
    $("#decision-error").hidden = false;
    return;
  }
  const body = { ...target, action: $("#action").value, remark: $("#remark").value.trim() };
  $("#save").disabled = true;
  try {
    const decisions = state.view === "rules" ? state.decisions : await api("/api/decisions");
    // The API normalizes manual input and returns the canonical key.
    const saved = await api("/api/decisions", "PUT", body);
    const previous = decisions.find((decision) => sameTarget(decision, saved));
    $("#review-dialog").close();
    toast("审核已保存", previous
      ? () => api("/api/decisions", "PUT", previous)
      : () => api(`/api/decisions/${saved.id}`, "DELETE"));
    await loadView();
  } catch (error) {
    text("#decision-error", error.message);
    $("#decision-error").hidden = false;
  } finally {
    $("#save").disabled = false;
  }
}

async function removeDecision(decision) {
  try {
    await api(`/api/decisions/${decision.id}`, "DELETE");
    toast("审核记录已撤销", () => api("/api/decisions", "PUT", decision), "恢复");
    await loadView();
  } catch (error) {
    showError(error);
  }
}

function toast(message, undo, label = "撤销") {
  clearTimeout(toastTimer);
  $("#toast").hidden = false;
  text("#toast-text", message);
  const control = $("#toast-undo");
  control.hidden = !undo;
  control.disabled = false;
  control.textContent = label;
  control.onclick = async () => {
    control.disabled = true;
    try {
      await undo();
      toast("操作已撤回");
      await loadView();
    } catch (error) {
      toast(error.message);
    }
  };
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, undo ? 10000 : 3500);
}

for (const tab of $$("[data-view]")) tab.addEventListener("click", () => setView(tab.dataset.view));
for (const tab of $$("[data-mode]")) tab.addEventListener("click", () => { setMode(tab.dataset.mode); loadView(); });
for (const link of $$("[data-go]")) {
  link.addEventListener("click", () => {
    $("#search").value = "";
    setView("review", link.dataset.go === "match" ? "match" : "all");
  });
}
$("#refresh").addEventListener("click", () => { refreshStatus(); loadView(); });
$("#days").addEventListener("change", () => { state.page = 1; loadView(); });
$("#overview-reviewed").addEventListener("change", loadView);
for (const selector of ["#review-reviewed", "#review-sort"]) {
  $(selector).addEventListener("change", () => { state.page = 1; loadView(); });
}
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.page = 1; loadView(); }, 180);
});
$("#prev").addEventListener("click", () => { state.page -= 1; loadView(); });
$("#next").addEventListener("click", () => { state.page += 1; loadView(); });
$("#add-rule").addEventListener("click", () => openDecision());
$("#decision-form").addEventListener("submit", saveDecision);
for (const id of ["#dialog-close", "#dialog-cancel"]) $(id).addEventListener("click", () => $("#review-dialog").close());
window.addEventListener("hashchange", followViewLocation);
new ResizeObserver(drawCharts).observe($("#trend-chart"));
new ResizeObserver(drawCharts).observe($("#quad-chart"));
initDomainView();
initAnalysis();
followViewLocation();
refreshStatus();
setInterval(refreshStatus, 5000);
