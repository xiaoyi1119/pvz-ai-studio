const $ = (id) => document.getElementById(id);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const api = async (path, options = {}) => {
  const response = await fetch(path, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
};
const post = (path, body = {}) => api(path, {method:"POST", body:JSON.stringify(body)});
let toastTimer, activeFilter = "all", lastEvents = [], streamRetryTimer, lastStreamResetAt = 0;
const metricHistory = {sun:[], wave:[], plant:[], zombie:[]};
let pendingAvatarData = "";

function resetStream(delay = 0) {
  clearTimeout(streamRetryTimer);
  streamRetryTimer = setTimeout(() => {
    lastStreamResetAt = Date.now();
    $("gameFeed").src = `/api/capture/stream?t=${lastStreamResetAt}`;
  }, delay);
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("show"), 2200);
}

function closeSettingsWithNotice(message) {
  if ($("settingsDialog").open) $("settingsDialog").close();
  requestAnimationFrame(() => toast(message));
}

function settingsFeedback(message = "", kind = "error") {
  const feedback = $("settingsFeedback");
  feedback.textContent = message;
  feedback.className = message ? `settings-feedback ${kind}` : "settings-feedback hidden";
}

function profileFeedback(message = "", kind = "error") {
  const feedback = $("profileFeedback");
  feedback.textContent = message;
  feedback.className = message ? `settings-feedback ${kind}` : "settings-feedback hidden";
}

function applyProfile(profile) {
  $("channelName").textContent = profile.name;
  $("channelAvatar").src = profile.avatar_url;
}

async function loadProfile() {
  try {
    applyProfile(await api("/api/profile"));
  } catch (error) {
    console.warn(error);
  }
}

function chip(el, on, yes, no) {
  el.innerHTML = `<i></i>${escapeHtml(on ? yes : no)}`;
  el.classList.toggle("off", !on);
}

