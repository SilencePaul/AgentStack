# 从 MM 管理团队、事件和维护

## 已实现入口

- `/diag`：业务 Router 心跳、通信和任务状态。
- `/diag show 任务ID`：阶段、PID、会话、最近工具、实际加载的工具与 skills。
- `/diag report 任务ID [页码]`：原用户在原频道读取脱敏报告。
- `/ops status`：独立维护进程和维护任务状态。
- `/ops start`、`/ops stop`、`/ops restart`：控制本维护进程启动的业务 Router；有运行任务时拒绝停止，不强杀、不自动重跑失败任务。
- `/ops repair 修复目标`：在独立 AgentStack checkout 启动维护编码团队，具备开发工具；修复、评审、测试后汇报，不自动替换运行版本。
- `/ops show 维护任务ID`、`/ops report 维护任务ID [页码]`、`/ops answer 维护任务ID 问题ID 答案`：查看维护、处理提问。
- `/ops resume`：检查维护故障后解除该项目的暂停；原失败任务不重跑。
- `/ops configure 自然语言目标`：AI 生成团队或 webhook 配置草案；缺信息时在 MM 提问。
- `/ops config 配置ID`、`/ops clarify 配置ID 补充说明`、`/ops apply 配置ID`：查看、修改、确认配置。确认绑定原发信人、频道和线程。
- `/ops teams`：查看当前团队规则与角色配置。

业务问答仍使用 `/router answer`；维护问答使用 `/ops answer`。业务 Router 和独立维护进程使用不同运行目录。不要同时在相同频道运行旧的 `router.diagnostics` 和新的维护进程，否则 `/diag` 会收到两个回复。

## 配置示例

在 MM 发：`/ops configure 创建名为 demo-mr 的空白演示团队，用于模拟 MR 评审，规则是不改代码、不合并。`

收到草案后在同一线程发 `/ops apply 配置ID`。创建真实项目时提供 SSH/HTTPS Git 仓库地址，机器已有的 Git 登录用于克隆。不能将 token 放进仓库 URL 或提示词。既有项目的 repository 元数据需在首次迁移时登记。

再发：`/ops configure 添加 mr-demo webhook，将 GitLab 项目 42 的 open、update、reopen MR 事件交给 demo-mr 评审，结果通知本线程，不自动合并。`

确认后向 `http://127.0.0.1:8788/hooks/gitlab/mr-demo` 发 GitLab MR 格式事件。必须提供 X-Gitlab-Token 和 X-Gitlab-Event；项目 ID、MR iid 和提交 SHA 必须有效。相同规则版本/项目/MR/SHA 只派发一次，新的 SHA 创建新任务。事件标题只作为不可信数据传入，不授予执行权限。缺少本地提交或目标分支时团队提问，不能假称已评审。

事件字段参照 [GitLab 官方 webhook 文档](https://docs.gitlab.com/user/project/integrations/webhook_events/)。本地监听仅绑定环回地址；真实 GitLab 联调需要可达的 HTTPS 入口、凭据与仓库访问。本轮模拟不代表已配置真实 GitLab webhook。

## 首次启动

先安装示例 Qoder skills，准备独立 AgentStack checkout 和已有 MM 配置。然后运行：

```powershell
python -X utf8 -m router.operations --config config.local.json --runtime BUSINESS_RUNTIME --ops-runtime OPS_RUNTIME --maintenance-path SEPARATE_AGENTSTACK_CHECKOUT --profiles examples/agent-profiles --projects-root NEW_REPOSITORIES_ROOT
```

Windows webhook secret 自动用当前用户 DPAPI 加密存入 OPS_RUNTIME/webhook-secret.xml；不要发送到 MM。新进程不会接管不属于它的旧 PID。首次迁移停止旧业务 Router 后，从 MM `/ops start` 启动新的受管实例。

## 当前边界

配置支持 leader/developer/reviewer/tester 四个槽位、受支持的 skills 和工具清单。自定义指令不授予操作系统隔离，也不能突破用户原始授权。维护 checkout 和业务代码分开，但 Bash 并非安全沙箱。

当前 webhook 只实现 MR 评审路由，未实现 GitLab 自动合并、GitLab webhook 的远程注册、任意新 skill 安装或任意专家角色编排。自动合并需要单独实现并校验目标分支、精确 SHA、CI/审批状态和合并授权；不能只靠模型口头 PASS。配置会对不支持的请求要求澄清。
