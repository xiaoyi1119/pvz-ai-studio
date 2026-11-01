const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => [...document.querySelectorAll(selector)];
const BACKEND = '';
const ui = { status: null, state: null, settings: null, profile: null, avatarDataUrl: '', sendingInstruction: false };
const CUSTOM_MODEL = '__custom__';

const actionNames = {
  place_plant: '种植植物', shovel: '使用铲子', fire_cob: '发射玉米加农炮',
  select_seeds: '选择本关植物', collect_items: '收集掉落物', set_auto_collect: '调整自动收集',
  wait: '等待下一次状态读取', terminate: '结束本次 Agent',
};
const phaseNames = {
  idle: '空闲', starting: '正在启动', observing: '读取状态', thinking: '思考中',
  thinking_user_instruction: '理解用户指令', acting: '执行操作', stopped: '已停止',
  waiting_for_user_resume: '等待恢复游戏', waiting_for_manual_transition: '等待手动换关',
  waiting_seed_selection_ui_stable: '等待选卡界面', waiting_for_level_intro: '等待关卡开始',
  recovering_action: '重新规划', recovering_injection: '操作恢复中', game_frozen: '游戏时钟停滞',
};

let toastTimer;
function showToast(message, error = false) {
  const toast = qs('#toast');
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.toggle('is-error', error);
  toast.classList.add('is-visible');
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2600);
}

async function requestJson(url, options = {}) {
  const target = url.startsWith('/api/') ? `${BACKEND}${url}` : url;
  const response = await fetch(target, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `请求失败（${response.status}）`);
  return body;
}

function openDialog(dialog) { if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal(); }
function setToggle(toggle, enabled) {
  toggle.setAttribute('aria-checked', String(Boolean(enabled)));
  toggle.classList.toggle('is-on', Boolean(enabled));
}
function setConnectionBadge(connected) {
  qs('#appStatusText').textContent = connected ? '游戏已连接' : '等待游戏';
  qs('#appStatusDot').classList.toggle('status-dot--amber', !connected);
  qs('#footerGame').textContent = connected ? '已连接' : '未连接';
}

function setChatAvailability(running) {
  const input = qs('#commandInput');
  const button = qs('#commandForm button');
  const prompt = qs('#chatPrompt');
  input.disabled = !running || ui.sendingInstruction;
  button.disabled = !running || ui.sendingInstruction;
  input.placeholder = running ? '输入简短的本局指令，例如：下一波先保留阳光' : '请先启动 Agent';
  if (prompt) {
    prompt.querySelector('strong').textContent = running ? 'Agent 已准备好' : '先启动 Agent';
    prompt.querySelector('p').textContent = running
      ? '发送一条简短要求，AI 会在下一次决策中读取并流式回复。'
      : '运行后可以发送简短的本局指令，AI 会在下一次决策中读取并回复。';
  }
}

function updateStatus(status) {
  ui.status = status;
  const connected = Boolean(status.plugin?.connected);
  const running = Boolean(status.agent?.running);
  const phase = status.agent?.phase || 'idle';
  setConnectionBadge(connected);
  qs('#agentState').textContent = phaseNames[phase] || phase;
  qs('#agentState').classList.toggle('is-running', running);
  qs('#footerAgent').textContent = running ? '运行中' : '已停止';
  qs('#footerModel').textContent = status.agent?.model || '—';
  qs('#footerPhase').textContent = phaseNames[phase] || phase;
  qs('#agentButton').textContent = running ? '停止 Agent' : '启动 Agent';
  qs('#agentButton').classList.toggle('is-running', running);
  setChatAvailability(running);
  const instruction = status.agent?.user_instruction?.latest;
  if (instruction?.state === 'thinking') {
    qs('#decisionAction').textContent = '正在完整读取用户指令';
    qs('#decisionReason').textContent = instruction.text;
    qs('#decisionCode').textContent = 'thinking';
    qs('#decisionTarget').textContent = '同一次 Agent 决策';
    qs('#decisionVerify').textContent = '思考中';
  } else {
    renderAction(status.agent?.last_action || instruction?.action);
  }
}

