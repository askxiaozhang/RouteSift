import { api } from "./api.js";
import { openDecision, removeDecision } from "./decisions.js";
import { state } from "./state.js";
import { $, button, el, kinds, table, tag, text } from "./ui.js";

let shadowrocketConfig = null;
let shadowrocketBusy = false;

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

function syncShadowrocketButtons() {
  const changed = shadowrocketConfig && $("#shadowrocket-rules").value !== shadowrocketConfig.rules;
  $("#shadowrocket-fields").disabled = shadowrocketBusy || !shadowrocketConfig;
  $("#shadowrocket-save").disabled = !changed;
}

async function updateShadowrocket(action) {
  if (shadowrocketBusy) return;
  shadowrocketBusy = true;
  $("#shadowrocket-error").hidden = true;
  syncShadowrocketButtons();
  try {
    await action();
  } catch (error) {
    text("#shadowrocket-error", error.message);
    $("#shadowrocket-error").hidden = false;
  } finally {
    shadowrocketBusy = false;
    syncShadowrocketButtons();
  }
}

function loadShadowrocket() {
  if (!$("#shadowrocket").open || shadowrocketConfig) return;
  updateShadowrocket(async () => {
    shadowrocketConfig = await api("/api/shadowrocket");
    $("#shadowrocket-rules").value = shadowrocketConfig.rules;
    $("#shadowrocket-url").value = shadowrocketConfig.subscription_url || "";
    $("#shadowrocket-remote").hidden = !shadowrocketConfig.subscription_url;
  });
}

function saveShadowrocket(event) {
  event.preventDefault();
  updateShadowrocket(async () => {
    shadowrocketConfig = await api("/api/shadowrocket", "PUT", {
      rules: $("#shadowrocket-rules").value, version: shadowrocketConfig.version,
    });
    $("#shadowrocket-rules").value = shadowrocketConfig.rules;
    text("#shadowrocket-status", "已保存，手机下次更新模块时会读取新规则。");
  });
}

function initRules() {
  $("#add-rule").addEventListener("click", () => openDecision());
  $("#shadowrocket").addEventListener("toggle", loadShadowrocket);
  $("#shadowrocket-rules").addEventListener("input", () => {
    text("#shadowrocket-status", "");
    $("#shadowrocket-error").hidden = true;
    syncShadowrocketButtons();
  });
  $("#shadowrocket-form").addEventListener("submit", saveShadowrocket);
  $("#shadowrocket-url").addEventListener("focus", event => event.target.select());
}

export { initRules, renderDecisions };
