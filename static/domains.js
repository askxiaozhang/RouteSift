"use strict";

const domainView = { result: null, point: null, plot: null, submitted: "", suggestionId: 0 };
const domainGrains = { "5m": "5 分钟", hour: "1 小时", day: "1 天" };
const domainColors = { PROXY: "#187b60", DIRECT: "#8caeb0", REJECT: "#a66742", UNKNOWN: "#76817c" };
const periodFormats = new Map();
let suggestionTimer;

function localDate(date) {
  return date.toLocaleDateString("sv-SE", { timeZone: timezone });
}

function defaultDomainDates(days = 7) {
  const today = new Date();
  const first = new Date(today);
  first.setDate(today.getDate() - days + 1);
  return {
    start: localDate(first),
    end: localDate(today),
    grain: days === 1 ? "5m" : days <= 7 ? "hour" : "day",
  };
}

function domainFormValues() {
  return {
    host: $("#domain-host").value.trim(),
    scope: $("#domain-scope").value,
    start: $("#domain-start").value,
    end: $("#domain-end").value,
    grain: $("#domain-grain").value,
  };
}

function domainHash(values = domainFormValues()) {
  if (!values.host) return "#domains";
  return "#domains?" + new URLSearchParams(values);
}

function adoptDomainHash(queryString) {
  const params = new URLSearchParams(queryString);
  const defaults = defaultDomainDates();
  const datePattern = /^\d{4}-\d{2}-\d{2}$/;
  $("#domain-host").value = (params.get("host") || "").slice(0, 253);
  $("#domain-scope").value = params.get("scope") === "suffix" ? "suffix" : "exact";
  const grain = params.get("grain");
  $("#domain-grain").value = Object.hasOwn(domainGrains, grain) ? grain : defaults.grain;
  for (const key of ["start", "end"]) {
    const input = $("#domain-" + key);
    input.max = defaults.end;
    const chosen = params.get(key);
    input.value = datePattern.test(chosen) ? chosen : defaults[key];
    if (!input.value) input.value = defaults[key];
  }
}

function domainLink(host, scope = "exact") {
  if (!host) return "未知目标";
  const values = state.view === "domains" && domainView.result
    ? domainView.result : defaultDomainDates(Number($("#days").value));
  const link = el("a", host, "domain-link");
  link.href = domainHash({
    host, scope: host.includes(":") || /^\d+(?:\.\d+){3}$/.test(host) ? "exact" : scope,
    start: values.start, end: values.end, grain: values.grain,
  });
  return link;
}

async function loadDomainSuggestions(query = "", showOnStart = false) {
  const id = ++domainView.suggestionId;
  try {
    const params = new URLSearchParams({ q: query, tz: timezone });
    const hosts = await api("/api/domains?" + params);
    if (id !== domainView.suggestionId) return;
    $("#observed-domains").replaceChildren(...hosts.map(({ host, bytes: traffic }) => {
      const option = new Option(bytes(traffic), host);
      return option;
    }));
    if (showOnStart) {
      const links = hosts.length ? hosts.slice(0, 6).map(({ host }) => domainLink(host)) : [domainLink("binance.com")];
      $("#domain-suggestions").replaceChildren(...links);
    }
  } catch {
    // Domain search also works without autocomplete.
  }
}

async function loadDomainView(id) {
  const values = domainFormValues();
  if (!values.host) {
    $("#domain-results").hidden = true;
    $("#domain-start-here").hidden = false;
    text("#domain-start-message", "输入域名，或从概览和审核队列中点选一个目标。");
    await loadDomainSuggestions("", true);
    return;
  }

  const filters = new URLSearchParams({ ...values, tz: timezone });
  if (!domainView.result || domainView.submitted !== String(filters)) {
    domainView.result = null;
    $("#domain-results").hidden = true;
    $("#domain-start-here").hidden = false;
    text("#domain-start-message", "正在查询 " + values.host + " 的流量…");
    $("#domain-suggestions").replaceChildren();
  }
  try {
    const result = await api("/api/domain-detail?" + filters);
    if (id !== state.requestId) return;
    domainView.submitted = String(filters);
    renderDomain(result);
  } catch (error) {
    if (id !== state.requestId) return;
    if (!domainView.result) text("#domain-start-message", "调整查询条件后可以重新查看。");
    throw error;
  }
}

