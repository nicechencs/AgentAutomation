// This page reads Runner facts. Only the backend starts and advances n8n workflows.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const labels = { dispatching: '正在触发 n8n', timed_out: '已超时', interrupted: '已中断', queued: '排队中', running: '运行中', paused: '已暂停', pausing: '正在暂停', cancelling: '正在取消', cancelled: '已取消', completed: '已完成', failed: '失败', blocked: '已阻塞', waiting: '等待中', approval: '待审批', awaiting_approval: '待审批', pending: '待审批', approved: '已批准', rejected: '已拒绝', coding: '编码阶段', review: '审查阶段', planning: '规划阶段', integration: '集成阶段', artifact: '产物验收', plan: '计划审批' };
const terminal = status => ['completed', 'failed', 'cancelled', 'rejected', 'timed_out', 'interrupted', 'stopped', 'error'].includes(status);
const badge = status => `<span class="status status-${['completed', 'approved'].includes(status) ? 'done' : ['failed', 'rejected', 'error'].includes(status) ? 'failed' : ['paused', 'pending', 'approval', 'awaiting_approval', 'waiting'].includes(status) ? 'approval' : 'queued'}">${esc(labels[status] || status || '未知')}</span>`;
let panel, timer, active = false, generation = 0, revision = 0, busy = false, confirmingCancel = false;
let health = null, runs = [], run = null, selectedId = '', connectionError = '', actionError = '', message = '', lastSync = '';
const controllers = new Set();

async function request(path, body) {
  const controller = new AbortController();
  controllers.add(controller);
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-UI-Request': '1' }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }), cache: 'no-store', signal: controller.signal });
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { throw new Error(`Runner 返回了非 JSON 响应（HTTP ${response.status}）。`); }
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : data.detail ? JSON.stringify(data.detail) : data.error || '';
      throw new Error(`HTTP ${response.status}${detail ? `：${detail}` : ''}${response.status === 404 ? '。当前地址没有对应 Runner API，请确认从 8000 端口打开。' : ''}`);
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Runner 请求已中止或超时；结果可能已生效，请查询状态后再操作。');
    throw error;
  } finally { clearTimeout(timeout); controllers.delete(controller); }
}

