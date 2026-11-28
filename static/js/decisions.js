import { api } from "./api.js";
import { state } from "./state.js";
import { $, bytes, el, kinds, showError, text, toast } from "./ui.js";

let editing = null;
let reloadView;

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
    toast(saved.desktop_synced ? "规则已保存，FlClash 已同步" : "规则已保存", async () => {
      if (previous) await api("/api/decisions", "PUT", previous);
      else await api(`/api/decisions/${saved.id}`, "DELETE");
      await reloadView();
    });
    await reloadView();
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
    toast("审核记录已撤销", async () => {
      await api("/api/decisions", "PUT", decision);
      await reloadView();
    }, "恢复");
    await reloadView();
  } catch (error) {
    showError(error);
  }
}

function initDecisions(reload) {
  reloadView = reload;
  $("#decision-form").addEventListener("submit", saveDecision);
  for (const id of ["#dialog-close", "#dialog-cancel"]) {
    $(id).addEventListener("click", () => $("#review-dialog").close());
  }
}

export { initDecisions, openDecision, sameTarget, removeDecision };