function renderAction(record) {
  if (!record) return;
  const action = record.action || 'wait';
  const args = record.arguments || {};
  const result = record.result || {};
  qs('#decisionAction').textContent = actionNames[action] || action;
  qs('#decisionReason').textContent = args.decision_reason || args.selection_reason || describeAction(action, args);
  qs('#decisionCode').textContent = action;
  qs('#decisionTarget').textContent = formatTarget(args);
  qs('#decisionVerify').textContent = result.status === 'rejected' ? '已拒绝' : result.status === 'ok' ? '已执行' : '已决策';
}
function describeAction(action, args) {
  if (action === 'wait') return `等待 ${args.time ?? 1} 秒后重新读取完整游戏状态。`;
  if (action === 'place_plant') return 'Agent 已根据本轮状态选择植物和落点。';
  if (action === 'select_seeds') return 'Agent 正在为当前关卡独立选择卡组。';
  if (action === 'shovel') return 'Agent 正在调整当前阵型。';
  return '该操作来自 Agent 本轮的实际工具调用。';
}
function formatTarget(args) {
  if (Number.isInteger(args.row) && Number.isInteger(args.col)) return `目标 ${args.row + 1} 路 · ${args.col + 1} 列`;
  if (Array.isArray(args.seeds)) return `${args.seeds.length} 张卡`;
  return '目标 —';
}

function updateState(state) {
  ui.state = state;
  const game = state.game || state;
  qs('#sunValue').textContent = game.sun ?? state.sun ?? '—';
  qs('#waveValue').textContent = `${game.wave ?? state.wave ?? 0} / ${game.total_wave ?? state.total_wave ?? 0}`;
  qs('#plantValue').textContent = state.counts?.plants ?? state.plants?.length ?? 0;
  qs('#zombieValue').textContent = state.counts?.zombies ?? state.zombies?.length ?? 0;
  qs('#levelCaption').textContent = game.level_label || state.level_label || '当前关卡';
  qs('#sceneCaption').textContent = `${game.scene || state.scene || '未知场景'} · ${game.board_rows || state.board_rows || 5} 路`;
  qs('#pauseCaption').textContent = game.paused || state.paused ? '已暂停' : '运行中';
  renderLanes(state);
}

function renderLanes(state) {
  const rows = Number(state.game?.board_rows || state.board_rows || 5);
  const threats = state.lane_threats || [];
  const mowers = new Map((state.lawn_mowers || []).map((item) => [Number(item.row), Boolean(item.alive)]));
  const scores = threats.map((item) => {
    const near = item.nearest_x == null ? 0 : Math.max(0, (720 - Number(item.nearest_x)) / 6);
    return Number(item.count || 0) * 18 + Number(item.total_hp || 0) / 75 + Number(item.eating_count || 0) * 30 + near;
  });
  const maxScore = Math.max(1, ...scores);
  const board = qs('#laneBoard');
  board.replaceChildren();
  for (let row = 0; row < rows; row += 1) {
    const raw = scores[row] || 0;
    const pressure = raw ? Math.max(10, Math.min(100, Math.round((raw / maxScore) * 92))) : 4;
    const level = pressure >= 70 ? 'high' : pressure >= 38 ? 'mid' : 'low';
    const mowerAlive = mowers.has(row) ? mowers.get(row) : false;
    const item = document.createElement('div');
    item.className = `lane-row${level === 'high' ? ' lane-row--focus' : ''}`;
    item.setAttribute('role', 'listitem');
    item.innerHTML = `<span class="lane-number">${row + 1} 路</span><svg class="mower${mowerAlive ? '' : ' mower--lost'}" aria-label="${mowerAlive ? '推车可用' : '推车已用或本局无推车'}"><use href="#i-mower"/></svg><div class="lane-track"><span style="--pressure: ${pressure}%"></span></div><span class="lane-state lane-state--${level}">${level === 'high' ? '高' : level === 'mid' ? '中' : '低'}</span>`;
    board.append(item);
  }
}

