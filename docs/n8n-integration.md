# n8n 静态接入（Fake Runner）

已实现持久化 Fake Runner、UI 接线、两条静态工作流与 Compose 配置。宿主机 Runner/UI 已启动，26 项测试通过，n8n 镜像实际版本确认为 2.43.7；Runner 镜像构建及显式 `DATA_DIR=/data` 的模块入口检查通过。尚未配置 n8n 凭据、导入发布工作流或完成端到端联调。用户已选择手工配置 `aa-runner-header` 与 `aa-webhook-header` 两项凭据，Agent 未生成或导入实际凭据；操作路径如下。真实 Agent CLI、Git 操作及 M4 未完成，Fake 的 check/review/commit/integrate 结果不代表真实项目测试、审查、提交或合并。

## 文件与版本

- `workflows/development.json`：开发模板，默认未发布。
- `workflows/local-document.json`：本地文档模板，默认未发布。
- `workflows/generate.py`：只生成上述静态 JSON，不读取环境、不访问 secret store、不创建或导入凭据。
- `deploy/compose.yaml`：独立 Compose 项目 `agentautomation`。
- `deploy/Dockerfile`：`python:3.13-slim`，COPY `pyproject.toml`、`src/`、`ui/` 后 `pip install .`。
- `deploy/.env.example`：三个空值占位符，不含实际密钥。

n8n 固定为 `docker.n8n.io/n8nio/n8n:2.43.7`。协调端报告该镜像摘要为 `sha256:3db21366be1d3c77c1708963129c051948573019659e8873e0294be035ffc1b2`；本静态文件任务没有拉取或运行镜像。Runner 基础镜像按要求使用 Python 3.13-slim；该标签本身不是 digest 固定。

Runner 启动命令为 `uvicorn agentautomation.app:app --app-dir /app/src --host 0.0.0.0 --port 8000`。显式 app-dir 确保 app 的 `__file__` 来自 `/app/src`，静态 UI 根目录正确指向 `/app/ui`。容器使用非 root 的 `runner` 用户，镜像默认 `DATA_DIR=/data`，Compose 显式保持同一配置。

## 拓扑与默认保护

| 服务 | 宿主机地址 | 容器内部地址 | 持久化 |
| --- | --- | --- | --- |
| n8n | `http://127.0.0.1:5678` | `http://n8n:5678` | `n8n_data:/home/node/.n8n` |
| Runner/UI | `http://127.0.0.1:8000` | `http://runner:8000` | `runner_data:/data` |

宿主机端口仅绑定 loopback。浏览器可使用 `http://localhost:5678` 访问 n8n 编辑器并完成默认账户初始化；保留 n8n 默认认证、cookie、环境访问和节点安全限制，没有无认证旁路或环境访问放松。Runner 不向 n8n 传递任何 secret 环境变量；工作流只引用两条 Header Auth credential。不要为接入设置关闭安全保护的环境变量。

Runner 配置固定为 `DATA_DIR=/data`、`N8N_WEBHOOK_BASE=http://n8n:5678/webhook`。`RUNNER_TOKEN` 和 `WORKFLOW_TOKEN` 仅由用户本地 `.env` 提供；n8n 的 `N8N_ENCRYPTION_KEY` 同样由用户提供并保持固定。Compose 不会把两个 Runner token 送入 n8n 环境，Code 节点不读取环境。

## 用户后续准备与启动

以下命令均从仓库根目录执行，且本轮没有执行。

1. 在本地将 `deploy/.env.example` 复制为 `deploy/.env`。用户自行填写 `N8N_ENCRYPTION_KEY`、`RUNNER_TOKEN`、`WORKFLOW_TOKEN`，不要把实际值贴进文档、工作流、命令行或版本控制。三个值不能为空。固定加密 key 用来解密已保存凭据，重启或重建时必须保留，并与 n8n 数据备份妥善保存。
2. 当前宿主机预览占用 8000；可在 `deploy/.env` 将 `RUNNER_HTTP_PORT=8001`，容器 UI 改从 `http://127.0.0.1:8001/#runner` 访问，n8n 内部调用地址仍是 `http://runner:8000`。若先关闭宿主预览，也可保留默认 8000。验证配置使用 `docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet`，避免普通 `config` 打印展开后的 secret。
3. 启动命令：`docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build`。
4. 打开 n8n 编辑器，按默认流程创建所有者账户并登录。Runner UI 为 `http://127.0.0.1:8000`。

不提供自动初始化、创建或导入 credentials 的脚本。也不要删除 `n8n_data` 后期待原凭据仍可解密。`docker compose down` 与 `down -v` 的数据行为不同；本说明不要求删除卷。

## 仅在编辑器手工配置两条凭据

在 n8n 编辑器的 Credentials 页面手工创建两条 **Header Auth**（内部类型 `httpHeaderAuth`）。凭据值只由用户在编辑器中输入，不执行 CLI/API 凭据导入。

