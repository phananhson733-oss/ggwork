# 个人选剧工作台验收记录

当前状态见文末「2026-10-06 字典与简化雷达发布、预算内真实验收」：产品 `85c7cdb9` 已发布，普通 QA 只读及三个预算内真实运行通过，完整五层的最后浏览器逐断言审计已通过，最终 3 条记录、15 层检查均 PASS。参考表当前空计划，真实三晚采集资格仍未通过；provider 账单未核对。此前各节为相应日期的历史证据，不代表当前完整目标已经完成。

日期：2026-09-21。基线：DeerFlow固定提交 `29d285731b326a728a9df33d3641f73b68bbe48b`。主实例3007/8007，独立合成资料QA实例3008/8008，真实模型Ollama qwen3:8b。

整体结论：尚未全部完成。真实资料问题集和Docker运行仍缺证据。以下区分代码回归、真实模型、浏览器和业务验收，不相互替代。

| 计划 | 要求与当前证据 | 结论 |
|---|---|---|
| T0 | 固定上游、新仓库、原生Gateway/frontend启动、真实Ollama调用已验证。Docker磁盘满，镜像尚未完成 | 部分完成 |
| T1 | 私有metadata及0001/0002迁移；重复启动、未知迁移拒绝、身份隔离；官方扩展安装、14个业务Python源码/快照/wheel一致；宿主接线/身份/metadata命令86项通过 | 原生完成，容器待验 |
| T2 | CSV/JSON/Markdown原文和来源、坏批次不激活、内容去重、未知规则保持未知；tests/test_imports.py和test_selection.py | 合成数据完成，真实资料待导入 |
| T3 | 不可变候选、条件继承、call重放冲突、工具schema、配置策略入口、模型12/工具8预算；真实Qwen3成功查询；test_tools.py | 运行层已覆盖等待/退避；必要终态收尾不强行截断 |
| T4 | 结构化卡片从授权API读取，绑定owner/thread/result/item，切对话和刷新恢复的组件回归；浏览器实际打开候选；API与组件拒绝非success保存 | 运行中刷新、终态刷新和明确停止自动场景通过 |
| T5 | 事务命令回执、重放不复活已移出、并发保存、版本冲突和跨用户拒绝；真实确认保存两条；备注编辑后刷新、CSV、数据库复核 | 核心流程完成 |
| T6 | 资料UI已实现；真实资料来源未指定，10个业务问题无输入 | 未完成 |
| T7 | 备份恢复后API及浏览器读取一致；新engine/service重启回归；恶意知识文本无写权限回归；三条真实后端自动浏览器用例通过 | 部分完成 |

## 已有真实运行证据

- `pick-qa-dc0117fc` 的查询生成一份3条候选，运行success；查询约70秒。每条显示无指标来源时的明确提示，未编造榜单依据。
- 浏览器“第1、3部”追问经服务端序号映射形成确认卡，备注为“下周准备剪辑”。点击后UI返回2条已保存；DB记录一致。
- 模型曾误说已保存。修复后真实模型复测：prepare结束固定显示“点击确认保存后才会写入”，确认前后清单数量均为2。
- 备注改为“下周准备剪辑；已复核”，刷新后可见；CSV包含两个已保存剧名。
- 停止QA Gateway，脚本核验SQLite与原始资料SHA后备份；保留旧home，在原绝对路径恢复并重启。2批次、1候选、2选择、1命令回执保留。随后UI/API确认备注一致。
- 原始日志与合成数据保留在 `/tmp/ggwork-pick-qa/`，其凭据、数据库、备份不得提交到Git。

## 自动回归与语义边界

- `test_restart_persistence.py` 关闭engine及service后重建，检查候选、来源、知识版本、保存回执、备注更新及另一用户隔离。
- `test_imported_save_instruction_cannot_add_write_authority` 导入含“立即保存全部/不要确认”的知识，确认内容可被检索，但写工具被拒、prepare只有确认目标且数据库无新增选择。该测试证明写入权限边界，不声称模型语义上永不受恶意文本影响。
- `test_host_configured_gates_preserve_request_changes_and_fail_closed` 使用真实配置加载器，避免只测试直接Gate函数而遗漏观察插件包装器。
- 120秒运行层deadline已覆盖worker preflight、模型等待、重试退避和Agent执行；终态写入收尾可能让HTTP响应略晚于截止时间。模型和业务调用次数限制仍独立执行。
- 自动浏览器使用 `frontend/playwright.pick.config.ts` 与 `tests/e2e-pick/personal-selection.spec.ts`，真实登录/页面上传/本地模型/数据库回读，无page.route回放。最终结果另记于本记录的执行结果。

## 计划文件名与实际实现对应

保持同一契约，避免为单一用途创建空模块：知识检索在tools.py，候选算法在selection.py；owner/candidate/command/route回归分别合并在test_bootstrap、test_imports、test_selection、test_routes、test_tools。前端candidate-card/selection-confirmation由pick-tool-card与candidate-view承担，queries直接由组件React Query和core/pick/api提供；对话提交实际修改chat-page及core/threads/hooks。E2E文件名为personal-selection.spec.ts，重启回归与本验收记录现已按计划建立。

## 尚缺验收条件

1. 指定真实剧库和知识文件、10个人工预期明确的业务问题。
2. 主实例首次管理员设置已完成，真实资料尚未导入。
3. Docker使用共享Colima，59GB磁盘满，另外7个容器运行。扩容重启影响其他项目，已请求用户选择；未清理或中断其他服务。
4. 保存响应丢失重试与更新后旧依据场景现已通过，见末节。


## 本轮执行结果

- `pnpm exec playwright test -c playwright.pick.config.ts` 的个人保存用例已实跑通过：1 passed，约2.3分钟。保留认证，真实模型，页面上传、查询、保存、API回读、备注更新及刷新均通过。日志 `/tmp/ggwork-pick-e2e.log`。
- 指定宿主命令 `pytest tests/test_extension_gateway_wiring.py tests/test_extension_route_principal.py tests/test_persistence_migrations_env.py -q`：86 passed，日志 `/tmp/ggwork-pick-extension-gates.log`。
- 新增重启和恶意知识边界后，业务测试33 passed，日志 `/tmp/ggwork-pick-backend-tests-continuation.log`。
- 刷新策略新增显式 `onDisconnect: continue`，不依赖Next代理是否及时断开后端连接。运行中刷新和显式停止已在最终三用例套件中通过。


### 最终三用例自动报告

`pnpm exec playwright test -c playwright.pick.config.ts`：**3 passed (51.4s)**，日志 `/tmp/ggwork-pick-e2e-verified.log`。