function routeBytes(counts) {
  return counts ? counts.up + counts.down : 0;
}

function domainPeriod(point, grain, offset = false) {
  const key = `${grain}:${offset}`;
  if (!periodFormats.has(key)) {
    const options = { month: "2-digit", day: "2-digit", timeZone: timezone };
    if (grain !== "day") Object.assign(options, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    if (offset && grain !== "day") options.timeZoneName = "shortOffset";
    periodFormats.set(key, new Intl.DateTimeFormat("zh-CN", options));
  }
  return periodFormats.get(key).format(point.start_ts * 1000);
}

function renderDomain(data) {
  domainView.result = data;
  domainView.point = null;
  $("#domain-tooltip").hidden = true;
  $("#domain-start-here").hidden = true;
  $("#domain-results").hidden = false;
  const proxy = routeBytes(data.totals.PROXY);
  const direct = routeBytes(data.totals.DIRECT);
  const connections = Object.values(data.totals).reduce((sum, route) => sum + route.connections, 0);
  const observed = data.series.filter((point) => point.sample_buckets > 0).length;
  const scope = data.scope === "exact" ? "仅此目标" : "含子域名";
  text("#domain-name", data.host);
  text("#domain-range", `${scope} · ${data.start} 至 ${data.end} · ${timezone}`);
  text("#domain-share", proxy + direct ? percent(proxy / (proxy + direct)) : "—");
  text("#domain-share-caption", "按代理与直连流量计算");
  text("#domain-connections", integer.format(connections));
  text("#domain-coverage", `采样覆盖 ${observed} / ${data.series.length} 个时段`);
  for (const [key, route] of [["proxy", "PROXY"], ["direct", "DIRECT"]]) {
    const counts = data.totals[route];
    text("#domain-" + key, bytes(routeBytes(counts)));
    text("#domain-" + key + "-io", `↑ ${bytes(counts?.up)} / ↓ ${bytes(counts?.down)}`);
  }
  const noTraffic = !Object.values(data.totals).some((counts) => routeBytes(counts));
  const empty = !observed && !connections ? "这段时间暂无采样" : "这段时间未观测到此目标的流量";
  text("#domain-chart .chart-empty", empty);
  $("#domain-chart .chart-empty").hidden = !noTraffic;
  const reviewStatus = $("#domain-review-status");
  reviewStatus.replaceChildren(...data.reviews.map((review) => {
    const marker = tag(review.action);
    marker.title = kinds[review.match_type] + " · " + review.match_value;
    return marker;
  }));
  if (!data.reviews.length) reviewStatus.append(el("span", "未审核", "tag"));
  $("#domain-audit").disabled = !data.targets.length;
  text("#domain-policy-summary", data.proxy_match_bytes
    ? `有 ${bytes(data.proxy_match_bytes)} 代理流量命中 MATCH 兜底。可查看具体目标后再决定是否调整。`
    : "按命中规则、策略组和实际节点汇总，显示流量最多的 50 项。");
  table("#domain-policy-table", [
    { label: "去向" }, { label: "命中规则" }, { label: "策略组" }, { label: "实际节点" },
    { label: "上传", numeric: true }, { label: "下载", numeric: true }, { label: "连接", numeric: true },
  ], data.policies.map((policy) => {
    const rule = el("span");
    rule.append(tag(policy.rule_type || "未知"));
    if (policy.rule_payload) rule.append(" ", policy.rule_payload);
    return [
      routes[policy.route_type], rule, policy.proxy_group || "—", policy.proxy_node || "—",
      bytes(policy.up), bytes(policy.down), integer.format(policy.connections),
    ];
  }), "该时段暂无分流记录。");

  $("#domain-hosts-panel").hidden = data.scope !== "suffix";
  if (data.scope === "suffix") {
    table("#domain-host-table", [
      { label: "完整域名" }, { label: "总流量", numeric: true },
      { label: "代理", numeric: true }, { label: "直连", numeric: true },
    ], data.hosts.map((host) => [
      domainLink(host.host), bytes(host.bytes), bytes(host.proxy_bytes), bytes(host.direct_bytes),
    ]), "该时段暂无子域名记录。");
  }
  const visibleTimes = data.series.filter((point) => point.sample_buckets || Object.keys(point.traffic).length);
  table("#domain-time-table", [
    { label: "开始时间" }, { label: "代理", numeric: true }, { label: "直连", numeric: true },
    { label: "拦截", numeric: true }, { label: "去向未知", numeric: true },
    { label: "上传", numeric: true }, { label: "下载", numeric: true },
  ], visibleTimes.map((point) => {
    const counts = Object.values(point.traffic);
    return [
      domainPeriod(point, data.grain, true),
      ...Object.keys(routes).map((route) => bytes(routeBytes(point.traffic[route]))),
      bytes(counts.reduce((sum, route) => sum + route.up, 0)),
      bytes(counts.reduce((sum, route) => sum + route.down, 0)),
    ];
  }), "该时段暂无可显示的采样记录。");
  drawDomainTimeline();
}

function drawDomainTimeline() {
  const data = domainView.result;
  if (!data || !data.series.length || state.view !== "domains") return;
  const area = canvas("#domain-chart");
  if (!area) return;
  const { ctx, width, height } = area;
  const left = 64;
  const top = 10;
  const bottom = height - 27;
  const span = width - left - 12;
  const gap = span / data.series.length;
  const max = Math.max(1, ...data.series.map(({ traffic }) => Object.values(traffic).reduce(
    (total, route) => total + routeBytes(route), 0,
  )));
  const unit = 2 ** Math.floor(Math.log2(Math.max(1, max / 4)));
  const ceiling = 4 * Math.ceil(max / (4 * unit)) * unit;
  domainView.plot = { left, span, gap };

  for (let level = 0; level <= 4; level += 1) {
    const y = bottom - ((bottom - top) * level / 4);
    ctx.fillStyle = "#e1e7df";
    ctx.fillRect(left, y, span, 1);
    ctx.fillStyle = "#536462";
    ctx.textAlign = "right";
    ctx.fillText(bytes(ceiling * level / 4), left - 6, y + 3);
  }

  const every = Math.max(1, Math.ceil(data.series.length / Math.max(1, Math.floor(span / 95))));
  data.series.forEach((point, index) => {
    const x = left + gap * index;
    if (!point.sample_buckets) {
      ctx.fillStyle = "#e3e8e066";
      ctx.fillRect(x, top, gap, bottom - top);
    }
    const barWidth = Math.min(38, gap >= 3 ? gap * .76 : gap);
    let y = bottom;
    for (const [route, color] of Object.entries(domainColors)) {
      const amount = routeBytes(point.traffic[route]);
      const bar = amount / ceiling * (bottom - top);
      ctx.fillStyle = color;
      if (bar) ctx.fillRect(x + (gap - barWidth) / 2, y - bar, barWidth, bar);
      y -= bar;
    }
    if (index % every === Math.floor(every / 2)) {
      ctx.fillStyle = "#536462";
      ctx.textAlign = "center";
      ctx.fillText(domainPeriod(point, data.grain), Math.min(width - 42, x + gap / 2), height - 8);
    }
  });

  if (domainView.point !== null) {
    const mark = left + (domainView.point + .5) * gap;
    ctx.fillStyle = "#335d50";
    ctx.fillRect(mark, top, 1, bottom - top);
    const tooltip = $("#domain-tooltip");
    const position = mark + tooltip.offsetWidth + 12 <= width ? mark + 10 : mark - tooltip.offsetWidth - 10;
    tooltip.style.left = Math.max(0, position) + "px";
  }
}

function selectDomainPoint(index) {
  const data = domainView.result;
  if (!data?.series.length) return;
  const bounded = Math.max(0, Math.min(index, data.series.length - 1));
  if (bounded === domainView.point) return;
  domainView.point = bounded;
  const point = data.series[bounded];
  const counts = Object.values(point.traffic);
  const tooltip = $("#domain-tooltip");
  tooltip.replaceChildren(
    el("strong", domainPeriod(point, data.grain, true)),
    el("span", `代理 ${bytes(routeBytes(point.traffic.PROXY))} · 直连 ${bytes(routeBytes(point.traffic.DIRECT))}`),
    el("span", `上传 ${bytes(counts.reduce((sum, route) => sum + route.up, 0))} · 下载 ${bytes(counts.reduce((sum, route) => sum + route.down, 0))}`),
    el("span", point.sample_buckets ? `${point.sample_buckets} 个采样窗口` : "该时段无采样"),
  );
  tooltip.hidden = false;
  drawDomainTimeline();
}

function clearDomainPoint() {
  if (domainView.point === null) return;
  domainView.point = null;
  $("#domain-tooltip").hidden = true;
  drawDomainTimeline();
}

function submitDomainForm() {
  const input = $("#domain-host");
  if (/^https?:\/\//i.test(input.value.trim())) {
    try { input.value = new URL(input.value.trim()).hostname.replace(/^\[|\]$/g, ""); }
    catch { /* The API will validate the target. */ }
  }
  updateViewLocation();
  loadView();
}

function initDomainView() {
  adoptDomainHash("");
  $("#domain-form").addEventListener("submit", (event) => { event.preventDefault(); submitDomainForm(); });
  for (const preset of $$("[data-domain-days]")) {
    preset.addEventListener("click", () => {
      for (const [key, value] of Object.entries(defaultDomainDates(Number(preset.dataset.domainDays)))) {
        $("#domain-" + key).value = value;
      }
      if ($("#domain-host").value.trim()) submitDomainForm();
    });
  }
  $("#domain-host").addEventListener("input", () => {
    clearTimeout(suggestionTimer);
    suggestionTimer = setTimeout(() => loadDomainSuggestions($("#domain-host").value.trim()), 230);
  });
  $("#domain-audit").addEventListener("click", () => {
    const data = domainView.result;
    if (!data) return;
    const current = data.reviews.find((review) => data.targets.some((target) => sameTarget(target, review)));
    const total = Object.values(data.totals).reduce((sum, route) => sum + routeBytes(route), 0);
    openDecision({ targets: data.targets, context: `${data.host} · 所选时段 ${bytes(total)}` }, current);
  });
  const chart = $("#domain-chart");
  const selectByPointer = (event) => {
    const plot = domainView.plot;
    if (!plot) return;
    const relative = event.clientX - chart.getBoundingClientRect().left - plot.left;
    if (relative < 0 || relative > plot.span) clearDomainPoint();
    else selectDomainPoint(Math.floor(relative / plot.gap));
  };
  chart.addEventListener("pointermove", selectByPointer);
  chart.addEventListener("pointerdown", selectByPointer);
  chart.addEventListener("pointerleave", clearDomainPoint);
  chart.addEventListener("blur", clearDomainPoint);
  chart.addEventListener("keydown", (event) => {
    const count = domainView.result?.series.length;
    if (!count) return;
    const current = domainView.point ?? 0;
    const next = { ArrowLeft: current - 1, ArrowRight: domainView.point === null ? 0 : current + 1, Home: 0, End: count - 1 };
    if (Object.hasOwn(next, event.key)) {
      event.preventDefault();
      selectDomainPoint(next[event.key]);
    }
    if (event.key === "Escape") clearDomainPoint();
  });
  new ResizeObserver(drawDomainTimeline).observe(chart);
}
