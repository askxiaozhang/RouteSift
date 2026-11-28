import { state } from "./state.js";
import { $, bytes, el, hourClock, integer, percent } from "./ui.js";

const trendView = { plot: null, point: null };

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
  const hourly = state.report.trend_grain === "hour";
  const left = 62;
  const top = 12;
  const bottom = height - 25;
  const span = width - left - 7;
  const gap = span / items.length;
  const max = Math.max(1, ...items.map(({ traffic }) => (traffic.PROXY || 0) + (traffic.DIRECT || 0)));
  const step = 1024 ** Math.floor(Math.log(max) / Math.log(1024));
  const ceiling = Math.max(4, Math.ceil(max / (step * 4)) * step * 4);
  const barWidth = Math.min(30, gap * .63);
  trendView.plot = { left, span, gap };
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
  const active = trendView.point === null ? null : items[trendView.point];
  items.forEach((item, index) => {
    const x = left + gap * (index + .5);
    if (item === active) {
      ctx.fillStyle = "#eef3ec";
      ctx.fillRect(left + gap * index, top, gap, bottom - top);
    }
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
    if (index % every === 0 || (!hourly && index === items.length - 1)) {
      ctx.fillStyle = "#536462";
      ctx.textAlign = hourly && index === 0 ? "left" : "center";
      ctx.fillText(hourly ? hourClock.format(item.start_ts * 1000) : item.day.slice(5), hourly ? left + gap * index : x, height - 8);
    }
  });
  if (hourly) {
    ctx.fillStyle = "#536462";
    ctx.textAlign = "right";
    ctx.fillText("24:00", left + span, height - 8);
  }
  if (active) {
    const mark = left + (trendView.point + .5) * gap;
    ctx.fillStyle = "#335d50";
    ctx.fillRect(mark, top, 1, bottom - top);
    const tooltip = $("#trend-tooltip");
    const position = mark + tooltip.offsetWidth + 12 <= width ? mark + 10 : mark - tooltip.offsetWidth - 10;
    tooltip.style.left = Math.max(0, position) + "px";
  }
}

function dayBreakdown(items) {
  const total = (route) => items.reduce((sum, item) => sum + (item.traffic[route] || 0), 0);
  const proxy = total("PROXY");
  const direct = total("DIRECT");
  return proxy + direct ? proxy / (proxy + direct) : null;
}

function selectTrendPoint(index) {
  const items = state.report?.trend;
  if (!items?.length) return;
  const bounded = Math.max(0, Math.min(index, items.length - 1));
  if (bounded === trendView.point) return;
  trendView.point = bounded;
  const item = items[bounded];
  const hourly = state.report.trend_grain === "hour";
  const label = hourly
    ? `${item.day} ${hourClock.format(item.start_ts * 1000)}–${bounded === items.length - 1 ? "24:00" : hourClock.format(item.end_ts * 1000)}`
    : item.day;
  const proxy = item.traffic.PROXY || 0;
  const direct = item.traffic.DIRECT || 0;
  const share = dayBreakdown([item]);
  const proxySwatch = el("span", `代理 ${bytes(proxy)} · ${share === null ? "—" : percent(share)}`, "swatch proxy");
  const directSwatch = el("span", `直连 ${bytes(direct)}`, "swatch direct");
  const tooltip = $("#trend-tooltip");
  tooltip.replaceChildren(
    el("strong", label),
    proxySwatch,
    directSwatch,
    el("span", `合计 ${bytes(proxy + direct)}`),
    el("span", item.sample_buckets ? `${integer.format(item.sample_buckets)} 个采样窗口` : hourly ? "该小时无采样" : "该日无采样"),
  );
  tooltip.hidden = false;
  trendChart(items);
}

function clearTrendPoint() {
  if (trendView.point === null) return;
  trendView.point = null;
  $("#trend-tooltip").hidden = true;
  if (state.report) trendChart(state.report.trend);
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

function initCharts() {
  const trendChartArea = $("#trend-chart");
  const selectTrendByPointer = (event) => {
    const plot = trendView.plot;
    const count = state.report?.trend.length;
    if (!plot || !count) return;
    const relative = event.clientX - trendChartArea.getBoundingClientRect().left - plot.left;
    if (relative < 0 || relative > plot.span) clearTrendPoint();
    else selectTrendPoint(Math.min(count - 1, Math.floor(relative / plot.gap)));
  };
  trendChartArea.addEventListener("pointermove", selectTrendByPointer);
  trendChartArea.addEventListener("pointerdown", selectTrendByPointer);
  trendChartArea.addEventListener("pointerleave", clearTrendPoint);
  trendChartArea.addEventListener("blur", clearTrendPoint);
  trendChartArea.addEventListener("keydown", (event) => {
    const count = state.report?.trend.length;
    if (!count) return;
    const current = trendView.point ?? count - 1;
    const next = { ArrowLeft: current - 1, ArrowRight: trendView.point === null ? 0 : current + 1, Home: 0, End: count - 1 };
    if (Object.hasOwn(next, event.key)) {
      event.preventDefault();
      selectTrendPoint(next[event.key]);
    }
    if (event.key === "Escape") clearTrendPoint();
  });
  new ResizeObserver(drawCharts).observe($("#trend-chart"));
  new ResizeObserver(drawCharts).observe($("#quad-chart"));
}

export { canvas, drawCharts, clearTrendPoint, initCharts };
