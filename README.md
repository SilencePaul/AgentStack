# AgentStack · 智能体协作栈

面向可插拔架构的多项目 AI 协作平台，整合编码 Agent、即时通信与工单系统，实现目标拆解、团队调度、执行监督和统一汇报。

当前已实现 Qoder CLI、Mattermost 和 Multica 接入。DeepSeek Harness 适配尚未实现。

用户给目标；Router 规划并派到登记项目，按 developer → reviewer → tester → Lead 四个独立 Qoder 会话执行，由程序监控、验收并汇总。

## 已实现

- 明确派单 `/router run 项目ID 目标`，或使用 `/router goal 总目标` 调用无工具规划器。授权私信也可直接发送自然语言目标。项目不明确时返回澄清问题，不直接执行。
- 子任务保留不可变原始用户目标，规划建议不能扩大授权；角色只执行分配项目部分。明确提及的注册项目 ID 限定派单范围。
- 规划结果经过结构校验；原始显式 `bridge-checks` 的路径、操作符、预期值不得被规划器删改。所有子任务一次事务提交，重复来源不重复派单。
- 默认最多 2 个项目任务并行，同项目串行；项目路径必须存在且不重叠。
- 每个任务固定通用规范、项目规范、背景及最近三份同项目历史报告。背景与报告单独作为上下文，不提升为通用规范。
- SQLite 保存任务、事件、规划与发送 Outbox。MM 消息处理、去重标记和回复入队在同一事务中提交。
- 总时限、无输出时限和进程退出监控；失败、中断或超时暂停该项目，其余项目继续。Windows Job Object 管理执行进程树，重启不重跑可能已有副作用的任务。
- 四阶段由 Router 顺序启动独立会话。评审和测试必须给出明确 PASS；缺失或 BLOCKED 均不能继续。没有独立验收条件只能标记 `reported_unverified`。
- 跨项目目标提供汇总；有任务失败时先发待处理通知，不等所有排队任务结束。
- MM 频道命令和授权私信入口；消息回帖去重、独立游标、历史缺口检测。首次连接不消费历史工作。
- Multica 接入配置工作区内带 `[router-project:项目ID]` 标记的 todo 工单，持久化领取意图、去重创建本地任务、写回结果。只有独立验收通过才改为 done；未核验或失败保持开放。提交前和回写前检查范围与内容是否变化。
- 网络暂时故障退避重试；执行任务不自动重试。已识别的 WAF 报告拒绝记录为待人工处理，保留本地原文。

## 当前边界与验证状态

项目隔离是目录、会话和上下文的逻辑隔离，不是 Windows 用户或容器权限隔离；执行器仍持有当前 Windows 用户权限。同项目共享工作目录，没有自动创建每任务 Git worktree。

原生异步 Team 在此前非交互测试中曾在等待成员时提前返回，因此当前使用程序编排的四阶段独立会话。监控依据进程、输出和期限，不是语义上的死循环检测。服务尚未安装为开机启动的 Windows 服务。

上下文仅来自登记背景和最近三份任务报告，不是无限聊天记忆。规划器只负责拆分与映射，模型规划仍需约束和验收；文件条件通过只证明所列条件，不代表整个业务目标都完成。Multica 接入按单个 Router 拥有配置工作区设计，不提供分布式领取锁。

**最终验证：** 自然语言私信触发两个项目，各自四阶段、共八个独立 Qoder 会话成功；文件验收通过，MM 汇总回帖已复拉确认。Multica 隔离测试工单 自动触发后回写 done，只有一个任务和一条结果评论。测试频道状态命令已回帖；服务重启后无重派或重复评论，通知队列为空。89 项自动化测试通过，包括六项目双槽位调度。完整索引见 `docs/verification-2026-11-01.json`。