function gameClock(value = 0) {
  const seconds = Math.max(0, Math.floor(value / 100));
  return `${String(Math.floor(seconds / 60)).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
}

function applyTheme(theme) {
  const selected = theme === "green" ? "green" : "bili";
  document.documentElement.dataset.theme = selected;
  localStorage.setItem("pvz-dashboard-theme", selected);
  document.querySelectorAll(".theme-button").forEach(button => button.classList.toggle("active", button.dataset.theme === selected));
}

function updateSparkline(name, value) {
  const history = metricHistory[name];
  history.push(Number(value) || 0);
  if (history.length > 18) history.shift();
  const values = history.length === 1 ? [history[0], history[0]] : history;
  const max = Math.max(1, ...values);
  const points = values.map((item, index) => {
    const x = values.length === 1 ? 0 : index / (values.length - 1) * 100;
    const y = 27 - item / max * 23;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  $(`${name}Spark`).setAttribute("points", points);
}

function renderCards(cards = []) {
  const el = $("cards");
  if (!el) return;
  if (!cards.length) {
    el.className = "seed-list empty";
    el.textContent = "进入关卡后读取卡片";
    return;
  }
  el.className = "seed-list";
  el.innerHTML = cards.map(card => {
    const progress = Math.round((card.cooldown_progress ?? (card.ready ? 1 : 0)) * 100);
    return `<article class="seed-card ${card.ready ? "ready" : ""}">
      <div class="seed-name"><span>${escapeHtml(card.name)}</span><span class="seed-index">#${card.index}</span></div>
      <div class="seed-meta"><span class="sun-cost">☀ ${card.cost}</span><span>${card.ready ? "可种植" : `${card.cooldown_seconds ?? card.cooldown ?? 0}s`}</span></div>
      <div class="cooldown-track"><i style="width:${progress}%"></i></div>
    </article>`;
  }).join("");
}

function renderRadar(state = {}) {
  const rowCount = Math.max(1, Math.min(6, Number(state.board_rows) || (["泳池", "雾夜"].includes(state.scene) ? 6 : 5)));
  const lanes = (state.lane_threats || []).filter(lane => lane.row >= 0 && lane.row < rowCount);
  const mowerRows = new Set((state.lawn_mowers || []).filter(mower => mower.alive).map(mower => mower.row));
  const zombies = state.counts?.zombies ?? state.zombies?.length ?? 0;
  $("zombieCount").textContent = zombies;
  const danger = [...lanes].sort((a,b) => (b.count || 0) - (a.count || 0) || (b.total_hp || 0) - (a.total_hp || 0))[0];
  $("dangerLane").textContent = danger?.count ? `${danger.row + 1} 行` : "—";
  const maxScore = Math.max(1, ...lanes.map(lane => (lane.count || 0) * 300 + (lane.total_hp || 0)));
  $("laneThreatList").innerHTML = Array.from({length: rowCount}, (_, index) => {
    const lane = lanes.find(item => item.row === index) || {count:0, total_hp:0};
    const score = (lane.count || 0) * 300 + (lane.total_hp || 0);
    const pressure = lane.count ? Math.max(8, Math.round(score / maxScore * 100)) : 0;
    const level = pressure > 72 ? "high" : pressure > 35 ? "medium" : lane.count ? "low" : "idle";
    const risk = level === "high" ? "高" : level === "medium" ? "中" : level === "low" ? "低" : "安全";
    const hasMower = mowerRows.has(index);
    return `<div class="lane-pressure ${lane.count ? level : "idle"}">
      <span class="lane-label">${String(index + 1).padStart(2,"0")}</span>
      <span class="lane-mower ${hasMower ? "" : "missing"}" title="${hasMower ? "割草机可用" : "割草机已失效"}"><i></i></span>
      <span class="pressure-wrap"><span class="pressure-track"><i style="width:${pressure}%"></i></span><small>${lane.count || 0} 只 · ${lane.total_hp || 0} HP</small></span>
      <span class="lane-risk">${risk}</span>
    </div>`;
  }).join("");
}

function renderZombies(state = {}) {
  const lanes = (state.lane_threats || []).filter(lane => lane.count > 0);
  $("zombieBadge").textContent = state.counts?.zombies ?? state.zombies?.length ?? 0;
  const el = $("zombieList");
  if (!lanes.length) {
    el.className = "zombie-list empty";
    el.textContent = "暂无僵尸数据";
    return;
  }
  const maxHp = Math.max(1, ...lanes.map(lane => lane.total_hp || 0));
  el.className = "zombie-list";
  el.innerHTML = lanes.map(lane => `<div class="zombie-row">
    <span class="lane-number">第${lane.row + 1}行</span>
    <span class="zombie-main"><b>${escapeHtml((lane.types || []).join("、") || "未知僵尸")}</b><small>最近 ${lane.nearest_col ?? "—"} 列 · 啃食中 ${lane.eating_count || 0}</small></span>
    <span class="zombie-hp-wrap"><b class="zombie-hp">${lane.total_hp} HP</b><span class="hp-track"><i style="width:${Math.round((lane.total_hp || 0) / maxHp * 100)}%"></i></span></span>
  </div>`).join("");
}

function describeEvent(event) {
  const labels = {
    requested:"请求动作", completed:"动作完成", failed:"动作失败", placement_precheck:"种植检查",
    waiting_resources:"等待资源", drag_not_accepted_fallback:"拖种未识别，已回退", drag_transport_failed:"拖动消息失败",
    started:"Agent 已启动", stopped:"Agent 已停止", iteration_started:"开始推理", iteration_completed:"完成推理",
    item_seen:"发现阳光", item_collected:"已拾取阳光", connected:"游戏已连接", disconnected:"游戏已断开",
  };
  const base = labels[event.event] || event.message || event.event || "系统事件";
  const detail = event.error || event.plant || event.item || event.action || event.goal || "";
  return detail ? `${base} · ${detail}` : base;
}

function renderLatestDecision(events = []) {
  if (!$("latestDecision") || !$("latestDecisionDetail")) return;
  const latest = [...events].reverse().find(event => ["action", "agent"].includes(event.category || event.kind));
  if (!latest) return;
  $("latestDecision").textContent = describeEvent(latest);
  const time = (latest.timestamp || latest.time || "").split("T")[1]?.slice(0,8) || "实时";
  $("latestDecisionDetail").textContent = `${(latest.category || latest.kind || "SYSTEM").toUpperCase()} · ${time}`;
}

function renderEvents(events = lastEvents) {
  const filtered = events.filter(event => ["agent", "action", "model", "error", "connection"].includes(event.category || event.kind));
  $("eventCount").textContent = filtered.length;
  const el = $("events");
  if (!filtered.length) {
    el.className = "events empty";
    el.textContent = "暂无决策记录";
    return;
  }
  el.className = "events";
  el.innerHTML = [...filtered].reverse().slice(0, 24).map(event => {
    const category = event.category || event.kind || "system";
    const time = (event.timestamp || event.time || "").split("T")[1]?.slice(0,8) || "--:--:--";
    return `<div class="event ${escapeHtml(category)}"><span class="event-marker"></span><div>
      <div class="event-head"><span>${escapeHtml(category.toUpperCase())}</span><time>${time}</time></div>
      <div class="event-body">${escapeHtml(describeEvent(event))}</div>
    </div>`;
  }).join("");
}

function resetGameView() {
  $("sun").textContent = "0";
  $("wave").textContent = "0/0";
  $("plantCount").textContent = "0";
  $("zombieCount").textContent = "0";
  $("scene").textContent = "等待游戏";
  $("clock").textContent = "00:00";
  renderRadar({});
  updateSparkline("sun", 0);
  updateSparkline("wave", 0);
  updateSparkline("plant", 0);
  updateSparkline("zombie", 0);
  renderCards([]);
}

async function refresh() {
  try {
    const [status, captureStatus, events] = await Promise.all([api("/api/status"), api("/api/capture/status"), api("/api/events?limit=80")]);
    lastEvents = events;
    renderEvents(events);
    chip($("gameChip"), status.plugin.connected, `游戏已连接 · ${status.plugin.version}`, "游戏未连接");
    chip($("captureChip"), captureStatus.has_frame, "实时画面已同步", "等待游戏画面");
    chip($("agentChip"), status.agent.running, `Agent 运行中`, "Agent 已停止");
    $("captureFallback").classList.toggle("hidden", captureStatus.has_frame);
    $("agentStateText").textContent = status.agent.running ? "运行中" : "待机";
    $("gameApiState").textContent = status.plugin.connected ? "已连接" : "未连接";
    $("gameApiState").classList.toggle("online", status.plugin.connected);
    $("currentModel").textContent = status.agent.model || "—";
    const lastModelResponse = [...events].reverse().find(event => (event.category || event.kind) === "model" && event.event === "response" && event.latency_ms != null);
    $("modelLatency").textContent = lastModelResponse ? `${Math.round(lastModelResponse.latency_ms)}ms` : "—";
    const minuteAgo = Date.now() - 60000;
    const actionsPerMinute = events.filter(event => (event.category || event.kind) === "action" && event.event === "completed" && Date.parse(event.timestamp || event.time || 0) >= minuteAgo).length;
    $("actionRate").textContent = actionsPerMinute;
    const streamStale = captureStatus.has_frame && (captureStatus.last_frame_age_ms ?? 0) > 2500;
    if (streamStale && Date.now() - lastStreamResetAt > 3500) resetStream();

    if (status.plugin.connected) {
      const state = await api("/api/state");
      $("sun").textContent = state.sun;
      $("wave").textContent = `${state.wave}/${state.total_wave}`;
      $("waveHint").textContent = state.game?.huge_wave_countdown > 0 ? `大波倒计时 ${state.game.huge_wave_countdown}` : "实时波次";
      const flow = state.flow || {};
      const typeTag = flow.kind === "conveyor" ? " · 传送带" : flow.kind === "special" ? " · 特殊关" : "";
      $("scene").textContent = `${state.level_label || ""}${state.level_label ? " · " : ""}${state.scene || "未知"}${typeTag}`;
      $("clock").textContent = gameClock(state.clock);
      $("plantCount").textContent = state.counts?.plants ?? state.plants?.length ?? 0;
      updateSparkline("sun", state.sun);
      updateSparkline("wave", state.wave);
      updateSparkline("plant", state.counts?.plants ?? state.plants?.length ?? 0);
      updateSparkline("zombie", state.counts?.zombies ?? state.zombies?.length ?? 0);
      renderCards(state.cards);
      renderRadar(state);
      if (status.agent.running && flow.agent_enabled === false) {
        const waitingLabels = {
          reward: "自动处理奖励",
          special_level: "特殊关待机",
          level_intro: "等待开场",
          manual_transition: "等待手动操作",
          lost: "等待重新开始",
        };
        $("agentStateText").textContent = waitingLabels[flow.phase] || "流程待机";
      }
    } else {
      resetGameView();
    }

  } catch (error) {
    console.warn(error);
  }
}

document.querySelectorAll(".theme-button").forEach(button => button.onclick = () => applyTheme(button.dataset.theme));
document.querySelectorAll(".filter").forEach(button => button.onclick = () => {
  document.querySelectorAll(".filter").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  activeFilter = button.dataset.filter;
  renderEvents();
});

$("gameFeed").onload = () => $("captureFallback").classList.add("hidden");
$("gameFeed").onerror = () => {
  $("captureFallback").classList.remove("hidden");
  resetStream(1800);
};

$("profileBtn").onclick = async () => {
  try {
    const profile = await api("/api/profile");
    $("profileName").value = profile.name;
    $("profilePreview").src = profile.avatar_url;
    $("profileAvatarFile").value = "";
    pendingAvatarData = "";
    profileFeedback();
    $("profileDialog").showModal();
  } catch (error) {
    toast(error.message);
  }
};
$("closeProfileBtn").onclick = () => $("profileDialog").close();
$("cancelProfileBtn").onclick = () => $("profileDialog").close();
$("profileDialog").onclick = event => {
  if (event.target === $("profileDialog")) $("profileDialog").close();
};
$("profileAvatarFile").onchange = event => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    profileFeedback("头像文件必须小于 5MB", "error");
    event.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    pendingAvatarData = String(reader.result || "");
    $("profilePreview").src = pendingAvatarData;
    profileFeedback("新头像已载入，保存后生效", "working");
  };
  reader.onerror = () => profileFeedback("无法读取所选图片", "error");
  reader.readAsDataURL(file);
};
$("saveProfileBtn").onclick = async () => {
  const button = $("saveProfileBtn");
  try {
    profileFeedback("正在保存频道资料…", "working");
    button.disabled = true;
    button.textContent = "正在保存…";
    const profile = await post("/api/profile", {
      name: $("profileName").value.trim(),
      avatar_data_url: pendingAvatarData,
    });
    applyProfile(profile);
    if ($("profileDialog").open) $("profileDialog").close();
    requestAnimationFrame(() => toast("频道头像和名称已更新"));
  } catch (error) {
    profileFeedback(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "保存资料";
  }
};

async function openSettings() {
  try {
    const data = await api("/api/settings");
    $("apiBaseUrl").value = data.base_url || "https://api.deepseek.com";
    $("apiModel").value = data.model || "deepseek-flash";
    $("apiKey").value = "";
    $("goal").value = data.goal || "自主完成当前关卡";
    $("apiKeyStatus").textContent = data.api_key_configured ? "本机已配置 API Key；留空不会覆盖" : "本机尚未配置 API Key";
    $("apiKeyStatus").classList.toggle("configured", data.api_key_configured);
    $("apiKeyStatus").dataset.configured = data.api_key_configured ? "true" : "false";
    $("modelOptions").classList.add("hidden");
    settingsFeedback();
    updateModelLoadButton();
    $("settingsDialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

function settingsPayload() {
  return {
    base_url: $("apiBaseUrl").value.trim(),
    model: $("apiModel").value.trim(),
    api_key: $("apiKey").value,
    goal: $("goal").value.trim(),
  };
}

function updateModelLoadButton() {
  const hasKey = $("apiKey").value.trim() || $("apiKeyStatus").dataset.configured === "true";
  $("loadModelsBtn").classList.toggle("hidden", !($("apiBaseUrl").value.trim() && hasKey));
}

$("apiBaseUrl").addEventListener("input", updateModelLoadButton);
$("apiKey").addEventListener("input", updateModelLoadButton);
$("loadModelsBtn").onclick = async () => {
  const button = $("loadModelsBtn");
  const options = $("modelOptions");
  button.classList.add("loading");
  button.disabled = true;
  options.className = "model-options loading-list";
  options.textContent = "正在获取可用模型…";
  try {
    const data = await post("/api/settings/models", {
      base_url: $("apiBaseUrl").value.trim(),
      api_key: $("apiKey").value,
    });
    options.className = "model-options";
    options.innerHTML = data.models.map(model => `<button type="button" data-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`).join("");
    options.querySelectorAll("button").forEach(item => item.onclick = () => {
      $("apiModel").value = item.dataset.model;
      options.querySelectorAll("button").forEach(other => other.classList.toggle("selected", other === item));
    });
  } catch (error) {
    options.className = "model-options error";
    options.textContent = error.message;
  } finally {
    button.classList.remove("loading");
    button.disabled = false;
  }
};

$("settingsBtn").onclick = openSettings;
$("closeSettingsBtn").onclick = () => $("settingsDialog").close();
$("settingsDialog").onclick = event => {
  if (event.target === $("settingsDialog")) $("settingsDialog").close();
};
$("saveSettingsBtn").onclick = async () => {
  const button = $("saveSettingsBtn");
  try {
    settingsFeedback("正在验证 API 与模型工具调用…", "working");
    button.disabled = true;
    button.classList.add("testing");
    button.textContent = "正在测试连接…";
    const data = await post("/api/settings/test-and-save", settingsPayload());
    $("apiKey").value = "";
    $("apiKeyStatus").textContent = data.api_key_configured ? "本机已配置 API Key；留空不会覆盖" : "本机尚未配置 API Key";
    $("apiKeyStatus").classList.toggle("configured", data.api_key_configured);
    $("apiKeyStatus").dataset.configured = data.api_key_configured ? "true" : "false";
    closeSettingsWithNotice(`模型测试通过 · ${Math.round(data.test.latency_ms)}ms，设置已保存`);
  } catch (error) {
    settingsFeedback(error.message, "error");
  } finally {
    button.disabled = false;
    button.classList.remove("testing");
    button.textContent = "测试并保存";
  }
};
$("saveDirectBtn").onclick = async () => {
  const button = $("saveDirectBtn");
  try {
    settingsFeedback("正在保存本地设置…", "working");
    button.disabled = true;
    button.textContent = "正在保存…";
    await post("/api/settings", settingsPayload());
    closeSettingsWithNotice("已跳过测试并保存设置");
  } catch (error) {
    settingsFeedback(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "直接保存并退出";
  }
};
$("startBtn").onclick = async () => {
  const button = $("startBtn");
  try {
    settingsFeedback("正在查找游戏并启动 Agent…", "working");
    button.disabled = true;
    button.textContent = "正在启动…";
    await post("/api/agent/start", {goal:$("goal").value});
    closeSettingsWithNotice("Agent 已启动，游戏连接成功");
    refresh();
  } catch (error) {
    settingsFeedback(`启动失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "启动 Agent";
  }
};
$("stopBtn").onclick = async () => {
  const button = $("stopBtn");
  try {
    settingsFeedback("正在停止 Agent…", "working");
    button.disabled = true;
    await post("/api/agent/stop");
    closeSettingsWithNotice("已请求停止 Agent");
    refresh();
  } catch (error) {
    settingsFeedback(`停止失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
};

applyTheme(localStorage.getItem("pvz-dashboard-theme") || "bili");
loadProfile();
refresh();
setInterval(refresh, 1000);