function renderWaitingLanes(rows = 5) {
  const board = qs('#laneBoard');
  board.replaceChildren();
  for (let row = 0; row < rows; row += 1) {
    const item = document.createElement('div');
    item.className = 'lane-row';
    item.setAttribute('role', 'listitem');
    item.innerHTML = `<span class="lane-number">${row + 1} 路</span><svg class="mower mower--lost" aria-label="推车状态待读取"><use href="#i-mower"/></svg><div class="lane-track"><span style="--pressure: 0%"></span></div><span class="lane-state">—</span>`;
    board.append(item);
  }
}

async function pollStatus() {
  try { updateStatus(await requestJson('/api/status')); } catch (_) { setConnectionBadge(false); }
}
async function pollState() {
  if (!ui.status?.plugin?.connected) return;
  try { updateState(await requestJson('/api/state')); } catch (_) { /* next poll retries */ }
}
async function pollCapture() {
  try {
    const capture = await requestJson('/api/capture/status');
    const ready = Boolean(capture.has_frame && (capture.last_frame_age_ms ?? 99999) < 4000);
    qs('#captureState').textContent = ready ? '画面就绪' : '等待游戏画面';
    qs('#captureDot').classList.toggle('status-dot--amber', !ready);
  } catch (_) { qs('#captureState').textContent = '画面未连接'; }
}
async function ensureGameConnection() {
  if (ui.status?.plugin?.connected) return;
  try { await requestJson('/api/connect', { method: 'POST', body: '{}' }); await pollStatus(); } catch (_) { /* game may not be running */ }
}