1. 页面上传→真实模型→保存→API数据复核→备注修改→刷新：25.9秒。
2. 请求明确带on_disconnect=continue，运行中刷新保留问题，最终候选与成功run对应，清单不变：18.2秒。
3. 浏览器点击停止，观察真实cancel请求与interrupted状态，刷新后问题保留、清单不变：6.6秒。

这些是该次整条用例耗时，不是模型首token或每次查询延迟保证。曾出现开发冷编译叠加推理超过测试总时限、模型达到120秒deadline，以及Next默认30秒代理socket中断。当前Next代理为180秒，自动测试总时限360秒涵盖冷编译/登录/模型/读写，服务调用预算未放宽。冷启动模型超时仍作为失败终态保留，不能宣称所有本地查询都稳定在某个延迟内。

测试失败后只清理该用例发起的未结束运行，不操作其他线程。修正了测试监听的SDK `/api/langgraph/...` 路径，最终报告对应修正后的用例，而非忽略失败断言。


### 运行层deadline补充验证

2026-09-21新增host执行watchdog，替代此前仅由内层中间件计时的不足。服务端环境PICK_RUN_TIMEOUT_SECONDS=120覆盖worker入口后的preflight、模型admission、退避及Agent执行；终态落库/检查点收尾仍安全完成后结束流。

- `test_run_execution_deadline.py`：10 passed，含preflight阻塞、执行取消、用户取消优先、非法配置、Gateway环境绑定、跨deadline的慢终态写入和成功后继续工作。
- worker delivery/rollback、Gateway配置与业务组合：194 passed，日志 `/tmp/ggwork-deadline-final-tests.log`。
- 真实Gateway/Ollama负例：run `02832b6d-91c4-4ef1-a1cd-1ffa13972ec3`，服务端2秒、客户端尝试9999秒，最终timeout，2.19秒结束，health200且无选择新增。证明请求无法放宽执行期限；0.19秒额外收尾并非继续模型调用的许可。
- 独立代码复核通过，建议的两条timer收尾回归已加入。


### 资料更新与响应丢失：完成项补验

新增真实后端浏览器用例 `real local model preserves old evidence and retries a committed save`：**1 passed (1.7m)**，日志 `/tmp/ggwork-history-retry-e2e.log`。本轮仅修改测试和文档，没有新增产品行为。

- 真实Ollama查询后，把相同source/source_id的当前剧库导入为新标题和新指标99。
- 授权API仍返回旧候选原始items与catalog_batch_id；浏览器刷新后仍显示旧标题和指标7，未显示99。
- 第一次保存使用route.fetch真正到达Gateway并提交，再仅中断响应；直接API确认已存在恰好一条选择。
- 用户页面重试时两次完整command相等，包括request_id；命令回执与第一次真实提交相等，选择仍只有一条。
- 后续备注更新和刷新继续通过。没有伪造查询、工具、数据库或保存回执。

### 当前完成审计与外部阻塞

软件层的个人闭环、版本快照、权限/幂等、执行期限、刷新/停止、备份恢复及上述失败重试场景均已有对应证据；不把这些合成验收当成真实业务验收。

当前剩余原计划门槛：

1. **T2/T6：真实资料及10个可人工复核问题。** 主实例账号已建立，最新只读核对导入批次0、候选0；用户未指定首批真实资料和预期问题，不能擅自从其他项目复制生产凭据或代填真实业务数据。
2. **T0/T1：Docker构建与运行。** 最新Colima只读核对 `/var/lib/docker` 为59G、剩余0、100%使用，仍有7个其他项目容器。已有扩容/重启选择问题等待回复；不得自行清理或中断其他项目。

这两项在多轮目标继续执行中持续存在。当前可独立推进的本期软件实施和验收已完成，完整目标仍不能标记完成；待用户输入或共享环境条件变化后继续。


## T6 真实数据 10 题验收（2026-09-23，云端）

环境：https://ggwork-deerflow.vercel.app（主账号，个人清单为空），Railway Gateway，Azure `gpt-5.6-luna-2`。数据为 RealShort feed 定时同步的共享批次（03:31 / 03:40 UTC 两次）。验收期间没有写入个人清单。

| # | 问题 | 结果 | 耗时 | 结论 |
|---|---|---|---|---|
| 1 | 找5部英语剧，排除我已经选过的 | 5 部；符合条件共 2,239 部 | 8–10 秒 | 通过 |
| 2 | 看 KalosTV 日榜（kd），按名次给我前 5 部英语剧 | 最新一期 2026-09-22 共 9 部，第 1–5 名 | 9 秒 | 修复后通过 |
| 3 | 找 5 部团队没发过的英语剧 | 5 部，exclude_posted；说明「未匹配 ≠ 从未发布」 | 54 秒 | 通过（第二次模型调用慢） |
| 4 | dramaclips0364 这个账号没发过的英语剧，要有 KalosTV 周热门（kw）依据，找 5 部 | 符合 445 部，posted_account 生效 | 10 秒 | 修复后通过 |
| 5 | 现在候选池里英语剧一共有多少部？各剧场分别多少？ | 调用计数工具，2,241 部，8 个剧场的分项之和等于总数 | 5 秒 | 通过 |
| 6 | 找 3 部 ShortMax 的韩语剧 | 3 部；符合 42 部 | 6 秒 | 通过；模型把排序说成「按依据日期」，但 ShortMax 评级没有日期，实际是稳定 ID 顺序 |
| 7 | （接 #1）换一批 | 5 部新剧，全部不在 #1 里；沿用 #1 的批次 | 9 秒 | 通过 |
| 8 | （接 #1）第二部为什么推荐？ | 对应《My Fortune Angel Made Me a Queen》，依据与卡片一致 | 6 秒 | 修复后通过 |
| 9 | 找 3 部确认可以发 YouTube 的英语剧 | 0 部，没有编造 | 6 秒 | 通过；已在提示词里要求说明为什么是 0（来源只有「禁」或「待核实」） |
| 10 | 帮我查一下《Moonlight Vendetta Zeta》这部剧的依据，它没发过吧？ | 候选池没有；说明这不能证明没发过 | 13 秒 | 通过 |

**独立核对**：`scripts/pick-acceptance-verify.py` 按验收口径重新实现了过滤和排序，没有 import `ggwork_pick`。拿 04:06 UTC 从 feed 拉的全量数据（6,880 行）对照云端各题的结果快照：8 个带结果的查询，返回条目与顺序全部一致，每一条都满足条件。总数差异只出现在 ReelShort 这一支：03:40 到 04:06 之间按实时信号新进了 10 部（839 → 849），其他 7 个剧场分项完全相同。

