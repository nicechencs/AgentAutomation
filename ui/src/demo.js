export const agentModels = {
  claude: ['Claude · 标准模型（模拟）', 'Claude · 深度审查（模拟）'],
  codex: ['Codex · 编码模型（模拟）', 'Codex · 快速模型（模拟）'],
};

export const workflowTemplates = [
  {
    id: 'development', name: '开发闭环', type: 'code',
    nodes: [
      { id: 'planning', label: '任务规划', type: 'agent', agent: 'claude', model: agentModels.claude[0] },
      { id: 'plan-approval', label: '计划审批', type: 'approval' },
      { id: 'coding', label: '并行编码', type: 'agent', agent: 'codex', model: agentModels.codex[0] },
      { id: 'validation', label: '执行检查', type: 'validation' },
      { id: 'review', label: '独立 Review', type: 'agent', agent: 'claude', model: agentModels.claude[1] },
      { id: 'decision', label: '审查通过？', type: 'condition' },
      { id: 'fix', label: '修复与复审', type: 'agent', agent: 'codex', model: agentModels.codex[0] },
      { id: 'commit', label: '任务提交', type: 'git' },
      { id: 'integration-approval', label: '集成审批', type: 'approval' },
      { id: 'integration', label: '串行集成', type: 'git' },
    ],
  },
  {
    id: 'local-document', name: '本地文档 / 分析', type: 'local-doc',
    nodes: [
      { id: 'analysis', label: '分析与生成', type: 'agent', agent: 'claude', model: agentModels.claude[0] },
      { id: 'validation', label: '产物检查', type: 'validation' },
      { id: 'review', label: '内容 Review', type: 'agent', agent: 'codex', model: agentModels.codex[1] },
      { id: 'approval', label: '人工审批', type: 'approval' },
      { id: 'archive', label: '保存本地产物', type: 'artifact' },
    ],
  },
];