function appendChatMessage(role, text) {
  qs('#chatPrompt')?.remove();
  const message = document.createElement('div');
  message.className = `chat-message chat-message--${role}`;
  if (role === 'ai') {
    const head = document.createElement('div');
    head.className = 'chat-ai-head';
    head.innerHTML = `<img src="${ui.profile?.avatar_url || '/static/avatar.jpg'}" alt=""><span class="chat-author">AI</span>`;
    message.append(head);
  } else message.setAttribute('aria-label', '用户指令');
  const body = document.createElement('p');
  body.textContent = text;
  message.append(body);
  qs('#chatMessages').append(message);
  qs('#chatMessages').scrollTop = qs('#chatMessages').scrollHeight;
  return body;
}
function parseSseBlock(block) {
  let event = 'message';
  const data = [];
  block.split(/\r?\n/).forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trim());
  });
  return data.length ? { event, data: JSON.parse(data.join('\n')) } : null;
}
async function sendInstruction(text, aiBody) {
  const response = await fetch(`${BACKEND}/api/agent/instructions/stream`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `发送失败（${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let replyStarted = false;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const packet = parseSseBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (!packet) continue;
      if (packet.event === 'status') aiBody.textContent = packet.data.message === 'AI 思考中' ? '思考中…' : packet.data.message;
      if (packet.event === 'delta') {
        if (!replyStarted) { aiBody.textContent = ''; replyStarted = true; }
        aiBody.textContent += packet.data.text || '';
        qs('#chatMessages').scrollTop = qs('#chatMessages').scrollHeight;
      }
      if (packet.event === 'done') { renderAction(packet.data.action); return; }
      if (packet.event === 'error') throw new Error(packet.data.message || '指令执行失败');
    }
    if (done) break;
  }
}

async function loadSettings() {
  const [settings, assist] = await Promise.all([requestJson('/api/settings'), requestJson('/api/strategy-assist')]);
  ui.settings = settings;
  qs('#endpoint').value = settings.base_url || '';
  populateModelOptions([], settings.model || '');
  qs('#agentGoal').value = settings.goal || '';
  qs('#apiKey').value = '';
  qs('#apiKey').placeholder = settings.api_key_configured ? '已配置，留空则保持不变' : '请输入 API Key';
  setToggle(qs('#assistToggle'), assist.enabled);
  const base = settings.base_url || '';
  qs('#provider').value = base.includes('openrouter.ai') ? 'openrouter' : base.includes('deepseek.com') ? 'deepseek' : 'compatible';
}

function populateModelOptions(models, selected = '') {
  const select = qs('#model');
  const unique = [...new Set((models || []).map((item) => String(item).trim()).filter(Boolean))];
  const current = String(selected || '').trim();
  if (current && !unique.includes(current)) unique.unshift(current);
  select.replaceChildren();
  unique.forEach((id) => {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = id === current && !(models || []).includes(id) ? `${id}（当前配置）` : id;
    select.append(option);
  });
  const custom = document.createElement('option');
  custom.value = CUSTOM_MODEL;
  custom.textContent = '自定义模型 ID…';
  select.append(custom);
  if (current) select.value = current;
  else if (unique.length) select.value = unique[0];
  else select.value = CUSTOM_MODEL;
  qs('#customModel').value = select.value === CUSTOM_MODEL ? current : '';
  syncCustomModelVisibility();
}

function syncCustomModelVisibility() {
  const custom = qs('#customModel');
  custom.hidden = qs('#model').value !== CUSTOM_MODEL;
  if (!custom.hidden) custom.focus({ preventScroll: true });
}

function selectedModel() {
  return qs('#model').value === CUSTOM_MODEL ? qs('#customModel').value.trim() : qs('#model').value.trim();
}
async function loadProfile() {
  ui.profile = await requestJson('/api/profile');
  if (ui.profile.avatar_url?.startsWith('/')) ui.profile.avatar_url = `${BACKEND}${ui.profile.avatar_url}`;
  qs('#profileName').value = ui.profile.name;
  qs('.profile-button span').textContent = ui.profile.name;
  qs('#profileAvatar').src = ui.profile.avatar_url;
  qs('#profilePreview').src = ui.profile.avatar_url;
}

qs('#settingsButton').addEventListener('click', async () => {
  try { await loadSettings(); openDialog(qs('#settingsDialog')); } catch (error) { showToast(error.message, true); }
});
qs('#profileButton').addEventListener('click', async () => {
  try { await loadProfile(); openDialog(qs('#profileDialog')); } catch (error) { showToast(error.message, true); }
});
function switchView(name) {
  qsa('[data-view-panel]').forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== name; });
  qsa('.nav-item').forEach((item) => {
    const active = item.dataset.view === name;
    item.classList.toggle('is-active', active);
    if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
  if (name === 'records') loadRecords();
  if (name === 'diagnostics') loadDiagnostics();
}

qsa('.nav-item').forEach((item) => item.addEventListener('click', () => switchView(item.dataset.view)));

function eventSummary(event) {
  const hidden = new Set(['timestamp', 'time', 'category', 'event', 'level', 'session_id', 'seq', 'monotonic_ms', 'api_key', 'authorization']);
  const details = Object.fromEntries(Object.entries(event).filter(([key]) => !hidden.has(key.toLowerCase())));
  const text = JSON.stringify(details, null, 0);
  return text === '{}' ? '—' : text;
}

async function loadRecords() {
  const body = qs('#recordRows');
  body.innerHTML = '<tr><td colspan="4" class="empty-cell">正在读取运行记录…</td></tr>';
  try {
    const events = await requestJson('/api/events?limit=120');
    body.replaceChildren();
    if (!events.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty-cell">还没有运行记录</td></tr>';
      return;
    }
    [...events].reverse().forEach((event) => {
      const row = document.createElement('tr');
      const time = document.createElement('td');
      const category = document.createElement('td');
      const name = document.createElement('td');
      const details = document.createElement('td');
      const stamp = event.time || event.timestamp;
      time.textContent = stamp ? new Date(stamp).toLocaleString('zh-CN', { hour12: false }) : '—';
      category.textContent = event.category || event.kind || 'system';
      name.textContent = event.event || event.message || '—';
      details.textContent = eventSummary(event);
      row.append(time, category, name, details);
      body.append(row);
    });
  } catch (error) {
    body.innerHTML = `<tr><td colspan="4" class="empty-cell"></td></tr>`;
    body.querySelector('td').textContent = `读取失败：${error.message}`;
  }
}

function setDiagnostic(id, text, state) {
  const item = qs(id);
  item.textContent = text;
  item.className = state ? `is-${state}` : '';
}

async function loadDiagnostics() {
  const errorBox = qs('#diagnosticError');
  errorBox.hidden = true;
  try {
    const [status, capture] = await Promise.all([requestJson('/api/status'), requestJson('/api/capture/status')]);
    updateStatus(status);
    setDiagnostic('#diagGame', status.plugin?.connected ? `已连接 · PID ${status.plugin.pid}` : '未找到游戏进程', status.plugin?.connected ? 'ok' : 'warning');
    const frameReady = capture.has_frame && (capture.last_frame_age_ms ?? 99999) < 4000;
    setDiagnostic('#diagCapture', frameReady ? `${capture.frame_width}×${capture.frame_height} · ${capture.measured_fps || 0} FPS` : capture.last_error || '等待有效画面', frameReady ? 'ok' : 'warning');
    setDiagnostic('#diagAgent', status.agent?.running ? `运行中 · ${phaseNames[status.agent.phase] || status.agent.phase}` : '已停止', status.agent?.running ? 'ok' : 'warning');
    setDiagnostic('#diagModel', `${status.agent?.model || '未配置'}${status.agent?.last_error ? ' · 有错误' : ''}`, status.agent?.last_error ? 'error' : 'ok');
    if (status.agent?.last_error) {
      errorBox.textContent = `最近错误：${status.agent.last_error}`;
      errorBox.hidden = false;
    }
  } catch (error) {
    ['#diagGame', '#diagCapture', '#diagAgent', '#diagModel'].forEach((id) => setDiagnostic(id, '检查失败', 'error'));
    errorBox.textContent = `诊断接口不可用：${error.message}`;
    errorBox.hidden = false;
  }
}

qs('#refreshRecords').addEventListener('click', loadRecords);
qs('#refreshDiagnostics').addEventListener('click', loadDiagnostics);
qsa('[data-settings-tab]').forEach((tab) => tab.addEventListener('click', () => {
  const target = tab.dataset.settingsTab;
  qsa('[data-settings-tab]').forEach((item) => {
    const selected = item === tab;
    item.classList.toggle('is-active', selected);
    item.setAttribute('aria-selected', String(selected));
  });
  qsa('[data-settings-panel]').forEach((panel) => { panel.hidden = panel.dataset.settingsPanel !== target; });
  qs('#saveSettings').textContent = target === 'api' ? '测试并保存' : '保存设置';
}));
qsa('.toggle-switch').forEach((toggle) => toggle.addEventListener('click', () => {
  if (!toggle.disabled) setToggle(toggle, toggle.getAttribute('aria-checked') !== 'true');
}));
qsa('[data-theme-choice]').forEach((choice) => choice.addEventListener('click', () => {
  qsa('[data-theme-choice]').forEach((item) => {
    const selected = item === choice;
    item.classList.toggle('is-selected', selected);
    item.setAttribute('aria-pressed', String(selected));
  });
  document.documentElement.dataset.visualTheme = choice.dataset.themeChoice;
  localStorage.setItem('pvz-theme', choice.dataset.themeChoice);
}));
qs('#provider').addEventListener('change', (event) => {
  if (event.target.value === 'deepseek') {
    qs('#endpoint').value = 'https://api.deepseek.com';
    populateModelOptions(['deepseek-chat', 'deepseek-reasoner'], 'deepseek-chat');
  }
  if (event.target.value === 'openrouter') {
    qs('#endpoint').value = 'https://openrouter.ai/api/v1';
    populateModelOptions([], '');
  }
});
qs('#model').addEventListener('change', syncCustomModelVisibility);

qs('#agentButton').addEventListener('click', async () => {
  const button = qs('#agentButton');
  button.disabled = true;
  try {
    if (ui.status?.agent?.running) await requestJson('/api/agent/stop', { method: 'POST', body: '{}' });
    else await requestJson('/api/agent/start', { method: 'POST', body: JSON.stringify({ goal: ui.settings?.goal || '' }) });
    await pollStatus();
  } catch (error) { showToast(error.message, true); } finally { button.disabled = false; }
});

qs('#commandForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = qs('#commandInput');
  const text = input.value.trim();
  if (!text) { showToast('请先输入要交给 AI 的本局指令'); input.focus(); return; }
  if (ui.sendingInstruction) { showToast('上一条指令仍在等待本轮决策'); return; }
  appendChatMessage('user', text);
  const aiBody = appendChatMessage('ai', '正在交给 Agent…');
  input.value = '';
  ui.sendingInstruction = true;
  const button = qs('#commandForm button');
  button.disabled = true;
  try { await sendInstruction(text, aiBody); await pollStatus(); }
  catch (error) { aiBody.textContent = `未完成：${error.message}`; showToast(error.message, true); }
  finally { ui.sendingInstruction = false; setChatAvailability(Boolean(ui.status?.agent?.running)); input.focus(); }
});
qs('#commandInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); qs('#commandForm').requestSubmit(); }
});

qs('#findModels').addEventListener('click', async () => {
  const button = qs('#findModels');
  button.disabled = true;
  button.textContent = '读取中…';
  try {
    const result = await requestJson('/api/settings/models', {
      method: 'POST', body: JSON.stringify({ base_url: qs('#endpoint').value, api_key: qs('#apiKey').value }),
    });
    const before = selectedModel();
    populateModelOptions(result.models, before);
    qs('#modelHint').textContent = result.models.includes(before)
      ? `已读取 ${result.models.length} 个模型，并保留当前选择。`
      : `已读取 ${result.models.length} 个模型；当前配置仍保留，请确认后保存。`;
    showToast(`已读取 ${result.models.length} 个模型`);
  } catch (error) { showToast(error.message, true); } finally { button.disabled = false; button.textContent = '获取模型'; }
});

qs('#saveSettings').addEventListener('click', async (event) => {
  event.preventDefault();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    if (!qs('#settings-api').hidden) {
      button.textContent = '正在测试…';
      const model = selectedModel();
      if (!model) throw new Error('请选择模型或填写自定义模型 ID');
      const result = await requestJson('/api/settings/test-and-save', {
        method: 'POST', body: JSON.stringify({
          base_url: qs('#endpoint').value, api_key: qs('#apiKey').value,
          model, goal: qs('#agentGoal').value,
        }),
      });
      showToast(`测试成功 · ${Math.round(result.test.latency_ms)}ms`);
      await loadSettings();
    } else if (!qs('#settings-assist').hidden) {
      await requestJson('/api/strategy-assist', {
        method: 'POST', body: JSON.stringify({ enabled: qs('#assistToggle').getAttribute('aria-checked') === 'true' }),
      });
      showToast('辅助增强设置已保存');
    } else showToast('外观设置已保存在本机');
    qs('#settingsDialog').close();
    await pollStatus();
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = qs('#settings-api').hidden ? '保存设置' : '测试并保存'; }
});

qs('#avatarDemo').addEventListener('click', () => qs('#avatarFile').click());
qs('#avatarFile').addEventListener('change', () => {
  const file = qs('#avatarFile').files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) { showToast('头像文件必须小于 5MB', true); return; }
  const reader = new FileReader();
  reader.onload = () => { ui.avatarDataUrl = String(reader.result); qs('#profilePreview').src = ui.avatarDataUrl; };
  reader.readAsDataURL(file);
});
qs('#saveProfile').addEventListener('click', async (event) => {
  event.preventDefault();
  try {
    await requestJson('/api/profile', {
      method: 'POST', body: JSON.stringify({ name: qs('#profileName').value.trim(), avatar_data_url: ui.avatarDataUrl }),
    });
    ui.avatarDataUrl = '';
    await loadProfile();
    qs('#profileDialog').close();
    showToast('资料已保存');
  } catch (error) { showToast(error.message, true); }
});
qsa('dialog').forEach((dialog) => dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); }));
qs('#gameStream').addEventListener('error', () => { setTimeout(() => { qs('#gameStream').src = `${BACKEND}/api/capture/stream?t=${Date.now()}`; }, 1800); });

async function bootstrap() {
  const theme = localStorage.getItem('pvz-theme') || 'green';
  qs(`[data-theme-choice="${theme}"]`)?.click();
  renderWaitingLanes();
  setChatAvailability(false);
  await Promise.allSettled([loadSettings(), loadProfile(), pollStatus(), pollCapture()]);
  await ensureGameConnection();
  await Promise.allSettled([pollStatus(), pollState()]);
  setInterval(pollStatus, 1000);
  setInterval(pollState, 1200);
  setInterval(pollCapture, 2500);
  setInterval(ensureGameConnection, 4000);
}
bootstrap();