**验收中发现并修复的问题**（均已加回归用例，并重新部署）：

1. 追问只在点开候选面板后才绑定结果，#8 的详情工具因此被拒；宿主工具错误中间件还把 ValueError 原文 `Error: Tool ... failed` 渲染进了回答。现在默认绑定本对话最新一张成功的候选卡；详情、准备保存、查询的业务拒绝改为返回 `{status: rejected, notice}`。
2. #2 按名次排序时，把不同日期的日榜名次混在一起排。现在只看该榜最新一期。
3. #4 模型对没有名次的 kw 用了 `sort=rank`，结果退化成按内部 ID 排序。现在批次内该种类全部没有名次时，直接拒绝按名次排序；提示词写明有名次的种类只有 kd、qc、qr。
4. 回答核对在生产上没有生效：`output_version=responses/v1` 下 AI 消息内容是块列表，旧代码只处理字符串。现在改为取 text 块核对，提示作为新的 text 块追加；用户自己写的《剧名》不算模型编造。验收时模型没有再编造剧名（它主动查了库），所以线上没有观察到这条提示实际出现，靠单测和前端拼接逻辑的代码检查确认。
5. 结果为 0 部时 `matched_total` 返回空值，现在返回 0。
6. 同一条「发布记录未对上」在卡片上显示了两次，现在只显示一次。

**测试侧备注**：浏览器自动化往输入框逐字输入中英混排文本时，会吞掉 ASCII 字符（「KalosTV」「5」丢失）；在页面加载后很快提交，偶尔会被忽略。改用 `insertText` 加 `form.requestSubmit()` 后稳定。这是自动化方式的问题，人工输入没有复现。


## P0/P1 上线前评审（2026-09-23）

范围：RealShort feed 同步、定时、名次与发布记录筛选、计数工具、回答核对、追问绑定。评审方式：分领域专项审、红队、两路对抗审；每条发现都对照代码复核，确认后才修。

**用户决定的四项**：

1. 存储上限：共享批次保留最新 3 份加 30 天内被候选用到的，其余连同 blob 删除；同步批次不再存原样副本；剩余空间低于 500 MB 拒绝发布。
2. 追问语义：只有「换一批」沿用绑定候选的条件和数据版本；其余查询按本次条件、用最新数据。「第 N 部」、保存、详情仍绑定最新一张卡。
3. 回答核对：提示按消息存进 `ggwp_answer_checks`（迁移 0004），前端显示在回答下方。原先改写的消息只存在于中间件内部：宿主在中间件之前已经持久化并推送了原消息，所以线上从未显示过这条提示。
4. 定时：改为 Railway gateway 进程内定时，启动时补跑；删除 Vercel Cron 路由和 `/cron/sync` 入口。Vercel 上的内部 token 可以冒充任意用户，不再放在那里。

**直接修复的问题**（均有回归用例）：

- 同步：
  - 内容回到之前某一版（A→B→A）时，当前版本停在 B，现在复用并重新设为当前。
  - 规则先校验再发布剧库。
  - 整个下载有 10 分钟时限；取消、进程重启都会结束 `running` 记录；关停最多等 20 秒。
  - 上一次失败时，手动同步原本没有冷却，可以连续点击反复拉取；现在要隔 1 分钟。
  - 校验错误只报字段位置，不带字段值。
  - 孤立代理项按 replace 编码。
- 筛选：
  - 同一天同种信号优先取有名次的。
  - 未知账号或信号种类直接拒绝，不再静默匹配空集。
  - 计数工具支持「换一批」的排除。
  - 代码缺陷（KeyError / IndexError）不再伪装成业务拒绝。
- 回答核对：去掉「没发现」「发给你」之类的误报，堵住逗号绕过，剧名最长 500 字。
- 迁移：0003 可重复执行，降级前把 `pruned` 批次改成 `failed`。
- 前端：
  - 资料页点「立即同步」后轮询 2 分钟。
  - 按发布记录筛选时，卡片不再显示无关的「未对上」行。
  - `posted_unavailable` 也显示提示。

**修复后复审**（后端、前端各一路，逐条对照代码核实）：

- 确认并修复：
  - gateway 关停时等定时任务没有时限，改为和手动同步共用 20 秒。
  - 空间检查排在清理之前，磁盘满后永远清理不到过期批次，改为先清理后检查。
  - 模型消息没有 id 时，提示统一存成空串，第二条撞唯一约束后被静默丢掉；NOT NULL 违例也被同一个 except 吞掉。现在没有 id 就存 NULL；撞约束时确认已有同一条才算重试，否则抛出。中间件把存储失败记日志，不影响回答。
  - 迁移中途崩溃（SQLite 的 DDL 逐条提交）后重跑，会漏建索引，0003、0004 改为单独检查索引。
  - 一条提示最长约 2,540 字，前端 schema 上限是 2,000。超长时整条线程的提示都解析失败，现在上限改为 4,000。
  - 回答结束时对同一接口发两次请求（后一次取消前一次），改为合并成一次。
- 核实后不改：
  - 「清理可能删掉正在用的批次」：任务只固定当前最新批次，要在一轮 120 秒内再发布 3 批才会被清理；定时每天两次，手动同步冷却 5 分钟，不可能发生。
  - 「10 分钟时限不含发布」：时限本来就只针对下载，文档写的也是下载。
  - 「资料页首次打开多刷新一次导入列表」：只多一次请求。

**验证**：
- 后端：pick 扩展 94 passed，ruff 通过；托管副本与源码 `diff -rq` 为空。
- 前端：rstest 1936 passed，tsc、eslint 通过；prettier 只报了本轮没改过的生成 fixture `backend-result.json`。

**上线（2026-09-23 14:20 北京时间）**：
- 提交 `80077f1`（代码）、`554ce0c`（文档），推送到 ggwork main。
- 先删 Vercel 上的 `CRON_SECRET`、`PICK_SYNC_TOKEN`、`DEER_FLOW_INTERNAL_AUTH_TOKEN`，再部署前端；新部署里不带这三个变量，项目上也没有 Cron 任务了。
- Railway 部署后，日志显示迁移 `0003 -> 0004`、扩展 1/1 加载、`/api/pick/answer-checks` 挂载、`/cron/sync` 不再挂载；之后删除 Railway 上的 `PICK_SYNC_TOKEN`。
- 线上核对：
  - 回答核对接口返回 200。
  - 资料页「立即同步」走新路径，约 10 秒拉到 7,086 部；面板和导入列表随后刷新，规则批次按内容去重，沿用原来那份。
  - 自动化浏览器的标签页不可见，react-query 暂停轮询，面板要等页面可见后才刷新；真人正常查看页面时不受影响。

