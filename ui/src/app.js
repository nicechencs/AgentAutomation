import { createDemoState, agentModels, workflowTemplates } from './demo.js';
import { mountRunner, unmountRunner } from './runner.js';

let state = createDemoState();
let view = location.hash === '#runner' ? 'runner' : 'tasks';
let filter = 'all';
let query = '';
let detailTab = 'overview';
let flowIndex = 2;
let templateIndex = 0;
let extraNodes = [];
let templates = structuredClone(workflowTemplates);
let settings = { concurrency: 2, rounds: 3, minutes: 45, approval: true };
const root = document.querySelector('#app');
const dialog = document.querySelector('#app-dialog');
const names = { coding: '编码中', review: '审查中', approval: '待审批', queued: '待执行', completed: '已完成', blocked: '已阻塞', paused: '已暂停', failed: '失败', integrated: '已合并', ready: '待合并', pending: '等待检查', passed: '检查通过', conflict: '存在冲突' };
const classes = { coding: 'running', review: 'review', approval: 'approval', queued: 'queued', completed: 'done', blocked: 'failed', paused: 'paused', failed: 'failed', integrated: 'done', ready: 'approval', pending: 'queued', passed: 'done', conflict: 'failed' };
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[x]));
const icon = (name, size = 18) => {
  const paths = {
    tasks: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 9 1 1 2-2m2 1h3m-8 6 1 1 2-2m2 1h3"/>',
    flow: '<rect x="3" y="3" width="7" height="6" rx="1"/><rect x="14" y="15" width="7" height="6" rx="1"/><path d="M6 9v9h8m3-3V6h-7"/>',
    merge: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M6 7v10m12-10c0 7-12 3-12 9"/>',
    repo: '<path d="M4 4h13a2 2 0 0 1 2 2v15l-4-2-4 2V6a2 2 0 0 0-2-2H4v16h7"/><path d="M6 8h2m-2 4h2"/>',
    settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    pause: '<path d="M8 5v14m8-14v14"/>',
    play: '<path d="m8 5 11 7-11 7Z"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  };
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.tasks}</svg>`;
};
const badge = status => `<span class="status status-${classes[status] || 'queued'}"><span aria-hidden="true">${['completed', 'integrated', 'passed'].includes(status) ? '✓' : status === 'blocked' || status === 'conflict' ? '!' : '●'}</span> ${esc(names[status] || status)}</span>`;
const project = () => state.projects.find(p => p.id === state.selectedProjectId) || state.projects[0];
const tasks = () => state.tasks.filter(t => t.projectId === state.selectedProjectId);
const selected = () => tasks().find(t => t.id === state.selectedTaskId) || tasks()[0];
const taskById = id => state.tasks.find(t => t.id === id);
const btn = (action, text, type = '', data = '') => `<button type="button" class="button ${type}" data-action="${action}" ${data}>${text}</button>`;
const activeRun = () => state.run.projectId === state.selectedProjectId;
const modelOptions = (agent, chosen) => {
  const models = agentModels[agent] || ['演示模型'];
  return models.map(m => `<option value="${esc(typeof m === 'string' ? m : m.id)}" ${(typeof m === 'string' ? m : m.id) === chosen ? 'selected' : ''}>${esc(typeof m === 'string' ? m : m.label || m.id)}</option>`).join('');
};

function render() {
  unmountRunner();
  const p = project();
  const title = { runner: 'n8n 运行', tasks: '任务工作台', flow: '流程编排', integration: '集成与清理', repos: '项目仓库', settings: '运行设置' }[view];
  const nav = [['runner', 'n8n 运行'], ['tasks', '任务工作台'], ['flow', '流程编排'], ['integration', '集成与清理'], ['repos', '项目仓库'], ['settings', '运行设置']];
  root.innerHTML = `<div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#tasks" data-view="tasks"><span class="brand-mark">${icon('flow', 23)}</span><span>Agent<span class="brand-light">Automation</span></span></a>
      <div class="project-switch"><label for="project-select" class="section-label">${view === 'runner' ? '演示项目（不影响 n8n）' : '当前项目'}</label><select id="project-select" ${view === 'runner' ? 'disabled' : ''}>${state.projects.map(x => `<option value="${esc(x.id)}" ${x.id === p.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select><span class="help-text">一个活动项目 · 多任务并行</span></div>
      <nav aria-label="主导航">${nav.map(([key, label]) => `<button class="nav-item" data-view="${key}" ${view === key ? 'aria-current="page"' : ''}><span class="nav-icon">${icon(key)}</span>${label}${key === 'integration' ? `<span class="nav-count">${state.integrations.filter(i => i.status !== 'integrated').length}</span>` : ''}</button>`).join('')}</nav>
      <div class="sidebar-footer"><div class="environment-dot"></div><strong>${view === 'runner' ? '本地 Runner 连接页' : '本地交互原型'}</strong><p>${view === 'runner' ? 'n8n / FAKE AGENT' : 'UI PREVIEW · V0.1'}</p><span>${view === 'runner' ? '连接状态见页面 · 无真实 CLI / Git' : '当前工作台使用演示数据'}</span></div>
    </aside>
    <main class="main">
      <header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> ${view === 'runner' ? '本地 Runner' : esc(p.name)} <span>/</span> <strong>${title}</strong></div><div class="top-actions"><span class="pill">${icon('shield', 14)} ${view === 'runner' ? 'n8n · Fake Agent' : '可信本地仓库'}</span><button class="icon-button" data-action="theme" aria-label="切换明暗主题">${icon('sun')}</button><span class="avatar">C</span></div></header>
      ${view === 'runner' ? '<div class="demo-banner"><span><strong>n8n 运行</strong> · 本页连接本地 Runner；n8n 连接与执行状态见下方，任务使用 Fake Agent，不执行真实 CLI 或 Git。</span><button class="button ghost small" data-view="tasks">返回演示工作台</button></div>' : '<div class="demo-banner"><span><strong>交互预览</strong> · 所有项目、日志和执行结果均为模拟数据；按钮不会运行 CLI 或修改 Git。</span><button class="button ghost small" data-view="runner">连接 n8n 运行</button><button class="button ghost small" data-action="reset">重置演示</button></div>'}
      <section class="page-heading"><div><div class="eyebrow">${view === 'tasks' ? 'RUN CONTROL / 执行与排查' : 'WORKSPACE / 项目管理'}</div><h1>${title}</h1><p class="page-description">${{ runner: '提交运行目标、查看后端状态并处理审批。流程由 n8n 推进，页面定期查询 Runner。', tasks: '查看任务如何并行推进，在审批、审查和合并前掌握每个决定。', flow: '从预置流程开始，为每个 Agent 节点选择执行器与模型。', integration: '先验证组合结果，再更新目标分支。任务与清理结果分别记录。', repos: '注册多个仓库，每次运行一个项目。切换项目不会自动启动任务。', settings: '设置有边界的自动化，保留人工介入和恢复的入口。' }[view]}</p></div><div class="page-actions">${view === 'tasks' ? `${btn('toggle-run', `${icon(state.run.status === 'paused' ? 'play' : 'pause', 15)} ${state.run.status !== 'running' ? '恢复演示' : '暂停运行'}`, 'ghost', !activeRun() ? 'disabled' : '')}${btn('new-task', `${icon('plus', 15)} 新建任务`, 'primary')}` : view === 'repos' ? btn('new-project', `${icon('plus', 15)} 注册仓库`, 'primary') : view === 'flow' ? btn('add-node', `${icon('plus', 15)} 添加节点`, 'primary') : ''}</div></section>
      ${view === 'runner' ? '<div id="runner-host"></div>' : view === 'tasks' ? renderTasks() : view === 'flow' ? renderFlow() : view === 'integration' ? renderIntegration() : view === 'repos' ? renderRepos() : renderSettings()}
      <footer class="page-footnote">AgentAutomation / ${view === 'runner' ? 'n8n 编排验证' : '首版方向验证'} <span>${view === 'runner' ? 'Runner 状态 · Fake Agent · 无真实 CLI / Git' : '演示状态仅保留至刷新'}</span></footer>
    </main></div>`;
  if (view === 'runner') mountRunner(root.querySelector('#runner-host'));
}

function renderTasks() {
  const list = tasks().filter(t => (filter === 'all' || (filter === 'attention' ? ['approval', 'blocked'].includes(t.status) : t.status === filter)) && `${t.title} ${t.id}`.toLowerCase().includes(query.toLowerCase()));
  return `<div class="run-strip"><div><span class="status-dot ${state.run.status === 'paused' ? '' : 'live'}"></span><strong>${activeRun() ? state.run.status === 'paused' ? '运行已暂停' : state.run.status === 'cancelled' ? '运行已取消' : '演示运行进行中' : '项目尚未运行'}</strong><span class="mono muted">${activeRun() ? esc(state.run.id) : '—'}</span></div><span>并发上限 <strong>${settings.concurrency}</strong> <span class="muted">/</span> 调用 ${esc(state.run.budget?.callsUsed || 12)} / ${esc(state.run.budget?.callsLimit || 60)} <span class="muted">· 模拟值</span></span></div>
    <div class="workspace-grid"><section class="content-panel"><div class="panel-header"><div><h2 class="panel-title">开发任务 <span class="muted">${tasks().length}</span></h2><p class="panel-subtitle">${activeRun() ? esc(state.run.goal) : '选择任务，或新建一个演示任务。'}</p></div><span class="pill mono">${esc(project().branch || 'integration/demo')}</span></div>
    <div class="toolbar"><div class="tabs">${[['all', '全部'], ['coding', '执行中'], ['attention', '待处理'], ['completed', '已完成']].map(([key, name]) => `<button class="tab ${filter === key ? 'active' : ''}" data-filter="${key}">${name}</button>`).join('')}</div><label class="search-wrap">${icon('search', 15)}<input id="task-search" class="search-input" placeholder="搜索任务或编号" value="${esc(query)}" aria-label="搜索任务"></label></div>
    <div class="task-list">${list.length ? list.map(t => `<button class="task-row ${selected()?.id === t.id ? 'selected' : ''}" data-task="${esc(t.id)}"><span class="task-symbol ${classes[t.status] || 'queued'}">${icon(t.type === 'document' || t.type === 'analysis' || t.type === 'local-doc' ? 'repo' : 'tasks', 19)}</span><span class="task-main"><span class="task-id mono">${esc(t.id)} <span class="muted">${t.type === 'document' || t.type === 'analysis' || t.type === 'local-doc' ? '· 本地产物' : '· 开发'}</span></span><span class="task-title">${esc(t.title)}</span><span class="task-meta"><span class="agent-tag">${esc(t.agent === 'claude' ? 'Claude Code' : 'Codex')}</span><span>${esc(t.dependencies?.length ? `依赖 ${t.dependencies.join(', ')}` : '可独立执行')}</span></span></span><span class="task-status">${badge(t.status)}<span class="task-time">${esc(t.elapsed || '尚未开始')}</span></span><span class="task-chevron">${icon('chevron', 15)}</span></button>`).join('') : '<div class="empty-state">没有匹配的任务。试试其他筛选，或新建一个演示任务。</div>'}</div>
    <div class="panel-bottom"><span>${icon('flow', 15)} 独立任务并行 · 依赖满足后启动 · 目标分支串行合并</span><button class="button ghost small" data-view="flow">查看流程 ${icon('arrow', 14)}</button></div></section>${renderInspector()}</div>`;
}

function renderInspector() {
  const t = selected();
  if (!t) return '<aside class="inspector"><div class="empty-state">选择一个任务查看执行详情。</div></aside>';
  const stages = [['planning', 'Planning', '分析与拆解'], ['coding', 'Coding', '独立工作区实现'], ['review', 'Review', '新会话独立审查'], ['fix', 'Fix / 复审', '有界修复'], ['commit', 'Commit', '通过后提交']];
  const isLocal = !t.worktree;
  const integrationApproval = t.stage === 'integration-approval';
  if (isLocal) stages.splice(0, stages.length, ['analysis', '分析与生成', '独立 Agent 会话'], ['validation', '产物检查', '内容与格式验证'], ['review', '内容 Review', '独立检查产物'], ['archive', '归档', '保存本地文件']);
  let current = t.status === 'completed' ? stages.length : integrationApproval ? 5 : t.status === 'queued' || t.status === 'blocked' ? 0 : t.stage === 'fix' ? 3 : t.status === 'coding' ? 1 : 2;
  return `<aside class="inspector"><div class="inspector-header"><div class="eyebrow mono">${esc(t.id)} / 任务详情</div><h2>${esc(t.title)}</h2><div class="inline-actions">${badge(t.status)}<button class="button ghost small" data-action="configure">${icon('settings', 14)} 配置</button></div></div><div class="detail-tabs tabs">${[['overview', '概览'], ['review', '审查'], ['logs', '日志']].map(([key, name]) => `<button class="tab ${detailTab === key ? 'active' : ''}" data-detail="${key}">${name}</button>`).join('')}</div><div class="inspector-body">
    ${detailTab === 'overview' ? `<p class="detail-description">${esc(t.description)}</p><div class="detail-grid"><div><span class="section-label">执行 Agent</span><span class="detail-value">${esc(t.agent === 'claude' ? 'Claude Code' : 'Codex')}</span></div><div><span class="section-label">尝试 / Review 轮次</span><span class="detail-value mono">${t.attempt || 1} / ${t.reviewRound || 1}</span></div></div><div class="section-label">执行阶段 · 每阶段新会话</div><div class="stage-list">${stages.map(([key, name, caption], i) => `<div class="stage-item"><span class="stage-number ${i < current ? 'done' : i === current ? 'current' : ''}">${i < current ? '✓' : i + 1}</span><div><strong class="stage-title">${name}</strong><span class="stage-caption">${esc(t.stageConfig?.[key]?.agent ? `${t.stageConfig[key].agent === 'claude' ? 'Claude Code' : 'Codex'} · ${caption}` : caption)}</span></div><span class="stage-state">${i < current ? '完成' : i === current ? '当前' : '等待'}</span></div>`).join('')}</div><div class="section-label">验收标准</div><ul class="acceptance-list">${(t.acceptance || ['产物满足任务输入要求', '验证及独立审查通过']).map(a => `<li>${esc(a)}</li>`).join('')}</ul>${t.worktree ? `<div class="section-label">隔离工作区</div><div class="code-path mono">${esc(t.worktree)}</div><span class="help-text mono">${esc(t.branch)}</span>` : '<div class="callout">本地产物任务，无需 Git 提交与合并。</div>'}` : detailTab === 'review' ? `<div class="section-label">结构化审查结果</div><div class="callout"><strong>${t.review?.verdict === 'pass' ? 'PASS · 审查通过' : t.status === 'approval' ? 'CHANGES REQUESTED · 等待修复确认' : 'Review 证据预览'}</strong><p>结果与当前候选绑定。修改代码后需要重新验证。</p></div>${(t.review?.findings || []).map(f => `<div class="finding"><span class="pill">${esc(f.severity || 'medium')}</span><strong>${esc(f.title || f.message || f.description || f)}</strong><p class="mono muted">${esc(f.file || f.location || '候选代码')}</p><p>${esc(f.description || f.detail || '需修改后重新执行验证及独立 Review。')}</p></div>`).join('') || '<div class="empty-state">当前没有阻断问题。仍需满足项目配置的验证条件。</div>'}<div class="section-label">检查记录 · 演示</div>${(t.checks || []).map(c => `<div class="check-row">${icon('check', 15)} ${esc(typeof c === 'string' ? c : c.name || c.command || '项目检查')} <span class="muted">${esc(c.status || '')}</span></div>`).join('')}` : `<div class="section-label">阶段日志 · 模拟输出</div><div class="log-box">${(t.logs || []).map(l => `<div class="log-line"><span class="log-time">${esc(l.time)}</span><span>${esc(l.message)}</span></div>`).join('') || '<div class="log-line">等待任务启动。</div>'}</div><div class="callout">日志、测试和候选内容共同用于排查。CLI 正常退出不等于任务验收通过。</div>`}
    </div><div class="inspector-footer">${t.status === 'approval' ? `${integrationApproval ? btn('merge', `${icon('check', 15)} 审批集成`, 'primary', `data-id="${esc(state.integrations.find(i => i.taskId === t.id)?.id || '')}"`) : btn('approve-task', `${icon('check', 15)} 批准并修复`, 'primary')}${btn('reject-task', '拒绝', 'ghost')}` : t.status === 'queued' || t.status === 'blocked' ? btn('start-task', `${icon('play', 15)} 模拟启动任务`, 'primary') : t.status === 'coding' ? btn('advance-task', '模拟进入 Review', 'primary') : t.status === 'review' ? `${btn('pass-task', '模拟审查通过', 'primary')}${btn('request-fix', '要求修复', 'ghost')}` : t.status === 'completed' ? btn('show-integration', `查看集成结果 ${icon('arrow', 15)}`, 'ghost') : btn('task-logs', '查看日志', 'ghost')}</div></aside>`;
}

function flowNodes() {
  const template = templates[templateIndex] || templates[0];
  return [...(template?.nodes || []), ...extraNodes];
}
function renderFlow() {
  const nodes = flowNodes();
  const node = nodes[Math.min(flowIndex, nodes.length - 1)];
  const nAgent = node?.agent || 'codex';
  const agentDisabled = node?.type !== 'agent' ? 'disabled' : '';
  return `<section class="content-panel"><div class="panel-header"><div><h2 class="panel-title">预置流程</h2><p class="panel-subtitle">这里验证节点与交互，实际编排将由 n8n 承担。</p></div><select id="template-select" aria-label="选择流程模板">${templates.map((t, i) => `<option value="${i}" ${i === templateIndex ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></div><div class="flow-track">${nodes.map((n, i) => `${i ? '<span class="flow-arrow" aria-hidden="true">→</span>' : ''}<button class="flow-node ${flowIndex === i ? 'selected' : ''}" data-node="${i}"><span class="section-label">${esc(n.type || 'Agent')}</span><strong>${esc(n.label)}</strong><span class="agent-tag">${n.agent ? n.agent === 'claude' ? 'Claude Code' : 'Codex' : n.type === 'approval' ? '人工决定' : 'Runner / 条件'}</span></button>`).join('')}</div><div class="flow-note">${icon('flow', 16)} Review 未通过 → 新 Fix 会话 → 重新验证与 Review；到达上限后停止。</div></section>
    <div class="workspace-grid flow-settings"><section class="content-panel"><div class="panel-header"><div><h2 class="panel-title">节点配置 <span class="muted">/ ${esc(node?.label || '未选择')}</span></h2><p class="panel-subtitle">项目默认 → 任务配置 → 节点覆盖。此处修改仅用于预览。</p></div><span class="pill">${esc(node?.type || 'agent')}</span></div><form id="flow-form" class="form-content"><div class="form-grid"><label class="field">节点名称<input id="node-label" name="label" value="${esc(node?.label)}" required></label><label class="field">执行 Agent<select id="node-agent" name="agent" ${agentDisabled}><option value="codex" ${nAgent === 'codex' ? 'selected' : ''}>Codex CLI</option><option value="claude" ${nAgent === 'claude' ? 'selected' : ''}>Claude Code CLI</option></select></label><label class="field">模型 · 模拟配置<select id="node-model" name="model" ${agentDisabled}>${modelOptions(nAgent, node?.model)}</select></label><label class="field">超时（分钟）<input id="node-timeout" type="number" min="1" max="120" name="timeout" value="${node?.timeout || 15}"></label></div><label class="check-row"><input type="checkbox" checked disabled> 每次执行启动独立新会话</label><label class="check-row"><input type="checkbox" id="node-readonly" ${node?.readonly ?? String(node?.label).toLowerCase().includes('review') ? 'checked' : ''} ${agentDisabled}> 尽可能使用只读权限</label><div class="modal-footer">${btn('save-node', '保存节点预览', 'primary')}</div></form></section><aside class="inspector"><div class="inspector-header"><h2>流程边界</h2></div><div class="inspector-body"><div class="callout"><strong>可配置，但保持简单</strong><p>首版支持 Agent、判断、审批、等待与 Runner 操作，不另建通用流程引擎。</p></div><div class="section-label">预览可以验证</div><ul class="acceptance-list"><li>每个节点使用不同 Agent / 模型</li><li>在关键动作前加入审批判断</li><li>开发任务与本地产物分别完成</li><li>失败分支和有界修复的入口</li></ul></div></aside></div>`;
}

function renderIntegration() {
  const list = state.integrations.filter(i => taskById(i.taskId)?.projectId === state.selectedProjectId);
  const done = tasks().filter(t => t.status === 'completed');
  return `<div class="workspace-grid"><section class="content-panel"><div class="panel-header"><div><h2 class="panel-title">目标分支集成队列</h2><p class="panel-subtitle">任务并行执行，同一目标分支依次更新。</p></div><span class="pill">串行 merge</span></div><div class="card-list">${list.map(i => { const t = taskById(i.taskId); return `<article class="integration-card"><div class="inline-actions"><span class="mono muted">${esc(i.id)}</span>${badge(i.status)}</div><h3>${esc(t?.title || i.taskId)}</h3><div class="branch-route mono">${esc(t?.branch || 'task/demo')} <span>→</span> ${esc(i.targetBranch)}</div><p class="muted">${i.status === 'integrated' ? '组合检查通过，已模拟更新目标。' : i.conflict ? '存在冲突，保留现场并等待人工处理。' : '准备隔离候选，检查通过且获批后更新目标。'}</p><div class="inline-actions">${i.status !== 'integrated' ? btn('merge', i.conflict ? '查看冲突' : '检查并审批合并', 'primary small', `data-id="${esc(i.id)}"`) : '<span class="help-text">✓ 集成完成 · 演示结果</span>'}${btn('inspect-integration', '查看证据', 'ghost small', `data-id="${esc(i.id)}"`)}</div></article>`; }).join('') || '<div class="empty-state">该项目没有集成候选。任务 Review 通过并提交后，会进入此队列。</div>'}</div></section><aside class="inspector"><div class="inspector-header"><h2>合并门禁</h2><p class="panel-subtitle">无冲突机械合并可以复用任务 Review。</p></div><div class="inspector-body"><div class="check-row">${icon('check', 16)} 任务提交已有有效 Review</div><div class="check-row">${icon('check', 16)} 组合检查通过</div><div class="check-row">${icon('check', 16)} 目标 HEAD 未变化</div><div class="check-row">${icon('check', 16)} 按项目策略取得审批</div><div class="callout">冲突解决产生的新修改，必须重新验证并接受针对性 Review。</div></div></aside></div><section class="content-panel cleanup-panel"><div class="panel-header"><div><h2 class="panel-title">工作区与分支清理</h2><p class="panel-subtitle">人工触发。未保存、未集成或活跃任务的成果会保留。</p></div><span class="pill">仅本地任务资源</span></div><div class="card-list">${done.map(t => `<div class="cleanup-row"><div><strong>${esc(t.id)} · ${esc(t.title)}</strong><p class="mono muted">${esc(t.branch || '本地产物 · Git 清理不适用')}</p></div><div class="inline-actions">${t.cleanup?.cleaned ? '<span class="status status-done">✓ 已清理 · 模拟</span>' : t.worktree ? `${t.integrated ? badge('integrated') : '<span class="status status-queued">尚未集成</span>'}${btn('cleanup', '安全清理', 'ghost small', `data-id="${esc(t.id)}"`)}` : '<span class="muted">不适用</span>'}</div></div>`).join('') || '<div class="empty-state">暂无已完成任务。</div>'}</div></section>`;
}

function renderRepos() {
  return `<div class="repo-grid">${state.projects.map(p => `<article class="content-panel repo-card"><div class="inline-actions"><span class="repo-emblem">${icon('repo', 24)}</span><span class="pill">${p.id === state.run.projectId ? '演示活动项目' : '已注册 · 未运行'}</span></div><h2>${esc(p.name)}</h2><p class="muted">${esc(p.description || '用于验证多仓库注册与单项目运行。')}</p><div class="section-label">本地路径 · 示例</div><div class="code-path mono">${esc(p.path)}</div><div class="section-label">集成目标</div><div class="mono">${esc(p.branch || 'integration/demo')}</div><div class="modal-footer">${btn('select-project', '打开项目', 'ghost', `data-id="${esc(p.id)}"`)}</div></article>`).join('')}</div><div class="callout">首版每个项目关联一个仓库。注册仓库与启动项目分开；这里的注册只添加浏览器内的演示记录，不访问磁盘。</div>`;
}

function renderSettings() {
  return `<section class="content-panel"><div class="panel-header"><div><h2 class="panel-title">运行限制</h2><p class="panel-subtitle">默认值可调整，启动后保留有效配置。</p></div><span class="pill">项目级 · 演示</span></div><form id="settings-form" class="form-content"><div class="form-grid"><label class="field">最大并发任务数<input id="max-concurrency" name="concurrency" type="number" min="1" max="6" value="${settings.concurrency}" required><span class="help-text">独立任务可并行，共享资源按需互斥。</span></label><label class="field">最大 Review 轮数<input id="max-rounds" name="rounds" type="number" min="1" max="10" value="${settings.rounds}" required></label><label class="field">运行时间上限（分钟）<input id="max-minutes" name="minutes" type="number" min="1" max="240" value="${settings.minutes}" required></label><label class="field">费用信息<input value="仅估算，不承诺精确费用硬上限" disabled></label></div><label class="check-row"><input id="approval-required" type="checkbox" name="approval" ${settings.approval ? 'checked' : ''}> 更新集成目标前人工审批</label><div class="settings-section"><h3>执行与恢复</h3><p class="muted">暂停阻止新节点启动；取消会终止相关执行。重启先核对进程、Git 和状态，不确定时交人工恢复。</p><div class="inline-actions">${btn('recover', '预览人工恢复', 'ghost')}${btn('cancel-run', '取消演示运行', 'danger')}</div></div><div class="settings-section"><h3>默认边界</h3><div class="check-row">${icon('shield', 16)} 不自动 Push、不部署、不向外部系统写入</div><p class="help-text">Worktree 是目录隔离，不代表安全沙箱；真实权限仍需执行环境验证。</p></div><div class="modal-footer"><button class="button primary" type="submit">保存演示设置</button></div></form></section>`;
}

function toast(message) {
  const el = document.createElement('div'); el.className = 'toast'; el.textContent = message;
  document.querySelector('#toasts').append(el); setTimeout(() => el.remove(), 4200);
}
function openDialog(title, body, footer = '', onSubmit) {
  dialog.innerHTML = `<form id="dialog-form"><div class="modal-header"><h2>${esc(title)}</h2><button type="button" class="icon-button" data-action="close-dialog" aria-label="关闭">${icon('close')}</button></div><div class="modal-body">${body}</div><div class="modal-footer">${btn('close-dialog', '取消', 'ghost')} ${footer}</div></form>`;
  dialog.showModal();
  dialog.querySelector('form').addEventListener('submit', e => { e.preventDefault(); onSubmit?.(new FormData(e.currentTarget)); });
}
function addLog(task, message) { (task.logs ||= []).push({ time: new Date().toLocaleTimeString('zh-CN', { hour12: false }), level: 'info', message: `[模拟] ${message}` }); }
function newTask() {
  openDialog('新建演示任务', `<p class="help-text">只添加原型中的任务，不调用 Agent 或访问仓库。</p><label class="field">任务标题<input id="new-task-title" name="title" placeholder="例如：为查询接口添加参数校验" required maxlength="80"></label><div class="form-grid"><label class="field">任务类型<select id="new-task-type" name="type"><option value="coding">开发任务</option><option value="document">本地文档 / 分析</option></select></label><label class="field">执行 Agent<select id="new-task-agent" name="agent"><option value="codex">Codex CLI</option><option value="claude">Claude Code CLI</option></select></label></div><label class="field">验收标准<textarea id="new-task-acceptance" name="acceptance" rows="3" placeholder="描述可检查的完成条件" required></textarea></label>`, '<button class="button primary" type="submit">添加演示任务</button>', data => {
    const id = `TASK-${Math.max(100, ...state.tasks.map(t => Number(t.id.split('-')[1]) || 0)) + 1}`;
    const git = data.get('type') === 'coding';
    state.tasks.push({ id, projectId: state.selectedProjectId, title: data.get('title').trim(), description: data.get('acceptance'), type: data.get('type'), agent: data.get('agent'), model: (agentModels[data.get('agent')] || [])[0], status: 'queued', stage: 'coding', dependencies: [], acceptance: [data.get('acceptance')], logs: [], review: { findings: [] }, branch: git ? `task/${id.toLowerCase()}` : null, worktree: git ? `D:/workspaces/demo/${id}` : null, cleanup: { cleaned: false }, integrated: false });
    state.selectedTaskId = id; filter = 'all'; query = ''; view = 'tasks'; dialog.close(); render(); toast('已添加演示任务，真实执行尚未接入。');
  });
}
function configureTask() {
  const t = selected();
  openDialog(`${t.id} · 节点配置`, `<p class="help-text">项目默认 → 任务配置 → 节点覆盖。模型名称为模拟选项。</p>${['coding', 'review'].map(stage => { const agent = t.stageConfig?.[stage]?.agent || t.agent || 'codex'; return `<div class="settings-section"><h3>${stage === 'coding' ? 'Coding 节点' : 'Review 节点'}</h3><div class="form-grid"><label class="field">Agent<select id="config-${stage}-agent" name="${stage}-agent" data-config-agent="${stage}"><option value="codex" ${agent === 'codex' ? 'selected' : ''}>Codex CLI</option><option value="claude" ${agent === 'claude' ? 'selected' : ''}>Claude Code CLI</option></select></label><label class="field">模型<select id="config-${stage}-model" name="${stage}-model">${modelOptions(agent, t.stageConfig?.[stage]?.model)}</select></label></div></div>`; }).join('')}`, '<button class="button primary" type="submit">保存配置预览</button>', data => {
    t.stageConfig ||= {};
    for (const s of ['coding', 'review']) t.stageConfig[s] = { agent: data.get(`${s}-agent`), model: data.get(`${s}-model`) };
    t.agent = t.stageConfig.coding.agent; t.model = t.stageConfig.coding.model; addLog(t, '更新了 Coding / Review 演示配置'); dialog.close(); render(); toast('已保存任务节点配置预览。');
  });
}
function handleAction(action, el) {
  const t = selected();
  switch (action) {
    case 'close-dialog': dialog.close(); break;
    case 'theme': { const dark = document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches); document.documentElement.dataset.theme = dark ? 'light' : 'dark'; break; }
    case 'reset': state = createDemoState(); extraNodes = []; templates = structuredClone(workflowTemplates); settings = { concurrency: 2, rounds: 3, minutes: 45, approval: true }; filter = 'all'; query = ''; view = 'tasks'; detailTab = 'overview'; render(); toast('已恢复初始演示数据。'); break;
    case 'new-task': newTask(); break;
    case 'configure': configureTask(); break;
    case 'toggle-run': state.run.status = state.run.status !== 'running' ? 'running' : 'paused'; render(); toast(state.run.status === 'paused' ? '演示已暂停，模拟启动操作将被阻止。' : '演示已恢复。'); break;
    case 'approve-task': if (state.run.status !== 'running') return toast('请先恢复演示运行。'); t.status = 'coding'; t.stage = 'fix'; t.attempt = (t.attempt || 1) + 1; addLog(t, '审批通过，创建新的 Fix 会话'); render(); toast('已模拟批准，进入新的 Fix 会话。'); break;
    case 'reject-task': t.status = 'blocked'; for (const i of state.integrations.filter(i => i.taskId === t.id)) i.status = 'blocked'; addLog(t, '审批被拒绝，任务保留现场'); render(); toast('已拒绝，演示任务进入阻塞状态。'); break;
    case 'start-task': {
      if (activeRun() && (state.run.status === 'paused' || state.run.status === 'cancelled')) return toast('运行已暂停或取消，请先恢复演示。');
      if (t.dependencies?.some(id => !taskById(id)?.integrated)) return toast('前置成果尚未集成，演示任务继续等待。');
      if (tasks().filter(x => ['coding', 'review'].includes(x.status)).length >= settings.concurrency) return toast('已达到并发上限。可先完成一个任务，或调整演示设置。');
      if (!activeRun() && state.run.status !== 'cancelled' && state.tasks.some(x => x.projectId === state.run.projectId && ['coding', 'review', 'approval'].includes(x.status))) return toast('另一个项目仍有活动任务，首版仅运行一个项目。');
      if (!activeRun()) { state.run.projectId = state.selectedProjectId; state.run.status = 'running'; }
      t.status = 'coding'; addLog(t, '已创建演示执行尝试'); render(); toast('已模拟启动任务。'); break;
    }
    case 'advance-task': if (state.run.status === 'paused' || state.run.status === 'cancelled') return toast('请先恢复演示运行。'); t.status = 'review'; t.reviewRound = (t.reviewRound || 0) + 1; addLog(t, '验证结束，新会话进入 Review'); detailTab = 'review'; render(); break;
    case 'request-fix': if ((t.reviewRound || 1) >= settings.rounds) { t.status = 'blocked'; addLog(t, '到达 Review 上限，等待人工处理'); } else { t.status = 'approval'; addLog(t, 'Review 请求修改，等待修复审批'); } render(); toast('已模拟要求修改。'); break;
    case 'pass-task': {
      if (state.run.status === 'paused' || state.run.status === 'cancelled') return toast('运行停止期间不能推进提交。');
      t.status = 'completed'; t.review = { verdict: 'pass', findings: [] }; addLog(t, t.worktree ? 'Review 通过，模拟生成任务提交并进入集成队列' : '产物通过验收，归档结果');
      if (t.worktree && !state.integrations.some(i => i.taskId === t.id)) state.integrations.push({ id: `INT-${state.integrations.length + 1}`, taskId: t.id, status: 'ready', targetBranch: project().branch || 'integration/demo', targetHead: 'demo-base', candidate: 'demo-candidate', conflict: false });
      render(); toast(t.worktree ? '已模拟通过审查与任务提交，可前往集成。' : '本地产物已模拟完成，无需 Git 集成。'); break;
    }
    case 'task-logs': detailTab = 'logs'; render(); break;
    case 'show-integration': view = 'integration'; render(); break;
    case 'select-project': state.selectedProjectId = el.dataset.id; state.selectedTaskId = tasks()[0]?.id; view = 'tasks'; render(); break;
    case 'new-project': openDialog('注册演示仓库', '<p class="help-text">不会检查磁盘，输入仅用于页面预览。</p><label class="field">项目名<input id="new-project-name" name="name" required maxlength="50"></label><label class="field">本地路径<input id="new-project-path" name="path" placeholder="D:/projects/example" required></label>', '<button class="button primary" type="submit">添加演示记录</button>', data => { state.projects.push({ id: `project-${Date.now()}`, name: data.get('name'), path: data.get('path'), branch: 'integration/demo', description: '新注册的演示项目，尚未连接磁盘。' }); dialog.close(); render(); toast('已添加演示仓库记录。'); }); break;
    case 'add-node': openDialog('添加流程节点', '<label class="field">节点类型<select id="new-node-type" name="type"><option value="approval">人工审批</option><option value="condition">条件判断</option><option value="agent">Agent 执行</option><option value="wait">等待</option></select></label><label class="field">节点名称<input id="new-node-label" name="label" required placeholder="例如：更新目标前确认"></label><p class="help-text">原型追加至流程末尾，用于验证节点与配置入口；不是运行中的 n8n 流程。</p>', '<button class="button primary" type="submit">添加节点预览</button>', data => { extraNodes.push({ id: `custom-${Date.now()}`, type: data.get('type'), label: data.get('label') }); flowIndex = flowNodes().length - 1; dialog.close(); render(); toast('已添加节点预览。'); }); break;
    case 'save-node': { const form = document.querySelector('#flow-form'); if (!form.reportValidity()) return; const data = new FormData(form); const node = flowNodes()[flowIndex]; if (node) { node.label = data.get('label'); if (node.type === 'agent') { node.agent = data.get('agent'); node.model = data.get('model'); node.readonly = form.querySelector('#node-readonly').checked; } node.timeout = Number(data.get('timeout')); } render(); toast('已更新节点预览配置。'); break; }
    case 'merge': {
      const i = state.integrations.find(x => x.id === el.dataset.id);
      if (!i) return toast('当前任务没有可集成候选。');
      if (i.status === 'blocked' && !i.conflict) return toast('集成审批已拒绝，保留现场。重置演示可重试。');
      if (i.conflict) { openDialog('集成冲突 · 保留现场', `<div class="callout"><strong>需要人工处理</strong><p>${esc(i.taskId)} 与目标分支存在模拟冲突。首版不会自动解决冲突。</p></div><p>处理后需要针对性 Review 和必要检查，再继续集成。当前演示不会修改文件。</p>`, btn('close-dialog', '知道了', 'primary')); break; }
      if (state.run.status === 'paused' || state.run.status === 'cancelled') return toast('运行停止期间不能更新集成目标。');
      openDialog('集成检查与审批 · 模拟', `<div class="check-row">${icon('check', 16)} 任务 Review：通过</div><div class="check-row">${icon('check', 16)} 组合检查：模拟通过</div><div class="check-row">${icon('check', 16)} 目标 HEAD：模拟未变化</div><div class="code-path mono">${esc(i.targetBranch)}</div><div class="callout">这是浏览器中的状态演示，不会执行 git merge 或更新真实分支。</div>`, '<button class="button primary" type="submit">批准模拟合并</button>', () => { i.status = 'integrated'; const task = taskById(i.taskId); if (task) { task.integrated = true; task.status = 'completed'; task.stage = 'integrated'; task.cleanup = { eligible: true, cleaned: false, reason: '已模拟合并，可预览安全清理' }; addLog(task, '模拟集成成功'); } dialog.close(); render(); toast('模拟集成完成，真实 Git 未改变。'); }); break;
    }
    case 'inspect-integration': { const i = state.integrations.find(x => x.id === el.dataset.id); openDialog('集成证据 · 演示', `<div class="detail-grid"><div><span class="section-label">目标 HEAD</span><span class="detail-value mono">${esc(i.targetHead)}</span></div><div><span class="section-label">候选</span><span class="detail-value mono">${esc(i.candidate)}</span></div></div><p>状态：${esc(names[i.status] || i.status)}</p><div class="callout">任务审查与集成验证分别记录。这里的哈希和检查均为示例，不代表真实执行。</div>`, btn('close-dialog', '关闭', 'primary')); break; }
    case 'cleanup': { const task = taskById(el.dataset.id); if (!task.integrated || task.cleanup?.eligible === false) return toast(task.cleanup?.reason || '任务成果尚未集成，不能清理。'); openDialog('确认安全清理 · 模拟', `<p>将模拟清理以下任务资源：</p><div class="code-path mono">${esc(task.worktree)}<br>${esc(task.branch)}</div><div class="check-row">${icon('check', 16)} 已集成、无活跃进程、证据已归档（模拟）</div><p class="help-text">不会删除真实目录或分支；任务和集成记录继续保留。</p>`, '<button class="button danger" type="submit">确认模拟清理</button>', () => { task.cleanup.cleaned = true; dialog.close(); render(); toast('已模拟清理，真实目录和分支未改变。'); }); break; }
    case 'recover': openDialog('人工恢复预览', '<div class="check-row">1. 停止新的任务领取</div><div class="check-row">2. 核对残留进程与工作区</div><div class="check-row">3. 核对 Git 与 SQLite 操作记录</div><div class="check-row">4. 确认重试或保留现场</div><p class="help-text">首版允许人工恢复，不承诺自动无缝续跑。原型没有读取真实进程或数据库。</p>', btn('close-dialog', '了解恢复路径', 'primary')); break;
    case 'cancel-run': openDialog('取消演示运行', '<p>演示状态将标记为已取消，现有任务和产物记录仍保留。</p><p class="help-text">没有真实进程需要终止，可通过“重置演示”恢复。</p>', '<button class="button danger" type="submit">确认取消演示</button>', () => { state.run.status = 'cancelled'; dialog.close(); render(); toast('演示运行已取消。'); }); break;
  }
}

document.addEventListener('click', e => {
  const v = e.target.closest('[data-view]');
  if (v) { e.preventDefault(); view = v.dataset.view; render(); return; }
  const task = e.target.closest('[data-task]');
  if (task) { state.selectedTaskId = task.dataset.task; detailTab = 'overview'; render(); return; }
  const f = e.target.closest('[data-filter]');
  if (f) { filter = f.dataset.filter; render(); return; }
  const d = e.target.closest('[data-detail]');
  if (d) { detailTab = d.dataset.detail; render(); return; }
  const n = e.target.closest('[data-node]');
  if (n) { flowIndex = Number(n.dataset.node); render(); return; }
  const action = e.target.closest('[data-action]');
  if (action) { e.preventDefault(); handleAction(action.dataset.action, action); }
});
document.addEventListener('change', e => {
  if (e.target.id === 'project-select') { state.selectedProjectId = e.target.value; state.selectedTaskId = tasks()[0]?.id; filter = 'all'; query = ''; render(); }
  if (e.target.id === 'template-select') { templateIndex = Number(e.target.value); extraNodes = []; flowIndex = 0; render(); }
  if (e.target.id === 'node-agent') document.querySelector('#node-model').innerHTML = modelOptions(e.target.value);
  if (e.target.dataset.configAgent) dialog.querySelector(`#config-${e.target.dataset.configAgent}-model`).innerHTML = modelOptions(e.target.value);
});
document.addEventListener('input', e => {
  if (e.target.id === 'task-search') { const pos = e.target.selectionStart; query = e.target.value; render(); const field = document.querySelector('#task-search'); field.focus(); field.setSelectionRange(pos, pos); }
});
document.addEventListener('submit', e => {
  if (e.target.id !== 'settings-form') return;
  e.preventDefault(); const data = new FormData(e.target); settings = { concurrency: Number(data.get('concurrency')), rounds: Number(data.get('rounds')), minutes: Number(data.get('minutes')), approval: data.has('approval') }; state.run.maxConcurrency = settings.concurrency; toast('已保存演示设置，真实 Runner 未连接。');
});
render();
