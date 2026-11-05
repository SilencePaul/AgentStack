# 十轮真实开发与纯 mock MR 全流程验证

用户目标保持完整：travel-helper开发完成，经AgentStack/MM流程至少十轮有实际改进的迭代；通过MM指导克隆模拟仓库、接收GitLab事件、Agent团队进行合并前核对并合并，多种情况每种至少三次成功。

## 计数和证据

- 十轮不能用同一指令的重试、重复事件或单纯修改报告凑数。每轮记录MM原始指令、task ID、修改diff、review/test结果和独立验收。失败尝试单独保留，只有符合该轮验收的真实改进计入完成轮数。
- 先审查当前规格、已完成任务与本机实现，不把历史deferred清单自动视作新需求；现有未提交文件先记录并保留。生产发布不由本地mock结果替代。
- MR模拟仓库必须位于专门fixture目录，模型不能指定任意本机路径。不会访问或合并真实GitLab MR。
- 所有MM操作通过ai-crew CLI；真实发送/回帖/问答作为证据，不用数据库直接插入任务替代端到端验收。

## 模式一：travel-helper

已发第1轮：修复verifyBriefing忽略fetchImpl，补真正执行函数的离线回归，以及非法发布源不创建目录的断言。

后续轮次依据产品规格与当前审查结果确定，覆盖实际功能、交互、错误恢复或测试发现的缺陷，不能预先将十轮全部缩成发布脚本的小修改。每轮进入下轮前独立验收并保存工作区状态。第十轮后再运行适用的全量lint/typecheck/test/build及浏览器验收，明确剩余外部生产门禁。

## 模式二：纯mock GitLab

需要补充的能力：受控mock仓库登记/克隆；模拟MR状态与CI/审批；独立接收线程；事件持久化去重与任务关联；版本绑定的合并前团队核对；MM合并命令；合并时再次核对head/base、CI与审批，实际更新本地mock目标分支并留下审计。

最低场景矩阵（各独立实例重复至少3次）：

1. 正常MR：MM创建/绑定团队→克隆→事件接收→核对通过→MM确认→实际合并→目标分支包含预期改动。
2. CI失败后恢复：失败时不得合并；修复并通过CI后重新核对，再合并。
3. 评审后head变化：旧审批和旧核对失效，拒绝旧SHA；新SHA核对后合并。
4. 合并冲突后恢复：冲突时目标分支不变；在mock源分支解决后重新核对、合并。
5. 重复/重启投递：重复事件不重复执行或合并；接收线程重启后仍保持去重。

“阻止一次错误合并”是场景中的必要断言，不替代该恢复场景最后实际成功合并的要求。无原始事件、独立分支状态及报告证据的结果不计成功。

## 当前进度

- 2026-11-06：重新核对本地服务、代码和travel-helper源码。服务PID已现场确认存活。
- 第1轮通过MM提交，task `35166e6d475c4944952daf9b78b5a627`，原消息 `8fn71snid7nrtgsneg59bq6esy`。此时仅已派发，尚不计入完成轮数。
- 两种模式都未完成，目标继续保持active。

### 首次执行结果与恢复