外部通知采用确定性状态、任务编号和本地证据索引；完整模型报告及独立验收结果保留本地。曾确认 HTTP 403、HTML 标题为 WAF 的报告请求拒绝，但未确认具体命中规则。这与旧的执行器 `blocked by policy` 拒绝是不同问题；旧拒绝的具体原因仍未知，直接 Python 成功也不能倒推出旧拒绝原因。

## 操作

在本目录运行 PowerShell，设置 `$env:PYTHONUTF8='1'`。登记项目及后续更新：

```powershell
python -m router.cli project-add project-a C:\Projects\project-a --rules project-a-rules.md --context project-a-context.md
python -m router.cli project-update project-a --rules revised-rules.md
```

变更只影响新提交任务；已提交快照不变。通用规范在 `router/common-rules.md`，修改后重启服务；项目规则不能放宽通用底线。

```powershell
python -m router.cli serve
python -m router.cli status
python -m router.cli stop
python -m router.cli goal "在已登记的两个项目中完成目标及各自验收条件"
```

`serve` 为前台运行；后台运行需由启动方管理进程生命周期。不要同时启动多个实例。

授权私信可直接发送完整目标；频道中使用以下命令：

```text
/router projects
/router goal 跨项目总目标及验收条件
/router run project-a 明确项目目标及验收条件
/router status
/router show 任务ID
/router pause
/router resume
/router project-resume project-a
```

`pause` 只暂停新任务执行调度，正在运行的任务继续；规划与消息接收仍可进行。`project-resume` 解除项目暂停，继续等待任务，不重跑失败任务。遇到澄清问题，应补充包含项目和目标的完整请求。

独立文件验收示例：

````text
```bridge-checks
{"files":[{"path":"result.txt","contains":"EXPECTED"}]}
```
````

本地证据：`.runtime/runs/<任务ID>/` 保存任务、各阶段报告与验收结果；各阶段子目录保存 prompt、stdout、stderr 和阶段结果。规划记录在 `.runtime/plans/`。本地 `show` 可查完整摘要。

澄清续答：`/router clarify 规划ID 完整补充说明`，保留原目标生成续接规划。远程续答必须来自同一频道与原发信人；历史缺少发信人标识的规划需重新提交完整目标。

## 配置和凭据

`config.local.json` 配置 Qoder 命令、并发数、规划模型、MM 机器人身份与授权来源；Multica 部分配置工作区、允许项目和预期身份。未配置的接入不启动。配置与 `.runtime/` 不入 Git。

MM/Multica 使用独立本机 ai-crew CLI 和凭据缓存，与个人技能目录分离。缓存为本机 JSON 格式，不是加密数据库；访问由本机 ACL 限制。此前运行身份 ACL 问题通过显式当前用户 SID 修复，这是独立于执行器策略拒绝的问题。

不共享包含凭据的配置或运行目录。项目进程会过滤 MM/飞书环境变量，但同一 Windows 用户仍可访问其权限允许的文件，不能把环境过滤当作系统级隔离。

## 本地测试

```powershell
python -m unittest discover -s tests -v
python -m compileall -q router tests
```

确定性测试与真实模型测试分别记录。真实烟测使用临时项目，不代表业务仓库开发验收。

## 外部依赖

Python 3.13、已登录的 Qoder CLI，以及由部署方自行提供的 ai-crew CLI。公司内部技能源码、凭据、网关地址和现场记录不包含在本仓库中。通信适配器要求的 CLI 能力见 `docs/integration-contract.md`。仅配置 Qoder 即可使用本地队列；MM/Multica 可选启用。


## 2026-11-04：任务续接与独立观察台

新增结构化提问回执、持久检查点、同一任务分阶段续接、独立只读网页和精简MM诊断。进程退出、角色返回与任务验收分开记录。

运行与升级见 [任务续接说明](docs/task-continuity.md)。当前仍使用四个独立Qoder角色会话，续接采用检查点＋新会话，未启用原生resume。

GitHub版本包含通用运行代码；组织专用CLI、凭据、内部安装配置和业务任务材料不随仓库分发。