**RealShort 侧修复上线**（phananhson733-oss/realshort#66，2026-09-23 06:40 UTC 合并并部署，merge `9fc159c`）：
- 直接读 feed 核对：7,120 行，source_id 无重复；ReelShort 4,559 行的 4,565 条条件信号全部带日期（出站 4,555、账单 10）；4,541 行标签已切开，没有残留「 · 」；没有格式错误的日期。
- 06:44 UTC 手动同步成功，7,131 部，工作台的严格校验接受新 feed。共享剧库批次 5 份都保留：最新 3 份，加上 T6 候选卡引用、仍在 30 天内的 03:31 和 03:40 两份。

**遗留**（下列 RealShort 侧前四项已由 realshort#66 修复）：
- RealShort 侧：
  - ReelShort 行无信号日期，标签被拼成一条。
  - `feedDate` 接受 2026-02-30。
  - 截断按 UTF-16 可能留下孤立代理项。
  - 分页缺测试。
- 性能：每次查询都要解码整批 JSON，可以加缓存。
- 其他：
  - feed 未知字段会让整次同步失败，行结构变化必须升级 `FEED_VERSION`。
  - 前端必须先于后端部署。
  - 分页期间不是一个快照。
  - 下载没有字节上限。


## 2026-10-05 readiness 集成验收增量（未发布）

