# AgentAutomation

本地自托管的 Agent 任务自动化系统，使用 n8n 编排、Python/FastAPI Runner 执行、SQLite 持久化，计划接入 Claude Code CLI 和 OpenAI Codex CLI。

当前已有需求文档、UI 交互原型、持久化 Fake Runner，以及两条 n8n 工作流和 Compose 配置。Runner/API 与 UI 接线已测试；n8n 工作流尚未完成凭据配置、导入发布及端到端验证。真实 CLI 和 Git 自动化尚未实现。

## UI 预览

```sh
npm --prefix ui run dev
```

打开 http://127.0.0.1:4173 ，无需安装运行依赖。可体验任务详情、新建任务、Agent/模型配置、流程节点、审批、集成与安全清理等交互。所有操作仅在浏览器内模拟，刷新重置。详见 [原型运行与走查说明](ui/README.md)。

## n8n 接入与 Fake Runner

```sh
python -m pip install -e .
python -m uvicorn agentautomation.app:app --app-dir src --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/#runner 。此入口使用真实 API 和 SQLite；缺少 n8n 配置时会显示未就绪并禁用创建，不会静默回退到演示。默认数据库位于 `.local/runner/`，可通过 `DATA_DIR` 覆盖。API 文档在 `/api/docs`。

完整容器拓扑、两条模板、Header Auth 凭据和发布步骤见 [n8n 接入说明](docs/n8n-integration.md)。本轮容器 Runner 仅用于 Fake 接入验证；生产主机 CLI、Review/Fix 循环和真实 Git 门禁仍待后续开发。原“流程编排”页面的编辑不会修改已导入的 n8n 工作流。

后端测试（PowerShell）：

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

## 首版范围

- 注册多个目标仓库，一次运行一个项目；首版每个项目关联一个仓库。
- 两种 CLI 可选，支持项目→任务→节点的 Agent/模型配置；各 Agent 阶段使用新会话。
- 独立任务使用 Worktree/分支，优先并行；依赖或资源受限时串行。
- n8n 提供开发流程和本地文档/分析流程，可加入判断、审批和等待；Runner 保持状态与 Git 门禁权威。
- 任务 Review/Fix 有界，通过后提交；同一目标分支串行 merge，并执行集成检查。无新增改写的机械合并可复用任务 Review，冲突停下交人工处理。
- 支持人工触发的安全 Worktree/本地任务分支清理、暂停、取消、超时和重启核对；异常允许人工恢复。
- 暂以当前 Windows 环境为首个验证基准，Runner/CLI 在主机运行，Compose 管理 n8n；这不是已验证的平台支持声明。

首版不做通用流程引擎、自动冲突修复、自动清理调度、自动接管、外部写入连接器或三平台同步交付。默认禁止自动 Push 和部署。

## 文档入口

- [首版开发计划](docs/development-plan.md)：11 个实现任务、依赖与并行分工、5 个里程碑及验收映射；已先完成 UI 并实现 Fake Runner/n8n 接入薄层，完整后端里程碑尚未达成。
- [当前需求基线](docs/requirements-current.md)：首版正式范围、默认策略及后置能力，是当前需求的唯一基线。
- [分阶段验收清单](docs/acceptance-criteria.md)：区分首版必过、能力引入时必过和后续增强；尚未执行测试。
- [设计取舍](docs/requirements-analysis.md)：说明简化原因，不再维护重复的需求或验收矩阵。
- [原始需求](docs/requirements-original.md)：保留首次提供的正文，作为历史来源；冲突处以当前需求基线为准。

模型、测试命令、并发上限等使用项目配置或有记录的默认值；缺少关键输入、能力或授权时才阻止运行。实际合并、删除和外部操作仍须遵循运行时配置与授权，文档更新不代表已经执行这些操作。
