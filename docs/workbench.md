# AgentStack 工作台

启动 Router 后，在同一安装目录运行 `python -m router.monitor`，打开 http://127.0.0.1:8787 。Windows 更新包提供 `08-workbench.cmd`。网页与 Router 共用 `.runtime/router.sqlite3`，监控服务单独运行；Router 离线时可保存操作，恢复后调度。

## 使用

- Team 执行设置：已有 Git 项目选择「独立 Git 克隆与分支」，设置并发数 1–8。实际并发同时受 config.local.json 的 concurrency（1–16）限制。设置仅作用于新任务。
- 同一 Team 每个任务使用独立克隆、独立分支，基线是本地已提交 HEAD；未提交改动不带入任务。monorepo 的 app 子目录保持相对路径。烟测项目可选择独立副本。
- 新建任务 → 查看目标 → 确认执行。选择任务后可查看公开输出、工具活动、角色/session/PID、代码差异和结果。网页每秒读取增量事件，不显示模型私有思考。
- Qoder 通过 needs_input 检查点提问，网页回复后继续；input_kind=confirmation 展示同意/拒绝按钮。确认绑定具体问题及工作区指纹。MM 和网页共享一次性问答状态，重复或过期回复拒绝。
- 继续任务会创建新的 CLI session，使用原任务目录和保存的检查点；这不是向正在运行的 CLI 任意注入文本。需要终止时使用「停止任务」，目录与记录保留。
- 配置 final_only 的 MM 任务只在结束时回原线程并 @原提问人；待回答/待确认的问题在网页处理。旧频道未设置 final_only 时仍兼容原问答通知。

## 部署边界

本版只监听 127.0.0.1，校验 Host、Origin 和本机网页令牌。请勿用反向代理公开端口；本机登录用户作为操作员，可管理该数据库中的任务。多人远程登录和按用户权限控制尚未实现。

独立目录防止并行任务互相覆盖文件，不是操作系统沙箱。Qoder 仍以当前操作系统用户运行；外部数据库、端口、服务账号及凭据需要项目自身隔离。模型通过 receipt 请求确认，网页并未拦截所有任意 shell 命令。禁止把这种确认视为操作系统级权限门禁。

测试和安装依赖现有 Python、Git、Qoder CLI。副本模式也用 Git 记录基线，排除 .env、config.local.json、node_modules、.venv 等。任务目录保留在 .runtime/workspaces；可从网页查看路径，再检查、提交或发布代码。Git 克隆 origin 指向本地来源仓库，发布到 GitLab 必须显式使用业务远端，不应 push 回本地来源目录。

GitLab MR 标题分流与测试比较沿用现有功能；自动创建/合并 MR 并不因工作台上线而自动获得。真实 app 路径、测试命令、频道及作者映射需现场绑定后逐项目验收。

## 验证

Mac 自动化涵盖并发独立目录、原目录未提交文件保护、问答去重、过期确认、取消、同源防护、输出脱敏、配置迁移及原有 Router 回归。Chrome 实际验证创建、确认、问答、草稿刷新保持、并发设置和移动宽度布局。Windows 真实 Qoder 与 MM 端到端行为待部署后核验。

参考 DeepSeek dsh-superagent-prototype 的任务/执行分离与交互式面板思路；本实现沿用现有 Python Router，没有复制该项目代码。
