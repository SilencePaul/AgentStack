# 项目 Agent 配置

当前支持每个项目的 leader、developer、reviewer、tester 四个执行槽位。每个槽位可配置独立身份、版本、SOUL.md 和 AGENT.md。示例在 examples/agent-profiles；这是根据用户提供的分类整理的适配模板，不是 Autowonder 原文，也不包含该平台 API。

## 使用

复制 examples/agent-profiles 到本地配置目录；修改 agents.json 中 PROJECT_NAME、PROJECT_TECH_STACK 和各角色文档，然后运行：

```powershell
python -X utf8 -m router.cli --runtime .runtime project-update 项目ID --agents 本地配置目录
```

导入时展开变量、检查目录边界与文件大小、保存 SHA-256 指纹。未解析变量报错，不读取环境变量，不执行模板。不要放入密码或 token。任务提交时将完整配置复制到不可变项目快照；重新导入只影响新任务。运行证据包含实际提示词，监控仅显示身份、版本和指纹，不公开提示词内容。

自定义配置只补充职责，不能覆盖用户授权、通用规则、角色限制和结构化回执协议。Leader 的全部阶段禁用工具及 MCP；其他角色的只读职责是提示词约束，当前 Qoder 工作区隔离不是 OS 沙箱。不要把提示词当作权限隔离。

## Leader 与续跑

新任务先运行 lead-plan，再进入 developer / reviewer / tester；评审或测试 BLOCKED 时进入 lead-triage。Leader 可以安排范围内返工（最多两轮）、发出 needs_input 问题或 STOP。最终 lead-report 汇总，Supervisor 独立验收。权限拒绝、真正进程失败、超时不被 Leader 重新授权执行。现有一次只读工具重试策略保持不变。

同一个 Leader 身份使用独立阶段会话与显式交接报告，不是一个永不退出的会话。每个项目上下文隔离，不直接共享别的项目记录。旧版检查点保留旧阶段编号和语义，不重放已经完成的阶段，也不丢弃已回答问题；新检查点保存协议版本。

## 其他专家

需求澄清员、DBA、冲突解决工程师可以作为后续独立角色目录；当前调度器尚未支持任意专家派单。尤其 DBA 的“仅开发库、只交回真人”、冲突解决的风险分级和 QA 部署，需要专门执行与权限约束，不能仅改身份名称就宣称支持。Java/MyBatis/Lombok 等规范放在对应项目的角色配置，不作为所有项目的全局规则；作者名须由项目确定。模板提到推送、部署、删文档或数据库变更不构成用户授权。

## 为一个角色指定 Qoder skills

在项目的 agents.json 中给角色添加 skills 数组。名称来自该项目工作目录下的 `qodercli skills list --all`，必须处于 Enabled 状态。示例：

```json
"tester": {
  "id": "QA",
  "version": "v1",
  "directory": "tester",
  "skills": ["verify"]
}
```

省略 skills 时沿用 Qoder 的发现结果，空数组限定为零个 skill。有名称时仅保留列出的 skill，缺失或禁用的名称会阻止该角色启动。列表随任务配置保存，skill 本体由 Qoder 从本机安装或项目目录读取。更新配置后重新执行上面的 project-update 命令，新任务生效。

每次启动前 Worker 以项目为工作目录读取 Qoder 目录，为该进程传入临时 --settings 过滤配置，并比对 stream-json 的 system/init.skills 实际名单。角色证据目录中的 stdout.log 可复核启动名单。skill 控制 Qoder 发现的工作方法，工具和项目权限由执行器单独配置。
