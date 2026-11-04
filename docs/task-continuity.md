# 任务续接与独立监控

## 本地启动
使用Python 3.13+、Git和已登录的原生Qoder可执行文件。Python运行代码仅使用标准库。

Windows PowerShell：
```powershell
python -m venv .venv
Copy-Item config.example.json config.local.json
```
编辑config.local.json中的qoder_command为本机可执行文件路径。MM配置沿用README中的连接器协议，提供自己的兼容ai_crew.py、网关、bot_id、channel_id及allowed_user_ids；仓库不含组织CLI或凭据。已有配置不要覆盖。

```powershell
.\.venv\Scripts\python.exe -m router.cli project-add demo C:\Projects\demo
.\.venv\Scripts\python.exe -m router.cli serve
```

独立窗口启动：
```powershell
.\.venv\Scripts\python.exe -m router.diagnostics
.\.venv\Scripts\python.exe -m router.monitor
```
诊断需要MM配置；网页不需要MM，Windows本机浏览器访问 http://127.0.0.1:8787 。也可用06-diagnostics.cmd与07-monitor.cmd。网页与诊断进程可在Router停止后继续读最后状态；整机停机则不可用。网页只监听loopback，无写接口，不开放远程访问。

## 提问和继续
角色遇到必须由人回答的问题，输出一个末尾JSON块后正常退出：
````text
```router-receipt
{"status":"needs_input","question":"是否保留空值？","completed":"已定位代码","remaining":"修改与测试"}
```
````
监督器验证进程正常返回与回执格式，保存检查点，将任务标记为waiting_input并在原MM线程通知。等待任务保留项目，释放全局执行名额；Router重启后仍等待。原任务用户在原线程回答：
```text
/router answer TASK_ID QUESTION_ID 答案
```
本机操作者可用：
```powershell
.\.venv\Scripts\python.exe -m router.cli answer TASK_ID QUESTION_ID "答案"
```
本机命令具有数据库操作者权限，可回答MM来源任务。MM命令检查原用户和线程；重复回答、旧问题ID不会再次推进。最多3轮已回答问题，超过后停止待检查。

## 状态和检查点
Task保留原需求、规则与验收条件；每次执行增加attempt。每个角色进程有自己的session ID与退出码。checks通过仅代表明确列出的文件条件，不能替代业务测试。

检查点记录已完成角色报告、当前阶段、问题与答案、Git HEAD、工作区和索引diff、非忽略未跟踪文件指纹。继续前发现代码改变则失败并暂停项目，不自动覆盖；非Git目录不能创建有效检查点。忽略文件不在快照范围内，未跟踪文件内容总量上限20 MiB。检查点不构成完整环境备份或防同账号写入的安全边界。

本版从检查点启动新session，不重跑已完成角色。原生session恢复需在目标Qoder版本验证后再接入。新任务不自动继承历史任务报告；每个前序角色摘要上限6000字符，总输入上限100000字符（不是token预算）。完整证据仍保留在各attempt目录。阶段结束生成receipt.json；需要回答时额外生成checkpoint.json。

## 低噪声观察
/diag默认只显示最多6个待处理任务；/diag show TASK_ID显示各角色、PID/session、最近工具、问题。网页展示最近100单及结果/证据路径，可切换完成历史；不触发模型、不读取原始工具参数。

## 升级与验证
停止旧Router和独立诊断，备份整个.runtime（包括SQLite侧文件）与config.local.json，再使用新代码。启动时自动增加continuation/attempt列。不要用旧版处理新版waiting_input任务；回退需使用升级前备份。Multica等待任务不会被写回为完成；跨平台问题卡暂未实现。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```
先在独立Git仓库完成“提问→进程退出→重启Router→回答→新session继续→四角色→最终文件核验”烟测，再接真实业务任务。

验证范围：本地标准库测试及模拟Qoder子进程已覆盖上述流程、身份/线程绑定、重复回答、工作区变化拦截和Router停机后的只读HTTP。真实Windows Qoder、MM回答往返及业务浏览器用例仍需部署验证。
