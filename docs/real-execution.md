# Windows 宿主真实执行

真实 Runner 在 Windows 宿主执行 Claude Code / Codex CLI、检查命令与 Git 操作；n8n 在 Docker Desktop Linux 容器中编排。浏览器访问 `http://127.0.0.1:8000/#runner`，Runner 访问 `http://127.0.0.1:5678/webhook`，n8n 访问 `http://host.docker.internal:8000`。本轮已执行最小真实模型探针与独立示例仓库验证；未启动或发布 n8n，以下部署命令供配置就绪后执行。

## 入口与当前边界

- `npm --prefix ui run dev` 的 4173 页面是浏览器内交互原型，刷新重置；原型操作不执行 CLI、测试、提交或合并。
- Runner 提供的 `/#runner` 页面连接 API 与 SQLite；创建表单默认 `real`，须明确填写模型与检查命令。应在详情中核对阶段、会话、检查、审查、审批及 Git 证据。
- `fake` 仅是显式选择的演示模式，不能代表真实验证结果。缺少 CLI、显式模型或检查配置时，真实运行拒绝执行，不回退 Fake。
- 当前真实流程按单任务闭环执行。`max_concurrency` 是兼容字段，不代表已实现多任务并行调度。
- 开发流程使用真实 Git worktree/任务分支、独立检查和审查、有界修复、提交与隔离集成。计划与集成有人工审批。不会自动 Push、部署或删除 worktree/分支；冲突和失败须检查后处理。
- 本地文档流程使用每次运行独立的产物目录，不接收目标 Git 仓库，产物审批后归档。

## 准备

需要 Python >=3.11、Git >=2.38（隔离合并树核对使用 `merge-tree --write-tree`）、所选 CLI 和已配置可用的账户。Claude 与 Codex 可同选一个提供方，但执行与审查均须单独指定模型；审查通过独立新会话完成，不继承实现会话。每个 Agent 调用都新建会话。

```powershell
python -m pip install -e .
Get-Command claude,codex,git
claude --version
codex --version
```

CLI 版本/帮助成功只证明安装和参数存在，不能证明账户、模型权限、额度、网络或沙箱工具执行可用。模型名必须是该账户实际可访问的明确名称；文档不预填模型，以免把示例误作可用配置。CLI 保留自身权限策略；权限拒绝会导致失败，不自动开启权限旁路。Claude 的首次写入许可等行为仍须真实联调确认。

在仓库根目录将 `deploy/.env.example` 复制为 `deploy/.env`，私下填写 `N8N_ENCRYPTION_KEY`、`RUNNER_TOKEN`、`WORKFLOW_TOKEN`，并保持 `RUNNER_HTTP_PORT=8000`。不要将实际值放入命令行、日志、截图或版本控制。加密 key 应固定保存并随 n8n 数据备份；更换它可能导致已有凭据无法解密。

启动脚本只识别上述四个键，不执行 `.env`；只接受单行字面量及可选的成对外引号，不支持变量展开、反引号、井号、重复键或 shell 语句。为使 Compose 与宿主解析一致，这些值中不要使用 `$`、反引号或 `#`。不读取 `.env` 中的其他配置；`DATA_DIR` 通过脚本参数覆盖，默认仓库 `.local/runner`。若目标仓库包含该数据目录，Git 工作区会放在 `%LOCALAPPDATA%/AgentAutomation/workspaces/<run_id>/`，保持任务 worktree 在目标仓库外；实际路径写入运行记录。

## 启动拓扑

只使用宿主版 Compose 文件，不与 `compose.yaml` 合并，也不要同时启动旧容器 Runner。

```powershell
# 静态检查不展开密钥；不会连接 Docker daemon。
docker compose --env-file deploy/.env.example -f deploy/compose.host.yaml config --no-interpolate --quiet
# 配置准备好后由用户执行；--quiet 避免打印展开后的配置。
docker compose --env-file deploy/.env -f deploy/compose.host.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.host.yaml up -d
# 另一个 PowerShell 窗口前台运行；Ctrl+C 停止。
./deploy/start-runner.ps1
```

