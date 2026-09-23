# 个人选剧工作台验收记录

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