| 凭据名称 | Header Name | Header Value（用户手工填） | 用途 |
| --- | --- | --- | --- |
| `aa-runner-header` | `X-Runner-Token` | 本地 `.env` 的 `RUNNER_TOKEN` | 所有 Runner GET 与 stage POST |
| `aa-webhook-header` | `X-Workflow-Token` | 本地 `.env` 的 `WORKFLOW_TOKEN` | 两条 production webhook 入站认证 |

保持名称完全一致。名称并不等于 n8n 自动分配的 credential ID。静态 JSON 的 `id/name` 都是上述名称占位引用；导入后必须手工打开节点选择刚创建的真实凭据，使 n8n 保存真实 ID，不能假设只按名称自动绑定成功。

## 导入、绑定、发布

推荐编辑器操作：在 Workflows 页面选择从文件导入，分别导入两份 JSON，保存各自工作流。不要导入凭据文件。

两份工作流都执行以下人工检查：

1. `Production webhook` 节点：POST、Header Auth，选择 `aa-webhook-header`，Path 分别为 `agentautomation-development` 与 `agentautomation-local-document`。
2. 所有 `GET before ...` 与 `POST ...` 节点：Generic Credential Type / Header Auth，逐一选择 `aa-runner-header`。development 有 14 个 HTTP 节点，local-document 有 10 个。
3. Code、If、Wait 节点保留导入定义；工作流 timeout 为 3900 秒。保存后确认无缺失凭据或节点错误。
4. 使用编辑器 **Publish** 发布两个工作流，确认 production webhook 已注册。Test URL `/webhook-test/...` 仅用于编辑器测试，Runner 不调用它。

生产入站地址：

- `POST http://n8n:5678/webhook/agentautomation-development`
- `POST http://n8n:5678/webhook/agentautomation-local-document`

两者只接收 JSON `{"run_id":"已由Runner创建的运行ID"}`，认证 header 为 `X-Workflow-Token`。Webhook 成功收到认证请求立即返回 202，后续 stage 错误记录在 execution；202 不表示流程已完成。UI 创建运行后由 Runner 发送 webhook，不应反复人工重发同一个 run。

CLI 备选只涉及工作流，以下是从官方固定版本源码核对的命令。本轮没有执行导入或发布：

```sh
# workflows 只读挂载在 /workflows；--separate 仅读取顶层 *.json。
docker compose --env-file deploy/.env -f deploy/compose.yaml exec n8n n8n import:workflow --separate --input=/workflows
# 导入后仍须在编辑器手工绑定凭据并保存。
# 下面的 ID 从编辑器 URL 获取，替换示意占位符后逐个发布。
docker compose --env-file deploy/.env -f deploy/compose.yaml exec n8n n8n publish:workflow --id=<development-workflow-id>
docker compose --env-file deploy/.env -f deploy/compose.yaml exec n8n n8n publish:workflow --id=<local-document-workflow-id>
# CLI 更改发布状态后，已运行的 n8n 必须重启才能应用。
docker compose --env-file deploy/.env -f deploy/compose.yaml restart n8n
```

`<...>` 是说明占位符，须替换后使用；PowerShell 不应原样执行含尖括号的命令。导入默认 inactive；不要使用 `--activeState=fromJson`。固定版本发布命令为 `publish:workflow --id=...`，没有批量 `--all` 发布支持，不使用已废弃的 `update:workflow --active=true`。首次导入前在编辑器完成所有者初始化。重复导入可能覆盖相同 ID 的工作流，修改前保留编辑器中的有效版本。

## 可审阅的阶段与 gate

development 的独立阶段为：

`plan → plan审批 → code → check → review → commit → integration审批 → integrate → archive`

local-document 的独立阶段为：

`plan → plan审批 → code → check → review → artifact审批 → archive`

每个阶段都在画布上展开为以下节点与连线：

1. `GET before <stage>`：认证请求 `GET /api/runs/{run_id}` 获取最新 run。
2. `Gate <stage>`：验证 run ID、模板、Fake 模式和状态；验证该 stage 的前置/刚完成 phase、approval 数组和有效审批。
3. `Terminal <stage>`：`completed/rejected/cancelled/failed/timed_out/interrupted` 路由到停止节点，不再启动 stage。
4. `Finished <stage>`：刚完成 phase 必须同时存在相同 `request_id` 的 receipt 和已完成 stage 记录，才转入下一个阶段。plan/commit/review 产生的审批会在下一阶段的 gate 等待。最后 archive 必须终态；非终态完成 receipt 走显式 Stop And Error。
5. review/commit/integrate/archive 前额外要求 `checks.status=passed` 且 `checks.source=fake`；commit/integrate/archive 前额外要求 `review.verdict=pass` 且 `review.source=fake`。失败或未知结果抛错，不进入下一阶段。
6. `Waiting <stage>`：暂停或任意 pending 审批进入 `Wait 2s <stage>`，然后回到同阶段 GET。`awaiting_approval` 却无 pending、缺失必要审批、重复/未知/非法审批、未知状态或非法 phase 都抛错关闭。
7. 执行分支认证请求 `POST /api/runs/{run_id}/stages/{stage}`，JSON 为 `{"request_id":"run_id:stage","execution_id":"当前$execution.id"}`；stage 响应是 run 对象。完成后回到同阶段 GET，重新观察状态并验证阶段回执，不盲目推进。

