import { drawCharts } from "./charts.js";
import { domainLink } from "./domains.js";
import { state } from "./state.js";
import { $, bytes, el, integer, percent, routes, table, text, timezone } from "./ui.js";

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
  const hourly = report.trend_grain === "hour";
  const covered = new Set(report.trend.filter((item) => item.sample_buckets).map((item) => item.day)).size;
  text("#coverage", `覆盖 ${covered} / ${report.days.length} 个自然日`);
  text("#trend-title", hourly ? "今日流量" : "每日流量");
  text("#trend-range", hourly
    ? `${report.days[0]} · 00:00–24:00 · ${timezone}`
    : `${report.days[0]} 至 ${report.days.at(-1)} · ${timezone}`);
  $("#trend-chart").setAttribute("aria-label", hourly
    ? "今日流量图，鼠标悬停或用左右方向键查看每小时明细"
    : "每日流量图，鼠标悬停或用左右方向键查看当日明细");
  breakdown(report.totals);

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

export { renderOverview };
