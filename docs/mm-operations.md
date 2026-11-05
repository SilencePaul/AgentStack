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
- `/ops skills`：查看本次维护进程启动时发现的可用 skill 名称。

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

配置支持 leader/developer/reviewer/tester 四个槽位、本机已安装启用的 skills 和受支持的工具清单。自定义指令不授予操作系统隔离，也不能突破用户原始授权。维护 checkout 和业务代码分开，但 Bash 并非安全沙箱。

当前 webhook 只实现 MR 评审路由，未实现 GitLab 自动合并、GitLab webhook 的远程注册、任意新 skill 安装或任意专家角色编排。自动合并需要单独实现并校验目标分支、精确 SHA、CI/审批状态和合并授权；不能只靠模型口头 PASS。配置会对不支持的请求要求澄清。

## 纯 mock GitLab 的 MM 全流程

`/ops mock create demo` 在维护 runtime 的隔离注册表创建本地 bare Git 仓库，返回 `mock://demo` 和模拟 project_id。通过 `/ops configure` 指定该 URI 创建团队，确认草案后实际克隆；不能借此克隆任意本机目录。模拟源分支和测试数据由测试夹具准备，不连接真实 GitLab。

在 MM 创建对应 webhook 规则后，依次使用：

```text
/ops mock open demo feature main
/ops mock ci MR_ID HEAD_SHA passed
/ops mock emit RULE_ID MR_ID
/ops mock check MR_ID TASK_ID
/ops mock merge MR_ID HEAD_SHA BASE_SHA
```

emit 经本机 HTTP 接收线程派单，返回任务 ID。团队工作区必须干净且空闲，接收器抓取注册表的提交并以 detached HEAD 检出。输入明确携带源、目标精确 SHA，不能使用陈旧本地分支或查询不存在的 upstream。重复事件返回原任务；新源/目标 SHA 绑定新的核对任务。

check 校验真实终态任务、reviewer/tester 的 PASS 与退出状态、最终工作区快照、干净工作区和当前源/目标 SHA，并独立执行 Git diff 检查，才登记版本绑定的核对记录。它不替代业务自动化测试；测试仍由团队执行并由联调验收独立复跑。merge 要求精确 SHA、同版本 CI 和核对记录通过，并通过 Git 原子引用事务更新模拟目标分支。重复合并返回已有结果，不重复创建合并。

`/ops mock show MR_ID` 查看已记录状态；`/ops mock inspect MR_ID` 刷新当前版本并诊断冲突文件。inspect 不授予批准、不合并、不移动分支。真实 GitLab 没有这套模拟 CI 或合并能力。

失败任务不会因重复投递而重跑。处理原因后，在 MM 对已暂停的项目执行 `/router project-resume TEAM_ID`，再显式发 `/ops mock retry RULE_ID MR_ID FAILED_TASK_ID`。只允许重试绑定同一 MR、团队及精确版本的失败任务；新任务保留 `retry_of`，原失败证据不变，同一重试请求仍去重。源或目标已经变化时应重新 emit 当前版本。

模拟命令队列先落盘再执行，结果发回命令线程；维护进程中断时，尚未确认效果的命令标为 interrupted，不盲目重放。凭据和 webhook secret 不出现在 MM 指令中。

`/ops mock restart-receiver` 只关闭并重新绑定本机 HTTP 接收线程，返回新的 generation。它不停止业务 Router、Qoder 任务或 MM 诊断；事件去重数据仍在原 SQLite 队列中。重启后可再次 emit 同一版本，确认返回原任务 ID。若重新绑定失败，错误仍通过独立 MM 通道返回，可排查端口后再次显式重启。

`router-receipt` 仅用于 `needs_input` 问答，成功报告使用角色规定的末行判定。若只读评审/测试在工具正常结束后输出了错误格式，监督器保留失败报告，并在工作区未变化时最多重新执行一次该阶段；与工具错误重试共用额度，不延长任务时限。纠正后的合法问题仍需等待原作者在原线程回答。写代码阶段、权限拒绝、工作区变化或重复错误不会套用这条格式重试路径。
