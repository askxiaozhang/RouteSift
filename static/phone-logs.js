"use strict";

const phoneRouteColors = { PROXY: "#187b60", DIRECT: "#8caeb0", REJECT: "#a66742", UNKNOWN: "#76817c" };
let phoneReport;
let phoneFilter = { host: "", scope: "suffix" };

function phoneLogTime(timestamp) {
  return trafficTime.format(timestamp * 1000);
}

async function loadPhoneLogs(id) {
  const params = new URLSearchParams(phoneFilter);
  const source = $("#phone-log-source").value;
  if (source) params.set("source", source);
  const report = await api(`/api/shadowrocket/connections?${params}`);
  if (id === state.requestId) renderPhoneLogs(report);
}

function renderPhoneLogs(report) {
  phoneReport = report;
  const { count, first, last, totals, hosts, host_count: hostCount, points, source, live_capture: live } = report;
  $("#phone-log-source").value = source;
  let status = live.error || "正在连接手机诊断地址";
  if (!live.configured) status = "尚未配置诊断地址。请在 Mac 的 RouteSift .env 中填写 SHADOWROCKET_LOG_URL。";
  if (live.connected) status = live.last_event_at
    ? `已连接 · 最近记录 ${phoneLogTime(live.last_event_at)}`
    : "已连接 · 等待手机产生分流记录";
  text("#phone-live-status", status);
  const routeCounts = Object.keys(phoneRouteColors).map(route => `${routes[route]} ${integer.format(totals[route])}`);
  text("#phone-log-summary", count
    ? `${phoneLogTime(first)} 至 ${phoneLogTime(last)} · ${timezone} · ${integer.format(count)} 条连接 · ${routeCounts.join(" · ")}`
    : (report.host ? `没有匹配 ${report.host} 的记录。` : "当前来源暂无连接记录。"));
  text("#phone-host-count", hostCount > hosts.length
    ? `${integer.format(hostCount)} 个目标，按连接次数显示前 ${hosts.length} 个`
    : `${integer.format(hostCount)} 个目标 · 按连接次数排序`);
  const countColumns = Object.keys(phoneRouteColors).map(route => ({ label: routes[route], numeric: true }));
  table("#phone-log-hosts", [
    { label: "域名 / IP" }, { label: "连接次数", numeric: true }, ...countColumns,
    { label: "最近连接" }, { label: "规则" },
  ], hosts.map(item => [
    button(item.host, () => {
      $("#phone-filter-host").value = item.host;
      $("#phone-filter-scope").value = "exact";
      phoneFilter = { host: item.host, scope: "exact" };
      loadView();
      $("#phone-filter-host").focus();
    }), item.connections, item.proxy, item.direct, item.reject, item.unknown, phoneLogTime(item.recent),
    button("添加规则", () => {
      const isIP = item.host.includes(":") || /^\d+(\.\d+){3}$/.test(item.host);
      const targets = [{ match_type: isIP ? "IP-CIDR" : "DOMAIN", match_value: item.host }];
      if (!isIP && item.host.includes(".")) targets.push({ match_type: "DOMAIN-SUFFIX", match_value: item.host });
      openDecision({ targets, context: `${item.host} · 电脑和手机共享直连与拦截规则`, proposal: { action: "DIRECT" } });
    }),
  ]), "暂无连接记录。");
  table("#phone-log-periods", [{ label: "时段起点" }, ...countColumns], points.map(point => [
    phoneLogTime(point.start), ...Object.keys(phoneRouteColors).map(route => point.routes[route] || 0),
  ]), "暂无分时记录。");
  $("#phone-log-chart .chart-empty").hidden = points.length > 0;
  drawPhoneLogChart();
}

function drawPhoneLogChart() {
  const area = canvas("#phone-log-chart");
  if (!area || !phoneReport?.points.length) return;
  const { ctx, width, height } = area;
  const points = phoneReport.points;
  const left = 35, right = width - 38, top = 10, bottom = height - 25;
  const peak = Math.max(...points.map(point => Object.values(point.routes).reduce((sum, count) => sum + count, 0)));
  const ceiling = Math.max(4, Math.ceil(peak / 4) * 4);
  const gap = (right - left) / points.length;
  const barWidth = Math.min(28, gap * .72);
  for (let level = 0; level <= 4; level += 1) {
    const y = bottom - (bottom - top) * level / 4;
    ctx.fillStyle = "#e1e7df";
    ctx.fillRect(left, y, right - left, 1);
    ctx.fillStyle = "#536462";
    ctx.textAlign = "right";
    ctx.fillText(String(ceiling * level / 4), left - 5, y + 3, left - 6);
  }
  const labelEvery = Math.max(1, Math.ceil(78 / gap));
  points.forEach((point, index) => {
    const x = left + gap * (index + .5);
    let y = bottom;
    for (const [route, color] of Object.entries(phoneRouteColors)) {
      const amount = (point.routes[route] || 0) / ceiling * (bottom - top);
      ctx.fillStyle = color;
      ctx.fillRect(x - barWidth / 2, y - amount, barWidth, amount);
      y -= amount;
    }
    if (index % labelEvery === 0) {
      ctx.fillStyle = "#536462";
      ctx.textAlign = "center";
      ctx.fillText(phoneLogTime(point.start), x, height - 7, 74);
    }
  });
}

async function uploadPhoneLogs(event) {
  event.preventDefault();
  const input = $("#phone-log-file");
  const [file] = input.files;
  if (!file) return;
  const upload = $("#phone-log-import");
  $("#phone-import-status").hidden = true;
  $("#phone-import-error").hidden = true;
  upload.disabled = true;
  input.disabled = true;
  try {
    const params = new URLSearchParams({ tz: $("#phone-log-zone").value.trim() });
    const result = await api(`/api/shadowrocket/connections?${params}`, "POST", file);
    text("#phone-import-status", `已导入 ${result.added} 条 · 重复 ${result.duplicates} 条 · 无法读取 ${result.skipped} 条`);
    $("#phone-import-status").hidden = false;
    input.value = "";
    $("#phone-log-source").value = "file";
    if (state.view === "phone-logs") await loadView();
  } catch (error) {
    text("#phone-import-error", error.message);
    $("#phone-import-error").hidden = false;
  } finally {
    upload.disabled = false;
    input.disabled = false;
  }
}

function initPhoneLogs() {
  $("#phone-log-zone").value = timezone;
  $("#phone-log-source").value = "";
  $("#phone-log-source").addEventListener("change", loadView);
  $("#phone-import-form").addEventListener("submit", uploadPhoneLogs);
  $("#phone-filter-form").addEventListener("submit", event => {
    event.preventDefault();
    phoneFilter = { host: $("#phone-filter-host").value.trim(), scope: $("#phone-filter-scope").value };
    loadView();
  });
  $("#phone-filter-form").addEventListener("reset", () => {
    const source = $("#phone-log-source").value;
    phoneFilter = { host: "", scope: "suffix" };
    requestAnimationFrame(() => {
      $("#phone-log-source").value = source;
      loadView();
    });
  });
  setInterval(() => {
    if (state.view === "phone-logs" && !document.hidden && $("#phone-log-source").value === "live"
        && !$("#refresh").disabled) loadView();
  }, 5000);
  new ResizeObserver(drawPhoneLogChart).observe($("#phone-log-chart"));
}