function update(selector, html) {
  const node = panel.querySelector(selector);
  if (node.innerHTML === html) return;
  const focused = node.contains(document.activeElement) ? document.activeElement.id : '';
  const scroll = [...node.querySelectorAll('[data-scroll]')].map(el => [el.dataset.scroll, el.scrollTop]);
  node.innerHTML = html;
  if (focused) document.getElementById(focused)?.focus();
  for (const [key, top] of scroll) [...node.querySelectorAll('[data-scroll]')].find(el => el.dataset.scroll === key)?.scrollTo(0, top);
}
function fakeReady() { return !connectionError && health?.status === 'ok' && health?.mode === 'fake'; }
function render() {
  if (!panel || !active) return;
  const connected = fakeReady();
  update('[data-runner-health]', connectionError ? `<div class="callout danger"><strong>Runner 连接失败 · 已显示的数据可能过期</strong><p>${esc(connectionError)}</p><a href="http://127.0.0.1:8000/#runner">打开本地 Runner 页面（8000）</a></div>` : health ? `<div class="callout ${connected ? '' : 'warning'}"><strong>${connected ? '已连接本地 Runner · Fake Agent' : `Runner 模式未确认：${esc(health.mode || '未知')}`}</strong><p>n8n 调用配置：${health.n8n?.configured ? '已设置' : '未设置'} · 连接：${health.n8n?.reachable ? '可连接' : '尚未连通'} · ${esc(health.n8n?.url || '地址未提供')}</p><p>${health.n8n?.configured && health.n8n?.reachable ? 'n8n 可连接，工作流执行结果见运行记录。' : '待连接 n8n；接入后由其编排。'}当前使用 Fake Agent，不运行真实 CLI 或 Git。${!connected ? '当前模式不支持本页操作。' : ''}</p></div>` : '<div class="callout">正在连接本地 Runner…</div>');
  update('[data-runner-feedback]', `${actionError ? `<div class="callout danger">${esc(actionError)}</div>` : ''}${message ? `<div class="callout">${esc(message)}</div>` : ''}`);
  const select = panel.querySelector('#runner-run-select');
  const options = '<option value="">选择运行</option>' + runs.map(r => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(labels[r.status] || r.status)} · ${esc(r.goal)}</option>`).join('');
  if (select.innerHTML !== options) select.innerHTML = options;
  select.value = selectedId;
  select.disabled = busy;
  const template = panel.querySelector('#runner-template');
  const supported = health?.templates || [];
  for (const option of template.options) option.disabled = !!health && !supported.includes(option.value);
  panel.querySelector('#runner-create').disabled = busy || !connected || !health?.n8n?.reachable || !health?.n8n?.configured || !supported.includes(template.value);
  panel.querySelector('#runner-refresh').disabled = busy;
  panel.querySelector('[data-runner-sync]').textContent = lastSync ? `最近同步 ${lastSync} · 每 1.5 秒查询` : '等待首次同步';
  const unavailable = busy || !connected || !run || terminal(run.status);
  panel.querySelector('#runner-pause').disabled = unavailable || ['paused', 'pausing', 'cancelling'].includes(run?.status);
  panel.querySelector('#runner-resume').disabled = unavailable || run?.status !== 'paused';
  panel.querySelector('#runner-cancel').disabled = unavailable || run?.status === 'cancelling';
  if (unavailable) confirmingCancel = false;
  panel.querySelector('[data-runner-cancel-confirm]').hidden = !confirmingCancel;
  update('[data-runner-summary]', run ? `<div class="inline-actions"><span class="mono">${esc(run.id)}</span>${badge(run.status)}</div><p class="runner-goal">${esc(run.goal)}</p><dl class="detail-grid"><div><dt>模板</dt><dd>${esc(run.template)}</dd></div><div><dt>当前阶段</dt><dd>${esc(labels[run.phase] || run.phase || '等待后端推进')}</dd></div><div><dt>阶段调用计数</dt><dd>${esc(run.cycles ?? '未知')}</dd></div><div><dt>n8n 执行 ID</dt><dd class="mono">${esc(run.n8n_execution_id || '尚未收到执行 ID')}</dd></div></dl>${run.error ? `<div class="callout danger">${esc(typeof run.error === 'string' ? run.error : JSON.stringify(run.error))}</div>` : ''}<p class="help-text">状态来自 Runner。执行 ID 表示关联的 n8n 执行，不代表流程已成功。</p>` : `<div class="empty-state">${selectedId ? '正在读取运行详情…' : '创建或选择一次运行，查看后端状态。'}</div>`);
  update('[data-runner-approvals]', run?.approvals?.length ? run.approvals.map((a, i) => `<article class="approval-card"><div class="inline-actions"><strong>${esc(labels[a.kind] || a.kind)}</strong>${badge(a.status)}</div><span class="mono muted">${esc(a.id)}</span>${a.status === 'pending' ? `<div class="inline-actions"><button id="runner-approve-${i}" class="button primary small" data-runner-decision="approved" data-approval-id="${esc(a.id)}" ${unavailable ? 'disabled' : ''}>批准</button><button id="runner-reject-${i}" class="button danger small" data-runner-decision="rejected" data-approval-id="${esc(a.id)}" ${unavailable ? 'disabled' : ''}>拒绝</button></div>` : ''}</article>`).join('') : '<div class="empty-state">当前没有审批记录。</div>');
  update('[data-runner-tasks]', run?.tasks?.length ? run.tasks.map(t => `<article class="integration-card"><div class="inline-actions"><strong>${esc(t.title)}</strong>${badge(t.status)}</div><p class="mono">${esc(t.id)} · 阶段 ${esc(t.stage || '未知')} · 尝试 ${esc(t.attempt ?? '未知')}</p><div class="log-box" data-scroll="${esc(t.id)}">${(t.logs || []).map(l => `<div class="log-line">${esc(typeof l === 'string' ? l : `${l.at || l.time || ''} ${l.message || JSON.stringify(l)}`)}</div>`).join('') || '暂无日志。'}</div></article>`).join('') : '<div class="empty-state">后端尚未生成任务。</div>');
  update('[data-runner-events]', `<div class="log-box" data-scroll="events">${run?.events?.length ? run.events.map(e => `<div class="log-line"><span class="log-time">${esc(e.at)}</span>${esc(e.message)}</div>`).join('') : '暂无运行事件。'}</div>`);
}

async function refresh() {
  const version = revision, session = generation, id = selectedId;
  try {
    const [nextHealth, list, detail] = await Promise.all([request('/api/health'), request('/api/runs'), id ? request(`/api/runs/${encodeURIComponent(id)}`) : Promise.resolve(null)]);
    if (!active || session !== generation || version !== revision) return;
    const nextRuns = Array.isArray(list) ? list : list.runs;
    if (!Array.isArray(nextRuns) || nextHealth.status !== 'ok') throw new Error('Runner 健康或运行列表响应不符合接口约定。');
    if (detail && detail.id !== id) throw new Error('Runner 返回的运行标识与查询不一致。');
    health = nextHealth; runs = nextRuns; connectionError = '';
    if (detail) run = detail;
    if (!selectedId && runs.length) { selectedId = runs[0].id; run = null; }
    lastSync = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  } catch (error) {
    if (active && session === generation && version === revision) connectionError = error.message;
  }
  if (active && session === generation) render();
}
async function poll() {
  const session = generation;
  await refresh();
  if (active && session === generation) timer = setTimeout(poll, 1500);
}
async function mutate(path, body, success, creating = false) {
  if (busy || !fakeReady()) return;
  busy = true; revision++; actionError = ''; message = '正在提交请求，请等待 Runner 确认…'; render();
  const session = generation;
  try {
    const result = await request(path, body);
    if (!active || session !== generation) return;
    if (creating) {
      if (!result.id) throw new Error('创建响应缺少运行 ID；请刷新运行列表核对，避免重复创建。');
      selectedId = result.id; run = result;
    }
    message = success;
  } catch (error) {
    if (active && session === generation) { actionError = `${error.message} 请求结果以查询为准，请勿盲目重复提交。`; message = ''; }
  } finally {
    busy = false; revision++;
    if (active && session === generation) { await refresh(); render(); }
  }
}

function createPanel() {
  const node = document.createElement('div'); node.className = 'runner-page';
  node.innerHTML = `<div data-runner-health role="status"></div><div data-runner-feedback role="status" aria-live="polite"></div><div class="workspace-grid"><section class="content-panel"><div class="panel-header"><div><h2 class="panel-title">创建 n8n 运行</h2><p class="panel-subtitle">Runner 触发实际 n8n 流程，浏览器仅提交操作和查询状态。</p></div><span class="pill">Fake Agent · 无真实 CLI / Git</span></div><form id="runner-create-form" class="form-content"><label class="field">运行目标<textarea id="runner-goal" name="goal" required maxlength="2000" placeholder="描述本次需要完成的目标"></textarea></label><label class="field">流程模板<select id="runner-template" name="template"><option value="development">开发闭环（Fake）</option><option value="local-document">本地文档 / 分析（Fake）</option></select></label><div class="form-grid"><label class="field">最大并发<select name="max_concurrency"><option value="1">1</option><option value="2" selected>2</option></select></label><label class="field">最大阶段调用数<input name="max_cycles" type="number" min="1" max="30" value="20" required></label></div><div class="modal-footer"><button id="runner-create" type="submit" class="button primary" disabled>创建并触发 n8n</button></div></form></section><section class="content-panel"><div class="panel-header"><h2 class="panel-title">运行与控制</h2><button id="runner-refresh" type="button" class="button ghost small">刷新连接</button></div><div class="form-content"><label class="field">选择运行<select id="runner-run-select"><option value="">选择运行</option></select></label><p data-runner-sync class="help-text"></p><div data-runner-summary class="runner-summary"></div><div class="inline-actions"><button id="runner-pause" class="button" type="button" data-runner-control="pause" disabled>暂停</button><button id="runner-resume" class="button" type="button" data-runner-control="resume" disabled>恢复</button><button id="runner-cancel" class="button danger" type="button" data-runner-control="cancel" disabled>取消运行</button></div><div class="callout warning" data-runner-cancel-confirm hidden><strong>确认取消这次运行？</strong><p>将提交取消请求，现有任务与审批记录保留。</p><div class="inline-actions"><button id="runner-confirm-cancel" class="button danger small" type="button">确认取消运行</button><button id="runner-keep-running" class="button small" type="button">继续保留运行</button></div></div><p class="help-text">暂停阻止后续节点启动；审批和控制确认后，由后端推进。运行状态以查询结果为准。</p></div></section></div><div class="workspace-grid"><section class="content-panel"><div class="panel-header"><h2 class="panel-title">任务与 Fake Agent 日志</h2></div><div data-runner-tasks></div></section><section class="content-panel"><div class="panel-header"><h2 class="panel-title">审批记录</h2></div><div data-runner-approvals></div></section></div><section class="content-panel"><div class="panel-header"><h2 class="panel-title">Runner / n8n 事件</h2></div><div class="runner-events" data-runner-events></div></section>`;
  node.addEventListener('submit', e => {
    if (e.target.id !== 'runner-create-form') return;
    e.preventDefault();
    const data = new FormData(e.target), goal = String(data.get('goal')).trim();
    if (!goal) { actionError = '请填写运行目标。'; render(); return; }
    if (!health?.n8n?.reachable || !health?.n8n?.configured) return;
    void mutate('/api/runs', { template: data.get('template'), goal, max_concurrency: Number(data.get('max_concurrency')), max_cycles: Number(data.get('max_cycles')), deadline_seconds: 300 }, '运行已创建；等待 n8n 编排，进度以后端状态为准。', true);
  });
  node.addEventListener('change', e => {
    if (e.target.id === 'runner-template') render();
    if (e.target.id === 'runner-run-select' && !busy) { selectedId = e.target.value; run = null; confirmingCancel = false; revision++; message = ''; actionError = ''; render(); void refresh(); }
  });
  node.addEventListener('click', e => {
    const button = e.target.closest('button');
    if (!button || button.disabled) return;
    if (button.id === 'runner-refresh') { void refresh(); return; }
    if (!selectedId || !run || busy) return;
    if (button.id === 'runner-keep-running') { confirmingCancel = false; render(); return; }
    if (button.id === 'runner-confirm-cancel') { confirmingCancel = false; void mutate(`/api/runs/${encodeURIComponent(selectedId)}/control`, { action: 'cancel' }, '取消请求已记录；等待查询确认运行状态。'); return; }
    if (button.dataset.runnerControl) {
      const action = button.dataset.runnerControl;
      if (action === 'cancel') { confirmingCancel = true; render(); panel.querySelector('#runner-confirm-cancel').focus(); return; }
      void mutate(`/api/runs/${encodeURIComponent(selectedId)}/control`, { action }, `${{ pause: '暂停', resume: '恢复' }[action]}请求已记录；等待查询确认运行状态。`);
    }
    if (button.dataset.runnerDecision) void mutate(`/api/runs/${encodeURIComponent(selectedId)}/approvals/${encodeURIComponent(button.dataset.approvalId)}`, { decision: button.dataset.runnerDecision }, '审批已记录；等待 n8n 后续处理，不代表任务或集成已完成。');
  });
  return node;
}

export function mountRunner(host) {
  unmountRunner();
  panel ||= createPanel(); host.append(panel); active = true; generation++; render(); void poll();
}
export function unmountRunner() {
  active = false; generation++; clearTimeout(timer);
  for (const controller of controllers) controller.abort();
}
