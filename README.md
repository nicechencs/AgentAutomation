# AgentAutomation

本地自托管的 Agent 任务自动化系统，使用 n8n 编排、Python/FastAPI Runner 执行、SQLite 持久化，接入 Claude Code CLI 和 OpenAI Codex CLI。

当前实现真实执行接口与单任务闭环：明确指定 CLI/模型和检查命令，经计划审批后进入 worktree 执行、检查、独立新会话审查、有界修复、提交及隔离集成。默认真实模式，缺 CLI、模型或检查配置会拒绝，不回退 Fake。Codex 已在独立示例仓库跑通真实开发闭环；两种 CLI 的结构化调用成功，Claude 写入受现有权限阻断。n8n 端到端验证仍待 Docker 引擎与凭据/发布配置就绪。安装检查、自动测试和真实环境验收分别记录。

## Windows 宿主执行

真实 Runner/CLI/Git 在 Windows 宿主运行，Compose 仅管理 n8n。先按 [真实执行与部署说明](docs/real-execution.md) 私下配置 `deploy/.env`，在 n8n 手工创建 Header Auth 凭据并导入、绑定、发布当前两条工作流。

```powershell
python -m pip install -e .
# 用户准备好 Docker Desktop 和本地配置后执行：
docker compose --env-file deploy/.env -f deploy/compose.host.yaml up -d
# 另一终端启动宿主 Runner，Ctrl+C 停止：
./deploy/start-runner.ps1
```

打开 http://127.0.0.1:8000/#runner 使用真实 API 与持久化记录，API 文档在 `/api/docs`。创建运行须填写目标仓库/分支（开发模板）、实现 CLI/模型、审查 CLI/模型与 argv 格式检查命令；详细参数和 API 示例见 [真实执行说明](docs/real-execution.md)。默认数据目录为 `.local/runner/`。

宿主 Runner 监听 `0.0.0.0:8000`，供 n8n 通过 `host.docker.internal:8000` 调用。UI 无 token 请求仅允许本机同源访问；n8n 读请求与阶段调用必须有有效 Runner token。启动脚本不修改防火墙。不要同时启动旧 `deploy/compose.yaml` 的容器 Runner。

旧 [n8n Fake 接入说明](docs/n8n-integration.md) 保留历史拓扑、凭据操作和静态验证记录；真实拓扑以 [宿主部署说明](docs/real-execution.md) 与当前工作流为准。原“流程编排”页面的编辑不会修改已导入的 n8n 工作流。

## UI 交互原型

```sh
npm --prefix ui run dev
```

打开 http://127.0.0.1:4173 体验任务详情、新建任务、配置、流程节点与审批。此页面仅在浏览器内模拟，刷新重置，不执行 CLI、检查或 Git 操作。详见 [原型走查说明](ui/README.md)。真实运行请使用 Runner 的 `/#runner` 入口。

## 测试与能力边界

后端自动测试（PowerShell）：

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

真实实现目前按单任务闭环执行，`max_concurrency` 字段不代表已实现多任务并行。Agent 各阶段使用新会话；Review/Fix 次数有上限。计划、集成或产物需要相应审批；默认不自动 Push、部署、清理 worktree/分支或修复合并冲突。失败时保留证据供检查，不能将 Fake 的通过记录作为真实检查或审查结果。

当前 Windows 环境是首个验证目标，不代表跨平台验收完成。已通过的真实示例、CLI 写入权限与完整 n8n 链路的待验收项见 [验证状态](docs/real-execution.md#验证状态与验收)；本轮自动测试结果由最终交付报告补充。

## 文档入口

- [真实执行与部署](docs/real-execution.md)：宿主部署、手工 n8n 配置、模型与 API 参数、证据和待验收项。
- [首版开发计划](docs/development-plan.md)：任务、依赖、里程碑与验收映射；计划内容不代表已实现或已验证状态。
- [当前需求基线](docs/requirements-current.md)：首版正式范围、默认策略与后置能力。
- [分阶段验收清单](docs/acceptance-criteria.md)：首版必过、能力引入时必过与后续增强。
- [设计取舍](docs/requirements-analysis.md)：简化原因。
- [原始需求](docs/requirements-original.md)：历史来源；冲突处以当前需求基线为准。