- 第1轮首次尝试35166e6d475c4944952daf9b78b5a627失败：代码已修为fetchImpl，新增离线用例，但缺陷复现没有封锁global fetch，触及公开站点；另有工具失败及后台事件。未通过监督器，不计成功。失败记录已通过MM标注处理说明，未抹除。
- 第1轮恢复任务cef80740aeeb419c95395aec450af145由MM消息exgbhuefhirwzk5cx6pyg3n57w提交：要求封锁globalThis.fetch、保持现有改动、只做离线测试、禁止后台执行，预期失败需显式断言状态。最近现场确认Qoder PID13592存活。
- 新增mock Git底层router/mock_gitlab.py及3个真实Git单测已通过；尚未接入MM和接收线程，因此不算多场景端到端成功。
- 发现并复现下一轮候选缺陷：apps/local-agent-bridge的build/typecheck/lint均使用node --check src/*.mjs；Windows Node22将通配符当字面路径，MODULE_NOT_FOUND。仅做只读复现，尚未修复。
- 完成轮数仍为0/10；mock端到端场景完成次数仍为0。

### 第1轮验收与第2轮派发

- 第1轮恢复任务已 succeeded；独立 business.verify 检查最终工作区一致，执行三份 briefing 测试，JUnit 12例全部通过，验收记录在 .runtime/goal-validation/round-01-receipt.json。当前完成1/10。
- 第2轮通过MM消息 gewtqbjctprb5dq3rmxps19b7h 派发，task 104cce04ade64a528ed7bc9684f2ea24，现场确认running；修复Windows bridge语法检查通配符问题。等待脚本因匹配“任务”而超时，实际回执为“已记录”；已查MM原始回帖和数据库确认并非投递失败。
- TeamAdmin新增仅允许已登记mock://仓库的克隆支持，相关测试通过。mock底层现有5项测试，额外覆盖冲突后解决合并、Git更新后DB终态写入中断的重启幂等恢复。仍未接入完整MM合并命令，不计端到端成功。

### 持久化MM模拟操作队列

- 新增router/mock_commands.py，测试先RED后GREEN：命令持久化、重复消息只执行一次、非法路径和自报approve拒绝。支持create/open/show/ci/emit/check/merge解析；emit/check回调尚待接通，因此不能宣称端到端完成。服务启动已构造隔离mock注册表，配置线程执行队列，/ops mock命令返回队列号并回原线程。服务尚未重启加载新代码，避免打断第2轮。
- ConfigurationPlanner现在识别已登记mock:// URI，TeamAdmin只从注册表解析克隆源。相关19项测试通过。
- 第2轮现场仍running，Qoder进程6236、父进程3880；Leader首次协议失败后安全重试成功，现在developer执行，未重复派发任务。

### MR接收与核对结果绑定

- 新增MockReview：校验事件与登记MR/团队仓库一致；队列事务内检查团队空闲、干净工作区，fetch本地mock仓库并checkout精确head。去重先于准备工作区，base纳入mock事件去重键。
- 核对要求真实终态任务、最新reviewer/tester PASS及退出0、最终工作区快照一致、HEAD一致、干净工作区、当前源/目标SHA一致、git diff --check通过，才登记核对记录；用户不能自报passed。4项专门测试通过；与webhook/operations的16项测试通过。单元测试中的构造报告不算真实团队验收。
- MM emit通过loopback HTTP送入独立webhook线程；限制原配置MM用户/频道，check连接MockReview。新代码仍未重启加载，因第2轮真实Qoder进程6236仍存活。最后观察developer事件为2026-11-05 17:16:32 UTC。

### 首次真正MM全链路成功与第2轮验收

- 第2轮首次任务104cce04失败（越范围全量Bridge测试、工具失败及后台事件），通过MM标注原始失败并恢复项目。恢复9c9ded2de332423ab2a1855b807ef876全团队成功，独立business.verify通过6个JUnit用例与实际源文件语法检查，计数2/10。
- 空闲后MM停止业务Router、核验维护空闲，再重启operations PID5832并通过MM启动新版业务Router PID29164。
- MM create创建mock://mr-normal-1；MM configure/apply创建mock-premerge团队并真实克隆，规则mock-mr-review绑定project_id1。普通场景1：MR ec9dbd020e824aee89772808bdaa7bfe、task bb4556ec9d1e46f6b9c141eb5e98d27c完成所有角色，独立3用例通过，MM check/merge成功，目标main实际da65d188d67c07d09d7226126f0da7297256f36a且内容正确。重复emit返回相同task。证据mock-normal-1.json；正常场景成功1/3，其他四类仍0/3。
- 正常场景2：MR deb93a6b71604f528fb1225d3b615b34，增量subtract方法及3用例，6例独立测试通过，MM派发中，证据mock-normal-2.json。
- 第3轮任务b6e3e717021a492494005da229112839经MM派发：离线队列结构化VERSION_CONFLICT响应也应暂停且保留队列，先回归后实现。
- 不采信开发者“Node22 --test字面通配符执行0例”的报告：独立临时哨兵用例确实执行并按预期失败，因此不修不存在的glob问题。前端全量基线60s观察期中有焦点失败；独立单例和整文件25例均通过，尚未判定产品缺陷。已确认没有残留vitest进程。Codex list_threads工具Transport closed，读取相关历史最终结果仍待完成。

### 第3轮及正常MR第二次验收

- 第3轮b6e3e717021a492494005da229112839成功，独立business.verify运行offlineQueue完整文件13例通过，工作区匹配，记录round-03-receipt.json；当前3/10。第4轮becb2a5c60374709aa6c9061c2aea295通过MM派发，修复TripProvider旧离线缓存失败回调污染新会话的问题。
- 正常MR第2次a8d34c63cb074c54935ddb6311b7edb5所有角色通过，独立6例通过，MM check/merge实际main=def8040fa0ef4ab8572b2aa9f4d7eab53771c760并含subtract实现。正常场景2/3；证据mock-normal-2.json。
- CI恢复第1次MR fd0a9938a87944db8bcd8187947afca5，MM设置failed后merge明确拒绝CI has not passed；之后正常MR2仍能以原base CAS合并，证明此前拒绝未修改目标。已通过MM恢复passed、emit刷新base并派发task ed6b8fa8add14b5091d504059744c433，尚未成功合并，故恢复场景仍0/3。证据mock-ci-recovery-1.json。

- 第4轮首次验收JSON仅path、缺exists，初始化拒绝未执行。我修正为exists:true，经MM resolve/project-resume后重新派发 56d31ae108874ba88e4975cb367335c6，原始失败保留。不计新增轮次。

### 正常场景3/3与CI恢复1/3

- CI恢复1任务ed6b8fa8add14b5091d504059744c433完成，独立4用例通过；MM check/merge及目标内容验证通过，main=4ec8ab83d9d31450d4b7806ce4756b74d1ee19e0，同时保留此前subtract。
- 新增忽略目录中的联调runner .runtime/goal-validation/mock_scenarios.py，每个MM命令落盘原消息和返回，可观察重试不重复派发，失败/待回答停止，真实团队终态后独立复跑测试再check/merge。exec session21235正在执行normal-3、ci-recovery-2、ci-recovery-3。normal-3 task76f11b3c65cc4165af87e9173cd1495f已成功、8例通过，main=aa2d77326be02e31e1b4de333226ae06c6b73539。当前normal3/3，CI恢复1/3，其他0/3。
- 第4轮56d31ae108874ba88e4975cb367335c6未通过：实际工具错误为Bash.dir_path已是apps/web却又cd apps/web，非RED测试失败。代码完成14例GREEN但监督器保留失败。已MM标注原因/resume，以ad7cc601c1074fae986ac2ef5f6a513a仅复核既有修复，明确cwd禁止重复cd。尚不计第4轮。
- 历史会话核对：桌面list_threads再次Transport closed；只读本机state_5.sqlite找到3条travel-helper会话，但rollout_path指向的文件全部不存在。因此旧会话最终结果证据仍缺失；当前仓库done规格已复核，不将历史deferred当新需求。

### 第4轮验收与CI恢复3/3

- 第4轮恢复ad7cc601c1074fae986ac2ef5f6a513a succeeded，独立business.verify 14例通过，工作区匹配，当前4/10。第5轮7406282423ad4da29a8467e787d75b0f派发，修复authServiceUrl IPv6 loopback及拒绝含凭据/query/fragment的配置。
- 通过MM configure/clarify/apply f22ba972dc1f0f0d814c，给travel-helper developer/tester追加dir_path规则、限定测试范围和预期RED捕获要求。核对repository/rules均未变化，未改tools/skills；初稿重复所有role说明被拒绝应用，clarify后仅追加两角色规则成功。
- CI恢复2 taskc387d121eac24dfd808a7df4acd8901b、恢复3 task674e53c25329477191bcc6bc3d9437b6均通过真实MM check/merge与独立测试/分支核对，CI场景3/3，normal3/3。联调脚本先前同文merge命令的失败/成功缓存冲突已修复（按expect_failure模式区分），未重跑团队，未误合并。旧session21235异常结束，恢复session38788已成功退出。
- 已启动head_scenarios.py，session31266，依次head-change-1/2/3：真实评审核对后再push源变更，MM旧SHA合并需拒绝且目标不变，随后新事件新任务再次核对才能合并。当前只开始第1个，尚不计成功。

### 冲突诊断与同版本显式重试

- 新增/ops mock inspect，报告精确head/base和冲突文件但不批准、不移动refs，测试先RED后GREEN。新conflict_scenarios.py已写，待head-change三次结束后执行conflict-1/2/3（当前未运行）。
- 第5轮740628...因Bash换行后独立分号语法错误未通过，代码已修改；MM标注/resume后派发d925d553bc594d51a86ca87225e75b98只读复核，明确每次单命令、无分号/管道。当前完成数仍4/10。
- head-change-1初始task1b90dcda121d43d09da8db00a4a1a5f5因detached HEAD查询@{u}失败。发现payload未明确精确target SHA和detached状态，已补齐并明确不要使用陈旧本地main。
- 新增/ops mock retry RULE MR FAILED_TASK：验证失败任务绑定同一MR/团队/精确版本，生成新sourcekey并保留retry_of；重复同一重试仍去重，原失败保留。190全量测试通过。idle后MM停止业务、reload operations新PID25616、MM启动，inspect/retry已加载。
- MM retry已真实派发a4c46f5ab8284c77a3a37ebafb64cd77，关联原失败；head_scenarios已支持该显式retry_task。旧session31266以失败退出，恢复runner新session33224运行中，后续head-change-2/3仍待执行。mock失败不计成功。

### 第5轮补齐共享校验与head变化1/3

- auth恢复d925d553bc594d51a86ca87225e75b98成功，独立14例通过，但共享ClientEnvironmentSchema仍拒IPv6/未拒凭据queryfragment，故不把局部修复计完整第5轮。通过MM派发同轮补齐c08cc0e61b924a8390a3b1238d5e6b2f，需contracts environment及web auth两个完整文件一起独立验收。当前仍4/10。
- head-change-1：原评审通过后源更新，旧合并明确被拒且目标不变；updated task b4afadf4edb94f778c34f9ae4b5ef207通过，独立11例及MM check/merge/目标内容通过。head变化1/3，累计7个场景成功。runner33224继续head-change-2 original。
- 新WebhookListener可独立重启HTTP监听、维持端口与持久化去重；/ops mock restart-receiver在独立命令队列调用，不动业务Router和队列。真实HTTP重启单测及命令去重测试通过，全量192通过。运行服务尚未reload此功能，需当前任务空闲后加载。
- mock_scenarios.py已扩展restart-1/2/3：派发后经MM重启接收器、再次emit应同task且同MR任务数不增，最后重复merge应同commit。脚本尚未实测该模式，不能计成功。

### 接收器重启上线与受限重试

- 新全量195测试通过。空闲后MM stop，operations重启PID9344，MM start观察新心跳；独立接收器重启已加载，尚待3次端到端验证。
- mock评审任务的developer只读保护、过滤Edit/Write、工作区未变且无拒权/模型错误时仅一次工具错误重试；普通开发任务不自动重试。新增3测试通过。
- head-change-2旧失败fa405f5d70ac4cb6979a40689bda34ff经MM resolve/resume，再显式retry得到175f944614ef407bb48ec39935ac2fa9，runner31022继续观察后再运行head-change-3。
- 第5轮两层组合48例独立验收通过，但Node复现尾随空?/#仍被解析器漏过；同轮派发869907f6c65749e78b2da0844d75954d补边界，不另计轮次。

### Head变化完成、移动端复测与后续队列

- head-change-2、head-change-3完整成功，9个模拟场景通过。head3 updated任务9badf32fb9864166899d9bdaea8f77ab的一次只读工具错误触发受限重试，原失败报告保留，随后reviewer/tester通过、独立13例及MM check/merge通过。
- remaining_mock.py session20235继续conflict-1/2/3，再restart-1/2/3，任一失败退出；当前conflict1已MM inspect发现冲突、夹具解决后emit真实团队复核。
- travel第5轮869907...因重复CLI路径失败，恢复9a921c407a8c48baa7a5fe94a05c1e4f仅只读复核；第6轮6fc77...已实现天气隔离12测试模型报告通过，但一次Read缺失文件导致失败，恢复8a5e86a1039942dd98d3699cb034a76b。均未计完成。accept_pending.py新session14537等待5再6并独立验收。
- Playwright40项：31通过/6跳过/3失败；iPhone三失败独立重复2次全部失败，证据.runtime/goal-validation/browser-repeat。第7轮298ff505cbfb491a8283e4dc0e3f4523定位日期tab焦点、日期警告未出现、保存中drag焦点，保留断言。
- 后续第8轮路线空数值/空坐标、第9轮SDK加载无界阻塞全局队列、第10轮回忆切换旧内容残留已通过MM排队；仅计真实复现修复且独立通过的轮次，当前仍4轮。

- 第5轮恢复9a921c407a8c48baa7a5fe94a05c1e4f终态通过，独立50例通过、快照匹配，当前5/10。验收14537继续等第6轮恢复；later验收56391等待第7-10轮。
- conflict1 task98baaedfd8644e9195aec0bc9ad7d5f1失败：developer对merge-base不存在路径git show失败，消耗一次只读重试；reviewer读取不存在.git/COMMIT_EDITMSG再次失败，门禁保留。已MM configure/apply 9ad20a4b53cd706ce0ce给3角色追加文件存在性预检，逐项核对tools/skills/repo/rules不变；MM resolve/resume/retry后新任务de16b012b8ed42ffbb0733984cffad8a运行，remaining_mock恢复session16509。

### 全量门禁和冲突问答

- typecheck/lint退出0（lint仍有warning），local模式build通过；网页独立完整36文件438例、backend125例、local脚本18例、operations/amap-capability/amap-poi-validation均通过。仍属开发中基线，最终改动后需复验。
- 完整pnpm test在contracts首层失败：decision-research.test.ts symlink(dir) Windows EPERM，120通过1失败。已MM派发第11轮08338c2695384248b82993eefc892ef0保留断言实现跨平台链接，不提权不跳过。
- Bridge单测在Windows失败，查docs/operations/environment-setup.md明确Bridge仅macOS运行；已停止这次不适用的测试进程树（根17800），未修改隔离或生产运行语义。已异步询问用户可用macOS机器/CI，答复待到；桌面历史会话接口仍Transport closed。
- conflict1新任务de16...实际通过、独立14例与MM check/merge通过，累计10场景。conflict2 task0ce01f6c1d914adab6868a0aa8b2a93a在reviewer询问+1覆盖+2，已按用户授权纯mock预设在原线程回答question8dfa3bd13b8b，明确非真实业务批准。Router确认检查点续跑；remaining_mock新session4604观察该任务后继续，未重跑已完成阶段。

- conflict2续跑attempt2真实角色记录包含reviewer BLOCKED → lead-triage → developer → reviewer PASS，当前tester阶段；这是允许回转的团队协作证据，不是直线四阶段。
- 第7轮developer仍在动态调试App.tsx RouteFocus，出现过临时/__probe-disabled__诊断条件，尚未结束，不得据临时禁用下通过宣称完成；最终必须检查临时探针清理并保留路由聚焦语义。

### 问答回执协议纠正（待加载）

- conflict2续跑实际tester PASS，但同时输出router-receipt status:pass，加末行ROUTER_TEST:PASS，严格问答解析拒绝，最终任务failed。原始回执保留，尚未合并/计成功。runner4604终态失败。
- 新增invalid_question_receipt明确分类、失败报告不附PASS verdict；只读阶段工具无错误/无拒权/无workspace变化时允许一次格式纠正，与原clean_retries共用预算、不延长deadline，mutating developer不重跑。合法needs_input仍wait并保留预算。Leader复用已有单次协议重试。原始格式错误报告只注入摘要，完整证据保留。5新测试，先3RED后GREEN，针对42通过、全量200通过、git diff --check通过。
- 已MM /router pause暂停新任务调度，运行中的travel第7轮298ff...继续（尚在developer，近期持续输出，不能因观察时间长重启）。待其终态后MM /ops stop，核对业务已停后重启operations（旧PID9344），/ops start，再/router resume。新协议代码当前未加载。之后resolve/resume mock项目并显式retry conflict2同版任务，更新case resolved.retry_task，重启remaining_mock.py。第6及8-11排队未丢。
- 验收watcher14537（5后6）与56391（7-10）此前仍live；若超时只重观察实际任务，不重跑业务。第11独立验收脚本尚待准备。

### 去重复审修复、服务加载与冲突2通过

- requesting-code-review技能要求独立只读review，已spawn mock_protocol_review（fork none）。发现旧已接收事件在MR合并/registry刷新后先被describe拒绝，不能返回既有任务。新增认证后、当前状态准入前的持久delivery receipt（规则revision/team+canonical event），mock事件携带并校验mock_target_sha以区分同源新目标。新事件仍严格准入，错误凭据拒绝，新规则不能首次接收已合并MR。2测试先RED，新目标cache回归也通过；补齐协议重试model/nativeasync/workspace/deadline及只读developer测试。全量205通过，reviewer复审无新增重要问题。旧未保存delivery receipt的历史试验事件不在新保证内。
- travel7到总时限timed_out，原修改保留；独立原3项两平台共6例现在5通过1失败，iPhone日期警告仍失败。临时诊断server4175已停止。探针观察快速fill后日期有时被旧值覆盖，单加blur/等待不稳定；新恢复9abc8ed0132c4c1dbd7c649ba67a20ab已MM排队，聚焦唯一日期竞态、禁止再扩展到行程备注，清理原任务debug文件。
- 空闲后MM /ops stop，重启operations PID6464，/ops start新心跳；/router resolve失败、project-resume两个项目及/router resume完成。协议/去重修复已加载。
- conflict2新retry62381518d2ec4f0ba263b55c7fd19de1通过，独立15例+MM check/merge通过，冲突2/3、累计11场景。runner42395开始conflict3 task8f6c89b15ab7410883fa84de94a86815，随后restart3次；restart脚本新增合并后的重复emit仍同任务断言。
- 天气第6轮8a5...进入needs_input：冻结gate检查identityOf但最终实现forecastKey。属于root验收绑定命名过细，已原线程回答question8e0fcd8b5166，仅授权内部函数及调用点等价重命名，禁止算法变动；原12例仍需独立复验。新accept_remaining.py session77844按实际队列6/8/9/10/11/7观察验收，已通过receipt可跳过（最终全量门禁仍另跑）。旧14537超时、56391遇旧7超时退出，均未重跑业务。

### 冲突3/3与动态独立验收

- 最终Windows本地组合回归26997退出0：final-local-20261106-045150/receipt.json passed=true，前后源码stamp均5c318047...。typecheck/lint/build、contracts121、web457、backend125、local18、operations及两组AMap检查通过；浏览器82项中74通过8原平台范围skip（对应平台均通过），无新skip。AgentStack全量205项再次通过（70.099s），git diff --check通过。11轮收据与15个mock MR达标；原生macOS/历史完成结果缺口没有用Windows或模拟结果替代。准备将AgentStack当前功能分支及验收文档上传GitHub，业务repo未提交/推送。

- 最后第9轮9c8...角色链完成，business-2capshc1独立timer探针及43例通过，快照与最后7轮相同。全部11轮收据通过（排除未接主入口的10轮后仍10轮实际改进）；accept_live观察15735退出0。最终组合回归26997已自动启动，已见typecheck/lint退出0，后续build/完整套件/browser仍运行，不能提前称组合通过。当前源码stamp为5c318047f888a391a8825abf038756123123d6d0defb65bd71656370e59ffdfc。

- 第7轮96a...已完成角色链，外部business-4pafnsr8实际app类型检查退出0、Overview26单元+12浏览器场景共38例通过，源码stamp一致，累计10轮（仍包括回忆组件）。当前最后第9轮只读9c8...running，之后26997才启动组合回归。基线e3e7c2...通过GitHub插件fetch_commit_workflow_runs返回空列表（该接口仅PR工作流、第一页），不能据此声称不存在其他CI，更不能称已验证macOS；本地没有.github目录。外部验证缺口仍如实保留。

- 第11轮cfb...角色链完成，外部business-wrx9je5y独立完整contracts121例通过、快照一致，累计9轮。第7轮类型补修96a...actual running，后续第9轮最终只读9c8...queued。新增audit_mock_receipts.py只读SQLite+Git核对15份mock收据：均merged、精确head/base的CI/check成立、源与目标均为merge祖先、merge均为main tip ae1cd6e...祖先；重启去重证据一致，退出0。mock-final-audit.json记录15份证据哈希，不另计新测试次数。

- 第9轮5745...修复同步throw后，根独立timer探针实际退出0（原错误身份保留、pending timer为0），验证脚本同时模拟window/global timer别名以避免实现绑定。但任务因新增回归的RED原生工具错误1次failed，开发报告GREEN43通过不能改判。已MM resolve/project-resume并新派最终只读9c8deba1092e48eeac0c5ca17bda5188；accept-map中9更新，8轮总数不变。当前11复核cfb...running，后续7补修96a...、9只读9c8...queued。无生产调用/业务push。

- 天气6最终任务e755...角色链完成，外部business-u1388jbi独立12例通过、源码stamp匹配，round-06-final-receipt.json已落，累计8轮。第9轮计时器补修5745ed3290d241f9842b46f581527366已实际running，后续11只读复核cfb...和7类型补修96a...仍在队列；15735继续观察，26997等待组合门禁条件。验收汇总文档同步8轮，不将回忆组件说成已接入主应用。

- 原7恢复9abc...最终failed：tester首次2次工具错误，唯一只读重试仍因额外目录探测Bash exit2失败；报告PASS不覆盖工具错误。已MM resolve原失败并project-resume，天气6最终只读复核e755...已实际running，队列随后9补修5745...、11复核cfb...、7类型补修96a...。新docs/travel-workflow-validation.md集中说明各轮独立证据与限制，当前7份通过，组合门禁仍未运行。不能把角色口头报告或文件存在检查当实际业务完成。

- 第7轮9abc...开发报告日期WebKit重复8次、完整smoke34通过6平台限定skip、Overview26通过，已清理_diag/_debug-focus探针，reviewer PASS、tester尚在运行。但根独立实际应用类型检查`tsc --noEmit -p tsconfig.app.json`退出1：OverviewPage.tsx(99,26) TS2554（新增useRef缺初始参数）。开发仅检查根空files配置导致漏检，不能计本轮完成。已MM派发同轮最小类型补修96a760a88cf84b369e5590998aa0bd02，accept-map中7切换新ID。round-07-accept.py增加真实app类型检查，并将原3浏览器失败场景在两个平台各重复2次（12次，无skip）后才合并JUnit。原9abc...终态仍需跟进，失败则查原因恢复调度；不因观察超时重启。

- 第11轮08338...因修复前EPERM的1次工具错误failed，开发报告完整contracts121通过但不能改判。已MM resolve/project-resume，排队只读复核cfb0785e99fd4e12bd4625bf8416ea85，accept-map同步11新ID。第7轮9abc...已运行。根只读WebKit诊断date-focus-probe.mjs发现屏蔽main.focus仍可复现，实际date控件type属性为text，新值出现在input事件后又回旧值；未确证根因，且随后业务worker修改源文件，不能把诊断对照当固定版本回归。专用Vite4176进程PID28568核对命令后已停止；浏览器均关闭，证据date-focus-probe.json保留。当前第7轮worker尝试日期同步重复effect保护，仍待自测/评审/独立验收，不能提前认为修好。

- 第10轮ac5...现已succeeded，外部business-fbxftw4n独立9例通过，round-10-final-receipt.json记录源码stamp一致。tester首次因1次工具错误failed，监督器保留报告并自动进行一次只读fresh验证，第二次PASS后Leader汇总；没有手动重启/重派。当前共7份改进收据通过（包含未接主入口的回忆组件轮次；最终仍完成其他10轮）。第11轮08338c2695384248b82993eefc892ef0已实际running，其后7恢复、6复核、9补修仍queued。15735继续外部验收，26997继续等待组合门禁条件。

- 第10轮ac5...developer已进入reported_unverified，reviewer正在运行。开发报告同一最终测试文件旧实现4失败/5通过、修复9通过，tsconfig.app.json类型检查与目标oxlint通过，原始RED/GREEN在业务ignored test-results内；外部15735尚未出收据，不能计完成。根复核发现MemoryLibrary/MemoryViewer当前未接主入口（BrowserRoot/main/App无引用），只表述组件修复，不称主页面已上线。后续完成要求保留其他10轮真实改进，不以未接入组件包装应用闭环。根tsconfig.json仅references/files空，最终build中的tsc -b会覆盖应用类型检查，不能只靠pnpm typecheck退出0声称全应用类型已核验。

- 已准备最终组合回归`.runtime/goal-validation/final_local_gates.py`，Python语法检查通过，等待进程session26997（旧准备版72333仅观察器已停止，不影响业务）。动态读取全部11轮收据与accept-map，队列空闲且已知debug文件清理后才启动；依次typecheck/lint/build、contracts、web、backend、local、operations、amap两组、smoke+Agent浏览器测试，前后源码stamp一致才计本地组合通过。JUnit缺失拒绝；仅已知平台限定skip且另平台同用例实际通过才允许。尚未运行这些最终门禁，不称最终通过；macOS运行器和历史完成结果仍列外部验证缺口。第10轮开发仍有新输出，未重启业务。

- 完整Agent交互Playwright基线62549退出0：42项中40通过，2项为原先设计的Chromium PNA专属用例在iPhone跳过，XML为.runtime/goal-validation/agent-browser-baseline.xml。包含真实本机HTTP/签名测试transport，但不等于macOS Codex运行器或生产验证。第9轮原任务ffa170...终态failed，4次工具错误（包含RED/中间失败），模型报告最终42通过不足以改判；独立timer脚本再次RED。已MM resolve记录原因，project-resume确认，下一任务第10轮ac5...实际running。补修5745...继续队列。第9轮accept脚本改为内嵌独立JS，保证business.verify复制approved.py后不依赖相邻脚本路径；observer15735仍live。

- 第9轮继续开发时独立检查发现同步loader throw路径泄漏deadline timer；`.runtime/goal-validation/round-09-loader-cleanup.mjs`使用真实TS源码+离线window计时器集合，原错误身份正确但pending.size=1（预期0），退出1。已加入round-09-accept.py前置独立门禁。通过MM派发同轮补修5745ed3290d241f9842b46f581527366，原任务ffa170...仍运行，未中断、未重复计轮次；accept-map中9切换补修ID，observer15735确认queued。后续须继续检查原任务终态，若失败导致项目暂停须先审查再通过MM恢复。macOS验证用户答复尚缺，历史Codex list_threads复查仍Transport closed；不因此阻断当前独立业务工作。

- 后续复核：runner42395退出0，restart1/2/3全部通过，五类mock场景各3次，共15次独立验收成功。restart3任务77cf384557be418ba86669d67937f545独立19例；MM check/merge、重复merge和合并后emit均已确认。新增docs/mock-mr-validation.md说明证据与边界，21项mock/webhook回归重新通过。业务动态验收15735继续运行；第8轮38例已通过，当前6个改进轮次完成，第9轮仍运行，其余待验收，不将mock完成等同整体目标完成。

- conflict3 task8f6c89b15ab7410883fa84de94a86815通过独立16例、MM check/merge，冲突3/3，累计12场景。runner42395进入restart1；实际MM restart-receiver后重复emit返回原task，正在等角色终态再check/merge及重复合并/合并后emit。
- 天气第6轮恢复8a5...attempt2因模型使用未提供且不存在的Node22.22.3路径失败；查project配置、repo、prompt均无此路径，不能把模型声称“旧指令”当事实。等价重命名已完成且模型报告12通过，但仍未验收。已MM resolve/project-resume，新只读复核e7552704fd574fda836f5e07e2fbdf16队尾，明确唯一22.23.4命令并去除绑定函数名的脆弱gate，业务12例仍需通过。
- 旧77844因6失败退出。新增accept_live.py session15735读取accept-map.json动态观察6-11，无需按编号顺序等待；一任务失败仅记录attention，不阻止其他任务完工即独立验收。后续恢复改map对应ID即可，避免重新验证早先已通过但工作区已变化的历史任务。最终全量门禁仍必须独立复跑。