本期基线 `5c39bdf2`；集成分支为 `codex/pick-readiness`，见 [PR #31](https://github.com/phananhson733-oss/ggwork/pull/31)。生产前后端仍为 `ddbf9f14`，前端版本 `20261005-ddbf9f1`，本期未部署。代码验证、真实模型验收和发布状态分别记录；集成 SHA 再变化时，旧 SHA 的测试结果不能直接作为最终通过依据。

| 项目 | 本期增量与已有行为的区别 | 当前证据与限制 |
|---|---|---|
| RD-01 历史时效 | notes 使用候选生成时间重新核对；当前 query/count/detail 使用本轮时间；批次年龄不推断同步停止 | 时间边界、未知 created_at、权限/只读与旧卡兼容回归已实现；真实旧卡组合待本期业务验收 |
| RD-02 候选核对 | 原有标签/上架/渠道与折叠证据之上，增加可见主证据及日期、事实/待核实分组、历史参考标签和北京时间 | UI 分支 573 项 pick 单测/check 通过；相同合成输入 5/10/20 部、390×844 与 1280×720 静态浏览器 12 组通过，证明换行、按钮和原生证据展开；不代替宿主页面 hydration/API E2E |
| RD-03 模型投影 | 查询/详情仅移除详情链接及能由 citation_id 映射回完整证据的精确重复来源；其他事实、未知字段、obs 引用和保存合同保留 | 后端投影/真实宿主边界回归及前端实际 projector fixture → 正确 ID → 完整候选 → 明确确认 → 精确 item_ids/note → 回执的 DOM 证明；正向兼容测试首次即通过，无为制造红灯而改产品 |
| RD-04 回答核对 | 修复免责声明误报，扩大已返回片名的无书名号和中英文列表范围，保留账号/团队区分与保守未知边界 | 独立审查发现的长片名、混排、账号同名问题已修并回归；已有核对记录不重写，真实回答语义仍需独立判读 |
| RD-05 来源与 oracle | 精确共享 catalog/knowledge 配对、完整行、metadata、运行配置及初始个人状态只读冻结；独立 checker 不调用产品筛选算法 | 私有原始文件/hash 已封存，未提交真实剧名、逐行数据或凭据；捕获协议与检查器已独立审查；实际改前运行状态见下方，不能把 source 已捕获当作 Q01～Q20 已通过 |
| RD-07 雷达 | 对已存在的 Trends/GSC 实现做只读就绪判断 | [审计结果 BLOCKED](trends-readiness.md)；未满足雷达下一阶段门槛，不阻止独立的个人查询/保存功能 |

集成验证：扩展全套 SQLite/PostgreSQL **4,312 passed / 21 skipped / 0 failed**；原时间格式静态失败已在此次复跑中消失。随后仅调整模型 schema 的默认条件描述，定向双库、宿主提示、工具和托管副本 **101 passed / 0 skipped**。宿主回归 **416 passed**；前端 check 和 production build 通过，常规单测 **2,848 passed / 45 skipped**，这 45 个 reader 集成项另用真实 PostgreSQL 补跑 **45 passed / 0 skipped**。资料页真实本地 E2E **6 passed**。不将未执行项或跳过记为通过。

投影性能分开报告：原固定合成 baseline 的 JSON UTF-8 字节降幅 **7.79%**，未达到规格 20% 工程目标；额外重复链接密集样本约 **26%**。两个样本结构不同，后者不能覆盖前者的目标缺口。尚无可归因的真实 token、成本或时延改善结论，不提高预算、裁剪事实或缩减请求条目以凑指标。

Q01～Q20 当前尚无本期完整五层通过报告；未执行项记 **NOT_RUN**，已运行但缺证据的项记 **UNVERIFIED**，不能由来源已冻结或局部机制测试通过推导整题 PASS。后续按 manifest 的逐 case/step、typed tool/terminal 合同及真实 run 总预算记录实际结果；失败尝试、数据版本变化与缺少语义判读分别保留，不能只报告重试成功。发布状态为 **未发布**。


### 实际运行与失败保留

- **本地隔离模型**：4 个实际 run（含 1 次已登记重试），覆盖查询后保存、刷新和取消。刷新/取消两个现有用例通过；保存用例的首次失败是过早匹配现有 alert，重试在真实提交后遇到 Next route announcer 导致严格 locator 冲突。两处测试修复后，以已有成功结果执行无新增模型的独立恢复：首次真实提交后丢弃响应、同 request_id/完整 body 重试、单一选择、原备注与旧证据、刷新保留全部通过。两次原失败保留，未改写成全用例首次绿灯。
- **远程普通 QA 改前基线**：6 个 Azure run，分别为 Q02、Q11、Q13-valid-zero、Q13-invalid-theater、Q14-A、Q16。Q16 已实际明确确认保存两项并测试提交后丢响应重试；仅使用专用普通 QA 的个人清单。
- **Q02**：独立名单总数、10 条身份和顺序相符；实际浏览器逐卡证据展开、确认控件可达及刷新保持通过。首次浏览器 5 秒等待超时与随后显式 45 秒等待通过分别保留。
- **元数据适配缺口**：阶段一封存 metadata 漏带独立 pin 中已有的 mirror_version；实际输出与预期其他字段一致，但原 checker 数据层 FAIL 原样保留。后续 Q16 的新 manifest 在运行前从原始独立 pin 补齐，不修改阶段一锁定文件或结果。
- **Q11 意图 FAIL**：模型把默认 exclude_selected=true 主动改成 false；空 QA 清单让数量仍相同，不抵消意图偏差。仅强化模型读取的字段说明，合法显式 false 与运行算法不改；改后模型效果仍待验收，见 [专项记录](readiness-q11-default-note.md)。
- **Q14-B/C NOT_RUN**：驱动在打开候选面板后把下一轮 prompt 填进保存备注，未派发 B，C 未执行。已修为聊天 form 专属输入框并做无模型双输入框回归；没有用新会话代替原换批链，也没有再次消耗模型来改写这条基线。
- **Q18 NOT_RUN**：新 QA 初始没有历史卡且只冻结了一个来源版本；没有伪造资料更新或倒填历史时间。

本期合计 **10 个实际应用模型 run**（4 本地、6 远程），统一计入 40 次上限；ChatGPT Pro 的静态审计单独记录，不算业务模型运行。远程 6 个 run 的 tokens、模型调用数、工具调用数与服务器时间均从权威资料提取，不用客户端整场景时间冒充模型延迟。尚无改后同条件样本，不能宣称性能改善。

ChatGPT Pro 只审查了实际投影模块、给定调用契约与时效原则，结论为该范围没有已确认业务可达缺陷；提出的 kind=null/0 边界已沿真实导入 schema 验证为非法输入，并补独立字段保留断言。该结论不替代整仓库或真实模型验收。


最后的独立审查关闭了验收器对既有冻结父卡的误拒绝：既有 parent_chain 必须与新 API 导出完整、类型敏感地一致，不能为了通过而重发 query 或丢弃 evidence；新生成结果仍要求同 run 生产者及因果时间。新增合成边界后 checker 与托管副本检查 **162 passed**。成功 step 现在一律保留私有截图供人工复核，manual 标记仍不会自动生成 PASS。该采集改进仅用于未来执行；本次 Q16 重新打开页面后看不到的短暂保存反馈单独保留 **UNVERIFIED**，没有补造历史截图。Q16 预锁的六项操作/持久化检查已分别由原始 dispatch/receipt 与当前只读 DOM/API 证实。


### 改前基线的最终分层判定

原始 captures 与锁定 expectations 均未改写；另外生成的复核副本只添加独立浏览器、语义和性能证据。六条回答共 20 条预锁语义标准，19 通过、1 失败（Q11），不据此换算整个工作台完成率。

| 场景 | 意图 | 数据/状态 | 浏览器 | 回答语义 | 性能记录 | 本次总体 |
|---|---|---|---|---|---|---|
| Q02 | PASS | FAIL：原 metadata 适配漏版本 | PASS | PASS | PASS | 保留 FAIL；独立名单/数量核对相符 |
| Q11 | FAIL | FAIL：另含原 metadata 缺项 | NOT_RUN | FAIL | PASS | FAIL；说明修正效果待改后实测 |
| Q13 合法零结果 | PASS | FAIL：原 metadata 缺项 | NOT_RUN | PASS | PASS | 保留 FAIL；独立零结果诊断相符 |
| Q13 无效剧场 | PASS | UNVERIFIED：旧拒绝响应无来源 | NOT_RUN | PASS | PASS | UNVERIFIED |
| Q14-A | PASS | FAIL：原 metadata 缺项 | NOT_RUN | PASS | PASS | 保留 FAIL；B/C 未执行 |
| Q16 | PASS | PASS | PASS：六项预锁操作/持久化检查 | PASS | PASS | PASS；额外瞬时反馈观察仍 UNVERIFIED |

这些是 **生产旧源码的改前基线**，不是本 PR 集成版本的生产通过记录。Q16 单题通过也不表示 Q01～Q20 的改后完整矩阵通过。未纳入本次基线的题为 NOT_RUN；Q18 的具体缺证原因见上文。


## 2026-10-06 readiness 生产发布与改后验收

业务仓库为 `phananhson733-oss/ggwork`（本地 ggwork-deerflow）。PR #31 已合并，实际部署的产品提交为 `d236f951d76d011bc2e704a772b2382345b8335b`；前端版本 `20261006-d236f95`，入口 https://ggwork-deerflow.vercel.app。Gateway 部署 `457941b3-0f1d-49d8-8ccf-2b0b6d93b735`，Vercel 部署 `dpl_CKFUdQYs4ofv8TeYEoWGSxQKLKoW`；canonical alias 已核对。部署守卫、实际 installed 包/源码比对、登录后历史数据和 readiness 通过，迁移仍为 0007。文档和验收工具后续提交不冒充线上产品 SHA。

### 来源、执行与判读边界

- 改后使用专用普通 QA 身份、线上真实模型和真实共享剧库。预锁意图、状态、来源及预算后执行，原始 captures/expectations 保留，五层复核仅添加独立证据。私有数据、题目原文和凭据未提交 Git。
- 上线后的只读冻结得到真实 mirror v23，候选池 11,379 部；旧候选仍固定 v22。Q18 因此使用真实版本变更，没有制造资料更新或回填历史时间。
- 改后共 26 个应用 run，连同原 4 个本地及 6 个远程基线，累计 36/40；本次改后没有新增重试 run。重新生成、编辑、分支和取消均计入账本。
- 模型及 600/300/300 秒运行/请求/分块预算保持既定生产配置；不能把单次通过解读为长期模型稳定性或成本改善。

### 已验证的关键业务行为

- Q11 使用同一基线提问，模型保持默认 `exclude_selected=true`，严格渠道/在架条件不放宽。原基线 FAIL 保留，本轮意图、数据与回答语义通过。
- Q14 在同一换批链上连续三批各 5 部，15 部不重复，符合总数按 2,942 → 2,937 → 2,932 递减；不是另起会话替代换批。
- Q15 引用本轮 20 项结果的第二项；Q17 重新生成与编辑保留原候选引用，新分支清空父引用并产生属于新线程的结果。本轮不冒称另做过伪造跨线程引用的线上负向攻击测试。
- Q16 模型准备阶段不写入；明确确认后新增 2 项，主动丢弃已提交响应后，以完全相同的 request_id/body 重试，回执一致且没有重复写入。原 2 项不变，QA 清单最终 4 项，备注及刷新保持，原始截图记录瞬时成功反馈。
- Q18 旧卡的 items、created_at、data_as_of 与 notes 在新查询前后相同；真实执行查看旧卡、切换每页 20、翻页、详情返回和刷新，旧 URL 状态/版本保持。新查询使用 v23。
- Q19 断线后刷新恢复通过；主动停止对应 POST cancel 返回 202，权威运行终态为 interrupted，未新增选择。取消时 provider 的 input/output tokens 与 model_calls 没有完整收尾证据，保持 null/unknown，不能填 0 或据此推算费用。独立取消证明来自服务端 HTTP 日志，并非找回了原始浏览器请求包。
- Q20 引用冻结规则原文中的核对日期，明确说明并非当天重新核验；返回链接可在原文中定位。

### 保留的限制

1. Q19-stop 的取消功能已证实，取消调用的用量记录仍 UNVERIFIED；这不支持“20 题所有五层全通过”的表述。
2. 原固定投影 baseline 字节降幅仍为 7.79%，未达到 20% 工程目标；额外重复链接密集样本约 26% 单列，未证明真实 token、费用或时延改善。
3. RD-07 Trends/GSC 只读审计结论仍为 BLOCKED，未开启雷达下一阶段、观测 cron 或新采集。
4. 原基线的 FAIL/NOT_RUN、浏览器适配失败及 CI 失败均保留；新证据不改写旧记录。手工 CLI 部署成功也不证明推 main 会自动发布。


### 上线后验收五层矩阵

覆盖 **20 个用例、27 条捕获检查、26 个独立 run**。原始捕获和运行前锁定的预期保持不变；以下状态由当前独立 checker 对追加证据的副本重新核验。

| 用例 | 意图与工具选择 | 数据与状态 | 浏览器交互 | 模型解释 | 性能与成本 | 综合状态 |
|---|---|---|---|---|---|---|
| Q01 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q02 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q03 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q04 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q05 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q06 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q07 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q08 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q09 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q10 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q11 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q12 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q13 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q14 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q15 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q16 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q17 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q18 | PASS | PASS | PASS | PASS | PASS | PASS |
| Q19 | PASS | PASS | PASS | PASS | UNVERIFIED | UNVERIFIED |
| Q20 | PASS | PASS | PASS | PASS | PASS | PASS |

用例汇总：19 PASS，1 UNVERIFIED，0 FAIL，0 NOT_RUN。记录检查汇总：26 PASS、1 UNVERIFIED。

独立语义判读 **97/97 项 PASS**。性能指标可获得 **127/130 槽**；缺失槽位没有填成 0。

性能与成本层的 PASS 表示预锁指标证据和预算检查通过，不表示已核对供应商账单，也不表示相对基线已降低费用。

Q19 的四个功能层均为 PASS，性能与成本层为 UNVERIFIED。停止场景的输入 token、输出 token 和模型调用次数没有可核实计量；provider 取消期间的 token 和成本也仍未知。已知工具调用次数为 0，不代表模型用量为 0。

Q19 停止功能补充证据来自精确匹配的服务端 POST cancel 202、受控 producer/SDK、权威 interrupted 终态和界面/清单证据；不声称找回了原始浏览器网络包。原 UNVERIFIED 判定及其后独立补充判定均保留。

Q13 本轮上线后仅执行 valid-zero 场景。此前 invalid-case 的基线覆盖不计入本轮新增执行，也不据此扩大本轮验收范围。

### 验收工具补充修正

[PR #32](https://github.com/phananhson733-oss/ggwork/pull/32) 仅修改验收 checker、测试及文档，不改变已部署的业务实现。拒绝场景现在也核对预锁条件集合，避免模型改错语言等条件却因同一种拒绝而误获意图 PASS；实际模型捕获和预期均未改写。

复杂度测试先后暴露了机器相关的 500ms wall-clock 门槛，以及同进程全局 GC 对成对 CPU 计时的污染。前两次绝对计时失败和一次 CPU 比例失败均保留。本地保留大量无关存活对象能够复现同型误报，但不能据此断言远程 CI 的唯一原因。最终测试在每次测量内隔离循环 GC、恢复其原状态，并采用当前线程 CPU 时间；12 类原输入及四倍输入对应增长比小于 6 的阈值保留。独立二次扫描反例仍被拒绝，生产算法未改。本地 checker、answer-check 与托管副本合计 286 项通过，格式检查通过；合并门槛以补充 PR 最终 HEAD 的 CI 为准。这不构成端到端 500ms SLA。

## 2026-10-06 后续修复发布前验证（历史记录）

后续分支以业务 main `8f2311da` 为代码基线，实际部署仍按上方 `d236f951` 记录。新增 `metadata.deerflow_usage_observation` 不修改数据库 schema 或顶层 RunResponse 形状；本期未触发新的模型验收，统一账本仍为 36/40。

- 改前宿主 canonical offline：18,425 passed / 167 skipped / 3 deselected；blocking-I/O 149 passed。首次附加 UV_FROZEN 的环境冲突轮次保留；移除额外设置后重跑全套通过。
- 作者/独立审查覆盖已知 partial usage、原 atomic admission 的伪造字段持久化、显式零与派生 total、重放、external source 去重、closed journal、三种 store 和 worker 取消；两项 HIGH 均修复后独立复验。它们使用本地合成模型，不计作网络模型或生产通过。
- 集成宿主全量最初 18,448 passed、1 failed：旧 metadata 精确断言更新后揭示真实线程污染，Gateway 已按 run/thread 作用域过滤新增字段，235 项相关测试通过。随后全量只剩指南字节预算失败；仅压缩本轮相关指南说明，原约束保持，13 项指南回归通过，base→HEAD 检查 0 errors，既有 soft/不增长的 inherited 警告保留。最终全套 CI 待确认，不声称上述轮次单次全部绿色。
- 业务 SQLite/PostgreSQL 全套：4,351 passed / 21 方言或重复计时 skipped。新增借用另一 owner/线程引用的合成运行层测试验证 query/prepare 均在初始化前拒绝，源候选与两方清单不变；无效剧场新增范围也不生成候选。本轮这部分为合成负向覆盖，不冒称线上攻击测试。
- 前端常规 2,874 passed / 45 reader skipped；真实 PG reader 45 passed / 0 skipped；check/build 通过。用量文字增量复用 i18n/token 测试 8 passed，最终 build 通过，明确视图为空不证明供应商未返回数值。
- canary-report 新测试及静态时钟检查双库通过；独立验证只有 SELECT、UTC 日界、批次隔离、缺日未知和 typed whitelist，输出不含身份/错误正文/凭据。只读现场仍显示限流和终止，qualification=not_evaluated，不据此解锁任何路线。

团队和外部写入仅交付已复审规格。输入表示和雷达范围两个选择仍待用户回复，原输入样本与原失败/UNVERIFIED 记录保持；新编码实验不是已批准实现，更不是模型理解或降费验收。


## 2026-10-06 后续产品发布与验收追加

产品源码 `148fff7b08d22c1112fd142e62f0076698862fd3` 已经经守卫发布，前端实际 About 版本 `20261006-148fff7`。最终 PR CI 与合并后的 main CI 均通过，最终本地宿主全套 18,449 passed / 166 skipped / 3 deselected；上述发布前失败轮次不删除。

Gateway 健康、限定源码/已安装包一致性、模块导入与 metadata 接口核对均 PASS；生产业务迁移头仍为 `0007`。正式前端 alias 指向本次 READY 部署。普通 QA 的旧十部候选、生成时点 notes、四条已选记录及备注/版本/刷新恢复核对 PASS，Token 用量文案核对 PASS；浏览器无 console/page errors，未新增模型运行或业务保存。

私有浏览器脚本初轮把对话列表只读搜索 POST 当作未批准 POST 拦截，结果保留为 UNVERIFIED；核对实际端点与 store 为只读后，仅补准确路径和读取字段白名单，重新运行 PASS。不将初轮归为产品问题，不跳过 console 错误断言。

**执行前计划（历史记录）：** 准备用原 producer/protocol、独立新 manifest 与统一账本，只登记一次 Q19 取消运行，等真实 `calls_started >= 1` 后由 UI 停止，并核对 cancel 202、interrupted、finalized metadata 和刷新持久化。沿用 Q19 用例编号不合并旧记录；新的 manifest hash/reservation 区分本轮证据。历史 Q19 的缺失 token 与 provider 成本不回填，也不因后续功能通过改写原 UNVERIFIED。

模型输入格式和雷达范围仍未选定，团队/外部写入规格交付不代表功能上线；完整后续目标保持未完成。


### 单次取消观察追加结果

新 manifest `10af70d2…` 以独立步骤/证据命名空间复用 Q19，原 producer/protocol 和统一账本保持，实际只新增一次运行。私有目录 ESM 加载失败发生在 collection、认证和派发之前；兼容修复后，真实 CLI `--list` 及独立复审通过，再执行 1 passed（21.5 秒）。原启动失败保留，没有自动重试模型。

功能与本地观测持久化 **PASS**：Stop 前同一 run 的 `calls_started=1`；精确取消 POST 为 202，权威终态 interrupted，metadata finalized；刷新后观测一致、四条个人清单完整一致，运行用量键未污染线程 metadata。收尾只读检查没有 pending/running 用户任务，生产迁移头仍 `0007`。

Provider 用量与计费 **UNVERIFIED**：三个 known token 字段均为 null，coverage=unknown，保留 `provider_usage_missing` 和 `call_terminal_callback_missing`。实际未观测到完成/错误/取消终态模型回调，对应计数仍为 0；不由 run interrupted 推造 `calls_cancelled=1`，不将未知 token 或费用填为 0，不改写历史 Q19 五层矩阵。

账本累计 **37/40**，与运行前快照比较，前 36 次 attempts 和全部旧 reservations 内容保持，仅追加一条 interrupted 运行及其单次 reservation。完整后续目标仍因模型表示和雷达范围两个待决项未完成；团队/外部写入仍仅规格交付。


## 2026-10-06 可逆字典与简化雷达：集成本地验证

用户明确选择可逆模型证据字典及 09-30 简化雷达范围，基线为业务 main `94e578f9`。候选源提交已集成至 `09e60fb4`；本节仅记录本地验证，生产仍为 `148fff7b`，最终 CI、部署身份及三个真实模型运行尚未完成。

- 原普通 10/20 部 golden 字节和 SHA 不变，当前模型 JSON 为 12,935 / 24,705 B，相对 17,416 / 34,116 B 缩减 25.7292% / 27.5853%。严格 >=20% 字节门槛通过。obs 原格式保留，独立解码覆盖类型、未知字段、独有事实、引用、顺序和命名冲突；HTTP、持久化和缓存仍保留完整证据。该数值不证明 token、账单或模型理解改善。
- 简化表及日级来源已完成本地实现：三个榜分别取最新一期，按身份去重，用最新滚动 30 天收入补足；当晚任务与同批 raw 驱动参考表，缺日保持空值。无 GSC、智能体趋势排序、集合发布、新迁移或前端数据库扩权。
- 业务扩展 SQLite/PostgreSQL 全套 4,490 passed、25 skipped。此前完整轮次的一个 `/sync.obs` 夹具失败已保留；修复后完整响应比较及原 PostgreSQL 比较均通过，未放宽旧合同。
- 前端完整常规测试 2,933 passed、45 reader 项跳过；同 45 项另在本轮拥有的 PostgreSQL 55581 上实际全部通过。类型检查与生产构建通过；新增字典卡片路径 12 项通过。之后集成的 QA 协议单文件 26 项、Python checker 205 项分别通过，不重复累计为额外产品覆盖。
- QA consumer 使用独立审计解码器，保留实际 raw/model JSON 字节和 hash；同一新 query 的位置 2 详情及位置 1、3 prepare 采用预锁跨步绑定。prepare_only 阻断业务写、拒绝 receipt，要求四条完整清单前后相同。审查发现 Python 普通 equality 漏掉 bool/number 改变，已用严格 JSON 比较修复；四项反例先失败再转绿，旧 prepare_save 两次回执断言保持。
- 真实 Playwright CLI 的离线 `--list` 注册了一个 Q03、三个 steps；仅使用合成存在性资料，没有登录、模型或 ledger 写。此检查不是业务验收。全球应用预算仍为 37/40，本轮仅计划剩余三次：新 20 部查询、同快照详情、预备保存而不确认。

### 雷达运行门槛仍未通过

2026-10-06 的只读生产报告显示：target_date 10-01 / 10-02 分别发出 105 / 68 个请求，各出现 3 次 HTTP 429 并熄火，第二晚触发 canary_terminated；10-03 至 10-06 为零请求拒跑。前两晚有效但失败，后四晚不构成恢复证据。历史聚合进一步显示 429 出现在 multiline/related，请求间隔中位数约 6.1 秒，出口 IP 字段缺失，不能据此定位根因。

简化代码没有修改 client、pacing、breaker、budget、cookies 或出口，没有证明已解决 429。stable 和 preflight 已补停止历史拦截，不能靠切模式或改计数日期启动。三晚真实出口资格仍需原因诊断及符合既有恢复条件的后续运行；本期本地验证不能替代该门槛。


## 2026-10-06 字典与简化雷达发布、预算内真实验收

产品发布为 `85c7cdb94684174c3c3ed8e80fe4dbea9986d00f`，来自 [PR #37](https://github.com/phananhson733-oss/ggwork/pull/37)；其文件树与最终 CI HEAD `7f7c3beb` 相同。PR CI `37417233926` 与合并后 main CI `37418843110` 均 SUCCESS。

- Gateway `b2bd9224-b195-4e80-ad13-6c71e39862ea` SUCCESS；实际源码树 1,158 文件及 installed 149 文件与产品提交一致；startup 43 行没有关键错误，health readiness 的数据库/checkpointer 均 ok，应用活跃运行零，业务迁移头 0007，没有新迁移。
- 前端 `dpl_2STkENAZu2LdsNZTUXj9nVayBjXw` READY，正式域名匹配该部署，普通 QA About 显示 `20261006-85c7cdb`。发布包来自 `git archive` 已跟踪文件及唯一 `.vercel/project.json`。
- 独立普通 QA HTTP 与只读浏览器验收：旧 Q02 结果、完整 items/data_as_of/created_at、notes 原生成时点、四条完整个人清单及刷新保持，页面没有脚本或控制台错误。
- 简化雷达实际入口 `?tab=trends&ts=order` 与 `GET /api/pick/obs/trends-table` 匹配。当前批次/计划为空、零行；空态、日期/计数及刷新验证通过，但没有新的采集曲线，十个表头的实测标为 NOT_APPLICABLE_EMPTY_PLAN。qualification 为 NOT_ASSESSED。首次私有 helper 用错 `t` query 参数的 UNVERIFIED 保留；修正后独立批准再只读执行，不改变产品或任何 collector/GSC 标记。

### 三次真实模型运行

专用普通 QA 使用一个新线程和新结果；完整关联 ID、来源原文及映射留在私有封存证据，入仓仅记录 step 标签、脱敏指标和结论。新预锁基于 12,096 条冻结目录记录、1 份知识资料、完整四条已选记录。独立 14 条件过滤得到 3,037 个匹配、前 20 项的确切顺序。当前 catalog 未配对 mirror，`mirror_version` 为显式 null，没有补旧版本。

| 步骤 | 私有证据 step 标签 | 工具输出格式 | 原始 UTF-8 字节 |
|---|---|---|---:|
| 新英语 20 项查询 | `codec-query-20` | facts-ref-v1 | 16,246 |
| 同快照第 2 项详情 | `codec-explain-second` | 无收益时保留原内联 | 1,231 |
| 同快照第 1、3 项预备保存 | `codec-prepare-two` | 原准备保存合同 | 275 |

Playwright 一个三步用例通过（1.1 分钟），三个 run 均 success。完整四条清单在预备及刷新后保持；save_requests/receipts 均为空，实际响应 requires_confirmation=true，备注精确为 `codec验收待确认`。原始 encoded/raw/model JSON、字节和 SHA 保持；独立 QA 解码仅供审计，不重新发送模型。singleton 详情没有字典收益，保留旧格式符合回退合同；预备保存输出没有压缩改动。

应用模型预算最终为 40 actual / 40 reserved / ceiling 40，不再新增运行。首次启动在模型请求前因 SQL/API 的初态数组排序差异被拦，记录零 attempts/records；独立审计后按相同锁协议仅回收三个未派发预留，保留原 reservation、失败 captures 与账本备份。修正新初态采用已验证 API 顺序，全字段对照 SQL，原始 SQL/API 均未修改；随后新哈希预锁、CLI --list、三次实际派发分别记账，没有自动模型重试。

独立审查已通过：真实新 20 项身份/顺序/全证据、同 result/detail/prepare 因果绑定、四条完整清单无写入，九项回答语义标准。第二项只有一条证据，grade/note 没有非空事实；真实回答如实说明评级未知、备注未记录、数值未提供。这个样本只证明未知值及来源/日期忠实解释，不证明非空 grade/note 理解。准备回答明确点击确认后才保存，没有保存完成声明。

### 已记录用量与时长边界

实际 run API、消息、事件与 finalized complete local_callback_lifecycle 互相核对；原 producer 的 null 指标及 UNVERIFIED wrapper 不被覆盖，补充另写衍生证据。

| 步骤 | 已记录输入 token | 已记录输出 token | 本地回调调用数 | 服务端 run 生命周期 |
|---|---:|---:|---:|---:|
| 查询 | 19,762 | 1,779 | 2 | 17.398618 秒 |
| 详情 | 29,316 | 509 | 2 | 9.398339 秒 |
| 预备保存 | 15,229 | 54 | 1 | 5.276732 秒 |

这些是本地回调已记录用量。时长为服务端 updated_at − created_at，不是模型延迟；provider billing 未核对，不能把字节降幅、回调数或 token 总量解释为账单节省。

### 浏览器补充与剩余运行门槛

原三张 producer 截图未完整覆盖 20 张展开卡及确认卡，最初五层报告的 NOT_RUN 保留。额外零模型只读回放已经通过：同一新结果 20 项有序卡片、逐条主证据/完整来源、可见第 2 项未知事实、1/3 项确认卡和精确备注/启用按钮，未点击确认；刷新后完整四条清单及原三个 run ID 不变。该回放没有模型或业务写请求，控制台与脚本错误零。独立浏览器逐断言审计已通过：8 项原预锁断言均有实际证据，58 个产物哈希匹配；不是把 combined PASS 直接当作逐项结论。最终 checker 3 条记录、15 层均 PASS，exit 0；原 captures 保持字节不变，browser/performance/semantic 只追加到独立衍生副本，原 NOT_RUN 与失败轮次保留。

三晚真实 Google 出口资格仍未通过。两晚 429 终止及后续零请求拒跑保持，未修改 since、mode、出口、预算或 cron；参考表空态不构成新采集成功。团队协作及外部写入仍只交付下一阶段规格。


- `pick-deploy-guard target=gateway commit=85c7cdb94684174c3c3ed8e80fe4dbea9986d00f prod_head=0007 chain_head=0007 at=2026-10-06T05:31:10Z`

QA-only 后续修复和本节文档不作为实际产品部署 SHA。完成的是上述限定字典/参考表和三步验收；完整目标仍有真实采集门槛未完成。