可用 `./deploy/start-runner.ps1 -Python 'C:/path/to/python.exe' -DataDir 'D:/private/runner-data'` 指定解释器与数据目录。脚本不安装依赖，不启动 Docker，不设置防火墙规则，不显示密钥。

宿主 Runner 必须监听 `0.0.0.0:8000` 才能让 Docker Desktop 容器访问。UI 无 token 访问须同时满足真实 loopback 客户端和 localhost Host/同源要求；n8n 的读操作和阶段调用须携带有效 `X-Runner-Token`。不要向局域网开放 UI、使用公开反向代理或将 Host 检查视为网络隔离。若 Windows 防火墙阻断 Docker→宿主连接，需要用户根据实际 Docker 网络手工配置最小允许规则，本项目不会自动更改防火墙。8000 被占用时应先处理冲突，不能只改端口而保留工作流旧地址。

Compose 项目名与 `n8n_data` 沿用 `agentautomation`，可接续原 n8n 数据；它只管理 n8n，不删除原 Runner 容器或卷。切换前手工停止旧 Runner 服务并确认端口。不要使用 `down -v` 清理持久化数据。

## 手工绑定 n8n

从 n8n 编辑器初始化并登录所有者账户。两条静态工作流仍需手工导入、绑定和发布，详见 [n8n 接入说明](n8n-integration.md) 的凭据步骤；该历史说明中的容器 Runner 地址与 Fake 专用 gate 以当前工作流及本文为准。

1. 在 Credentials 创建 Header Auth：`aa-runner-header`，Header Name 为 `X-Runner-Token`；`aa-webhook-header`，Header Name 为 `X-Workflow-Token`。值只在编辑器私下填写。
2. 导入当前 `workflows/development.json` 与 `workflows/local-document.json`。确认所有 Runner HTTP 节点指向 `http://host.docker.internal:8000`，并接受 real 模式和真实检查/审查证据。旧 Fake-only 定义不可直接用于真实运行。
3. 每个 Runner HTTP 节点手工选择实际 `aa-runner-header`，两个 Production webhook 选择 `aa-webhook-header`。名称占位符不等于真实 credential ID。
4. 保存并 Publish 两条工作流，使用 `/webhook/agentautomation-development` 与 `/webhook/agentautomation-local-document`；Runner 不调用 `/webhook-test/`。202 仅表示 webhook 接收成功。

不自动创建、导入凭据或发布工作流。n8n `/healthz` 可达也不代表工作流已正确发布；需结合 n8n Executions 和 Runner 阶段记录验证。

## API 示例

`POST /api/runs` 默认 `mode=real`。以下示例必须先修改仓库路径、目标分支、目标和两处模型，再由用户执行；它会创建真实运行并调用 n8n，不属于静态检查。检查命令是 argv 数组的数组，直接运行，不使用 shell 字符串；`&&`、管道及重定向不能作为 shell 语法使用。命令在任务目录或隔离集成目录执行，应适合干净 worktree，不能依赖原工作区未提交文件或原目录独有的虚拟环境。

```powershell
$body = @{
    mode = 'real'
    template = 'development'
    goal = '填写具体、可验收的目标'
    repository_path = 'D:/projects/your-repository'
    target_branch = 'main'
    agent = 'codex'
    model = 'REPLACE_WITH_YOUR_AVAILABLE_MODEL'
    reviewer = 'claude'
    review_model = 'REPLACE_WITH_YOUR_AVAILABLE_REVIEW_MODEL'
    check_commands = ,@('python', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    max_fixes = 1
    deadline_seconds = 1800
    max_concurrency = 1
} | ConvertTo-Json -Depth 8
$run = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/runs' `
    -ContentType 'application/json' -Headers @{'X-UI-Request'='1'} -Body $body
