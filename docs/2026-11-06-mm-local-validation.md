# MM 管理与事件路由本地复测

时间：2026-11-05 至 2026-11-06，Windows 本机；不是公司内网部署验收。

代码分支：codex/mm-operations。自动测试：176 项全部通过。独立代码审查发现的调度竞态、运行目录混用、暂停状态残留、角色局部更新丢配置及 webhook 旧草案覆盖问题均已修复。

## 实际验证

| 场景 | 结果与证据 |
| --- | --- |
| 角色配置 | 本机原有四个项目均已导入 v2 角色配置；另一次真实 Qwen3.8-Flash 五阶段冒烟启动清单与工具/skill 白名单一致。Leader 无业务工具，开发、评审、测试各自独立配置。 |
| 停机诊断 | 业务 Router 已停止时，MM `/diag status` 返回停止状态；诊断没有依赖业务模型运行。 |
| 服务控制 | 从 MM `/ops start`、`/ops stop`、最终版本 `/ops restart` 收到真实结果回帖，新心跳确认；未重跑失败任务。 |
| MM 创建团队 | 自然语言配置产生草案 6ea7831354c87ba6b28f；原线程确认后创建 mm-mr-smoke，不需要用户手改本机配置文件。 |
| MM 绑定事件 | 草案 5bb44a44a0205fcd548f 确认后，模拟 GitLab 项目42绑定 mr-local-smoke → mm-mr-smoke，结果通知确认线程。 |
| HTTP webhook | 首次与重复请求均返回任务 e74d0cc4609343a1b2b3b9a6fb9523ba；错误 token 返回403；维护进程重启后再次投递仍返回原任务。 |
| 模拟 MR 团队 | 本地专用 Git fixture 的 MR !1 完成五阶段；两项 Python 测试通过。未访问或合并真实 GitLab MR。 |
| 独立维护编码 | 任务43aa5b4a1b20415291a61fa973b3ae88，仅在独立 AgentStack-maintenance checkout 新增 tests/test_diagnostic_labels_extra.py；Qoder 开发/评审/测试完成，本机再次运行3个测试通过。修改未自动部署到业务运行目录。 |
| MM 问答续跑 | 任务a53382f2305a47519b9e65e7b17d9275在第一阶段询问报告格式；MM回答“简洁”后，从检查点新会话继续到最终汇报。 |
| 工作台真实项目 | 任务e78263ceb8094572889d7c92e05d3419运行verify:local，lint、类型检查、138测试通过。首次tester有一次不存在文件的读取错误，保留失败记录；一次干净只读重试完成，随后Leader汇报。 |
| travel-helper真实项目 | 任务e579d905c346464cb513b2326aa50073被reviewer阻塞，Leader STOP。源码确认verify-briefing.mjs接受fetchImpl但调用全局fetch；本轮只读授权未修改它。项目保持暂停，报告可通过MM查询。 |

上述无独立业务验收条件的任务，最终保持 reported_unverified；不能把工具调用正常、测试通过或模型 PASS 等同于全面业务验收。travel-helper 的 failed 是发现业务问题后的实际结果，不能写成全部项目通过。

## 实测反馈后修正

- Windows PowerShell 继承 PowerShell7 的 PSModulePath 导致加密模块加载失败：清除该子进程的继承模块路径后，DPAPI凭据读写验证通过。
- 服务控制结果的通知去重键含点号，被MM适配器校验拒绝：改成兼容字符，增加回归测试；最终重启回执已送达。
- 启动时动态读取本机已启用skills，MM `/ops skills` 已列出实际清单；不再限于四个示例skill名称。

复测结束时：业务 Router 与独立维护进程均在线，MM connected；没有 running/queued/waiting_input 任务，也没有待发送通知。travel-helper的历史失败和本次代码问题保留，不以重启清除。

## 使用与边界

见 [MM操作说明](mm-operations.md)。当前配置支持四个角色槽位、项目规则/提示词、已安装skill选择、GitLab MR评审事件路由和原MM线程通知。

尚未实现：自动安装任意skill、任意专家图编排、任意通知对象解析、真实GitLab webhook远程注册、自动合并策略与执行。真实联调还需要可达HTTPS入口、GitLab项目地址及受控凭据。模拟链路通过不代表这些外部能力已经配置完成。