export function createDemoState() {
  const stageConfig = () => ({
    planning: { agent: 'claude', model: agentModels.claude[0] },
    coding: { agent: 'codex', model: agentModels.codex[0] },
    review: { agent: 'claude', model: agentModels.claude[1] },
    fix: { agent: 'codex', model: agentModels.codex[0] },
  });
  const task = (number, title, overrides = {}) => {
    const id = `TASK-${number}`;
    return {
      id, projectId: 'project-orbit', title, description: '虚构演示任务，所有执行与操作仅在原型中模拟。',
      type: 'code', status: 'queued', stage: 'planning', progress: 0, dependencies: [],
      agent: 'codex', model: agentModels.codex[0], branch: `task/${number}`,
      worktree: `D:/demo-workspaces/orbit/${id}`, attempt: 1, reviewRound: 0, elapsed: '尚未开始',
      acceptance: ['满足任务说明与范围', '必要检查通过', '独立 Review 通过后提交'],
      stageConfig: stageConfig(), review: { verdict: 'pending', findings: [] }, logs: [],
      checks: [], artifacts: [], cleanup: { eligible: false, reason: '任务成果尚未集成', cleaned: false },
      integrated: false, ...overrides,
    };
  };
  return {
    demo: true, notice: '交互原型 · 演示数据 · 不调用真实 Agent，不修改仓库',
    selectedProjectId: 'project-orbit', selectedTaskId: 'TASK-104',
    projects: [
      { id: 'project-orbit', name: '轨道任务板', description: '虚构示例 · 当前活动项目', path: 'D:/demo-repos/orbit-board', branch: 'integration/demo', status: 'running' },
      { id: 'project-paper', name: '纸飞机笔记', description: '虚构示例 · 已注册，未运行', path: 'D:/demo-repos/paper-notes', branch: 'integration/demo', status: 'idle' },
      { id: 'project-garden', name: '像素花园', description: '虚构示例 · 已注册，未运行', path: 'D:/demo-repos/pixel-garden', branch: 'integration/demo', status: 'idle' },
    ],
    run: {
      id: 'RUN-DEMO-024', projectId: 'project-orbit', status: 'running',
      goal: '完善任务板的筛选、状态展示与说明文档', maxConcurrency: 2, activeCount: 2,
      reviewLimit: 3, elapsed: '18 分 42 秒', approvalPolicy: '计划及集成前审批',
      budget: { callsUsed: 12, callsLimit: 30, minutesUsed: 18, minutesLimit: 60 },
    },
    tasks: [
      task(101, '补充任务状态与空白页面', {
        status: 'completed', stage: 'integrated', progress: 100, elapsed: '6 分 12 秒', integrated: true,
        reviewRound: 1, review: { verdict: 'pass', findings: [] },
        checks: [{ name: '状态转换检查', status: 'passed' }, { name: '空状态展示', status: 'passed' }],
        cleanup: { eligible: true, reason: '已集成、工作区干净、无活跃进程', cleaned: false },
        logs: [{ time: '10:06:12', level: 'success', message: '模拟集成完成，目标分支包含任务成果。' }],
      }),
      task(102, '实现任务列表筛选', {
        status: 'coding', stage: 'coding', progress: 58, elapsed: '4 分 08 秒', dependencies: ['TASK-101'],
        description: '支持按任务状态和关键词筛选，保留清晰的无匹配结果提示。',
        logs: [{ time: '10:17:03', level: 'info', message: '新的编码会话已启动（模拟），与 TASK-103 并行。' }, { time: '10:18:42', level: 'info', message: '正在补充筛选交互与键盘操作。' }],
        cleanup: { eligible: false, reason: '编码会话仍在运行', cleaned: false },
      }),
      task(103, '审查任务详情与依赖展示', {
        status: 'review', stage: 'review', progress: 76, agent: 'claude', model: agentModels.claude[1],
        elapsed: '2 分 31 秒', reviewRound: 1, dependencies: ['TASK-101'],
        checks: [{ name: '组件检查', status: 'passed' }, { name: '依赖展示检查', status: 'passed' }],
        review: { verdict: 'pending', findings: [{ id: 'F-103-1', severity: 'suggestion', file: 'task-detail.js:42', message: '建议为阻塞依赖补充跳转入口。', blocking: false }] },
        logs: [{ time: '10:17:50', level: 'info', message: '独立 Review 会话读取候选快照，不继承编码对话（模拟）。' }],
        cleanup: { eligible: false, reason: 'Review 会话仍在运行', cleaned: false },
      }),
      task(104, '提交任务看板快捷操作', {
        status: 'approval', stage: 'integration-approval', progress: 92, elapsed: '8 分 20 秒', reviewRound: 2,
        description: '暂停、重试与查看日志入口已完成；审查通过，等待批准集成至演示目标分支。',
        review: { verdict: 'pass', findings: [{ id: 'F-104-1', severity: 'minor', file: 'task-actions.js:28', message: '重复点击可能创建重复请求，已补充按钮锁定与请求去重。', blocking: false, resolved: true }] },
        checks: [{ name: '操作状态检查', status: 'passed' }, { name: '重复请求检查', status: 'passed' }, { name: '集成候选检查', status: 'passed' }],
        logs: [{ time: '10:12:10', level: 'info', message: '首轮审查要求修复重复点击问题。' }, { time: '10:15:24', level: 'success', message: '新的 Fix 与复审会话完成，审查通过（模拟）。' }, { time: '10:18:02', level: 'warning', message: '候选检查通过，等待人工集成审批。' }],
        artifacts: [{ name: '审查记录（演示）', path: 'demo-artifacts/TASK-104/review.json', type: 'review' }],
      }),
      task(105, '验证筛选与详情组合行为', {
        status: 'queued', stage: 'planning', dependencies: ['TASK-102', 'TASK-103'],
        description: '等待筛选与详情任务集成后，从包含前置成果的基线执行组合验证。',
        logs: [{ time: '10:18:05', level: 'info', message: '依赖未满足，等待前置成果，不启动空转会话。' }],
      }),
      task(106, '处理快捷键合并冲突', {
        status: 'blocked', stage: 'integration', progress: 88, elapsed: '5 分 16 秒', reviewRound: 1,
        review: { verdict: 'pass', findings: [] },
        description: '模拟的集成冲突需要人工处理，保留现场；解决后的新增修改需要针对性 Review。',
        cleanup: { eligible: false, reason: '存在未解决冲突与未集成成果，禁止正常清理', cleaned: false },
        logs: [{ time: '10:16:10', level: 'warning', message: '快捷键配置出现模拟冲突，目标分支未更新。' }],
      }),
      task(107, '整理使用说明与验收清单', {
        type: 'local-doc', status: 'completed', stage: 'archived', progress: 100, agent: 'claude', model: agentModels.claude[0],
        branch: null, worktree: null, elapsed: '3 分 05 秒', reviewRound: 1,
        acceptance: ['说明结构完整', '关键操作有示例', '产物能够供后继任务读取'],
        review: { verdict: 'pass', findings: [] },
        artifacts: [{ name: '使用说明（演示）', path: 'demo-artifacts/guide.md', type: 'markdown' }],
        cleanup: { eligible: false, reason: '非 Git 任务，Worktree 与分支清理不适用', cleaned: false },
        logs: [{ time: '10:14:30', level: 'success', message: '本地文档产物已归档（模拟），无需创建 Git 提交。' }],
      }),
    ],
    integrations: [
      { id: 'INT-104', taskId: 'TASK-104', targetBranch: 'integration/demo', status: 'approval', checks: 'passed', targetHead: 'demo-base-04', candidate: 'demo-candidate-104', conflict: false },
      { id: 'INT-106', taskId: 'TASK-106', targetBranch: 'integration/demo', status: 'blocked', checks: 'blocked', targetHead: 'demo-base-04', candidate: 'demo-candidate-106', conflict: true },
    ],
    activity: [
      { time: '10:18:42', level: 'info', message: 'TASK-102 与 TASK-103 正在并行执行（模拟）。' },
      { time: '10:18:02', level: 'warning', message: 'TASK-104 等待集成审批。' },
      { time: '10:16:10', level: 'warning', message: 'TASK-106 合并冲突已保留现场，等待人工处理。' },
      { time: '10:06:12', level: 'success', message: 'TASK-101 已集成，可手动安全清理。' },
    ],
  };
}