# 查询不需要把 token 放在命令行；仅限本机 UI 来源。
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/runs/$($run.id)"
```

`max_fixes` 范围 0–3，默认 1；`deadline_seconds` 默认 1800、允许 30–3600。`max_cycles` 默认 20、允许 1–30，限制阶段调用次数。修复次数上限不表示保证成功：检查、审查或结构化输出失败不能伪装为通过。开发模板要求可用的 Git 仓库和目标分支；本地文档模板使用 `template='local-document'` 且省略 `repository_path`/`target_branch`，仍须提供 agent/model/reviewer/review_model/check_commands。

审批使用 `POST /api/runs/{run_id}/approvals/{approval_id}`，JSON 为 `{"decision":"approved"}` 或 `{"decision":"rejected"}`；控制使用 `POST /api/runs/{run_id}/control`，JSON 为 `{"action":"pause"}`、`resume` 或 `cancel`，均需要本机 UI 与 `X-UI-Request: 1`。暂停不再启动新的 Agent/检查会话，已在途会话可收尾；取消/超时会停止 CLI 进程树，保留工作区。Git 提交和最终目标更新按临界区串行处理：若不可中断的 Git 写入已开始，取消请求等待该操作完成并落证据，随后阻止后续阶段；取消不回滚已完成提交或集成。暂停、取消、拒绝和超时的实际效果应结合当前进程与阶段证据核对。不要重复手工发送同一 run 的 webhook；响应丢失时先核对 execution。

## 验证状态与验收

2026-10-17 本地完整回归：100 项 Python 测试通过；UI JavaScript 语法检查、24 组 real/fake 工作流门禁与表单契约检查、宿主 Compose 静态解析通过。Python 自动测试的 Agent 输出使用注入响应；其中实际文件、Git、进程树取消与检查命令真实执行。下面单独记录真实模型示例，避免混淆两类证据。

本次环境只读检查发现 Claude Code 2.1.302、Codex CLI 0.163.0、Python 3.13.5 与 Git 2.55.0 已安装；Docker 客户端 29.6.1、Compose v5.3.0 可用，但 `desktop-linux` daemon 的 named pipe 不存在，默认 n8n `/healthz` 不可达。这是检查时的状态，不代表后续启动后的结果。

已实际验证：Codex CLI 使用 `gpt-6.1-sol`、Claude Code CLI 使用本机已配置的 `codex/gpt-6.1-sol` 均返回成功结构化探针；后者是该本机 CLI 的模型配置，不代表 Anthropic 原生模型已验收。Codex 在新建的专用示例仓库实际修改文件、运行检查、独立新会话审查、创建任务提交、在独立集成工作区执行组合检查并更新示例目标分支，最终 completed。证据位于 `.local/live-examples/6599bec5-66ba-4f45-8d75-3d0c58a182e8/evidence.json`，该示例直接驱动 Store 阶段，**不是 n8n 端到端验证**。示例自动批准仅适用于脚本自己新建的测试仓库，产品运行仍需人工点击审批。

Codex 的本地文档流程也已实际生成 `result.md`、运行内容检查、独立审查并完成产物审批归档，证据位于 `.local/live-examples/fb5d9e23-94f1-4ca1-9e48-2bb6f6db3a05/evidence.json`。Claude 文档写入示例的 Write 被现有账户权限拒绝，运行停止；未更改权限或改用其他身份重试被拒绝的运行。Codex 示例是另一次新建的独立验收，不是该失败运行的自动回退。Windows 进程树取消、超时、真实 Git 冲突/检查失败/候选变化阻断已用本地进程和临时仓库测试覆盖。尚待验证：Claude 的获授权写入；Docker→宿主网络与 Header Auth；工作流导入发布；经 n8n 的完整审批闭环。未改动用户业务仓库或发布工作流。

CLI 按当前账户权限执行；工作目录和提示词不是操作系统沙箱。Runner 专用 token 不传入 Agent 环境，provider 认证由本机 CLI 管理。部署时使用专用账户与受控项目目录；本版本不宣称提供跨账户文件系统隔离。

验收建议先在专用测试仓库运行一次最小开发任务：人工核对计划再批准，检查独立会话 ID、argv/退出码、审查结论、修复计数、任务提交、集成检查与目标分支结果；再分别验证拒绝、取消、超时和检查失败。最后验证本地文档产物审批。日志与 SQLite 可能包含项目内容及模型输出，应保存在本机受控目录，不公开发布。