支持状态：`dispatching/running/awaiting_approval/paused/completed/rejected/cancelled/failed/timed_out/interrupted`；phase：`created/planned/coded/checked/reviewed/committed/integrated/archived`；审批 kind：`plan/integration/artifact`，status：`pending/approved/rejected`。

HTTP 节点超时 10 秒，非 2xx 与连接错误均失败 execution，没有 continueOnFail、neverError=true 或自动重试。暂停/取消与阶段启动间仍可能有并发时序，Runner 会再次检查状态；阶段响应后工作流重新 GET。因此暂停后不自动跨越下一阶段。

Fake stage 默认同步延迟 0.35 秒；code 由 Runner 控制最多两个 task 并行，n8n 不另起 task 并行。工作流不包含 Fix 循环。`max_cycles`、deadline 由 Runner 校验，默认 20 次 stage 调用与 300 秒；创建参数允许 max_cycles 1–30、deadline 30–3600 秒。development 正常调用 7 次 stage，local-document 5 次；2 秒 GET 轮询不增加 stage 次数。n8n executionTimeout 与全局上限固定 3900 秒，Runner deadline 在轮询 GET 时也会终止 run。不要将轮询或执行记录当作跨重启自动恢复承诺；Runner 对执行中断会停止并要求检查。

## 用户后续联调验收

这些操作需要用户手工填好环境值、创建凭据、绑定并发布工作流后进行，本轮未实测：

- UI 创建 development Fake run：到达 plan 审批，批准后经过 code/check/review/commit；integration 批准后 integrate/archive，最终 completed/archived。
- UI 创建 local-document Fake run：plan 审批后 code/check/review；artifact 批准后 archive，不执行 commit/integrate。
- 等待审批时暂停再批准，确保暂停期间 phase 不推进；恢复后继续。拒绝与取消应停止，终态不得启动下一阶段。
- 不批准直到 deadline，Runner 返回 timed_out，n8n 停止；设置小于正常阶段数的 max_cycles，确认 Runner 关闭运行。
- 凭据故意选错或 Runner 不可达时 execution 应报错；未知 phase/status 不应进入 POST。
- 查看 n8n Executions 和 Runner UI 记录，确认 execution ID、`run_id:stage` 请求回执和审批顺序一致。不要贴出凭据值或包含实际环境展开的配置输出。

静态校验可以运行：

```sh
python -I workflows/generate.py
# 不解析实际 .env、不输出环境值的语法检查：
docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --no-interpolate --quiet
```

本轮静态验收已通过：development 53 个节点、local-document 39 个节点；JSON 解析、节点 ID/名称与连接目标、两种凭据引用、HTTP 失败策略，以及 280 个 JavaScript gate 输入断言（推进、暂停、审批等待、全部终态、非法 phase/status、检查与审查失败）均通过。`docker compose ... config --no-interpolate --quiet` 也通过，未解析或显示 secret。本任务没有启动服务、构建 Runner 镜像、导入或发布工作流，也没有在 n8n 运行时验证节点导入、凭据绑定、持久化或完整审批流程。上述静态任务状态与协调端已完成的宿主机启动/镜像验证分别记录，不代表项目整体未运行。新增 Dockerfile 默认 `DATA_DIR` 后未重新构建验证；协调端此前通过的是显式设置该变量的镜像入口。固定镜像内 Code JavaScript 节点及默认 task runner 的实际运行同样未测，未为此放松任何安全限制。Compose 使用官方 `N8N_WEBHOOK_URL`，避免已弃用的 `WEBHOOK_URL` 别名。

## 官方依据

固定 `n8n@2.43.7` GitHub 源码用于核对节点参数与 CLI；示例不是从其他版本推测：

- [HTTP Request 版本映射](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/HttpRequest/HttpRequest.node.ts) 与 [V3 schema](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/HttpRequest/V3/Description.ts)：使用节点 4.2、`genericAuthType=httpHeaderAuth`、JSON body 和响应错误默认关闭。
- [Webhook 节点](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/Webhook/Webhook.node.ts) 与 [参数定义](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/Webhook/description.ts)：使用节点 2.1、`authentication=headerAuth`、立即响应及 v2 嵌套 responseCode。
- [Wait](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/Wait/Wait.node.ts)、[Code](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/Code/Code.node.ts)、[If V2](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/If/V2/IfV2.node.ts)、[Stop And Error](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/nodes-base/nodes/StopAndError/StopAndError.node.ts)。
- [工作流导入 CLI](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/cli/src/commands/import/workflow.ts)、[发布 CLI](https://github.com/n8n-io/n8n/blob/n8n%402.43.7/packages/cli/src/commands/publish/workflow.ts)、[官方 CLI 文档](https://docs.n8n.io/hosting/cli-commands/)：导入 inactive、逐个 publish、CLI 发布后重启。
