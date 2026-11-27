const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const text = (selector, value) => { $(selector).textContent = value; };
const integer = new Intl.NumberFormat("zh-CN");
const clock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Singapore";
const hourClock = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone });
const trafficTime = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone,
});
const routes = { PROXY: "代理", DIRECT: "直连", REJECT: "拦截", UNKNOWN: "去向未知" };
const kinds = { DOMAIN: "精确域名", "DOMAIN-SUFFIX": "域名后缀", "PROCESS-NAME": "进程名", "IP-CIDR": "IP / CIDR" };

let toastTimer;

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

function selectedDays() {
  return Number($("#days input:checked").value);
}

function showError(error) {
  text("#load-error", error.message);
  $("#load-error").hidden = false;
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
    } catch (error) {
      toast(error.message);
    }
  };
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, undo ? 10000 : 3500);
}

export { $, $$, text, integer, clock, timezone, hourClock, trafficTime, routes, kinds, el, button, bytes, percent, tag, table, selectedDays, showError, toast };
