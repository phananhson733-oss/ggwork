# 选剧工作台趋势雷达实现计划（定稿，2026-09-25）

> **2026-09-30：范围已缩减为简化版。** 用户认为本计划相对需求做重了。现行范围见 [简化版范围](2026-09-30-trends-radar-simplified-scope.md)，被搁置的任务与步骤列在第 8.2 节。本计划其余部分保留作记录；已上线的 S0 到 S6 与批次 0 到 2a 的代码照常使用。

**输入**
- 设计：`docs/plans/2026-09-25-trends-radar-design.md`（0ccb470，用户已批准；下文「设计 x.y」指它的小节）。
- 草稿：`scratchpad/plan/impl-plan-draft.md`。两份批判（Claude 对抗式批判、gpt-6-astra 批判）逐条对着设计与仓库核对，处置见第 14 节。
- 四份接入点调研：backend、infra、frontend、realshort。行号按 worktree 0ccb470 核对。
- 任务写法参照 P2 实现说明：每个任务有规格、先写的测试、验收。

**基线**
- 工作台 worktree：`/Users/wzb/Code/ggwork-deerflow-wt/trends`，分支 `feat/trends-radar`，基于 28c1dbe（与 ggwork main 相同），当前 HEAD 0ccb470（设计入库）。
- 生产迁移头是 0006。P2、P3、P4 已上线，P2-8a 已合入，设计里「等 0005/0006」「等 P2-8a」的前置都已满足（设计 3.8、第 8 节分期表、第 9 节 #8 随之作废）。
- RealShort：只读参考检出 `realshort-pick-export-v2`（816ca2e）。导出脚本另开 worktree，基于 `origin/main`（c45c520 或更新）新建分支，不动参考检出。

**用户 09-25 批准**（原话六条）：按建议写；批准阶段 0（小测试）与金丝雀，即对 Google Trends 的低频自动访问；本机网络是自家网络；本机登录的 GSC 账号是属性 Owner；批准在 RealShort 仓库写旧页解析导出脚本；其余按设计第 9 节推荐落地。
- 两条带「应该」的说法按轻量方式核实，不另找用户：Owner 身份由 U2 添加服务账号时直接验证，添加不了就说明不是 Owner，改请实际 Owner 操作；本机出口由 TR-05 在报告里记下运行时是否设了代理环境变量（本地检查，不外发请求）。阶段 0 的结论本来就不当作出口结论（设计 4.11）。

**缩写**：`gp/` = `customizations/pick-workbench/ggwork_pick/`，`t/` = `customizations/pick-workbench/tests/`，`fe/` = `frontend/src/`，`ft/` = `frontend/tests/unit/`，`RS:` = RealShort 仓库，`plan` = `docs/plans/2026-09-23-supabase-pick-board-plan.md`。

**设计里的行号已过时，实现以下表为准**

| 设计写的 | 实际位置 |
|---|---|
| `gp/selection.py:11` RULE_VERSION | `:13`；`RANKING_VERSIONS` 在 `:16`，是允许值集合，不是实现分发表 |
| `:121` 默认按依据日期排序 | `:134-136` |
| `:187` 起，非 rank 排序记成 evidence-date-v1 | `ranking_version_for` 在 `:33-34`，写入在 `:351` |
| `:187-190` 版本校验 | `_parent_versions` 在 `:253-263`，回放标记在 `:180`，回放入口 `replay_view` 在 `:158-183` |
| `:258` `conditions_json=effective.model_dump()` | `:352` |
| `gp/repository.py:22` RETAIN_REFERENCED | `:23`；`prune_shared` 在 `:712-744` |
| `fe/core/pick/api.ts:102-109` listPickResults | `:77-85`，没有组件调用它。真正会整批解析失败的是 `getPickResult`（`:87-93`）和 `listSavedPicks`（`:107-112`，快照 schema 用的是 `pickItemSchema`） |
| `fe/core/pick/api.ts:42` kind 只认两种 | `:43` |
| 设计 3.4「按 plan:920 的规则」 | 规则原文在 plan:922 |
| （设计未写）重钉路径 | `gp/tools.py:53-56` 的 `_pin_latest` 在 `use_latest` 时再次 `repin(current_pin())`；首次钉住在 `gp/context.py` 的 `PickTask.repository` |
| （设计未写）结果视图 | `gp/routes.py:127` 的 `status_view` 返回 `result_view(record)` 加运行状态 |
| （设计未写）回答核对接线 | `gp/middleware.py:103` 的 `_record_checks` 只传 `known_titles`、`posted_checked`；`gp/answer_check.py:40` 两个参数都是必填关键字参数 |
| （设计未写）JSON 列全覆盖测试 | `t/test_pan_runbook_sql.py:444-450`，名单在 `t/pan_runbook.py:34-47` |
| （设计未写）扩展启动失败 | `backend/packages/harness/deerflow/extensions/gateway.py:575-610` 失败放行；`/health/ready`（`backend/app/gateway/app.py:1023-1038`）不看扩展 |

**定稿相对草稿的主要变化**
1. 补齐 7 天准入的取数：A″ 每轮取 `all` 与 `final` 两版（D27）；Vd 按切片版本判有效，Vh 每轮重取（D26）；页面集合 P 只由身份与冻结快照产出，不读明细（D25）。
2. 新增共享数据合同 TR-33、人工决定生效链 TR-35、部署守卫 TR-34、影子全链路验收 TR-36；TR-23 拆成 TR-23a（核对与准入）和 TR-23b（发布、提示、里程碑、端到端）。
3. 集合冻结统一成 `FrozenInputs`（D36）；清理按通道与模式分组并保护当前 live（D30）；联动只在同模式内配对，冻结事实与判定时刻的可行动性分开（D13）。
4. 预算与熔断的「当天」对 Trends 改按 target_date（D23）；canary2 计划约 430 次、上限 600（第 9 节）。
5. RealShort 侧不改线上跳转，导出脚本直接调用线上未改动的解析链（D32）。
6. 阶段 0 四种去向落成可执行分支（第 8 节）；S12 拆成 GSC 与 Trends 两个独立开关。
7. 工作台 42.25 人日，RealShort 2.0，另留验收修复 3–5（第 13 节）。

---

## 1. 目标与范围

**目标**：两条可核验的观测链路。
- Trends：直连采集，得到站外提前发现的线索。
- GSC：站内曝光加码。
- 两条链路共用一套集合冻结、资料页与智能体条件。所有结论都能从冻结的判定行复现，任何地方都不把「未观测到」说成零。

**范围**（对照设计第 8 节，逐条落到任务）

| 设计第 8 节第一版 | 任务 |
|---|---|
| Trends 链：直连客户端（限速、熔断、持久状态、租约代次） | TR-02、TR-03、TR-04、TR-13、TR-14 |
| Trends 链：阶段 0 | TR-05；去向见第 8 节 |
| Trends 链：A/B 清单与歧义 | TR-18、TR-35 |
| Trends 链：rising、emerging、cooling（颗粒度按阶段 0 选定） | TR-17、TR-20 |
| Trends 链：发现段（闸门 B 通过才做） | TR-19 |
| Trends 链：资料页与智能体条件 | TR-24、TR-26、TR-27 |
| GSC 链：新页与旧页归因 | TR-08、TR-22 |
| GSC 链：切片修订语义 | TR-21 |
| GSC 链：覆盖三层 | TR-09、TR-23a |
| GSC 链：描述性标签与正式门槛，7 天 rising | TR-09、TR-23a、TR-23b |
| 共享：0007、角色与授权 | TR-11、TR-12 |
| 共享：集合冻结、`sort=obs` | TR-33、TR-20、TR-23b、TR-26、TR-27 |
| 共享：同国家联动展示 | TR-10、TR-20、TR-24 |
| 共享：提前量里程碑 | TR-10、TR-20、TR-23b |
| 共享：资料页（含「可粘贴」行、告警横幅） | TR-24、TR-25 |
| 上线门槛（阶段 0、金丝雀、影子抽检） | TR-05、TR-30、TR-31、TR-36 |

**不做**（照设计第 8 节，外加三条本计划明确排除的）
- 设计原列：按标题自动归并身份（只出建议）；因果标签；反弹与高位稳定；跨剧排名；池外剧进智能体；SERP、Topic、锚点库；逐剧 YouTube；母本聚合；付费适配器；自动触发站点动作；外发通知。
- 本计划另外排除：
  - `rs_legacy` 新资源：之后单独批准。
  - 官方 Trends API 接入：只提交申请。
  - 后备代理和供应商适配器：只保留 `TrendsSource` 抽象，触发条件出现后再单独批准。

**分期对照**：设计 W0 对应第 11 节「用户亲自做的事」；期 1 对应批次 0 与批次 1；期 2 对应批次 2a 与 2b；期 3 对应批次 3。

---

## 2. 四个前提如何落地（设计第 0 节）

| 前提 | 落到的任务 | 验收（测试名：断言要点） |
|---|---|---|
| 1. 「未观测到」贯穿资料页、智能体与联动，任何地方都不简称为零 | 措辞常量与禁用词：TR-10 的 `gp/observe/wording.py`、TR-16 的 `fe/core/pick/obs-format.ts`。存储上未观测记 NULL 并带行数，不写 0：TR-09、TR-21、TR-23a。资料页：TR-24（含曲线）。智能体证据与提示词：TR-27、TR-28。联动：TR-10 | `test_wording_forbidden_terms`：所有输出模板、证据 label/note、提示词里都没有「0 曝光」「零曝光」「为零」「没有曝光」。`test_unobserved_stored_null`（TR-09、TR-23a）：未观测窗口在判定行里值为 NULL、`row_count=0`；实现里 `sum([])` 得出的 0 不得写入。`obs-format.test.ts`：值为 null 时渲染「在 GSC 返回的数据里未观测到」。`obs-spark.test`（TR-24）：遇到 null 断开曲线，不画成零线。`test_evidence_null_not_zero`（TR-27）：证据 value 为空串加「未观测到」note，不是 `"0"`。`test_from_zero_semantics`：「从零起量」的 note 是「基线未观测到」。`test_link_unobserved_label`：「未观察或低量」一栏的文案不含零 |
| 2. 两份下界一致只是准入规则，不证明完整，也不证明统计独立 | TR-09 的准入函数只返回 `admitted` 与原因，不返回「已核实」。质量注记的检验方法写死在 D38，由 TR-09 实现、TR-23a 写进判定行。TR-24 的文案 | `test_admission_wording`：结果字段与文案只有「两份下界一致（准入）」，没有「完整」「已核实」「独立」。`test_quality_note_values`：固定夹具上的 p 值与 BH 调整值与手算一致（误差 1e-9）。`test_quality_note_not_gate`：p 值与 BH 结果改变时，标签不变 |
| 3. 逐剧核对与实际判定的国家、窗口、页面集合、dataState 对齐，10% 的分母与取值口径写死 | 口径见下方，由 TR-09 的 `coverage.per_identity_consistency`、`pageset.page_set` 实现，TR-23a 构造请求（D25、D26、D27） | 见下方「口径」后的测试清单 |
| 4. Vh、Vd 的覆盖、失败与陈旧都要验收；14 个 PT 日证据不齐，不出 7 天正式标签，也不出对应联动 | TR-23a 记录逐剧核对状态（`ggwp_gsc_vchecks`）；TR-23b 汇总进集合并做端到端；TR-10 的联动只接受正式标签；TR-24 展示计数 | `test_vd_missing_day_blocks_7d`：14 天里缺 1 天，没有 7 天正式标签，联动表里也没有这部剧。`test_vh_failed_descriptive`：过滤请求失败时只出描述性标签。`test_vh_never_reused`：上一轮的 Vh 结果本轮不用。`test_vd_reused_until_slice_version_changes`：14 天的切片生效版本与 dataState 都没变时沿用 Vd；任一天换了版本就重取，重取失败只出描述性标签。`test_set_vcheck_summary`：集合里记下请求数、成功、失败、截断、陈旧、复用、正则溢出各自的计数，资料页照数显示。`test_seven_day_formal_label_from_empty_db`（TR-23b）：空库经真实 runner 与假 transport 跑满冷启动与 16 个 PT 日，A′ 可用时照样产出 7 天正式标签；测试里不预先塞入 A″ 或 Vd |

**前提 3 的口径（写死，改口径要升 `gsc-rules` 版本）**
- **比较对象**：身份 i × 国家 c（或全站）× 窗口 W。
  - 24 小时标签：W0 与 W−1 各比一次。
  - 7 天标签：最近 7 个完整 PT 日与前 7 天，逐个窗口各比一次。
  - 全站 = 所有国家求和，包括 GSC 的未知国家。
- **页面集合 P**（D25）：由身份与本轮 `FrozenInputs` 产出，**不读 C、D、E 的任何行**。
  - 新页：锚定正则 `^https://<主机>/<locale>/drama/[^/?#]+-(<id>|…)$`。id 取 i 的正典 book_id，加上冻结镜像版本 `rs_ids` 里 `canonical_id` 指向它的非正典 id；主机写法与 URL 形态以 TR-07 的实测为准。
  - 旧页：冻结快照里 `target_kind=drama` 且解析到 i 的 URL 原串，逐条精确匹配；已转博客的不算。
  - 明细汇总 X_det 用同一个谓词去筛 C/D/E 的行；Vh、Vd 的 `includingRegex` 由同一份清单按长度上限分块生成。
- **dataState**（D27）
  - 24 小时用 `hourly_all`。
  - 7 天逐日跟明细当天用的一致：当天生效的是 E 切片就用 `final`，是 D 切片就用 `all`。全站层按同一规则选 A″f 或 A″a；Vd 按 dataState 分组发请求。
- **指标**：曝光。标签用到点击时（高点击率），对点击再做同样的比较。
- **两个值**
  - X_det：C（24 小时）或 D/E（7 天）里 page∈P、country=c、时间∈W 的和；没有任何行时是「无行」，存 NULL，不是 0。
  - X_flt：Vh 或 Vd 响应里 country=c、时间∈W 的和；同样区分「无行」。
- **一致**：|X_flt − X_det| ≤ max(0.10 × max(X_flt, X_det), 3)。分母取两者中较大的。整数运算，不取整。一边无行、另一边有值时按 0 参与差值，但判定行里仍记「无行」。
- **取值**
  - 一致时，正式标签取 max(X_flt, X_det)。
  - 「基线未观测到」要求 W−1 两边都**没有行**，而不是值为 0；同时 W0 一致且 ≥20。
- **以下任一情况只出描述性标签**：过滤请求失败；截断；正则分块中任一块失败或溢出；Vh 不是本轮取的；Vd 不满足 D26 的有效条件；窗口或 dataState 与明细不同。
- **测试**（TR-09）
  - `test_consistency_denominator_is_max`：100 对 91 一致；100 对 89 不一致；2 对 5 一致；**X_flt=90、X_det=100 与 X_flt=100、X_det=90 两个方向都判一致**（分母取小或只取 X_flt 时会判不一致）。
  - `test_consistency_same_pageset`：X_det 与过滤正则由同一份 P 产出，测试篡改任一边会变红。
  - `test_pageset_independent_of_detail`：某剧在 C 里零行时，生成的正则仍匹配它的新页与旧页原串；把实现换成「从明细行收集 P」会变红。`[反例 22]`
  - `test_consistency_country_scoped`：USA 一致、GBR 不一致时，只放行 USA。
  - `test_consistency_datastate_mismatch`：final 对 all 时判为不可比。
  - `test_from_zero_requires_no_rows`：W−1 有一行值为 0 时，不算「未观测到」。

---

## 3. 实现决策（调研暴露、设计未写死、或批判补出的地方）

| # | 决定 | 理由 |
|---|---|---|
| D1 | cron 的数据库层用 SQLAlchemy `create_async_engine(poolclass=NullPool)`，PG 走 asyncpg 驱动。加一把 `asyncio.Lock`，保证同一时刻最多一条连接；每一步用完即关。JSON 序列化器用宿主的同一个对象 | asyncpg 只能连 PG，与「SQLite 用 BEGIN IMMEDIATE」矛盾，这样两个要求都满足；conftest 的 SET 拦截与序列化器检查只对 Engine 生效 |
| D2 | 不复用镜像锁，也不复用 `ggwp_sync_runs`。并发只靠 `ggwp_obs_runtime` 的租约行加 `FOR UPDATE` | 持锁方受 0006 的 CHECK 约束；会话级锁会一直占着连接；900 秒过期规则会误判长会话 |
| D3 | pick_observer 分两份交付：`bootstrap-observer.sql` 加 undo 在生产执行一次；同时改全量 bootstrap 供新项目用。必须先于 0007 部署。另备一个可重复执行的 `observe.admin regrant` | `bootstrap.sql:15-16` 的 CREATE ROLE 不能重跑；0007 遇到角色不存在只会跳过授权 |
| D4 | `CONNECTION LIMIT` 按设计取 20（规则在 plan:922） | 与 plan:922 一致，用户已按推荐批准。单连接由 TR-13 的测试钉住 |
| D5 | 镜像内自检比对四样：collector_version 与环境变量 `PICK_OBS_EXPECTED_COLLECTOR`、迁移头、`current_user`、包内容摘要（打印）。迁移头的规则：**生产的头必须是本镜像迁移链里认识的修订，且不早于 0007**；认不出就在任何 HTTP 之前以退出码 2 退出。手册写明：以后任何新迁移上生产，都要同步重部署 gateway 与两个 cron（TR-34 守卫提示） | 非 editable wheel 做不到设计写的「`__file__` 指向快照」；「完全相等」会让另一会话的任何 0008 一上线就让两个 cron 停摆，而「只要认识」既能拦住旧镜像，又不误伤新迁移；cron 停摆时 `run_missed` 横幅会亮 |
| D6 | Railway 配置文件按目录区分：`/deploy/pick-obs/trends/railway.toml`、`/deploy/pick-obs/gsc/railway.toml`。startCommand 写成 `/bin/sh -c "cd /app/backend && exec python -m …"`。Dockerfile 的 CMD 不动 | `deploy/` 下已有上游的 helm，单独子目录不混；自定义文件名未经文档确认；startCommand 按 exec 形式执行，不展开变量；CMD 被 `test_pick_cloud_entrypoint.py:361-366` 钉住 |
| D7 | `gp/__init__.py` 把 context、routes、service 挪进 `install()` 里导入，`extension` 留在顶层 | 这些模块会把 langgraph、fastapi、`load_dotenv` 带进 cron |
| D8 | 观测证据沿用现有 evidence 形状：`kind` 取 obs_trends、obs_gsc、obs_discovery，结构化内容拍平进 value、label、note、grade、source_ref，条目不加新键。结构化数据留在判定行，按 `source_ref=obs:<set_id>:<row_id>` 回查。结果级只加一个可选的 `observations`，内容存在候选集的 `obs_as_of_json` 里（D28） | 设计 7.4 写了「不加 payload」；strict 的 evidence 与 `selectionSchema.snapshot_json` 都不用改，老前端也能解析新条目 |
| D9 | 资料页的 trends、search 两个 tab 走与镜像解耦的早分支（照 imports 的写法），读 `pick_obs` 视图。不进 PORTED_FROM 与 parity。pick-board 下不新增 client 组件；交互组件只放 `fe/components/workspace/pick/obs/`，并在 contracts.test 的登记表里逐个写明 | 观测集合与镜像版本无关，镜像读不了时观测仍要能看 |
| D10 | 状态横幅与联动的可行动性都是「纯函数 + 双实现」：Python（gateway `/sync` 的 `obs` 键、智能体证据）与 TS（资料页）各写一份，共用夹具 `t/fixtures/obs_status_cases.json`、`t/fixtures/obs_link_cases.json` 钉住同一输出。时间一律显式传入 `now`，前端不许调用无参 `Date.now()` | 避免两份实现漂移 |
| D11 | 两个开关。cron 上的 `PICK_OBS_PUBLISH`：等于 `1` 时集合发成 live，否则发成 shadow。gateway 上的 `PICK_OBS_AGENT`：**同时控制工具 schema 与行为**。`gp/tools.py` 在导入时按它选用 `PickConditions` 或带七个观测字段的 `PickConditionsObs`；关着时模型看到的参数与改动前逐字相同，带观测条件的存量卡片换一批时返回「趋势条件尚未开放」。读存量卡片（回放、换一批的父条件）一律用 `PickConditionsObs` 解析 | 设计 7.5「最后改提示词」：字段 description 就是给模型的提示，开关前暴露会让模型反复调用被拒；Railway 改变量即重启，导入时读取与 P4-1 的启动时读取同一做法 |
| D12 | 人工操作（别名确认、对应确认、暂停、人工加入最多 50 条、歧义改判、提示标无关）经 gateway 新路由写入追加式表 `ggwp_obs_decisions`（自增 id）。资料页仍然只读。生效链见 D24 | reader 角色只读；写入要有认证过的 owner，缺失就拒绝 |
| D13 | 联动：**冻结事实**与**可行动性**分开。任一采集服务发布集合时，对「新集合 × 对方通道最新的**同模式**集合」按 link-rules 版本物化事实行进 `ggwp_obs_links`（同国家状态组合、标签、配对内部时效：GSC W0 与 Trends 最新块终点相隔 ≤48 小时、两集合发布时刻差）。可行动性（Trends 集合 ≤26 小时、GSC 集合 ≤6 小时、非陈旧）在**判定时刻**用显式 `now` 计算：智能体取查询时刻并物化进证据，资料页取请求时刻。智能体读钉住那一对的物化行，不自己重算事实 | 发布事务同时写集合与联动行，最新的一对必然已物化；前端不复刻事实计算；停采后已有「双涨」行会在判定时刻被撤下动作；影子期 shadow 对 shadow 配对，联动可以在打开开关前验证 |
| D14 | 旧页快照由 pick_observer 导入和清理：observer 可写 `ggwp_obs_legacy`。导入在本机用 observer 的 DSN 执行 | 导入与「保留到没有集合引用」的清理都是观测侧的事；不在本机放 deerflow_app 的凭据 |
| D15 | 非正典 id 用镜像的 `rs_ids` 解析；`promoters_cnt` 里程碑读 `pick_mirror.series`。授权三处：0007 给 observer `pick_mirror` 的 USAGE 以及 `versions`、`series` 的 SELECT；以后每个新版本在 `mirror/publish.py` 发布时授予该版本 schema 的 USAGE 与 `rs_ids` 的 SELECT（守卫与 reader 相同）；`regrant` 对**所有 status=published 的现存版本**补授同样的权限 | observer 是后建的角色，现存版本不会自动获得授权；这是对镜像发布步骤的一处改动，要与另一会话协调（第 10 节 S0） |
| D16 | observer 对 `ggwp_candidate_sets` 只有列级 SELECT：id、trends_set_id、gsc_set_id、created_at | 清理时判断「是否被引用」只需要这几列 |
| D17 | 金丝雀走 0007 路线，不挂卷 | 见第 9 节 |
| D18 | 查询词写入前先过网盘清洗，命中就丢弃该行并计数；`pan-redact.sql` 对 `ggwp_gsc_query_daily` 用 DELETE，不用 UPDATE。cookie 罐存成 Text 密文，不进视图 | 查询词是自然键的一部分，UPDATE 会撞唯一约束 |
| D19 | 带观测条件且没指定 sort 时，有效排序是 `obs`；指定 `sort=obs` 却没有观测条件，拒绝 | 设计 7.3 |
| D20 | 出口 IP 探测**默认关闭**：回显地址是对第三方的外发请求，要 U13 批准后才配置。开启后在每次会话开头、每次熔断后、每 20 次请求各测一次，请求行记下最近一次的值和测量时刻；关闭时记 NULL | 设计要求逐请求记录出口，但回显服务不能每个请求都打，而且它本身是新的外发 |
| D21 | 任务分支不提交托管副本，跑测试时显式 `--deselect` `test_managed_copy`。批次合并后在集成分支统一执行 `deerflow extensions upgrade` 并提交；完整套件（含 `test_managed_copy`）只在集成分支跑 | 避免各分支的托管副本互相冲突；执行代理不会误以为任务分支必须全绿到托管副本 |
| D22 | 覆盖率用 `uv run --with coverage` 临时测量，不改锁文件；`ggwork_pick.observe` 要求 ≥80% | 仓库的 dev 组里没有 pytest-cov |
| D23 | 预算与熔断计数的「当天」：Trends 按 **target_date**（会话 02:00 UTC 截止的那天），20:30 到次日 01:45 的会话同属一天；「连续 2 天」「7 天内 3 次」都按 target_date 数。GSC 的请求计数按 UTC 日，只作配额记录。`ggwp_obs_budget` 主键 (channel, budget_day) | 设计 3.5 表写的是「UTC 日」，照做会在 00:00 把预算清零、解除熄火，重演设计 4.3 要防的放大模式；这是对设计的细化，不改变它的意图 |
| D24 | 人工决定的生效链：`decisions_state.effective(decisions, upto_id)` 纯函数算出有效状态；采集服务每次发布读 decisions 到当时最大 id，记作 `decisions_version` 冻结进集合。对应确认按 (identity, 平台, 规范化标题) 记，撤销、改标题、改平台、经别名换了身份都失效，要重新确认；失效是永久的，改回不恢复（G3）：Trends 每个会话开头按上一份失效账本（最近一个折叠过的会话，不论是否发布集合）加上它读到的批次之后新发布的各共享批次，算出新的账本存进会话批次，发布时冻结进 `FrozenInputsTrends.lapsed_confirmations`。别名类决定由 gsc 服务在每轮第 0 步写成新的别名版本（D43）。确认在该通道下一个集合生效：Trends 最长约一天，而 confirmed 本来就要等 D+1 的集合 | 设计 7.1「只读判定行」：智能体实时读 decisions 会让旧卡随撤销而变；冻结进集合，旧集合永远不变 |
| D25 | 页面集合 P 只由身份与 `FrozenInputs`（正典 id、冻结镜像版本的 `rs_ids`、冻结旧页快照）产出，不读明细；明细汇总与过滤请求共用这一份 | 从明细收集 P 会让「整部剧漏在明细外」时两份下界都为 0 而判一致，反例 22 假绿 |
| D26 | Vh 每轮重取、从不复用。Vd 结果在满足三条时跨轮沿用：它比较的 14 个 PT 日与本轮相同；这 14 天的 D/E 切片生效版本 id 都没变；每天的 dataState 都没变。V 状态行记下这三样，任一条不满足就对受影响的身份重取 | 设计 5.2 定 Vd「每天一次」，每轮全量重取既违背频率又浪费配额；只按「是不是本轮取的」判断又会让 8 个集合里只有 1 个带 7 天正式标签 |
| D27 | 每轮取两份日级同口径总量：A″a（`[date]`/`all`/`byPage`）与 A″f（`[date]`/`final`/`byPage`），各覆盖最近 16 个 PT 日，写进 `ggwp_gsc_totals`。A 与 A′ 照设计每轮取。7 天全站层逐日按当天明细的 dataState 选 A″f 或 A″a | 设计 5.2 只写了 A″ 的 `all`，5.6 却要求 A″ 与明细同一 dataState，这是设计遗留的缺口；两次日级请求很便宜 |
| D28 | 候选集的 `obs_as_of_json` 结构：`{trends: {set_id, published_at, latest_block_end}, gsc: {set_id, published_at, cutoff}, link_rules_version, judged_at, observations: {...}}`。`result_view` 只在它存在时加 `observations`，取自这里 | 覆盖率与排除计数必须随记录冻结，`test_mirror_frozen` 的 `query == result_view(record)` 才能成立；观测时点绝不写进 `data_as_of_json` |
| D29 | 版本分发表：`selection.py` 的 `RANKERS = {"evidence-date-v1": …, "signal-rank-v1": …, "obs-v1": …}`，`link_rules.py` 的 `LINK_RULES = {"link-rules-v1": …}`。换一批与回放按记录的版本查表执行，查不到就拒绝；`RANKING_VERSIONS` 改为由 `RANKERS` 的键导出 | 现有 `RANKING_VERSIONS` 只是允许值集合，保留旧名字而实际调用最新函数照样能过「版本存在」检查 |
| D30 | 清理按 (channel, mode) 分组：每组保留最新 3 个；每个通道当前生效的 live 集合永远保留；30 天内被候选集引用的保留（stamp 文本比较）。联动行随任一端集合清理。提示与里程碑 180 天，不随集合清理 | GSC 每天 8 个集合，全表取前三会挤掉 Trends；开关关闭后 cron 仍产 shadow，不分组会挤掉当前 live |
| D31 | 观测条件在设计 7.3 的六个字段之外加第七个 `trend_include_presumed`（bool，默认 false），表达「接受强证据的推定对应」 | 设计 4.7 与第 9 节 #4 要求推定对应须显式要求，但六个字段表达不了；复用 `trend_include_first` 会混淆两个独立维度。属设计缺口 |
| D32 | RealShort 导出**不改线上跳转路径**：`legacy-redirect.ts`、`queries.ts`、`page-cache.ts`、`next.config.ts` 都不动。导出脚本在 `react-server` 条件下直接调用线上未改动的 `redirectLegacy`，捕获 Next 抛出的跳转与 404，再用现有的 `getDramaBySlug` 取 book_id。只有探针证明做不到时才走「纯函数 + 薄适配」，那时需 U15 明确批准，并在合并前做全量等价比对与预览部署核对 | 用户批准的是「新写导出脚本」；线上 308 跳转承接着 49% 的 GSC 点击 |
| D33 | 里程碑「进入编辑精选表」的来源：RealShort 同一 PR 里的 `export-editorial-history.ts` 遍历 `src/lib/editorial-picks.ts` 的 git 历史，输出每个 bookId 与 locale 首次加入、移除的提交时刻；工作台 `admin import-editorial` 导入。git 历史不可变，随时可补，不依赖上线当天。U14 不批准时，`eval-rules-v1` 在定稿时明确不含这一事件 | RealShort 的 `src/lib/pick` 里没有精选数据，精选随代码走 |
| D34 | 0007 在两种库上预置 `ggwp_obs_runtime` 的两行（trends、gsc），写法照 0006 的 `INSERT … ON CONFLICT DO NOTHING`。运行时行不存在或读不了，都以退出码 3 退出，零 HTTP | 首次运行不需要额外的初始化步骤；「行被删掉」不会被当成新开始 |
| D35 | 运维命令是包 `gp/observe/admin/`：`__main__.py` 自动发现同包的 `cmd_*.py`，每个模块导出 `NAME` 与 `main(argv)`，各任务只新增自己的模块。手册是目录 `docs/pick-workbench/observe-runbook/`，一个主题一个文件，TR-29 写索引 | 免去多个任务轮流改同一个文件 |
| D36 | `FrozenInputs`：每轮（GSC）或每个会话（Trends）开头读一次并在整轮使用，冻结进集合的 `frozen_inputs_json`。内容：来源共享剧库批次、别名版本、`decisions_version`、市场表版本、规则名与全部参数、link-rules 版本、`collector_version`；Trends 另加颗粒度；GSC 另加旧页快照 `snapshot_id`、镜像版本号（`rs_ids` 所在）、各切片的生效版本 id 与 metadata、A/A′/A″ 总量行 id、逐剧核对汇总 | 两份批判都指出 GSC 冻结清单比设计 7.1 缩小；采集途中镜像或别名更新会让归因、P 与冻结记录来自不同版本 |
| D37 | 提示表加 `mode` 与 `root_identity`（沿冻结别名版本追到最早的身份）。去重键 = root_identity × 通道 × 状态 × 国家或 geo × mode。提前量评估只用 live 提示，从打开发布开关起算；shadow 提示不占 live 的 14 天去重窗口。池外发现按（语种，规范化剧名）键，在它首次唯一匹配进共享池时记 `pool_entry` 里程碑；匹配到多行记 `pool_entry_ambiguous`，单列不进分子 | 别名变化不能打断去重；影子期不能污染评估；池外发现没有池内身份 |
| D38 | 质量注记的检验方法（`gsc-rules-v1` 的参数）：对每个 7 天 rising 评估取 W0、W−1 各 7 个日值。率比 RR = ΣW0 ÷ ΣW−1；ΣW−1 无行或为 0 时不检验，注记「基线未观测，不检验」。离散度 φ = max(1, Σ(x−μ)²/μ ÷ 12)，μ 为所在窗口日均。z = ln RR ÷ √(φ(1/ΣW0 + 1/ΣW−1))，单侧 p = ½·erfc(z/√2)。BH 的族 = 同一 GSC 集合里做了检验的全部 7 天评估，q = 0.10。只用标准库 `math` | 设计 5.8 要求 p 值与 BH 只作注记，草稿没写方法，`test_quality_note_not_gate` 会空绿 |
| D39 | 联动时效与前端展示按颗粒度无关的锚点写：`latest_block_end`（方案 H 是 B6 终点即 `window_end`；方案 D 是最近完整日的日末）；资料页按判定行里的块定义画块，不写死 B1–B6 | 阶段 0 可能选 D，设计 6.1 的「B6」只对小时级成立 |
| D40 | 影子全链路验收：`admin shadow-e2e` 在 gateway 容器里用 `ObsPin(mode="shadow")` 钉最新的一对 shadow 集合，按固定的六组观测条件做一次不落库的查询，输出 JSON，再在本机用前端 strict schema 校验。`mode` 参数只有这条命令能传，工具与路由都传不了 | ObsPin 只读 live；不这样做，打开开关时才第一次走完整链路 |
| D41 | 部署守卫 `scripts/pick-deploy-guard.py`，两个会话部署 gateway、cron、前端前都跑：工作区干净且没有 `.env*`；HEAD 等于刚 fetch 的 `ggwork/main`（守卫默认远端 `ggwork`，其 URL 必须指向共享仓库；本检出的 `origin` 是上游 DeerFlow，不是部署来源）；用 observer 的 DSN 文件读生产迁移头，本地迁移链认不出就拒绝；打印要记进 progress.md 的 SHA 与迁移头；前端模式另用 `git archive` 导出干净目录再部署。守卫只核对来源，不核对合同能力（第 10 节回滚规则） | S0 写的「通知另一会话」只靠人转告；记忆里记过 Vercel CLI 会上传 gitignore 文件 |
| D42 | `check_answer` 的 `trend_checked` 是**必填**关键字参数，没有默认值；唯一调用点 `_record_checks` 传 `task.trend_checked` | 给默认值会让生产调用点漏接线而测试全绿 |
| D43 | 别名表只由 gsc 服务写：每轮第 0 步，按上一版本、当前共享批次与 `decisions_version` 生成新版本（确定性，内容没变就不产生新版本）；trends 只读最新版本 | 两个通道各有租约，双写会竞争；gsc 服务在阶段 0 的任何去向下都上线 |

---

## 4. 设计各节 → 任务对照（无遗漏核对表）

| 设计节 | 覆盖任务 |
|---|---|
| 0 结论、方案骨架、五处收紧、四个前提、交付 | 全计划；前提见第 2 节；交付与工作量见第 13 节 |
| 1.1 两个通道、分工边界 | TR-17、TR-23b（两个通道同期交付）；TR-24（按身份并排展示） |
| 1.2 不经中转；决策 5；`obs_gsc` 与 v1 `gsc` 分开标注；v1 `gsc` 信号是否为空 | 决策 5 已提交；TR-27、TR-28（证据 kind 与提示词）；TR-24（标注）；S4 只读核实 v1 `gsc` 信号计数 |
| 1.3 market-map-v1、geo 选择、语种不推国家 | TR-10（市场表）；TR-18（首轮 geo 与档位）；TR-05（BG、DE、FR、IT 对照）；TR-22（国家只看 page×country） |
| 2.1 编辑精选是争议案例集 | TR-31（逐条记录 v2 结果，允许「待核实」「不判定」） |
| 2.2 可粘贴行 | TR-23b（字段冻结进判定行）；TR-24（展示与复制） |
| 2.3 v9.5 对照 | TR-09、TR-17、TR-27（逐条实现或删除，见各任务） |
| 3.1 cron 服务、打包、自检、延迟导入 | TR-01、TR-13、TR-14、TR-15、TR-21、TR-34 |
| 3.2 批次、集合、预算桶 | TR-11（表与唯一约束）；TR-13（预算，D23）；TR-14、TR-21 |
| 3.3 租约代次与三个边界 | TR-13（唯一写入口）；TR-14、TR-20、TR-21、TR-23b（运行器级接管测试） |
| 3.4 角色、授权、`pick_obs`、连接账 | TR-11（0007 授权段）；TR-12；TR-29（连接账写进手册）；TR-31（实测峰值） |
| 3.5 0007、候选集加列、保留、容量、网盘清洗 | TR-11；TR-20（清理，D30）；TR-23b |
| 3.6 资料页读法 | TR-24 |
| 3.7 告警落点 | TR-25（状态接口、红色横幅）；TR-24（观测横幅）；TR-27（回答带陈旧提示） |
| 3.8 上线依赖与回滚 | 第 10 节；TR-16、TR-27（回滚矩阵）；TR-34 |
| 4.1 客户端、合同核实、条款做法 | TR-02；TR-05（逐项核实）；TR-24（「Data source: Google Trends」） |
| 4.2 限速 | TR-03；TR-14（执行器接线） |
| 4.3 熔断与持久状态 | TR-03、TR-04、TR-13 |
| 4.4 cookie 与出口 | TR-04、TR-02（D20） |
| 4.5 容量、分配、任务清单、截断顺序 | TR-14（框架与截断落表）；TR-18（按表分配） |
| 4.6 观察清单 | TR-18、TR-35 |
| 4.7 请求形态、歧义、身份证据、人工确认 | TR-18；TR-35（确认生效链）；TR-25（确认入口）；TR-27（未确认不进智能体，D31） |
| 4.8 发现段 | TR-19 |
| 4.9 判定规则、颗粒度、确认、优先级、不变性测试 | TR-05（定稿规则全文）；TR-17 |
| 4.10 失败与陈旧 | TR-02（fetch_status）；TR-20（carried_over、stale、80% 覆盖门槛）；TR-27（26 小时）；TR-14（全零率、userType） |
| 4.11 阶段 0、金丝雀、后备、官方 API | TR-05；第 8 节；TR-30；第 9 节；U9；`TrendsSource` 抽象在 TR-02 |
| 5.1 GSC 接入、私钥、配额 | TR-06；TR-07（真实查询验收）；U1、U2 |
| 5.2 请求 A 到 Vd、rowLimit、冷启动 | TR-07（实测）；TR-21（A、A′、A″a、A″f、C、D、E、Q）；TR-23a（Vh、Vd） |
| 5.3 切片、修订、零的含义、可用区间 | TR-21；TR-09 |
| 5.4 窗口与共同截止 | TR-09 |
| 5.5 页面归因、旧页快照 | TR-08、TR-22 |
| 5.6 覆盖三层与两层准入 | TR-09、TR-23a、TR-24 |
| 5.7 国家展示 | TR-10、TR-24 |
| 5.8 gsc-rules-v1 | TR-09（含 D38）、TR-23a |
| 6.1 联动 | TR-10、TR-20（物化）、TR-23b、TR-24、TR-27 |
| 6.2 提前量 | TR-10（指标）；TR-20、TR-23b（提示与里程碑）；TR-08、TR-23b（编辑精选事件，D33）；TR-31（周复盘） |
| 6.3 时间线 | TR-14、TR-15（cron 时刻）；TR-29 |
| 7.1 钉住与冻结 | TR-33、TR-26（D36、D28、D29） |
| 7.2 身份连续性 | TR-18；TR-35；TR-32；U8 |
| 7.3 条件、排序、剧库范围 | TR-27、TR-26 |
| 7.4 结果、提示词、核对 | TR-27、TR-28 |
| 7.5 发布顺序 | 第 10 节 |
| 8 验收、反例表、上线门槛 | 第 12 节逐行指派；TR-30、TR-31、TR-36 |
| 9 拍板 #1–#11 | #1 已提交；#2 TR-10、TR-18；#3 TR-05、第 8 节；#4 TR-27、D31；#5 TR-09；#6 TR-18、TR-35、TR-32；#7 TR-15；#8 作废（P2 已上线）；#9 TR-08、TR-32；#10 TR-02、TR-30；#11 TR-10、TR-31、U10 |
| 10 风险表 | 各行缓解分别在 TR-18、TR-21、TR-22、TR-27、TR-28、TR-26、TR-31、TR-05、TR-03、TR-14、TR-02、TR-15、TR-13、TR-34 |
| 11、12 差异与评审记录 | 仅作背景，无任务 |
| 13 用户批准 | 第 11 节、第 9 节 |

---

## 5. 代码布局

**工作台：`gp/observe/`**（新建。gateway 只按需延迟导入 `read.py`、`decisions.py`、`status.py`、`decisions_state.py`、`link_rules.py`、`eligibility.py`）

| 文件 | 职责 | 任务 |
|---|---|---|
| `__init__.py` | 包说明，不导入任何子模块 | TR-01 |
| `versions.py` | `COLLECTOR_VERSION`、各规则版本常量、最低迁移头 `"0007"` | TR-01 |
| `clock.py` | 可注入的时钟与随机源；分块 sleep（每块 ≤60 秒，醒来时续租） | TR-01 |
| `errors.py` | 错误脱敏（只留类名与 SQLSTATE）；退出码 0、1、2、3、130 | TR-01 |
| `contract.py` | 共享数据合同：条件字段与枚举、`observations`、`FrozenInputs`、证据拍平规则、决定种类、状态码、V 状态与总量行形状（只有数据类与常量，没有逻辑） | TR-33 |
| `crypto.py` | Fernet/MultiFernet 封装；密钥只从环境变量读，逗号分隔以便轮换 | TR-04 |
| `state.py` | 不可变的 `RuntimeState`、`StateStore` 协议、`FileStateStore` | TR-04 |
| `db.py` | NullPool 引擎；方言事务骨架（PG 用 `FOR UPDATE`，SQLite 用 `BEGIN IMMEDIATE`） | TR-13 |
| `lease.py` | 租约的获取、续租、接管；`LeasedWriter` 是采集侧唯一的写入口；`DbStateStore` | TR-13 |
| `selfcheck.py` | 启动自检（D5） | TR-13 |
| `frozen.py` | `read_frozen_inputs()`：每轮或每会话开头读一次（D36） | TR-21 |
| `wording.py` | 「未观测到」「两份下界一致（准入）」等措辞常量与禁用词 | TR-10 |
| `market_map.py` | market-map-v1：展示分组与 geo、alpha-3 的对应 | TR-10 |
| `link_rules.py` | `LINK_RULES` 分发表；`link_facts()` 与 `link_actionable()`（D13、D39） | TR-10 |
| `eligibility.py` | 智能体资格真值表（D31） | TR-10 |
| `status_rules.py` | 状态码到横幅级别的分类，含按时间判陈旧 | TR-10 |
| `leadtime.py` | eval-rules-v1 事件清单与四个提前量指标（D37） | TR-10 |
| `decisions_state.py`、`catalog_history.py` | 人工决定 → 有效状态（D24）；对应确认失效的原因，只读地读共享剧库批次（G3） | TR-35 |
| `store.py` | 观测表通用读写：集合发布、判定行、联动物化、提示、里程碑、发现、清理（D30） | TR-20 |
| `read.py` | gateway 侧：按集合与身份读判定行与联动行，跨批次映射 | TR-26 |
| `decisions.py` | gateway 侧：追加写人工决定 | TR-25 |
| `status.py` | gateway `/sync` 的 `obs` 键 | TR-25 |
| `admin/__main__.py` | 子命令发现（D35） | TR-01 |
| `admin/cmd_*.py` | `regrant`（TR-12）、`reset_disable`（TR-13）、`identity_churn`（TR-18）、`import_legacy`（TR-22）、`import_editorial`（TR-23b）、`canary_report`（TR-30）、`shadow_sample`、`leadtime_report`（TR-31）、`shadow_e2e`（TR-36） | 各任务 |
| `trends/__main__.py` | cron 入口：`run`、`status`、`--selfcheck-only` | TR-14 |
| `trends/run.py` | 会话执行器：批次、`window_end`、续跑、硬截止；发布接线由 TR-20 加 | TR-14、TR-20 |
| `trends/units.py` | 查询单元、任务清单展开、截断顺序；`WatchTaskSource`（TR-18）、种子单元（TR-19） | TR-14 |
| `trends/lapses.py` | 对应确认的失效账本：会话开头折叠、随计划存进批次行、逐会话接力（D24，G3 复审） | TR-35（G3），TR-20 接线 |
| `trends/canary.py`、`canary_controls.json` | 金丝雀任务来源；对照清单（身份键与 geo） | TR-14、TR-05 |
| `trends/source.py`、`client.py`、`parse.py`、`cookies.py`、`egress.py` | `TrendsSource` 协议、httpx 直连、解析与 10 种 fetch_status、cookie 罐、出口探测 | TR-02、TR-04 |
| `trends/pacing.py`、`breaker.py`、`budget.py` | 令牌桶与分段、熔断、按 target_date 的预算与模式上限 | TR-03 |
| `trends/stage0.py`、`stage0_report.py` | 阶段 0 执行与报告 | TR-05 |
| `trends/rules.py` | trend-rules-v1 | TR-17 |
| `trends/watch.py`、`ambiguity.py`、`alias.py` | watch-rules-v1 与 A/B 档、三道歧义与身份证据、别名（追加式版本） | TR-18 |
| `trends/discovery.py` | 发现段 | TR-19 |
| `trends/publish.py` | Trends 集合发布、carried_over、覆盖率门槛、一致性复取 | TR-20 |
| `gsc/__main__.py`、`run.py`、`plan.py`、`slices.py` | cron 入口、轮次执行器、请求计划（含 A″a、A″f）与冷启动、切片版本 | TR-21 |
| `gsc/auth.py`、`client.py` | 服务账号 JWT、`searchanalytics.query` 与 metadata | TR-06 |
| `gsc/probe.py`、`export_urls.py` | 实测与 90 天 URL 清单 | TR-07 |
| `gsc/cutoff.py`、`coverage.py`、`pageset.py`、`quality.py`、`rules.py` | 可用区间与共同截止、覆盖与一致性、页面集合 P、质量注记、gsc-rules-v1 | TR-09 |
| `gsc/attribution.py`、`legacy.py` | 归因、旧页快照导入与校验 | TR-22 |
| `gsc/verify.py`、`admission.py` | Vh、Vd 请求构造与状态；两层准入落地 | TR-23a |
| `gsc/publish.py`、`milestones.py`、`editorial.py` | GSC 集合发布、提示、里程碑、编辑精选历史 | TR-23b |

- **测试**：放在 `t/observe/`，夹具放在 `t/fixtures/{trends,gsc,obs_contract}/`。夹具不得用 `.env*`、`*.key`、`*.pem`、`credentials.json` 这类文件名；测试用的私钥在测试里现场生成。
- **改动的既有文件**：`gp/` 下的 `__init__`（TR-01）、`contracts`、`tools`（TR-27）、`selection`（TR-26、TR-27 先后）、`pin`、`context`、`repository`（TR-26）、`middleware`、`answer_check`（TR-28）、`models`（TR-11）、`routes`（TR-25），以及 `mirror/publish.py`（TR-12，D15）；新增 `migrations/versions/0007_observe.py`；`pyproject.toml`；`skills/public/pick-drama/SKILL.md`。
- **部署与文档**：`deploy/pick-obs/{trends,gsc}/railway.toml`；`scripts/pick-deploy-guard.py`、`scripts/pick-validate-result.ts`；`docs/pick-workbench/supabase/` 下的 `bootstrap-observer.sql` 与 `-undo.sql`，以及 `bootstrap.sql`、`bootstrap-undo.sql`（TR-12）、`pan-check.sql`、`pan-redact.sql`（TR-11）；`docs/pick-workbench/observe-contract.md`（TR-33）；`docs/pick-workbench/observe-runbook/` 目录；改 `supabase.md`、`progress.md`；CI 工作流的触发路径加 `deploy/pick-obs/**`。
- **前端**
  - 修改：`fe/core/pick/{types,format}.ts`、`views/replay-rules.ts`、`fe/server/pick-board/{db,errors,index}.ts`、`fe/core/pick-board/request.ts`、`toolbar.tsx`（只加 tab 条目）、`page.tsx`（只加一个分支）；TR-25 另改 `fe/core/pick/sync-schema.ts`、`fe/server/pick-board/gateway.ts`。
  - 新建：`fe/core/pick/obs-format.ts`、`fe/core/pick/obs-link.ts`（D10 的 TS 实现）、`fe/server/pick-board/queries-obs.ts`、`fe/app/workspace/pick-data/obs-route.tsx`、`views/{trends-view,search-view,obs-detail-view,obs-spark,paste-row}.tsx`、`views/obs-banner-rules.ts`、`fe/components/workspace/pick/obs/*.tsx`（TR-25 建，D9 登记）。
- **RealShort**（新 worktree，D32）
  - 新建：`scripts/export-legacy-resolution.ts`、`scripts/verify-legacy-live.ts`、`scripts/export-editorial-history.ts`、`tests/{legacy-export,next-matcher-contract,editorial-history}.test.ts`；`package.json` 加 `legacy-export`、`editorial-history` 两个脚本。
  - 不改：`src/lib/legacy-redirect.ts`、`src/lib/queries.ts`、`src/lib/page-cache.ts`、`next.config.ts`。

---

## 6. 批次、依赖与并行规则

```
批次0     TR-01 骨架 ∥ TR-33 共享数据合同
批次1a    TR-02 TR-03 TR-04 TR-06 TR-09 TR-10 TR-11 ；(RS)TR-08 代码与探针
批次1b    TR-07(06,U1,U2) ─> TR-05(02,03,04,07；本机跑 2 天) ；TR-12(11) ；TR-16(33)
批次2a    TR-13(04,11) ─> TR-14(02,03,13,05 的对照清单) ─> TR-15(14) ；TR-34(11) ；TR-35(11,33)
          └─> G3 ─> 上线步骤 S0–S7 ─> 金丝雀 TR-30（10 个日历日，与下面并行）
批次2b-i  TR-17(G2) TR-18(11,14,33,35) TR-21(06,07,13,33) TR-22(09,12,13；验收用 TR-08 快照) TR-25(10,11,33,35)
批次2b-ii TR-20(10,13,17,18,21,35) TR-23a(09,21,22) TR-24(10,11,16,25)
批次2b-iii TR-19(闸门 B,18,20) TR-23b(20,23a)
批次3     TR-26(11,20,23b) ─> TR-27(26,S1) ─> TR-28(27) ；TR-36(27) ；TR-31 ；TR-29 跨批 ；TR-32 可选
```

- TR-20 依赖 TR-21 只是为了复用 `frozen.py`；TR-19 与 TR-23b 都只调用 TR-20 的 `store.py`，不改它。
- 同一小批次内没有依赖边；有依赖的一律分到前后两个小批次。

**并行规则**
- 每个任务开自己的 git worktree，分支 `feat/trends-radar-tr-XX`，从 `feat/trends-radar` 切出，完成后合回。
- 同一批内的任务不改同一文件。跨批的先后写明，例如 TR-26 先于 TR-27，两者都改 `selection.py`；TR-14 建 `trends/run.py`，TR-20 在后一批加发布接线。
- 测试清单类文件由单一任务拥有：
  - `t/test_pg_migrations.py`、`t/test_mirror_migrations.py`、`t/pan_runbook.py`、`t/test_pan_runbook_{sql,text}.py` 归 TR-11（0007 新增的 JSON 列会让 `test_the_scripts_cover_every_json_column_of_the_workbench` 变红，所以分类必须与 0007 同一任务）；
  - `t/test_bootstrap_sql.py` 归 TR-12；
  - `t/test_frontend_contract.py` 归 TR-27；
  - 前端 `contracts.test` 的 client 组件登记表归 TR-25。
- 运维命令与手册按 D35 各写各的文件，不共享编辑。
- 前端与另一会话在同一仓库并行。前端任务（TR-16、TR-24、TR-25）开工前和合并前各 rebase 一次最新 ggwork main；`toolbar.tsx`、`page.tsx` 只加最少行数。
- 执行 upgrade 时，`DEER_FLOW_CONFIG_PATH` 指向 scratchpad 里一份 `config.pick.example.yaml` 的副本，并设 `UV_EXTRAS=postgres`；绝不指向主目录的 config（D21）。
- 改合同（TR-33 的模型与夹具）只能在 TR-33 或经 G 节点批准的后续 PR 里做，两端测试同时改。

---

## 7. 任务清单

写法：**文件**是要改或新增的；**测试**是先写、先看它失败的用例（格式为「用例名：断言要点」）；标 `[反例 n]` 的对应设计第 8 节反例表第 n 行（编号见第 12.2 节）。所有数据库测试都在 SQLite 与 PG 两种库上跑。每个任务的 PR 都附「先红后绿」的日志；标了反例的测试还要附「换成错误实现后变红」的日志。

### TR-01 骨架、依赖、延迟导入与运维命令框架（批次 0，0.5 人日，无依赖）
- **目标**：给所有任务一个共同起点；cron 能以轻量导入启动；运维命令与手册的布局定下来（D35）。
- **文件**：`gp/__init__.py`；`gp/observe/{__init__,versions,clock,errors}.py`；`gp/observe/admin/{__init__,__main__}.py`；`pyproject.toml`；`backend/uv.lock`；`docs/pick-workbench/observe-runbook/README.md`（只放目录）。
  - pyproject：显式加 `pyjwt[crypto]` 与 `cryptography`，版本号改为 0.3.0。
- **测试**
  - `test_observe_import_is_light`：在子进程里 `import ggwork_pick.observe.versions`，`sys.modules` 里没有 `deerflow.runtime`、`deerflow.config.app_config`、`fastapi`、`alembic`。`test_host_serializer_import_is_light`：子进程导入 `deerflow.persistence.engine` 也不带出这些模块；不满足时改走 D1 的注入方式，并在本任务里写明。
  - `test_install_api_version`：`install.__deerflow_api__ == "0.2.1"`，install 之后路由照常注册。`test_exit_codes`：五个退出码常量。
  - `test_admin_discovers_commands`：临时包里放两个 `cmd_*.py`，都能被发现并按 `NAME` 调用。`test_admin_unknown_command_exit_2`。
- **验收**：两种库全套件绿（任务分支 deselect `test_managed_copy`，D21）；`uv lock --check` 通过；集成分支刷新托管副本后完整套件绿；按 local-run.md 本机启动 gateway 冒烟通过。

### TR-33 共享数据合同（批次 0，0.75 人日，与 TR-01 并行）
- **目标**：前后端、两个采集服务、资料页在写代码之前共用一份 wire 与数据库合同，避免 TR-16 先发却拿着手写形状。
- **文件**：`gp/observe/contract.py`；`docs/pick-workbench/observe-contract.md`；`t/fixtures/obs_contract/*.json`；`t/observe/test_contract.py`。
- **规格**（每项都有正例与反例夹具）
  - 条件：七个观测字段（D31）的名字、类型、枚举。`trend_state`：rising/emerging；`trend_geos`：WW 或 alpha-2；`trend_include_first`、`trend_include_presumed`：bool；`gsc_state`：rising/surge/from_zero/high_ctr/rank_push/present；`gsc_countries`：alpha-3 或 ALL；`link_state`：both_rising/trends_lead_page/trends_lead_distribution/site_only/cooling。`unmappable_conditions` 里观测字段的固定顺序。
  - `observations`（strict）：两个集合 id 与截至时刻、link-rules 版本、身份覆盖率（池内身份数、Trends 有观测数、GSC 有观测数）、按排除原因的计数。
  - 证据拍平规则：三种 kind 的 value、label、note、grade、source_ref 形状；「未观测到」的表示（前提 1）。
  - `obs_as_of_json`（D28）、`frozen_inputs_json`（D36）、V 状态行、总量行、联动事实行、提示行的字段。
  - `pick_obs` 八个视图的列名与类型（含 `mode`）。
  - 决定路由的请求体（九种 kind）；`/sync` 的 `obs` 键；状态码清单（TR-10 的清单加 `legacy_snapshot_missing`）。
  - 资格真值表夹具 `obs_eligibility_cases.json` 与联动夹具 `obs_link_cases.json`（输入 → 输出，TR-10 与 TR-24 共用）。
- **测试**：`test_contract_fixtures_valid`（每个正例能解析，反例被拒）；`test_contract_doc_lists_every_field`（文档字段表与模型字段逐一相等，照 `test_pan_runbook_text` 的写法）。
- **验收**：G1 与本计划一起审过；之后改合同要走第 6 节的规则。

### TR-02 Trends 客户端与解析器（批次 1a，1.25 人日，依赖 TR-01）
- **目标**：实现 `TrendsSource` 的直连版本（设计 4.1、4.4），失败从不产生数值。
- **文件**：`trends/{source,client,parse,egress}.py`；`t/observe/test_trends_client.py`、`test_trends_parse.py`；`t/fixtures/trends/*.json`（先用构造的夹具，阶段 0 之后换成脱敏的真实响应）。
- **测试**
  - `test_strip_xssi_prefix`：`)]}'` 与 `)]}',` 都能剥掉。`test_params_fixed`：带 `tz=0`、`hl=en-US`。`test_timeline_keeps_partial_and_usertype`：两者原样保留。
  - `test_fetch_status_ten_kinds`：ok、ok_zero、no_data、rate_limited、blocked_redirect、html_body、forbidden、server_error、timeout、parse_error 各一个夹具。
  - `test_failure_has_no_values`：所有失败状态的 values 都是 None，不是全零。`[反例 1]`
  - `test_sorry_redirect_detected_without_follow`：`follow_redirects=False`，Location 指向 sorry 或 consent 主机时判 blocked_redirect。`test_variant_lines_order`：多条线按请求顺序返回，裸剧名那条可单独取出。
  - `test_no_cookie_in_logs_or_repr`：caplog、异常文本、repr 里都没有 cookie 值。`test_egress_recorded_per_request`：开启探测时每个结果都带最近一次出口 IP 与测量时刻。`test_egress_disabled_records_null`：未配置回显地址时记 NULL，零外发（D20）。
- **实现要点**：每条 cookie 罐每天最多预热一次；被限流时保留原罐。响应体设大小上限，超时 `Timeout(30, connect=10)`，transport 可注入（照 `mirror/client.py`）。
- **验收**：MockTransport 下全绿；不新增依赖。

### TR-03 限速器、熔断与预算状态机（批次 1a，1.0 人日，依赖 TR-01）
- **目标**：实现设计 4.2、4.3 的全部参数，外加 D23。纯状态机，时钟与随机源注入，状态能序列化成 dict。
- **文件**：`trends/{pacing,breaker,budget}.py`；`t/observe/test_trends_pacing.py`、`test_trends_breaker.py`。
- **测试**
  - `test_pacing_envelope_3h`：模拟跑 3 小时，任一分钟 ≤12 次；任意 60 分钟 ≤200 次；每连续 40 分钟后空闲 10 分钟；平均每分钟 2.9 次 ±0.3；单元内间隔 1.5–3 秒；单元间隔 25–35 秒。
  - `test_noop_pacer_fails_envelope`：把 pacer 换成空操作跑同一驱动，包络断言必须失败。`[反例 2]`（执行器层的接线测试见 TR-14）
  - `test_429_zero_requests_30min`：之后 30 分钟内零请求，30 分钟时发一次探针。`test_probe_ladder`：探针失败时暂停依次升到 60、120、240 分钟，成功后半速继续。
  - `test_extinguish_conditions`：探针失败 3 次、当天熔断 3 次、累计 429 满 5 次、出现一次验证码或同意墙，任一条都让当天熄火；sorry 页当天熄火。
  - `test_cross_midnight_same_budget_day`：22:30 熄火后，00:10 的触发仍按同一 target_date 视为已熄火，预算不清零。`test_cross_day`：连续 2 个 target_date 熄火，下一个上限减半；7 个 target_date 内熄火 3 次，进入 disabled 并出告警码。
  - `test_retry_policy`：429 不重试；5xx 或超时重试 1 次，间隔 30–60 秒；连续两次按限流处理。
  - `test_budget_reserved_before_send`：发出前扣减，超时和结果未知的不退回。`test_remaining_marked_skipped_breaker`：熔断后剩余单元记 `skipped_breaker`。
  - `test_mode_caps`：canary1 计划 220、上限 220；canary2 计划约 430、上限 600；stable 计划 ≤650、封顶 800。`test_mode_plan_fits_window`：计划量 ÷ 2.9 + 40 分钟熔断余量必须不超过「截止 − 起跑」，否则拒绝这组配置（stable 放到 800 时必须同时提前起跑）。**G3 改**：参数换成第 9 节的新表；窗口检查换成 `capacity.py` 的三种夜晚（`test_trends_capacity.py`），`budget.ModeLimits` 只校验形状（`test_mode_limits_leave_the_fit_to_capacity`）。
- **验收**：只用纯函数和假时钟，不碰网络。

### TR-04 持久状态、文件型状态与 cookie 加密（批次 1a，0.5 人日，依赖 TR-01）
- **目标**：状态读不到当天就不跑；阶段 0 在本机有可靠的持久状态（设计 4.3、4.4）。
- **文件**：`gp/observe/{crypto,state}.py`、`trends/cookies.py`；`t/observe/test_state_file.py`。
- **测试**
  - `test_missing_file_requires_init`：文件不存在且没带 `--init-state`，抛 `StateUnavailable`。`test_corrupt_or_wrong_key_unavailable`。`test_mode_600_enforced`：权限宽于 600 时拒绝读取。
  - `test_atomic_write`：写到一半中断，旧文件仍完整。`test_restart_keeps_pause`：保存 `paused_until` 后重新加载，暂停仍然有效。`test_key_rotation`：旧密钥能读，新密钥写。
  - `test_ua_bound_to_jar`：UA 与 cookie 罐绑在一起。`test_no_secret_in_logs`：日志里没有密钥和 cookie。
- **实现要点**：默认路径 `~/.ggwork-obs/trends-state.json`，目录 700。密钥来自 `PICK_OBS_STATE_KEY`，或权限为 600 的 `PICK_OBS_STATE_KEY_FILE`。
- **验收**：本机 `init-state` 之后连续两次运行，状态都能读回；删掉密钥后拒绝运行，而且零 HTTP。

### TR-05 阶段 0 执行与报告（批次 1b，1.5 人日，依赖 TR-02、TR-03、TR-04；输入依赖 TR-07 的正对照清单或已有的 GSC 手工导出）
- **目标**：跑完设计 4.11 的两道闸门，定稿颗粒度与 trend-rules-v1 全文，并按第 8 节选定去向。
- **文件**：`trends/{stage0,stage0_report}.py`、`trends/canary_controls.json`；`t/observe/test_stage0.py`。
  - 原始响应存 `~/.gstack/projects/ggwork-deerflow/artifacts/trends-stage0/`，不进仓库。报告写到同目录，文件名 `trends-stage0-report-<date>.md`。
  - `canary_controls.json` 只存身份键与 geo，剧名在运行时从当前共享批次取；它是 TR-14 金丝雀的输入。
- **规格**
  - 输入是对照清单 JSON，分四组：正对照 15–20 部 GSC 里「精确剧名查询有展示」的剧，按真实所在国设 geo；负对照是泛词剧、已下架剧；地区对照 BG、DE、FR、IT 各约 4 部；用户在自己浏览器导出的 3 个词的 CSV（U5）。
  - 每部剧取小时级 `now 7-d` 与日级 `today 1-m`。约 10 个标题加上种子取 `relatedsearches`（闸门 B）。
  - 接口核实：169 个小时点、`isPartial` 的位置、`userType` 的取值、空响应的形状、同参数隔几分钟重复请求的差异。
  - 总量 120–180 次，分 2 天跑；全程照 TR-03 限速，任何限流信号都照 TR-03 停。报告记下运行时是否设了代理环境变量。
- **测试**
  - `test_visibility_metrics`：「非零小时 ≥12/144」「非零日 ≥N/30」在夹具上算对。`test_gate_verdicts`：正对照可见率 ≥50% 且每天非泛词判定 ≥5 条，才判闸门 A 通过。
  - `test_four_routes`：A、B 的四种组合各自映射到第 8 节的去向，输出里带该去向的任务增删清单与开关设定。`test_manual_shape_compare`：手工导出与抓取序列的形状比较（秩相关）有确定输出。`test_budget_respected`：两天的计划总量 ≤180。
- **交付物**
  - 报告：两道闸门的判定与去向；选 H、D 还是 H+D；N 的取值；3 小时滞后值（按 `isPartial` 的位置回标）；trend-rules-v1 全文；选 D 或 H+D 时的「数据合同变更单」（块定义、`latest_block_end` 的算法、前端展示字段，D39）；占位阈值。
  - 脱敏后的真实响应夹具，换掉 TR-02 的构造夹具。
- **验收**：本机跑完两天；报告经 G2 审过；用户确认颗粒度（U5）。**2026-09-29 G2 已定**：去向 `b_only`、颗粒度 `H`，trend-rules-v1 全文随 TR-17 取消不再写；U5 的 CSV 形状比对豁免到 S12b 之前（第 8.1 节）。

### TR-06 GSC 客户端（批次 1a，0.75 人日，依赖 TR-01）
- **目标**：实现设计 5.1。签名、私钥归一化与 403 文案照抄 `RS:src/lib/gsc-api.ts:45-130` 与 `gsc-encoding.ts`；入库、窗口与合并逻辑不照抄。
- **文件**：`gsc/{auth,client}.py`；`t/observe/test_gsc_client.py`。
- **测试**
  - `test_private_key_normalize`：去掉两端引号，字面 `\n` 换成真换行，只接受 PKCS#8。`test_jwt_claims`：scope 是 `webmasters.readonly`，RS256，用现场生成的密钥验签。
  - `test_query_body`：rowLimit 25000，dataState、aggregationType、dimensionFilterGroups 原样透传。`test_truncated_flag`：行数等于 rowLimit 时标 truncated。
  - `test_metadata_parsed`：解析出 `first_incomplete_hour` 与 `first_incomplete_date`。`test_quota_error_classified`：短期配额、长期配额、403 分别归类。`test_no_key_in_logs`：日志里没有私钥。
- **实现要点**：凭据读环境变量 `PICK_GSC_SA_EMAIL`、`PICK_GSC_SA_PRIVATE_KEY`；本机也可读权限为 600 的 `PICK_GSC_SA_FILE`。属性来自 `PICK_GSC_SITE_URL`。
- **验收**：MockTransport 下全绿；TR-07 的 P1 真实调用成功。

### TR-07 GSC 实测与 URL 清单导出（批次 1b，0.5 人日，依赖 TR-06、U1、U2）
- **目标**：拿真实响应核实设计 5.2 里的「待实测」项与 D25、D27 用到的形态，并给 RealShort 导出准备输入。
- **文件**：`gsc/{probe,export_urls}.py`；`t/observe/test_gsc_probe.py`（只测解析与汇总）。
- **实测**
  - P1：Restricted 权限下一次真实 `searchanalytics.query`，作为设计 5.1 的接入验收，也顺带证明 U2 由 Owner 完成。
  - P2：小时数据加 `byPage` 聚合（A′）是否支持。
  - P3：`[hour,page,country]` 的行数、截断、配额消耗、按国家拆分重发的效果。
  - P4：`includingRegex` 的语法（RE2、必须锚定）与长度上限；`[hour,country]` 与 `[date,country]` 加过滤能否用（Vh、Vd 两种形态）。
  - P5：A 与 C 在不同 PT 日上的 metadata 水位与滞后。
  - P6：A″ 用 `final` 覆盖 16 个 PT 日时，哪些日子有行、哪些没有；与 `all` 的差异。
  - P7：页面 URL 的主机与路径写法（决定 D25 正则的主机部分）。
- **结论**写进 `artifacts/gsc-probe-<date>.md`，同时回填 TR-21、TR-23a 的参数。
- **导出**：近 90 天 page 维度的 JSONL，每行 `{raw_url, clicks, impressions}`；原串不解码，只对完全相同的串去重；按月份和国家拆分，保证每份响应都不满额；附窗口、dataState、输入 sha256。同时列出 Q 维度里精确剧名查询有展示的剧，作为阶段 0 的正对照候选。
- **验收**：七项都有明确结论，写明「支持」「不支持」或「退路」。

### TR-08 RealShort 旧页解析导出与编辑精选历史（RealShort 仓库，批次 1a 起，2.0 人日，代码依赖无，导出运行依赖 TR-07 的 URL 清单与 U7；设计 5.5、D32、D33）
- **目标**：用线上**未改动**的解析链算出不可变快照；顺带导出编辑精选的历史（U14 批准时）。
- **文件**：见第 5 节 RealShort 部分。线上运行代码一行不改。
- **规格**
  - 先做 0.5 人日的探针：在 `react-server` 条件下能否直接导入 `redirectLegacy` 与 `page-cache` 的读函数并连只读库运行。能，就走零改动方案；不能，停下来走 U15。
  - 零改动方案：
    - `normalizeLegacyUrl(rawUrl, siteHost)`：读取真实的 `nextConfig.redirects()` 与 `rewrites()`，用 Next 服务端自己的匹配器执行（`getPathMatch`，加 `matchHas` 与 `prepareDestination`；设 `sensitive:false`，合并 has 捕获到的参数），再模拟 proxy 的小写化，最多 5 跳。这是脚本里的新代码，不替换线上任何一处。
    - 对归一后的路由参数调用线上的 `redirectLegacy`，`toPath` 与路由文件里的一致；捕获 Next 的跳转错误取出目标 URL，捕获 404 记 `unresolved`。
    - 目标是剧目页时，用现有的 `getDramaBySlug` 得到组正典 book_id；目标是博客时记博客路径。
  - manifest：`snapshot_id`、`realshort_commit`、`resolver_fingerprint`（`legacy-redirect.ts`、`legacy-url.ts`、`next.config.ts` 三个文件内容的 sha256）、`as_of`、剧库指纹、`next_version`、输入 sha256。逐行：`raw_url`、`target_kind`、`reason`、`locale`、`book_id`、`first_hop_book_id`、`via`、`landing_path`、`blog_path`、`hops[]`。
  - 拒绝导出：工作区不干净；或 `git diff <生产部署的提交> HEAD -- src next.config.ts` 非空（运行代码必须与线上部署完全相同）。
  - `export-editorial-history.ts`（D33）：在 origin/main 上遍历 `src/lib/editorial-picks.ts` 的 git 历史，输出每个 (bookId, locale) 的首次加入与移除提交时刻和 SHA；不联网，结果确定。
- **测试**
  - `legacy-export.test.ts` 用夹具覆盖：`/detail/38000`、`/detail/49020`（应到博客）、`/bg/detail/38000?id=38000`、`/bg/video-play/38000/<西里尔>/episode-2`、`/video-play/38000?episodesNum=3`、`/?id=38000`、`/for-you?id=72142`、`/zh-TW/…`、`/ZH-tw/…`、`/BG/…`、`/blog/mighty-and-great-genie`、`/bg/detail/1/-`、`/xx/detail/1/x`、www 与外站 host。
  - `test_two_query_ids_distinct`：`/en?id=38000` 与 `/en?id=49020` 是两条记录。`[反例 24]` `next-matcher-contract.test.ts`：钉死 Next 版本与匹配器行为。
  - `test_refuses_when_runtime_differs_from_deployed`；`editorial-history.test.ts`：用临时 git 仓库构造加入、移除、再加入三次提交，输出与预期一致。
- **差分核对**（2026-09-25 G2 评审后改）：快照的口径是「按当前数据库、经线上未改动的解析链算出的落点」，不声称等于生产在同一时刻的行为（生产有 24 小时与 6 小时的数据缓存，过期后先回旧值）。`verify-legacy-live` 对生产站 `fetch(url,{redirect:"manual"})` 逐跳跟随，每秒至多约 1 次，只请求页面路由白名单（`/api/`、`/admin`、`/_next/` 一律不碰），判定分一致、不一致、无法验证三态（5xx、429、没走完的跳转、预期状态未知都记无法验证，0 条不判通过）。发布闸门：工作台按原串查表的旧页形态行（与 `--legacy-only` 同一判定）必须全部核对、全部一致；其余行抽查点击最高 200 条加随机 100 条，结果只报告不挡发布。核对可断点续跑。剧库指纹含章节（每剧行数与 serial 范围），全部解析查询在同一个 REPEATABLE READ 只读事务里读；导出有总时长上限。
- **若探针失败（需 U15）**：改成「纯函数 + 薄适配」之前，先用新旧两份实现对 90 天全量 URL 逐条比对，100% 相同；再在 Vercel 预览部署上跑 `verify-legacy-live`；两项都过才合并；上线后对生产再跑一次。工作量另加约 1 人日，单独报。
- **顺序**：PR 只含脚本与测试，合并后在与生产部署运行代码相同的提交上导出，差分核对通过，再交给 TR-22 与 TR-23b 导入。
- **验收**：`pnpm test` 与带 react-server 条件的脚本测试都绿；差分 100% 一致；线上运行代码零改动（PR 的 diff 只有 `scripts/`、`tests/`、`package.json`）。

### TR-09 GSC 规则、可用区间、共同截止、页面集合与覆盖准入（纯函数，批次 1a，1.5 人日，依赖 TR-01、TR-33）
- **目标**：实现设计 5.3、5.4、5.6、5.8 与前提 2、3，以及 D25、D26、D27、D38 的纯函数部分。
- **文件**：`gsc/{cutoff,coverage,pageset,quality,rules}.py`；`t/observe/test_gsc_cutoff.py`、`test_gsc_coverage.py`、`test_gsc_pageset.py`、`test_gsc_quality.py`、`test_gsc_rules.py`。
- **测试**
  - `test_usable_range_three_cases`：A 缺水位时本轮不出正式 24 小时窗口；PT 日结束早于 A 的水位，可用区间是整日；跨过 A 的水位却缺字段，记 `watermark_absent`。
  - `test_cutoff_ignores_history_day_ends`：22 日切片完整、A 的水位在 24 日中午时，H_c 不被压回 23 日。`[反例 23]` `test_cutoff_48h_gap`：[H_c−48h, H_c) 有断口时不出正式 24 小时窗口。`test_provisional_hours_excluded`：水位之后的小时标「暂定」，不进比较。
  - `test_site_admission`：A′ 与 C 的缺口 ≤τ 记 usable，否则 gap_exceeded；A′ 不支持且 A″ 失败记 unverifiable，只出描述性标签。`[反例 5]` `[反例 17]`
  - `test_site_admission_7d_datastate_per_day`：14 天逐日比较，当天明细是 E 用 A″f，是 D 用 A″a；任一天缺对应的那份总量，这个 7 天窗口记 unverifiable。
  - `test_per_identity_gap_blocks`：全站缺口 3%，但某剧的页全部漏在明细外，这部剧只出描述性标签。`[反例 22]` `test_pageset_independent_of_detail`（第 2 节）。`test_from_zero_needs_v_agreement`：W−1 漏掉某页、W0 出现 20 次曝光，没过 Vh 不出正式「从零起量」。`[反例 16]`
  - `test_unobserved_not_zero`：过滤请求与明细在 W−1 都无行时，文案是「未观测到」，不是 0。`[反例 26]` `test_unobserved_stored_null`（前提 1）。`test_7d_needs_vd_14_days`：前 7 天超出小时数据范围时，要有 Vd 覆盖 14 个 PT 日才出正式标签。`[反例 27]`
  - `test_vd_valid_rules`：`vd_valid()` 在窗口、任一天的切片版本 id、任一天的 dataState 三者之一变化时返回 False，否则 True；`test_vh_never_reused`。
  - `test_stale_slice_descriptive_only`：窗口碰到陈旧切片，只出描述性标签。`[反例 28]` `test_no_causal_label`：查询组成变化、平均排名不变而曝光上涨时，只写「曝光上升」「未伴随排名改善」「原因未定」。`[反例 12]`
  - `test_label_thresholds`：曝光飙升（W−1 ≥20 为正式门槛）、高点击率、排名冲顶（只作全站证据）、7 天 rising（≥100、比值 ≥1.5、增量 ≥30）按表判定，每个标签都输出命中条件原文与原始计数。
  - `test_quality_note_values`、`test_bh_family_per_set`（族是同一集合里做了检验的全部 7 天评估）、`test_quality_note_not_gate`、`test_quality_skipped_without_baseline`（D38）。另加第 2 节前提 3 的全部测试。
- **验收**：纯函数，覆盖率 ≥90%。

### TR-10 联动、资格、市场表、措辞、状态分类与提前量指标（纯函数，批次 1a，1.0 人日，依赖 TR-01、TR-33）
- **目标**：实现设计 6.1、6.2、1.3、5.7 的纯函数，前提 1 的措辞，D13、D31、D37、D39；两个采集服务、智能体、gateway 共用同一份实现。
- **文件**：`gp/observe/{market_map,link_rules,eligibility,wording,status_rules,leadtime}.py`；`t/fixtures/obs_status_cases.json`；对应测试。
- **规格**
  - 状态码清单：`stale_26h`、`not_published_low_coverage`、`extinguished_today`、`disabled_7d`、`canary_terminated`、`usertype_changed`、`all_zero_jump`、`legacy_unmapped_2pct`、`legacy_snapshot_missing`、`gsc_gap_exceeded`、`gsc_unverifiable`、`run_missed`（02:30 UTC 仍没有当天批次；GSC 超过 4 小时没有新轮次）、`shadow_mode`、`db_size_cap`。
  - `link_facts(trend_row, gsc_row, pair)` 只看同国家，产出标签与配对内部时效；`link_actionable(facts, trends_published_at, gsc_published_at, now)` 判定时刻计算。分发表 `LINK_RULES`（D29）。
  - `agent_eligibility(row, conditions)`：Trends 结果要求 confirmed（`trend_include_first` 时放宽到 first）、剧名清楚、对应已确认（`trend_include_presumed` 时放宽到强证据推定）、不陈旧、不是 carried_over、不是 B 档、不是 shared_title、不是 unstable、对照可用；emerging 只在 `trend_state=emerging` 时进入。GSC 结果只要求过了两层准入的正式标签，且没有 migration_suspect、mapping_changed，**不要求对应确认**（页面归属由 URL 决定）。每条排除都带原因，供计数。
  - eval-rules-v1 的后续事件清单照设计 6.2，编辑精选事件按 D33；池外发现的入池关联按 D37；改清单要升版本。
- **测试**
  - `test_link_same_country_only`：US 的 Trends 上升与 MX 的 GSC 上升不算双涨。`test_cooling_blocks_same_country_only`：一国 cooling 只拦该国的加码建议。`[反例 4]` `test_ww_sitewide_parallel_only`：WW 与全站同时上升只显示「全球同向」，不给动作。`[反例 19]`
  - `test_link_pair_timeliness`：GSC W0 与 `latest_block_end` 相隔超过 48 小时显示「时效不符」；H 与 D 两种块定义各一例（D39）。`test_link_actionability_read_time`：没有新发布，`now` 跨过 GSC 6 小时或 Trends 26 小时，动作撤下、事实不变。`test_link_degraded_no_country_24h`：GSC 降级时停用按国家的 24 小时联动。
  - `test_link_excluded_flags`：带 migration_suspect、mapping_changed、gap_exceeded、unverifiable、unstable 的不进联动。`test_market_map_display_only`：分组只用于展示，BGR 单列并注明「保语页面口径」。
  - `test_eligibility_cases_fixture`：真值表夹具逐行相等。`test_gsc_only_no_correspondence_needed`。`test_presumed_requires_flag`。
  - `test_wording_forbidden_terms`（前提 1）。`test_status_cases_fixture`、`test_link_cases_fixture`：结果等于夹具，TR-24 的 TS 端用同一份夹具。
  - `test_leadtime_denominator`：满 14 天的全部提示都进分母，不满的单列「观察中」；提示前已有后续事件的记为负的滞后。`[反例 13]` `test_alert_dedupe_14d`：同一个去重键 14 天内只记第一次。`[反例 20]` `test_dedupe_survives_alias_change`：别名换了身份，root_identity 不变，去重照旧。`test_shadow_alerts_excluded`：shadow 提示不进指标，也不占 live 的去重窗口。`test_pool_entry_ambiguous_counted_separately`。`test_leadtime_min_30`：不足 30 件不下结论。
- **验收**：纯函数，覆盖率 ≥90%；夹具由 TR-24 的 TS 端复用。

### TR-11 迁移 0007、models 与网盘清洗覆盖（批次 1a，1.75 人日，依赖 TR-01、TR-33）
- **目标**：实现设计 3.5 的表，外加 D12、D13、D26、D27 所需的四张表；只加表和可空列；新 JSON 列同一任务里归进网盘清洗名单。
- **文件**：`gp/migrations/versions/0007_observe.py`、`gp/models.py`；`t/test_pg_migrations.py`（`TABLES` 全等断言）、`t/test_mirror_migrations.py`；`t/observe/test_0007.py`；`t/pan_runbook.py`、`t/test_pan_runbook_{sql,text}.py`；`docs/pick-workbench/supabase/pan-check.sql`、`pan-redact.sql`。
- **规格**
  - 新表：设计 3.5 的 17 张，加 `ggwp_obs_decisions`（追加式，自增 id）、`ggwp_obs_links`（联动事实，主键含 link-rules 版本）、`ggwp_gsc_vchecks`（V 状态，D26）、`ggwp_gsc_totals`（A、A′、A″a、A″f 总量，D27），共 21 张。
  - 约束：`ggwp_obs_batches` 建部分唯一索引 (target_date) WHERE channel='trends'；`ggwp_gsc_slices` 建部分唯一索引 (dataset, pt_date) WHERE active；`ggwp_obs_budget` 主键 (channel, budget_day)（D23）；`ggwp_obs_runtime` 主键 channel，并预置 trends、gsc 两行（D34）；`ggwp_obs_sets` 有 channel、mode（live/shadow）、status（published/pruned）、`frozen_inputs_json`；`ggwp_obs_alerts` 有 mode、root_identity（D37）。
  - 时间列 `String(40)`，JSON 列 `sa.JSON`，cookie 罐存 Text。`ggwp_candidate_sets` 加三个可空列，写法照 0005 的 `COLUMNS` 循环。
  - PG 分支：`CREATE SCHEMA IF NOT EXISTS pick_obs`，对 PUBLIC、anon、authenticated 收回权限。视图 `sets`、`states`、`links`、`discoveries`、`alias_queue`、`confirm_queue`、`alerts`、`run_status` 用 `CREATE OR REPLACE`，列与 TR-33 合同一致（含 mode），不开 `security_invoker`，`ggwp_*` 写全限定名；运行时表（cookie 罐、请求日志、租约）不进视图。
  - 授权照 `0006:125-140`：reader 来自 `PICK_MIRROR_READER_ROLE`；observer 来自新变量 `PICK_OBS_OBSERVER_ROLE`，默认 `pick_observer`；角色不存在就告警并跳过。observer 的授权清单见 TR-12（0007 里的授权段由本任务按那份清单写）。
  - 网盘清洗：0007 的每个 JSON 列归进 `JSON_COLUMNS` 或 `KEPT_JSON_COLUMNS`；剧名、查询词、发现词的文本列加进两个脚本的 LOCATIONS；`ggwp_gsc_query_daily` 用 DELETE（D18）。
- **测试**
  - `test_0007_idempotent`：版本号改回 0006 再升级能跑过。`test_0007_downgrade_roundtrip`：从 head 降到 0004 再升回；SQLite 上先删索引，再用 batch 删列。`test_models_match_migration`：每张表、每一列都与 `models.metadata` 一致。
  - `test_runtime_seed_rows`：升级后两行都在；重跑不重复；降级再升回仍在。`test_budget_day_key`。`test_active_slice_unique`：同一数据集同一天写第二个 active 版本会失败。
  - `test_views_pg_only`、`test_reader_reads_views_not_tables`、`test_no_anon_usage_pick_obs`、`test_grant_skipped_without_role`。
  - `test_the_scripts_cover_every_json_column_of_the_workbench` 保持绿；`test_query_daily_redact_deletes`；`test_scripts_run_on_0006_db`；`test_pan_runbook_text` 的计数随之更新。
- **验收**：两种库绿；`pg_template` 下全部 PG 测试绿。

### TR-12 pick_observer 角色、授权、regrant 与连接账（批次 1b，1.0 人日，依赖 TR-11）
- **目标**：实现设计 3.4 的角色与授权（D3、D14、D15、D16）；生产上能先建角色、后跑 0007；现存镜像版本也能读。
- **文件**：`docs/pick-workbench/supabase/` 下的 `bootstrap-observer.sql` 与 `-undo.sql`、`bootstrap.sql`、`bootstrap-undo.sql`；`supabase.md`（第 6 节的数字、`\dn+` 的预期、连接预算表）；`gp/mirror/publish.py`（`grant_observer`）；`admin/cmd_regrant.py`。测试：`t/test_bootstrap_sql.py`、`t/observe/test_observer_rights.py`。
- **规格**
  - 角色：`pick_observer LOGIN NOINHERIT CONNECTION LIMIT 20`；以 deerflow_app 身份授 `USAGE ON SCHEMA deerflow`；默认值 `search_path=deerflow`、`timezone=UTC`、`idle_in_transaction_session_timeout`、`statement_timeout`。
  - 表级授权。读：`ggwp_import_batches`、`ggwp_drama_versions`、`ggwp_alembic_version`、`ggwp_obs_decisions`；`ggwp_candidate_sets` 只给四列（D16）；`pick_mirror` 的 USAGE 以及 `versions`、`series` 的 SELECT。写：其余 `ggwp_obs_*`、`ggwp_gsc_*` 与它们的序列，包括 `ggwp_obs_legacy`（D14）。碰不到：会话、checkpoint、用户表、`pick_obs`。
  - `regrant`：幂等补齐上面的表级授权，并对每个 status=published 的镜像版本补授 schema USAGE 与 `rs_ids` SELECT（D15）。
  - undo 的顺序：先以 deerflow_app 身份收回表与 schema 上的授权，再收回库级授权，最后 `DROP ROLE`。
- **测试**
  - `test_observer_bootstrap_incremental`：在已跑过全量 bootstrap 的库上，增量脚本能执行且没有 WARNING。`test_observer_undo_rerun`：undo 之后能重跑。
  - `test_observer_rights`：能写 `ggwp_obs_*`；读不到 `ggwp_selections`、checkpoint、用户表、`pick_obs`；候选集只能读那四列。
  - `test_publish_grants_observer_rs_ids`：新镜像版本发布后 observer 能读 `rs_ids`、读不到该版本的其他表；角色不存在就跳过。
  - `test_regrant_covers_published_versions`：observer 建角色之前已发布的两个版本，regrant 之后 observer 都能读 `rs_ids`。`test_observer_reads_current_rs_ids_readonly`：以 observer 连接、只读事务读当前版本的 `rs_ids` 后回滚。
  - 既有断言跟着改：`ROLES`、CREATE ROLE 原文、ACL 断言（schema 从两个变成三个）、`leftovers`。
- **验收**：手册里的数字与 SQL 表头、计数测试一致；连接账写明 pick_observer 的上限是 20，实际最多 2 条，用 plan:923 的方法实测。

### TR-16 前端放行可选字段与回滚矩阵（批次 1b，1.25 人日，依赖 TR-33；设计 7.5 第 1 步）
- **文件**：`fe/core/pick/{types,format,obs-format}.ts`、`views/replay-rules.ts`、`fe/server/pick-board/replay.ts`；测试在 `ft/core/pick/{types,api,format,contract,contract-fixtures}.test.ts`、`ft/components/workspace/pick/{candidate-view,pick-tool-card}.dom.test.tsx`、`replay-rules.test.ts`；夹具 `ft/core/pick/fixtures/backend-result-obs.json`（由 TR-33 合同夹具生成，TR-27 再用真实同步重新生成）。
- **规格**
  - `sort` 加 `"obs"`；条件加七个可选键（D31）；结果加可选的 `observations`（strict，形状照 TR-33）；evidence 不改（D8）。
  - `AGENT_ONLY_LABELS` 的顺序与后端一致，用 TR-33 的共用夹具钉住。`conditionsLine` 为 obs 排序出文案；`evidenceLine` 对 obs_* 的 kind 走 `obs-format.ts`。
- **测试**
  - `contract-fixtures.test`：TR-33 的正例全部能解析，反例全部被拒。
  - `types.test`：旧夹具与 obs 夹具都能解析，未知键仍被拒绝，`sort=obs` 被接受。`api.test`：`listSavedPicks` 返回新旧快照混合、`getPickResult` 返回新形状，都能解析。`contract.test`：两份夹具都能解析。
  - `candidate-view.dom`：值为 null 时渲染「未观测到」。`replay-rules.test`：新条件键映射成仅智能体可用的条件，顺序与后端一致。`client-bundle.test`：`obs-format.ts` 不带进 pick-board 模块。perf 预算数字不变。
- **回滚矩阵**：第 10 节的逐格表写进 runbook；本任务覆盖前端的四格（新前端 × 旧卡、新卡、混合会话、存量快照）。`[反例 14]`
- **验收**：先于任何会输出新字段的 gateway 上生产（第 10 节 S1），经 TR-34 的前端模式部署。

### TR-13 数据库状态、租约代次、唯一写入口与自检（批次 2a，1.5 人日，依赖 TR-04、TR-11）
- **目标**：实现设计 3.3，覆盖三个边界，两种方言都要有；采集侧所有写库只能经 `LeasedWriter`（批判 A-7）。
- **文件**：`gp/observe/{db,lease,selfcheck}.py`、`admin/cmd_reset_disable.py`；`t/observe/test_lease.py`、`test_selfcheck.py`、`test_write_paths.py`。
- **测试**
  - `test_takeover_bumps_generation`。`test_stale_owner_cannot_write_or_publish`：租约过期后旧进程恢复，扣预算、写响应、发布集合、切换切片指针、写提示与里程碑都抛 `LeaseLost`，零写入。`[反例 10]` `test_takeover_serialized_with_write`：两个进程并发时，接管与写入被串行化。
  - `test_collectors_write_only_via_lease`：导入图检查，`observe/trends/*`、`observe/gsc/*` 不直接导入 `sqlalchemy.ext.asyncio` 或 `observe.db` 的写接口，只能经 `LeasedWriter` 与 `store`。
  - `test_budget_not_refunded`。`test_cross_midnight_budget_same_target_date`（D23）。`test_single_connection`：PG 上按 application_name 查 `pg_stat_activity`，任何时刻 ≤1 条，步骤之间 0 条。`test_renew_during_4h_pause`。
  - `test_state_unreadable_no_run`、`test_missing_runtime_row_exit_3`：读失败或行不存在都以退出码 3 退出，零 HTTP（D34）。
  - `test_selfcheck`：`current_user` 不是预期角色、迁移头不被本镜像认识或早于 0007、collector_version 与环境变量不符，任一条都在任何 HTTP 之前以退出码 2 退出。`[反例 15]` `test_selfcheck_accepts_known_newer_head`：头是本镜像认识的更新修订时通过（D5）。`test_no_session_set`：只用 `SET LOCAL`。
  - `test_reset_disable`：disabled 状态只能经这条命令清除，并记操作人。
- **实现要点**：续租间隔 60 秒，租期 5 分钟；每个请求前都调一次 `ensure_fresh()`；`application_name` 分别是 `ggwp-obs-trends` 与 `ggwp-obs-gsc`。`DbStateStore` 用 TR-04 的加密序列化把 cookie 罐存进 Text 列。
- **验收**：两种库绿；PG 上的并发用例连跑 20 次，没有偶发失败。

### TR-14 Trends 会话执行器与 cron 入口，含金丝雀模式（批次 2a，2.0 人日，依赖 TR-02、TR-03、TR-13，输入依赖 TR-05 的 `canary_controls.json`）
- **目标**：实现设计 3.2、4.5、4.10、6.3 的会话部分，能直接跑金丝雀；限速与预算在执行器层接线（批判 C-17）。
- **文件**：`trends/{units,canary,run,__main__}.py`；`t/observe/test_trends_run.py`、`test_trends_wiring.py`。
- **规格**
  - 流程：① 自检，取租约，在租约之下读状态（TR-13 的 `collector_session`，读到的就是接着要写的状态）；② 按 target_date 取或建批次，`window_end` 在建批次时写入；③ 展开任务清单，被截断的单元按截断顺序落表；④ 逐单元执行：等 pacer，扣预算，发 HTTP，写请求行与原始行（预热、探针、重试同样走这一步）；⑤ 更新熔断状态，检查截止时间；⑥ 收尾写汇总：覆盖率、熔断事件、全零率、userType 变化、状态码（D10）。金丝雀会话不发布集合；发布接线由 TR-20 加。
  - 模式（第 9 节，G3 改）：stable 暂定 17:30 起跑、计划 ≤350、上限 525；canary1 21:00 起跑、计划 220；canary2 18:30 起跑、计划约 300、上限 450；都在 01:45 硬截止。早于起跑时刻被触发，退出码 0，什么都不做。节奏由 `PICK_OBS_TRENDS_PACE` 选（默认 `user`），模式在该节奏下放不进窗口以 2 拒跑。
  - 金丝雀期出现一次验证码或同意页，或其他原因熄火累计 2 天，写入 `canary_terminated`，之后拒绝再跑（G3 改：原文「验证码或熄火 2 次」有歧义）。
  - 金丝雀的负载闸门与 `preflight` 命令，批次的 `plan_json.notes` 记节奏与 `late_admission`（第 9 节，G3 加）。
  - `CanaryTaskSource`：`canary_controls.json` 里的对照；当前共享批次里 `listed_at` 在 14 天内的欧美六语剧名（只用裸剧名形态）；每个 geo 一条对照序列；按比例混入 relatedsearches（去向为「只过 A」时不混）。
  - 每周线上合同检查（设计第 10 节，U12 批准后开启）：每周一用固定参数取 2 个单元，计入当天预算；解析失败写 `parse_error` 告警码，不写值。
- **测试**
  - `test_window_end_shared_2210_0130`：22:10 与 01:30 抓到的序列共用同一个 `window_end`。`test_resume_after_midnight_same_window`：午夜后崩溃续跑，target_date 与 `window_end` 都不重算。
  - `test_transport_level_envelope`：MockTransport 按假时钟记下每个请求的时刻，跑一个完整会话（注入 429、探针、5xx 重试），在传输层日志上断言 TR-03 的包络；断言请求数 = 预算扣减数 = 请求行数。`test_transport_level_noop_pacer_red`：执行器里的 pacer 换成空操作，同一断言失败。`[反例 2]`
  - `test_takeover_mid_http_trends`：HTTP 返回前租约被接管，请求行、预算、原始行都不由旧 owner 提交。`[反例 10]`
  - `test_startup_error_matrix`：用真实的 `__main__` 复跑 TR-13 在替身入口上测过的错误矩阵（自检各项不符为 2；状态读不回来的各种情形、运行时行缺失为 3；租约被占、启动时等锁或语句超时为 1），每种都零 HTTP。
  - `test_deadline_truncates`、`test_truncation_order`（规则优先级、`listed_at` 降序、最新依据日期、identity 哈希轮换）、`test_uncovered_units_listed`。
  - `test_canary_terminate_rule`、`test_all_zero_rate_and_usertype_alert`、`test_idempotent_after_publish`（已发布的 target_date 再被触发，退出码 0）。
- **验收**：用 MockTransport 加本机 PG 端到端跑一个模拟日（时钟加速）。

### TR-15 Trends 打包、Railway 配置与部署验证（批次 2a，0.5 人日，依赖 TR-14）
- **目标**：实现设计 3.1 的打包路径与 trends cron 配置（D5、D6、D21）。gsc 的配置随入口在 TR-21 里做。
- **文件**：`deploy/pick-obs/trends/railway.toml`；`t/observe/test_railway_config.py`；CI 工作流的触发路径；`docs/pick-workbench/observe-runbook/packaging.md`。
- **规格**：`[build]` 与根目录 `railway.toml` 相同。`[deploy]`：`startCommand = '/bin/sh -c "cd /app/backend && exec python -m ggwork_pick.observe.trends run"'`；`restartPolicyType="NEVER"`；不写 `healthcheckPath`；`cronSchedule = "*/30 17-23,0-1 * * *"`（G3 改，原为 `20-23`），早于模式起跑的触发（至少 17:00 那次）在程序里直接退出。
- **测试**：`test_trends_railway_config_pinned`（逐字段）；`test_root_railway_untouched`；`test_dockerfile_cmd_untouched`（沿用既有的钉住测试）；`test_trends_entrypoint_argv`（能解析子命令；带 `--selfcheck-only` 时自检完就退出）。
- **验收**：配置测试绿，CI 触发路径已含 `deploy/pick-obs/**`。部署验证见第 10 节 S6。

### TR-34 部署守卫（批次 2a，0.5 人日，依赖 TR-11）
- **目标**：把跨会话防护从口头转告变成脚本（D41）。
- **文件**：`scripts/pick-deploy-guard.py`；`t/test_deploy_guard.py`（按路径导入）；`docs/pick-workbench/observe-runbook/deploy-guard.md`。
- **规格**
  - 子命令 `gateway`、`cron <service>`、`frontend`。共同检查：工作区干净、没有未跟踪的 `.env*`；`git fetch` 后 HEAD 等于 `ggwork/main`（默认远端 `ggwork`，见 D41）；打印 SHA 与要记进 progress.md 的一行。
  - `gateway` 与 `cron` 另查：用 observer 的 DSN 文件（600 权限，路径来自 `PICK_OBS_DSN_FILE`）读 `ggwp_alembic_version`，本地迁移链认不出就拒绝；上一次记录的生产提交必须是 HEAD 的祖先。
  - `frontend` 另做：`git archive <HEAD> frontend`（HEAD 已核对等于 `ggwork/main`）导出到新建的干净目录，提示在那里执行 Vercel 部署。
  - DSN、口令一律不打印。
- **测试**：`test_refuses_dirty_tree`、`test_refuses_not_origin_main`、`test_refuses_unknown_prod_head`、`test_accepts_known_head`、`test_refuses_non_ancestor_prod_commit`、`test_never_prints_dsn`（用假 git 与假库）。
- **验收**：两个会话都用它部署（U16 转告）；S1 起每次部署的输出行都记进 progress.md。

### TR-35 人工决定的生效链（批次 2a，0.75 人日，依赖 TR-11、TR-33）
- **目标**：定义「决定 → 有效状态 → 版本 → 集合冻结」这条链的唯一实现（D24，批判 A-2、C-12）。
- **文件**：`gp/observe/decisions_state.py`；`t/observe/test_decisions_state.py`。
- **规格**
  - `effective(decisions, upto_id) -> EffectiveDecisions`（不可变）：按 id 顺序应用，后者覆盖前者。内容：别名确认、拒绝、手动配对（交给 D43 的别名刷新写成新版本）；对应确认（键 = identity、平台、规范化标题）；人工加入与暂停（生效条目上限 50）；歧义改判；提示标无关。
  - 对应确认失效的情形：撤销；确认之后任一共享批次里该身份的规范化标题或平台变过、身份不在批次里、批次明细已清理无从核对；手动配对把它当旧身份配走。失效永久，改回不恢复，只有新的确认能恢复（G3 评审 P2-2）：`lapse_causes(state, carried, sightings)` 累加并记下每条失效的原因与批次（`lapse` 是它的 id 版），`catalog_history.read_sightings` 只读地读批次，`trends/lapses.py` 的账本逐会话接力（G3 复审 P2），`correspondence(..., lapsed=)` 按冻结的失效集合判。
  - 读取函数 `read_decisions(conn, upto_id)` 只读。
- **测试**：`test_confirm_then_revoke`；`test_title_change_invalidates_confirmation`；`test_alias_change_requires_reconfirm`；`test_old_set_unchanged_after_revoke`（decisions_version=k 的集合不受 k 之后的决定影响）；`test_watch_add_cap_50_effective`；`test_order_by_id_deterministic`；`test_unknown_kind_rejected`。G3 加：`test_title_revert_does_not_restore_confirmation`、`test_platform_revert_does_not_restore_confirmation`、`test_alias_swap_and_back_does_not_restore_confirmation`、`test_old_set_keeps_its_frozen_lapses`、`test_lapse_causes_name_the_batch_and_how`、`test_every_batch_in_the_window_counts_even_one_before_the_confirmation`；`t/observe/test_catalog_history.py`（两种库，含内容复用后重新发布的批次、没有要问的身份也读窗口）；`t/observe/test_trends_lapses.py`（账本、没发布集合的夜晚之后不因清理批次全部失效、三个会话 A→B→A、读不回来的账本不被跳过）。
- **验收**：纯函数覆盖率 ≥90%。

### TR-17 Trends 判定规则（批次 2b-i，1.0 人日，依赖阶段 0 报告过 G2）

> **2026-09-29 G2：取消**（去向 `b_only`，第 8.1 节）。下文保留作记录，不实现。
- **目标**：按报告选定的颗粒度（下面以小时级为例）实现设计 4.9。
- **文件**：`trends/rules.py`；`t/observe/test_trends_rules.py`（用真实夹具加构造序列）。
- **测试**
  - `test_rules_distinguish`：一组期望标签各不相同的夹具，换成恒等或常量实现必然失败。`test_nonzero_hours_counted_not_inferred`：在「推算」会得出不同结果的夹具上，非零小时按原始点数。`[反例 1]`
  - `test_window_is_batch_not_series`：22:10 与 01:30 两条序列尾点不同，按批次 `window_end` 切块与按各自尾点切块得出不同标签；实现必须得出前者。`[反例 1]`
  - `test_scale_invariance`：序列整体乘以常数，标签不变。`test_strong_variant_no_flip`：同一请求加进一条更强的变体线，裸剧名线的标签不翻转。`[反例 11]` `test_low_swap_bounded`。
  - `test_partial_insufficient_window`：窗口含 partial 点或缺小时，记 `insufficient_window`。`test_control_unavailable`：对照序列 B5 全零时记 `control_unavailable`，不除以零。`[反例 25]`
  - `test_confirmed_comparability`：五个可比条件逐一破坏，都得不到 confirmed；规则改版前后各一天 rising，也不算 confirmed。`[反例 18]` `test_unstable_from_refetch`。
  - `test_confirmed_across_start_change`：起跑时刻改动（含 canary1 → canary2 → stable）前后两天各一次 rising，`window_end` 的前移按两个批次实际冻结的值算、不按起跑时刻推算；超出 20–28 小时时得不到 confirmed，只记 first（G3 处置，第 14.3 节「分层 A（窗口间隔）」）。
  - `test_priority_order`、`test_only_ok_and_ok_zero_judged`、`test_emerging_cooling`。
  - `test_latest_block_end_output`：判定行带 `latest_block_end`（D39）。
- **验收**：规则文本与阶段 0 报告逐句对应；阈值全部是版本化的参数。选 H+D 时 +0.5 人日。

### TR-18 观察清单、歧义、身份证据与别名（批次 2b-i，2.0 人日，依赖 TR-11、TR-14、TR-33、TR-35）
- **目标**：实现设计 4.5 到 4.7 与 7.2：每天自动重算观察清单与请求形态，身份只在证据够时才续接。
- **文件**：`trends/{watch,ambiguity,alias}.py`、`trends/units.py` 的 `WatchTaskSource`；`admin/cmd_identity_churn.py`；对应测试。
- **规格**
  - watch-rules-v1 的五条规则；A、B 档与到期；B 档首次通过后次日升 A 档。剧名先去配音标记；身份必须在当前共享批次里；人工加入与暂停取自 TR-35 的有效状态。
  - 规则 4 读对方通道最新的**同模式** GSC 集合；还没有时记「规则 4 无数据」进批次汇总，不报错。
  - 按设计 4.5 分配每天的容量；每个 geo 一条对照序列，用本地化的「short drama」。
  - 请求形态：剧名清楚的，一个请求放裸剧名加三个本地化意图变体；泛词或被更长剧名包含的，只放三个变体；同语种同名多行标 `shared_title`。
  - 歧义三道：常用词表、包含关系、相关查询（有效期 14 天，查不到记 `unresolved`）。去向为「只过 A」时第三道输出 `manual_required`，进资料页歧义队列，经 TR-25 的 `ambiguity_override` 处理。身份证据等级分强、中、弱，带版本。
  - 别名：去掉括号备注后上游 ID 完全相同、平台和语种也相同的记 auto；标题匹配只出 suggested；环、多对多、跨平台、跨语种一律拒绝；追加式，每次变更产生新版本。`alias.refresh()` 的调用方是 gsc 服务（D43，TR-23b 接线）。
  - 跨批次映射函数 `map_identities(set_batch, cand_batch, alias_version)`，纯函数、结果确定，供 TR-26 使用。`identity-churn` 只读比较两个共享批次的身份集合，在生产上跑之前先经 U8 批准。
- **测试**
  - `test_generic_title_no_bare_line`：泛词请求里没有裸剧名，结果记「歧义，不判定」。`[反例 2]` `test_same_title_same_platform_not_merged`。`test_title_revision_suggested_only`。`[反例 8]`
  - `test_out_of_pool_same_name_needs_confirm`：同平台池外旧作与候选同名，最多标「推定对应」。`[反例 21]` `test_alias_append_only_versions`；`test_alias_rejects_cycles_cross`；`test_alias_refresh_deterministic`（输入不变不产生新版本）。
  - `test_watch_rules_counts`：用 09-21 形态的夹具批次算出 A、B 档数量与截断名单。`test_b_tier_never_to_agent`。`test_rule4_same_mode_only`。`test_manual_required_route`。
- **验收**：夹具批次跑出的档位数量、歧义计数、别名建议可复算；B 档与未确认的结果都带不进智能体的标记。

### TR-21 GSC 采集服务、切片版本与入口（批次 2b-i，2.0 人日，依赖 TR-06、TR-07、TR-13、TR-33）
- **目标**：实现设计 5.2、5.3 的采集与切片修订语义，D27 的两份日级总量，D36 的冻结输入读取，以及 gsc cron 的入口与配置。
- **文件**：`gsc/{__main__,run,plan,slices}.py`、`gp/observe/frozen.py`；`deploy/pick-obs/gsc/railway.toml`；`t/observe/test_gsc_run.py`、`test_gsc_slices.py`、`test_frozen.py`；`t/fixtures/gsc/`（TR-07 实测后的脱敏响应）。
- **规格**
  - 每轮：自检，取租约，分配轮次 id，`read_frozen_inputs()` 读一次冻结输入并在整轮使用，按计划发请求。A 与 A′（不支持时只用 A″）；A″a 与 A″f 各覆盖最近 16 个 PT 日；C 对与 [H_c−48h, now] 相交的每个 PT 日各一个切片；D 补取直到最新完整日成功；E 取 D−3 到 D−5，35 天内缺 final 或失败的日期每天重试；Q 每天一次、只取 1 天。
  - 冷启动：分几天回补约 35 天的 final 日级数据，注意配额；历史不足 28 天的身份打标。
  - 切片替换：新版本全部写完后，在一个事务里切换生效指针，再删旧版本。截断时按国家拆分重发；失败或截断时旧版本继续生效、切片标陈旧；新鲜数据不翻页。
  - 10 分钟硬截止；逐轮记请求数与配额错误；小时表保留 10 天，日级表 35 天，总量与 V 状态 10 天；查询词入库前先过网盘清洗（D18）。所有写入经 `LeasedWriter`。
  - 配置：`startCommand` 照 TR-15 换成 `gsc`；`cronSchedule = "25 */3 * * *"`，03:25 那轮在 03:35 截止，与 03:40 的镜像同步错开。
- **测试**
  - `test_revision_drops_row`；`test_country_shard_fail_keeps_old_no_zero`（`[反例 6]`）；`test_new_version_truncated_old_active_stale`（`[反例 28]`）；`test_shapes_never_mixed`；`test_deadline_10min`。
  - `test_a2_all_and_final_each_round`：每轮都写 A″a 与 A″f 两份总量，final 缺的日子记「无行」。
  - `test_frozen_inputs_read_once`：归因之后、V 请求之前切换镜像版本与别名版本，本轮仍用开头读到的那组输入。
  - `test_takeover_mid_round_gsc`：HTTP 返回前租约被接管，切片指针切换、总量写入都不由旧 owner 提交。`[反例 10]`
  - `test_cold_start_spread`；`test_quota_logged`；`test_query_pan_scrub_drop`。
  - `test_gsc_railway_config_pinned`；`test_gsc_entrypoint_argv`。
- **验收**：用假 transport 连跑 8 轮（一天），切片切换、冷启动回补、截止都与预期一致，两种库都绿。

### TR-22 GSC 归因与旧页快照导入（批次 2b-i，1.25 人日，依赖 TR-09、TR-12、TR-13；验收用 TR-08 的快照）
- **目标**：实现设计 5.5 与 5.6 第一层。
- **文件**：`gsc/{attribution,legacy}.py`、`admin/cmd_import_legacy.py`；`t/observe/test_gsc_attribution.py`、`test_legacy_import.py`。
- **规格**
  - 新页：URL 尾部必须是 24 位十六进制，并与 v1 `source_id` 解码出的正典 id、locale 一致；非正典 id 经**冻结镜像版本**的 `rs_ids` 解析（D15、D36），解析不了的记 `noncanonical_unresolved`。归属判断复用 TR-09 的 `page_set` 谓词。
  - 旧页：用 GSC 返回的完整原串在集合冻结的快照里精确查表，查不到的记 `legacy_unmapped`。转博客的旧页单列，不并入剧的总量。站点级页面单列；查询 Q 只给推断归因，单独展示。
  - `mapping_changed`：解析版本变化带来的差额单列，这部剧本轮不出上升标签。`migration_suspect`：旧页占比在两个窗口间变化超过 30 个百分点。未映射的旧页点击超过旧页总点击的 2% 时写告警码。
  - `admin import-legacy --file --manifest`：校验 manifest 与 sha256；快照不可变；旧快照保留到没有集合引用为止。
- **测试**
  - `test_page_country_not_language`（`[反例 3]`）；`test_mapping_only_change`（`[反例 7]`）；`test_raw_url_exact_key`（`[反例 24]`）；`test_no_double_attribution`；`test_layer1_conservation`；`test_legacy_import_rejects_mutation`。
  - `test_rs_ids_from_pinned_version`：镜像在本轮中途发布新版本，归因仍用冻结的版本。
- **验收**：用 TR-08 的真实快照（去掉剧名）加构造明细，第一层守恒成立，各类未归因的计数可复算。

### TR-25 人工操作、状态接口、红色横幅与交互组件（批次 2b-i，1.25 人日，依赖 TR-10、TR-11、TR-33、TR-35）
- **目标**：实现设计 3.7 的告警落点与 4.7 的人工确认入口（D10、D12）；先于 TR-24 建好交互组件。
- **文件**：`gp/routes.py`、`gp/observe/{decisions,status}.py`；`t/observe/test_decisions.py`、`test_sync_obs.py`；前端 `fe/core/pick/sync-schema.ts`、`fe/server/pick-board/gateway.ts`、`fe/components/workspace/pick/obs/*.tsx`、DataImports 的横幅、`contracts.test` 的登记表。
- **规格**
  - gateway 路由 `POST /api/pick/obs/decisions`：九种 kind（TR-33）；请求模型用 StrictInput，写入过 `storable`；必须是认证过的 owner，缺失或为 default 就拒绝；记录操作人；写入前用 TR-35 的 `effective` 校验（watch_add 在生效条目满 50 条时拒绝）。
  - `/sync` 加 `obs` 键；前端在 `sync-schema.ts` 里声明为 `.nullable().optional().catch(undefined)`，`gateway.ts` 同步 extend。「同步与导入」tab 的红色横幅按 `obs` 键显示。
  - 交互组件放 `fe/components/workspace/pick/obs/`；`contracts.test` 的断言由「不新增 client 组件」改为「只允许该目录下登记过的 client 组件」，每个登记项写理由。
- **测试**：`test_decisions_owner_required`、`test_decisions_append_only`、`test_watch_add_cap_50`（第 51 条被拒绝）、`test_sync_obs_key_shape`（与 TR-33 合同一致）；前端 `sync-schema.test`（`obs` 键缺失或形状不对，不影响其余字段）、`data-imports.dom.test`（红色横幅按状态显示）、`contracts.test`（未登记的 client 组件会让测试变红）。
- **验收**：以真实认证走一遍确认、暂停、人工加入，决定表只追加；`/sync` 加了 `obs` 键后，旧前端照样能解析。

### TR-20 集合发布通用层、Trends 发布、联动物化、提示与清理（批次 2b-ii，1.5 人日，依赖 TR-10、TR-13、TR-17、TR-18、TR-21、TR-35）
- **目标**：拥有 `store.py` 的全部通用部分；实现设计 3.5 的保留、4.10、6.2 的提示记录、7.1 的集合冻结（Trends 一侧），把发布接进 `trends/run.py`。
- **文件**：`gp/observe/store.py`、`trends/publish.py`、`trends/run.py`（加发布接线）；`t/observe/test_trends_publish.py`、`test_store.py`、`test_store_prune.py`。
- **规格**
  - `store.py` 提供：发布集合（channel、mode、`frozen_inputs_json`、`decisions_version`）；写判定行；联动物化（新集合 × 对方通道最新的同模式集合，D13）；写提示（D37）；写里程碑；写发现；清理（D30）；发布前检查 `PICK_DB_SIZE_CAP_BYTES`。全部经 `LeasedWriter`，集合、判定行、联动行在同一事务里提交。每个公开函数的第一个参数是 `LeasedStep`（只读的是 `ReadStep`），导出给采集代码的名字登记进 `test_write_paths.py` 的 `DOORS`（`test_store_takes_a_step` 检查签名）。超过 30 秒的步骤（大批清理、联动物化）用 `writer.step(limits=StepLimits(...))` 只放宽这一步。
  - Trends 批次收尾：会话开头读 `FrozenInputs`；先按规则判定，再对 first 命中做一致性复取（预算 ≤50），标出 unstable；用 TR-35 的有效状态与本会话折叠出的失效账本（`trends/lapses.py`：会话开头折叠，随计划存进批次行，不论是否发布；集合冻结它的 `lapsed`，日志与 `summary_json` 写 `describe` 的一行）写对应确认状态进判定行（接线见 progress.md 的 TR-35 交接）；然后发布。`PICK_OBS_PUBLISH=1` 时集合为 live，否则为 shadow。
  - 判定行存全部中间量与原始计数。没刷新到的行沿用上次状态和自己的 `window_end`，标 `carried_over`；超过 3 天记 `stale`。A 档覆盖率低于 80% 不发布，上一个集合继续生效，批次写红色状态码。
  - 清理：见 D30；请求与原始表 35 天；提示与里程碑 180 天。
- **测试**
  - `test_low_coverage_keeps_previous`；`test_carried_over_not_agent_eligible`；`test_publish_after_lease_loss_refused`（`[反例 10]`）；`test_takeover_before_publish_commit_trends`：在真实 `run.py` 里注入「发布事务提交前租约被接管」，集合、判定行、联动行、提示都没有落库。
  - `test_prune_respects_references`；`test_prune_keeps_current_live_per_channel`：开关关闭后连发 4 个 shadow，当前 live 仍在。`test_prune_per_channel`：GSC 的 8 个集合不挤掉 Trends。`test_prune_race_with_candidate`：清理与候选物化并发，候选要么拿到完整证据，要么报「已过保留期」，不会有半截证据。
  - `test_sets_immutable`：原始数据、规则、别名分别升级后，旧集合的判定行不变。`[反例 9]` `test_alerts_dedupe`（`[反例 20]`）；`test_shadow_alerts_do_not_consume_live_dedupe`。
  - `test_links_same_mode_only`：shadow 集合只和 shadow 配对，live 只和 live 配对。`test_decisions_version_frozen`。`test_shadow_vs_live`。
  - D24 接线（G3 复审）：`test_correspondence_lapse_carried_across_sets`（三个模拟日标题 A→B→A，第三天仍是 unconfirmed，新确认后恢复）；`test_lapse_ledger_reused_batch`；`test_lapse_ledger_pruned_batch_named`；`test_lapse_ledger_after_withheld_session`；`test_lapse_ledger_resume_not_recomputed`；`test_lapsed_confirmations_frozen_nontrivial`（冻结值等于账本，模拟里有非空的一天，挡住恒为 `[]` 的实现）。
- **验收**：用加速时钟跑连续 3 个模拟日，集合、carried_over、stale、提示去重、联动、清理、对应确认的失效结果都与预期表一致。G4 核对 D24 接线：上面六个用例在真实 `run.py` 上通过，且三日模拟里 `lapsed_confirmations` 有非空的一天。

### TR-23a GSC 逐剧核对与两层准入（批次 2b-ii，1.5 人日，依赖 TR-09、TR-21、TR-22）
- **目标**：实现设计 5.6 的两层准入、前提 3、4，以及 D25、D26 在请求层的落地。
- **文件**：`gsc/{verify,admission}.py`；`t/observe/test_gsc_verify.py`、`test_gsc_admission.py`。
- **规格**
  - 对本轮数据套用 TR-09 的函数，按两层准入决定出正式标签还是描述性标签，原因写进判定行。
  - 逐剧核对只对将出正式标签的身份发请求。Vh 用 `[hour,country]` 加锚定正则，正则由 `page_set` 产出，按 TR-07 测得的长度上限分块；每轮重取。Vd 用 `[date,country]`，按 dataState 分组；满足 D26 时沿用上一次的结果，否则重取。
  - V 状态写 `ggwp_gsc_vchecks`：身份 × 窗口 × 国家 × dataState、X_flt（无行记 NULL）、请求状态、分块数、14 天的切片版本 id、取数时刻、是否复用。
- **测试**：第 2 节前提 4 的测试；`test_vh_regex_matches_pages_absent_from_C`（`[反例 22]`，请求构造层）；`test_vd_reused_until_slice_version_changes`；`test_vd_chunk_failure_descriptive`；`test_regex_overflow_counted`；`test_unobserved_stored_null`。
- **验收**：一轮夹具里三层数字、逐剧核对计数、正式与描述性标签都可由冻结输入复算。

### TR-24 资料页 trends、search 两个 tab（批次 2b-ii，2.5 人日，依赖 TR-10 的夹具、TR-11 的视图、TR-16、TR-25；设计 3.6、5.6、5.7、2.2）
- **目标**：资料页只读 `pick_obs` 视图就能展示两个通道、联动与横幅，不回查明细表。
- **文件**：见第 5 节前端部分；测试在 `ft/server/pick-board/`、`ft/app/`、`ft/components/workspace/pick-board/views/` 下，另扩展 `tests/e2e-pick/pick-data-board.spec.ts`。
- **规格**
  - 数据访问：`db.ts` 的 `makeScope` 加 `obsDb`（search_path 为 `pick_obs`）。观测专用错误类：`42P01` 显示「观测数据未就绪」，`42501` 显示「观测数据不可读」；`mirrorNoticeOf` 与 `MirrorNoticeKind` 同步扩展。
  - 请求：`request.ts` 的 TABS 加 `trends`、`search`；新 URL 参数 `obs` 钉住集合，照 `cleanVersion` 校验；`loadTab` 对这两个 tab 显式抛错，不能静默落进 default。`page.tsx` 加早分支进 `obs-route.tsx`（D9）。
  - 缓存：已发布集合的判定行与联动事实不可变，按集合 id 用 `remember()` 缓存；「当前集合」每次实时查；联动的可行动性每次请求用请求时刻重算（D13，`obs-link.ts` 与 Python 共用夹具）。
  - trends 视图：按身份 × geo 展示状态、中间量、按判定行里的块定义画的服务端 SVG 曲线（D39，遇 null 断开）、`window_end`、carried_over 与 stale；「歧义，不判定（设计如此）」单独计数；未覆盖单元名单；发现队列；影子集合带「影子」标记；「Data source: Google Trends」。**G2（2026-09-29）定为 `b_only`**：trends 视图只做「逐剧趋势未上线，发现队列已启用」的说明、发现队列与未覆盖清单，不画逐剧曲线（第 8.1 节）。
  - search 视图：三层覆盖常驻；按 market-map 分组，BGR 单列；描述性标签与正式标签分开，都带命中条件与原始计数；「暂定」「完整」标记；逐剧核对的计数（含复用）；质量注记；「未观测到」的措辞；可粘贴行（纯文本加选中复制）。
  - 详情页：两个通道并排；联动标签与可行动性；「全球同向」「时效不符」「不同市场信号」。横幅：`obs-banner-rules.ts` 是纯函数，用 `obs_status_cases.json` 与 Python 端对照。
  - 登记：`queries-surface` 的 `PAGE_ENTRY` 与 `INTERNAL` 加 `obsDb`；`pick-data-page.support` 的 `LOADERS` 登记新 loader；board_fixture 加 `pick_obs` 的视图与授权。
- **测试**
  - `request.*.test`；`db.test`（`obsDb` 以单查询只读事务执行）；`queries-obs.integration.test`（以 reader 身份跑，零 skip）；`pick-data-page.dom.test`（两个 tab 都能打开，镜像读不了时观测照样可看）。
  - `obs-banner-rules.test`、`obs-link.test`：结果与夹具一致。`obs-spark.test`：null 断开。`contracts.test`：没有无参 `Date.now()`，只用看板调色板，链接 `prefetch={false}`，只引用 TR-25 登记过的 client 组件。`obs-views.test`：没有禁用词（前提 1、2）。`[反例 26]`
  - e2e：在本机 QA 实例上跑 `pick-data-board.spec.ts`，扩展到两个新 tab，零 skip。
- **验收**：perf 预算数字不变；不进 PORTED_FROM，也不进 parity。

### TR-19 发现段（批次 2b-iii，1.5 人日，依赖闸门 B 通过、TR-18、TR-20）
- **目标**：实现设计 4.8。
- **文件**：`trends/discovery.py`、`trends/units.py` 的种子单元；`t/observe/test_discovery.py`。
- **规格**
  - 种子：设计 4.8 那 8 个，查 WW、US、GB 的网页搜索，另加 4 组本地化种子；YouTube 属性到稳定期再加；种子清单先经用户审（U6）。
  - 抽取：去掉种子词、平台词、意图词后规范化，在同语种共享池里精确匹配；短名和泛词要带平台词或意图词作上下文；命中照样过歧义判定。
  - 分流：唯一匹配且剧名清楚的进 A 档 14 天；其余进发现队列；池外命中只在资料页显示，按（语种，规范化剧名）记键供入池关联（D37）。写入经 TR-20 的 `store`。
  - **G2（2026-09-29）定为 `b_only`**：没有 A 档，池内唯一匹配进 `queue`，池外命中仍是 `display_only`；集合的规则引用、`a_tier_coverage` 与发布条件按第 8.1 节的交接项在 G4 之前定。
- **测试**：`test_reelshort_stay_goes_through_ambiguity`；`test_youtube_hit_discovery_only`；`test_out_of_pool_never_agent`；`test_breakout_representation`（按阶段 0 的真实夹具解析）；`test_out_of_pool_key_recorded`。
- **验收**：用阶段 0 的真实 relatedsearches 夹具跑出发现队列，唯一匹配的才进 A 档。闸门 B 不过时本任务取消（第 8 节）。

### TR-23b GSC 集合发布、提示、里程碑、可粘贴行与端到端（批次 2b-iii，1.25 人日，依赖 TR-20、TR-23a）
- **目标**：实现设计 6.2 的 GSC 提示与里程碑、2.2 的可粘贴行、7.1 的 GSC 集合冻结；把 D43 的别名刷新接进 gsc 轮次；用真实 runner 做端到端。
- **文件**：`gsc/{publish,milestones,editorial}.py`、`gsc/run.py`（接线）、`admin/cmd_import_editorial.py`；`t/observe/test_gsc_publish.py`、`test_gsc_e2e.py`。
- **规格**
  - 轮次第 0 步：`alias.refresh()`（D43）。
  - 发布经 TR-20 的 `store`：冻结 D36 列出的 GSC 全部输入；联动物化同模式配对；没有导入旧页快照时不发布，写 `legacy_snapshot_missing`。
  - GSC 提示：曝光飙升、从零起量、7 天 rising 的正式标签写进提示表（D37）。
  - 里程碑：每轮读当前共享批次，把变化增量写进里程碑表。事件：进入 v1 候选池、首次榜单证据、GSC 越过「从零起量」或 rising、`promoters_cnt` 上升（读 `pick_mirror.series`）、进入编辑精选表（取自导入的精选历史，D33）、池外发现入池（D37）。上线当天给每个身份记一条「上线前已存在」。
  - 可粘贴行冻结进判定行：剧名、现行剧目页完整 URL（取自正典新页）、展示与点击（注明窗口）、主要查询词、核实日期（取集合日期）、备注（规则版本、Trends 状态与 geo）。本站没有现行剧目页的剧不生成。
- **测试**
  - 端到端（空库、真实 runner、假 transport、时钟加速，**测试里不预塞 A″、Vd 或判定行**）：`test_seven_day_formal_label_from_empty_db`（含一轮 E 修订后 Vd 重取）；每个 GSC 反例一轮夹具：`test_e2e_counterexample_5`、`_16`、`_22`、`_26`、`_27`、`_28`，接线错了会变红。
  - `test_takeover_before_gsc_publish`（`[反例 10]`）；`test_publish_refused_without_legacy_snapshot`；`test_gsc_alerts_written`；`test_editorial_milestone_from_history`；`test_milestone_baseline_day1`；`test_three_layers_present`；`test_frozen_inputs_complete`（集合的 `frozen_inputs_json` 含 D36 的每一项）。
  - `test_paste_row_only_reelshort_with_page`；`test_paste_row_url_contract`（URL 形如 `/<locale>/drama/<slug>-<book_id>`，满足 RealShort `editorial-sheet.ts:108-121` 的解析）。
- **验收**：端到端测试两种库都绿；集合里的三层数字、逐剧核对汇总、正式与描述性标签都可由冻结输入复算。

### TR-26 ObsPin、冻结、读取与回放（批次 3，1.5 人日，依赖 TR-11、TR-20、TR-23b；设计 7.1、7.3）
- **目标**：一次运行里同时钉住剧库批次与观测集合，候选集冻结后永不重算；两条钉住路径都带上观测集合。
- **文件**：`gp/{pin,context,repository,selection,tools}.py`（`tools.py` 只改 `_pin_latest`）、`gp/observe/read.py`；`t/test_obs_pin.py`、`t/test_obs_frozen.py`、`t/test_obs_replay.py`。
- **规格**
  - `ObsPin(trends_set_id, gsc_set_id)`：在 `pin_statement` 里加两个标量子查询，只取 live 集合；`mode` 参数只供 TR-36 的命令。两种库都支持，不放进 PG 分支。
  - `current_pin()` 不改；另加 `current_pins()`。`PickTask` 新增字段 `obs_pin`、`trend_checked`（本任务拥有 `context.py` 的这两处改动）。首次钉住（`PickTask.repository`）与 `use_latest` 重钉（`tools._pin_latest`）两个调用点都改用 `current_pins()`，四元组不动。
  - 带观测条件时，`query_with_record` 写三个新列；`obs_as_of_json` 按 D28 存 `observations` 等；`result_view` 只在它存在时加 `observations`。观测时点绝不写进 `data_as_of_json`。
  - 版本：`RANKERS` 分发表（D29）；`_parent_versions` 按分发表校验，查不到就拒绝；父结果没有集合、本次却带趋势条件，拒绝；父集合已被清理，报「已过保留期」。
  - 跨批次映射：集合的来源剧库批次与候选集的剧库批次相同就直接用；不同时用 `map_identities`（冻结的别名版本），映射不到的标 `set_batch_mismatch`。映射结果写进候选证据；回放时对全部匹配行用同一个纯函数按冻结版本重算，结果与当时一致。
  - 回放：`SelectionService.replay` 在异步侧先取好判定行与联动行，再传进 `to_thread`；集合已清理时抛 `ReplayGone`（410），不是 409；`ranking_reproducible` 认 obs-v1。
  - 只对共享剧库：钉住的是个人批次时拒绝并提示。
- **测试**
  - `test_frozen_under_upgrades`：原始数据、规则、别名、联动规则分别升级后，旧候选、换一批、计数、详情都不变。`[反例 9]` `test_ranking_dispatch_uses_recorded_version`：测试里注册一个行为不同的 `obs-vT`，换一批按记录的版本执行；删掉该版本后拒绝。`test_link_rows_read_for_pinned_pair`：link-rules 换成行为不同的测试版本后，旧卡的联动证据不变。
  - `test_current_pin_unchanged`（`test_mirror_pin` 原样通过）；`test_use_latest_repins_obs_atomically`：`use_latest` 之后剧库批次与观测集合来自同一条语句。
  - `test_personal_batch_rejected`；`test_parent_without_set_rejected`；`test_parent_set_pruned_message`；`test_obs_not_in_data_as_of`；`test_observations_in_result_view_frozen`（`test_mirror_frozen` 的 `query == result_view(record)` 仍成立）。
  - `test_replay_obs`：回放 obs 结果不返回 409；`test_replay_mapping_deterministic`；`test_replay_set_pruned_410`。
- **验收**：两种库绿；`test_mirror_pin`、`test_mirror_frozen`、`test_mirror_replay` 原样通过。

### TR-27 条件、筛选、obs-v1 排序与证据（批次 3，1.75 人日，依赖 TR-26、TR-16 已上线）
- **目标**：实现设计 7.3、7.4 的条件、排序与证据，第 9 节 #4 的默认用法，D11 的 schema 开关，D31。
- **文件**：`gp/{contracts,selection,tools}.py`；`t/test_obs_conditions.py`、`t/test_tools.py`、`t/test_frontend_contract.py`；重新生成 `ft/core/pick/fixtures/backend-result-obs.json`。
- **规格**
  - `PickConditionsObs(PickConditions)` 加七个字段，description 就是给模型的提示；`filters_obs` 属性。`conditions_json` 与 `_request_hash` 用同一个剔除口径：未设置的观测字段去掉。
  - `tools.py` 在导入时按 `PICK_OBS_AGENT` 选用条件模型（D11）；关着时带观测条件的存量卡片换一批返回「趋势条件尚未开放」。
  - `_row_matches` 按身份到状态的映射过滤，状态不写进 row dict；`count` 同样生效；`unmappable_conditions` 的顺序与 TR-33 合同一致。
  - obs-v1 排序（在 `RANKERS`）：先比最好的状态（confirmed > first > 只有 GSC 正式标签），再比命中国家数，再比最新的 `latest_block_end` 或 GSC 截止，最后比 identity。
  - `candidate_item` 追加 obs 证据条目（D8）：每条候选最多 6 条，每次新建 dict，总数受 50 上限约束，超出时先保 obs 条目；未观测按前提 1 表示。联动读钉住那一对的物化事实行，可行动性按查询时刻计算并物化进证据（D13）。
  - 资格用 TR-10 的 `agent_eligibility`；被排除的按原因计数进 `observations`。
  - 状态码：没有 live 集合时「趋势数据尚未就绪」；Trends 集合超过 26 小时时「数据陈旧」，不给加码建议，也不返回空结果。
  - `trend_checked` 在三种情况下置真：带观测条件的查询；带观测条件的计数；读到含 obs 证据的条目详情。工具 docstring 列出新参数（只在开关打开的变体里）。
- **测试**
  - `test_tools.py`：`additionalProperties is False`。`test_tool_schema_unchanged_when_off`：开关关着时 args schema 与改动前的快照逐字相同；打开时含七个字段。`test_request_hash_stable_without_obs`：不带观测条件时哈希与改动前的夹具相同。
  - `test_default_eligibility`：B 档、shared_title、emerging、未确认的都被排除，并各自计数。`[反例 21]` `test_presumed_requires_flag`；`test_gsc_only_formal_eligible`；`test_cooling_blocks_country`（`[反例 4]`）；`test_stale_notice`；`test_evidence_null_not_zero`；`test_detail_obs_sets_trend_checked`。
  - `test_frontend_contract`：重新生成带观测条件的夹具，并用 TR-33 的合同校验；`NEW_COLUMNS` 加三列；新增 `/selections` 快照路径的测试。回滚矩阵的后端格（第 10 节）。`[反例 14]`
- **验收**：两种库绿；前端合同测试用重新生成的夹具仍绿。

### TR-28 提示词、check_answer 与开关（批次 3，0.75 人日，依赖 TR-27）
- **目标**：实现设计 7.4 的提示词与核对，对应 7.5 第 5 步；核对从工具一路接到 middleware（D42）。
- **文件**：`gp/{middleware,answer_check}.py`、`skills/public/pick-drama/SKILL.md`；`t/test_answer_check.py`、`t/test_middleware_obs.py`。
- **规格**
  - `PICK_INSTRUCTIONS` 在 `PICK_OBS_AGENT=1` 时追加设计 7.4 的四句，外加一句「说明观测集合的截至 T」；措辞与「不使用外部搜索」区分开；v1 `gsc` 信号的口径按 S4 的核实结果写。SKILL.md 同步改。
  - `check_answer` 加必填关键字参数 `trend_checked`；`_record_checks` 传 `task.trend_checked`。触发词只认「谷歌/Google/搜索（趋势|热度）」「站外（热度|趋势）」，复用 `_claims` 与否定前缀。
- **测试**：`test_answer_check_trend`（命中触发词且没查过时给出笔记；否定句不触发；榜单回答不误报；样本加进 `t/test_answer_check.py:34-57`）；`test_record_checks_passes_trend_checked`：走真实的 `_record_checks`，工具置真后笔记消失，未置真时出现；`test_check_answer_requires_trend_checked`：漏传参数抛 TypeError；`test_prompt_gated`。
- **验收**：现有回答样本零新增误报；开关关着时提示词逐字不变。

### TR-36 影子全链路验收（批次 3，0.5 人日，依赖 TR-27）
- **目标**：打开任何开关之前，在生产 shadow 数据上走通「两个 shadow 集合 → 联动 → 候选 → 严格前端解析」（D40）。
- **文件**：`admin/cmd_shadow_e2e.py`；`scripts/pick-validate-result.ts`；`t/observe/test_shadow_e2e.py`。
- **规格**：命令在 gateway 容器里经 `railway ssh` 执行，用 `ObsPin(mode="shadow")` 钉最新的一对 shadow 集合，对六组固定观测条件各做一次不落库的查询（不调 `add_result`），把结果 JSON 写到标准输出；本机用 `pick-validate-result.ts` 以前端 strict schema 校验。输出存 artifacts，不进仓库。
- **测试**：`test_shadow_mode_not_reachable_from_tools`（工具与路由都传不了 mode）；`test_shadow_e2e_dry_run_writes_nothing`；`test_shadow_e2e_output_matches_contract`。
- **验收**：S11 之后在生产 shadow 数据上跑通并过严格解析，是 S12a、S12b 的门槛之一。

### TR-29 手册（跨批次，0.5 人日）
- `docs/pick-workbench/observe-runbook/` 的索引与跨主题部分：两个服务的配置与变量名；自检；reset 命令；金丝雀汇总 SQL；回滚规则、最低可回滚版本与逐格回滚矩阵；两个开关；旧页快照与精选历史导入；regrant；部署守卫；新迁移上线流程（D5：同步重部署 gateway 与两个 cron）；连接账演练；提前量周复盘；设计里的行号更正。各任务写自己的主题文件（D35）。
- 同步更新 `supabase.md` 与 `progress.md`。
- **验收**：手册里每条命令都在本机或测试库上跑过一次；文中数字与计数测试一致（`test_pan_runbook_text` 的写法）。

### TR-30 金丝雀执行、汇总与一次修复重跑（S7 之后，1.5 人日）
- `admin canary-report`：每天把汇总存进 artifacts 目录（Railway 日志只留 7 天）。按 target_date 汇总：成功率、熔断次数、验证码与熄火、新鲜率；首次限流前的累计请求数与时刻；出口 IP 分布（D20 开启时）；userType。
- 判定：阶段 1 与阶段 2 各自的通过条件（第 9 节）；出现终止条件就停；允许一次修复后重跑（半速 3 天）；切换到后备的触发条件单独列出，交用户定。
- **测试**：`test_canary_report_metrics`：在夹具库上算出的成功率、新鲜率、首次限流位置与手算一致；`test_report_groups_by_target_date`。
- **验收**：两个阶段各有一份汇总与结论，存进 artifacts；经 G5 审过。

### TR-31 影子运行、抽检与编辑精选回归（批次 3 并行，1.0 人日）
- `admin shadow-sample`：三个日期各抽 confirmed 30 个，ambiguous、sparse、未覆盖、未命中各 15 个，交人工标注（U10）；另抽身份证据等级；GSC 正式标签单独分层。
- 编辑精选 12 条：逐条记录 v2 给出的结果，允许「推断证据待核实」「歧义，不判定」。
- GSC 阈值按国家出报告：命中数、小基数占比、误报与漏报；回标 τ。
- 连接峰值实测；提前量周报 `leadtime-report`（只用 live 提示，D37）。
- **测试**：`test_shadow_sample_strata`：各层样本数正确，固定种子可复现；层内不够时如实报不足，不跨层补。
- **验收**：三个日期的样本都已人工标注，报告经 G5 审过。

### TR-32 RealShort：build.py 生成 id 时去掉括号备注（可选，0.25 人日）
- 只在 TR-18 的 auto 别名规则上生产之后合并，这样切换那一天会自动续接。
- 测试：新 id 不含括号备注；旧 id 与新 id 能被 auto 规则连上。

### 0008 预留（0.5 人日）
- 批次 2b 或 3 若发现 0007 之外还缺 schema，只能新开 0008（只加表与可空列）。上线流程：TR-34 守卫 → gateway → 两个 cron 同步重部署（D5）。

---

## 8. 阶段 0 的四种去向 → 执行分支

TR-05 报告经 G2 定去向；下表是预先定好的分支，G2 只选一行，不再临时设计。

| 结论 | Trends 侧任务 | GSC 侧 | 开关与门槛 | 人日变化 |
|---|---|---|---|---|
| A、B 都过 | 全部照做 | 照做 | S12a（GSC）与 S12b（Trends）各自门槛见第 10 节 | 0 |
| 只过 A | TR-19 取消；TR-18 的歧义第三道输出 `manual_required`，由资料页歧义队列与 `ambiguity_override` 处理；金丝雀与稳定期不混 relatedsearches | 照做 | S12b 门槛不变 | −1.5 |
| 只过 B | 逐剧轮询降为旁证：Trends 采集只跑种子、发现队列与对照序列，不跑 A/B 清单；TR-17 取消；TR-18 只做发现命中所需的歧义与别名部分（−1.0）；TR-19 照做；智能体侧 `trend_state`、`trend_include_first`、`trend_include_presumed` 不开放，`link_state` 只剩 `site_only`；资料页 trends tab 只显示发现队列 | 照做；腾出的约 2 人日投 GSC 阈值回标（TR-31 加量） | S12b 门槛改为「金丝雀阶段 2 达标 + 发现段影子抽检」 | −2.0（TR-17 −1.0、TR-18 −1.0），GSC +2.0 |
| 都不过 | Trends 采集不上线：取消 TR-17、TR-19，TR-20 只保留通用 `store.py`（−0.5），TR-18 只保留别名（−1.0），S5–S7、S10、TR-30 不做；资料页 trends tab 只显示「未上线，保留人工对照入口」的说明；智能体侧不开放任何 trend_* 字段 | 照做 | 只有 S12a；S12b 不执行 | 约 −5.5 |

- 选 D 或 H+D 时：TR-17 按报告的数据合同变更单实现，联动时效与前端曲线按 D39 的锚点，不改 TR-10、TR-24 的接口；选 H+D 时 TR-17 +0.5，A 档单元减半，TR-18 的分配随之调整。

### 8.1 G2 决定（阶段 0 部分，2026-09-29 用户拍板）

**结果**：去向「只过 B」（`b_only`），颗粒度 `H`，节奏 `user`。Railway 变量 `PICK_OBS_TRENDS_ROUTE=b_only`、`PICK_OBS_TRENDS_GRANULARITY=H` 显式写入，`PICK_OBS_TRENDS_PACE` 不设（即 `user`）；在下一次经守卫的 cron 切换（`packaging.md` 第 5 节第 4 步）之前改，金丝雀期间都不改。U5 的「用户确认颗粒度」由本次拍板完成；「浏览器导出 3 个词的 CSV 做形状比对」经用户批准豁免到 S12b 之前补，不挡 S7–S11。

**性质**：阶段 0 报告里闸门 A 小时级不过、日级**未定**（已观测正对照 7 部，不到 8 部），闸门 B 过。日级维持「未定」，不改判、不下调 N；`b_only` 是用户批准的对未定分支的显式处置：现有样本撑不起逐剧路线，不再为闸门 A 补样本。它不是判定器自动得出的，以后引用时不要写成「A 不过」。

**依据**（数字经 codex 对原始数据与代码复核）
- 闸门 A：小时级正对照 1/8 可见、区域对照 0/9；日级正对照 2/7（N=12），未观测 8 部中 7 部 `no_data`、1 部限流，只补那 1 部最好 3/8。区域对照（四国近期新剧）两种颗粒度都 0 部可见；本次测过的大盘短语只有 US `short drama` 可用；剧名相关查询这次全是空列表。
- 闸门 B：相关查询可用 7/7、6/7，种子拿到列表 2/2、1/2，explore 80 次全部 `USER_TYPE_SCRAPER`；这些相关查询按 `now 7-d` 取。判据之外的风险：种子列表里像剧名的很少（第一份 reelshort-US 23 条热门只有 1 条），发现段产出可能很薄。
- 颗粒度取 H 不取 D：闸门 B 的证据在 H 上；`run.py` 的 `window_end`（整点减 3 小时）就是 H 的口径，D 要先出数据合同变更单、按颗粒度分 `window_end`、定义 TR-30 的日级新鲜率并再审；b_only 不做逐剧判定，D 的可见度优势用不上。
- 节奏维持 `user`：第 1 天 `design` 在第 56 个请求首次 429，第 2 天 `user` 在第 17 个、又在第 28 个；放慢没有推迟 429，也没有证据表明加快更安全。按真实容量模型重放第 2 天的两次熔断，canary1 在 `user` 下覆盖 86.8%（`design` 97.8%），canary2、stable 都在 98% 以上；但两次熔断的一晚在金丝雀任一阶段都不达标，容量放得下不等于能通过。

**任务与步骤的变化**（上表「只过 B」一行，加下列细化）
- TR-17 取消；第 14.3 节里负责人是 TR-17 的处置（如「分层 A（窗口间隔）」）随之不再执行。TR-18 只做发现命中所需的歧义与别名，相关查询照常分类，不因这次为空就硬编码成 `unresolved`。TR-19、TR-20 照做。TR-31 加 GSC 阈值回标约 2 人日。
- **TR-19、TR-20、TR-24 的交接项**（b_only 下合同尚未定义，G4 之前写进各自规格）：① 合同要求 Trends 集合带 `trend-rules-*` 规则引用（`contract_views.py`），TR-17 取消后发现集合挂什么规则版本；② `SetSummaryTrends.a_tier_coverage` 必填，没有 A 档时怎么表示，「A 档覆盖率低于 80% 不发布」换成什么发布条件；③ `DiscoveryRoute` 在 b_only 下没有 `a_tier`：池内唯一匹配进 `queue`，池外命中仍是 `display_only`，两者不合并；④ shadow/live 标识照旧；⑤ TR-24 的 trends 视图写「逐剧趋势未上线，发现队列已启用」，只列发现队列与未覆盖清单，search 视图不变。
- S7：前提不变。它只验证采集机制（出口、限速、预算、租约）；金丝雀没有种子负载，不能替代发现段验收。代码里 ROUTE 对金丝雀只决定混不混 relatedsearches，`both` 与 `b_only` 行为相同，所以金丝雀的请求形态与 S6 核对过的一致。市场对照词表按 `H` 冻结：小样本验证要另经用户批准，没批准就按现有 `canary_controls.json` 冻结并记下版本（第 14.3 节两路的 B）。
- S10 的 TR-17 换成 TR-19；S12b 按上表改门槛，TR-36、G5 保留，U5 的 CSV 比对在它之前补；S13 只开放上表允许的字段，G6 与 M1 回滚下限保留（第 10 节各行已注明）。

**后续（不在本次 G2 解决）**
- 金丝雀若限流：TR-30 写的「修复后半速重跑 3 天」没有比 `user` 更慢的预设，到时另开任务；失败就停在该关口，改过参数的运行不接着累计原周期（第 9 节）。
- 容量模型的 `LIMIT_AT=56` 只建一次熔断，第 2 天实况是第 17、28 个；TR-30 首晚校准回答耗时时一并改。
- 发现段产出：G4 只审 TR-19 的真实夹具与接线；影子第一周统计同语种池内唯一匹配的发现命中，放在 S12b/G5 之前，产出门槛到时另请用户定。
- 金丝雀以逐剧单元为主，b_only 稳定期主要是种子与大盘。若金丝雀因量被限，转后备之前先考虑把金丝雀改成 b_only 的实际形状（另开任务，重新数验收）。
- G2 的 GSC 实测一项等 U1/U2 之后单独交审；RealShort 零改动方案已于 2026-09-25 随 TR-08 审过。

决定稿（证据表、容量重放、选项对比、codex 审查处置）在 `~/.gstack/projects/ggwork-deerflow/artifacts/trends-stage0/trends-g2-decision-2026-09-29.md`，与阶段 0 报告同目录，不进仓库。

### 8.2 范围缩减为简化版（2026-09-30 用户拍板）

**结果**：用户认为本计划相对需求做重了，要求缩减。缩减后的范围、工作项与上线顺序在 [简化版范围](2026-09-30-trends-radar-simplified-scope.md)，以它为准。一句话：每晚取我们自己数据里最热的 100 部剧，逐部查 Google Trends 的日级走势，资料页 trends 页签出一张只读参考表；不进智能体，不改候选排序。

**对 8.1 的改动**
- 去向由 `b_only` 改为逐剧核对，颗粒度由 `H` 改为 `D`，节奏 `user` 不变。切到新任务来源的那次部署一起改变量：`PICK_OBS_TRENDS_MODE=stable`、`PICK_OBS_TRENDS_ROUTE=a_only`、`PICK_OBS_TRENDS_GRANULARITY=D`。在那之前，金丝雀照 8.1 的变量跑。
- 闸门 A 的日级结论仍是「未定」，本节不改判。闸门 A 的门槛是给自动判定定的；简化版只出参考表，不自动判定、不发布集合，用户在知道「已观测 7 部里可见 2 部」的前提下批准上线。以后引用时不要写成「A 已过」。
- 8.1 说选 `D` 要先出数据合同变更单，指的是集合与判定行里的块定义。简化版不写集合与判定行，只读原始曲线，所以只需要把 `window_end` 按颗粒度分开（简化版范围第 6 节第 2 项）。

**搁置**（不删，文字保留作记录；以后要做再单独立项）
- 任务：TR-17、TR-18、TR-19、TR-20、TR-21、TR-22、TR-23a、TR-23b、TR-26、TR-27、TR-28、TR-31、TR-36；TR-24、TR-25 已在任务分支写好的只读入口留用横幅与页签外壳，trends 页签内容按简化版替换，search 页签先隐藏；TR-25 的写接口部分（任务分支 `feat/trends-radar-tr-25-24` 里拆成 TR-25b）搁置。
- 上线步骤：S8 到 S13。
- 关口：G4、G5、G6 随所属任务搁置。
- 用户待办：U1、U2（GSC 服务账号）随 GSC 一线搁置；U5 的 CSV 形状比对、U10 的影子标注不再需要；U9（申请官方 Trends API）仍建议做，出口被拦时它是后备之一。
- 8.1 里 TR-19、TR-20、TR-24 的五项合同交接随之搁置。

**保留**
- 已上线的 S0 到 S6，以及批次 0 到 2a 的代码。
- S7 缩成「canary1 验证出口」，通过与停止标准见简化版范围第 10 节；TR-30 缩成这次验证的小结。
- 回滚规则、部署守卫、部署时段（第 10 节，`packaging.md` 第 7 节）不变。简化版不加数据库迁移，生产迁移头仍是 0007。

**新工作项**：简化版范围第 6 节第 1 到 9 项，粗估 6 到 8 人日。

---

## 9. 金丝雀：推荐走 0007 路线

**推荐**：金丝雀的持久状态放进数据库，也就是 0007 的 `ggwp_obs_runtime` 与 `ggwp_obs_budget`。0007、角色与打包提前到批次 1 与 2a，金丝雀在批次 2a 部署后立即开跑，与批次 2b、3 的开发并行。不挂卷。

**理由**
1. P2 已上线，设计里「等 0007 就要等 P2」的阻塞没有了。0007 只加表和可空列，可以先于任何读它的智能体代码上生产。
2. 金丝雀要验证的正是生产用的那套机制：租约代次、预算发出即扣、熔断状态持久、请求日志。用文件状态跑完金丝雀，换成数据库后这些路径得重新验证。
3. 证据留存：`ggwp_obs_requests` 保留 35 天，比 Railway 日志的 7 天长，汇总可以直接用 SQL 算。
4. cron 服务能不能挂卷没有核实；挂卷的服务一服务只能一个卷，重新部署还有停机。
5. 文件型状态（TR-04）仍然要写，只用于本机的阶段 0，以及 0007 被卡住时的后备。后备启用的条件：批次 2a 完成后 5 个工作日内 0007 仍上不了生产。启用前先实测 cron 能否挂卷。

**模式参数**（G3 重排。生产节奏 `user`；01:45 截止与 D23 不变：D23 那一行写的「20:30 到次日 01:45」是 G3 之前 stable 的起跑，起跑提前之后，从起跑到次日 01:45 的会话照样同属一个 target_date；金丝雀起跑不早于 18:00 UTC）

| 模式 | 起跑 | 窗口 | 计划 | 上限 | 容量估算：无熔断 / 一次 429 / 一次 429 + 午夜后崩溃 |
|---|---|---|---|---|---|
| canary1 | 21:00 | 285 分钟 | 220 | 220 | 127 分钟 100% / 230 分钟 98.9% / 250 分钟 98.9% |
| canary2 | 18:30 | 435 分钟 | 约 300 | 450 | 175 分钟 100% / 321 分钟 99.2% / 同左（午夜前跑完） |
| stable（暂定） | 17:30 | 495 分钟 | ≤350 | 525 | 206 分钟 100% / 372 分钟 99.3% / 同左 |

- **容量检查**（`trends/capacity.py`）取代原来的「计划 ÷ 2.9 + 40 分钟」。原检查只算了一次熔断暂停，没算暂停之后当天余下的半速，也只认一个节奏。现在按节奏逐个请求重放一晚：真实的限速器、熔断器与预算，随机间隔都取上限，每个回答 2 秒，每个单元的完成时刻再加 10 分钟余量。三种夜晚：(a) 无熔断；(b) 第 56 个请求 429（阶段 0 第一天的位置），暂停 30 分钟，探针正常，当天余下半速；(c) 同 (b)，而且午夜后第一个请求在外时进程崩溃，租约失效后的下一次半小时触发续跑，那个单元重做。模式的最大计划要满足 (a) 覆盖 100%、(b) 覆盖 ≥95%，否则配置校验以 2 拒跑（每次触发与 `--selfcheck-only` 都校验）；(c) 只报告。表中分钟是起跑到最后一个单元完成（含余量），百分比是覆盖的单元。`test_trends_capacity.py` 用真实的入口与执行器（假时钟、MockTransport）跑同样的三种夜晚，每种夜晚都钉住估算覆盖不多于、完成不早于真跑。估算偏保守只靠这 10 分钟余量：`user` 节奏下快慢由补桶速度决定，回答耗时与间隔取上限几乎不起作用（两者都换到另一头，估算变化不到 1 分钟）；不加余量时，按模式的标准计划重放的限速夜晚比真跑早 1–2 分钟完成（canary1 一次 429：219.8 对 220.5 分钟，加崩溃：240.2 对 242.1 分钟）。重放把 429 打中的单元算作丢失，执行器则保留它在 429 之前拿到的序列，所以估算的覆盖率偏低。这是**每个回答 2 秒的基准估算**，只在所测的回答耗时与三种夜晚里不偏早：G3 复核用真实执行器把每个回答改成 29 秒，canary1 无熔断实际约 177.5 分钟、估算仍是 127 分钟，一次 429 实际约 249.9 分钟、估算 230 分钟（两者都仍在窗口内）。10 分钟余量不是普遍上界，首晚要记下实际回答耗时与截止余量，据此校准（TR-30）。
- **节奏**：`PICK_OBS_TRENDS_PACE`，默认 `user`（令牌桶 4、每分钟补 2，阶段 0 第二天的节奏）；`design`（桶 8、每分钟补 4）是设计 4.2 的原值。阶段 0 的 `--pace` 与生产读同一份预设（`pacing.PRESETS`）。取值不认识以 2 拒跑。每个批次在 `plan_json.notes.pace` 记下这一晚的节奏（预设名、桶容量、每分钟补充数），`status` 的 plan 行与 `preflight` 都显示，TR-30 按库逐晚核对节奏没变。
- **canary1** 计划不变，只把起跑从 22:00 提前到 21:00：22:00 起 (b) 覆盖 95.6%，刚过线，(c) 只剩 85.7%。
- **canary2** 减到约 300、18:30 起跑、上限为计划的 1.5 倍：原来 22:00 起 430，在 `user` 节奏下 (a) 只覆盖 88.6%、(b) 只有 47%。18:30 起按 (a)(b) 最多能排约 415（415 时 (b) 96.1%，420 时 94.4% 不过）；若 (c) 也要 ≥95%，约 390（390 时 (c) 96.4%，400 时 93.6%）。取 300 由模拟确认：`test_simulated_canary2_night` 289 个请求，2 小时 47 分跑完。
- **stable** 暂定 17:30 起跑、≤350、上限 525（计划的 1.5 倍），容量检查放得进（17:30 起按 (a)(b) 最多约 480）。**TR-18 接上任务来源、G4 定稳定期参数时重定**；原来 20:30 起 650 在 `user` 节奏下 (a) 只覆盖 81%。
- 上限是熔断与重试之后的硬顶，不是计划量；按上限排期会让一次熔断就截断剩余单元，与「新鲜率 ≥95%」「允许熔断 1 次」自相矛盾，所以计划量低于上限。
- **cron** 改为 `*/30 17-23,0-1 * * *`，最早一次 17:00 覆盖最早的起跑（stable 17:30）；早于模式起跑的触发在程序里退出 0。部署时段相应改为 UTC 02:00–17:00（`packaging.md` 第 7 节，`test_cron_schedule_fits_the_session_modes` 钉住）。
- **负载闸门**（`trends/admission.py`）：金丝雀每晚建批次之前，截断到计划之后的计划请求不少于当天计划量的 80%，匹配到的正对照不少于清单正对照的一半；不满足就在任何请求之前以 2 拒跑，当天的拒跑行写 `not_published_low_coverage`（复用合同已有的码）与概览，这一天**不算金丝雀日**，不计入三天或七天。被拒过的一晚，补齐之后由同一晚稍后的触发接管开跑的，批次的 `plan_json.notes.late_admission` 记 `true`：它的起跑与 `window_end` 都变了（`window_end` 按接管那次触发的整点算），窗口也短了，同样不计入三天或七天；按时建批次的记 `false`。`status` 显示每个批次的计划概览、缺的对照（个数与前 5 个 identity）、节奏与 `late_admission`。S6 与 S7 之间的 `preflight`（只读、零 HTTP，S6a）随 S6 的自检部署一起跑：自检配置的启动命令先 `--selfcheck-only`，通过了才 `preflight`，同一次部署的日志里先后是这两行，部署的退出码是 `preflight` 的（`packaging.md` 第 3.2 节、第 6 节）。不另开命令：cron 服务平时没有在跑的容器可以 `railway ssh`，在本机 `railway run` 又要把状态密钥带到本机。
- 通过条件照设计 4.11：阶段 1 三天没有任何限流信号；阶段 2 七天成功率 ≥98%、熔断 ≤1 次、验证码与熄火为 0、新鲜率 ≥95%。**终止**：出现一次验证码或同意页立即终止；其他原因（限流、熔断次数、探针失败）熄火累计 2 天终止。判定只读已提交的预算行，起跑前与收尾用同一条规则，收尾前崩溃也照样成立。

**时间线（日历日，以批次 0 开工为第 1 天；假设最多 4 个实现代理并行、1 个整合者，用户每个 U 步骤在 1 个工作日内响应）**
- 第 1 天：批次 0；用户做 U1、U2。
- 第 2–4 天：批次 1a；第 3 天 TR-07 实测与导出。
- 第 4–6 天：批次 1b；阶段 0 在本机跑第 4–5 天，报告第 6 天出，G2 第 6–7 天。
- 第 6–9 天：批次 2a；G3 第 9–10 天；上线步骤 S0–S7 第 10–11 天。
- 金丝雀阶段 1 第 11–13 天，每天约 220 次；阶段 2 第 14–20 天，计划约 300、上限 450；需要修复重跑时再加 3 天；负载闸门拒跑的日子不算，顺延。
- 期间不换任务来源和参数，这样结论可比。节奏、起跑时刻与负载都算参数，中途改了就从改后的第一天重新数验收。金丝雀结束后才换成 `WatchTaskSource`（TR-18），并切到 stable 模式。
- 金丝雀阶段 1 21:00 起跑，`window_end` 为 D−1 18:00；阶段 2 18:30 起跑，D−1 15:00；稳定期暂定 17:30 起跑，D−1 14:00（设计 6.3 原写 22:00、20:30，G3 因生产节奏提前）。换起跑时刻的第一天，相邻两天 `window_end` 的间隔是 21 小时（阶段 1 → 2）与 23 小时（阶段 2 → 稳定期），仍在设计 4.9 第 6 条确认要求的 20–28 小时之内；以后重定 stable 起跑时，前后相差超过 4 小时，换后第一天只能记 `first`。

---

## 10. 部署与上线步骤

步骤编号 S0–S13（S6 与 S7 之间另有 S6a），与第 3 节的实现决策 D1–D43 区分。「对外」指改动生产库、生产服务或共享远端，每一步执行前都要用户明确同意。gateway、cron、前端的每次部署都先跑 TR-34 守卫，输出行记进 progress.md。部署来源一律是经守卫核验的 `ggwork/main`：守卫默认远端 `ggwork`，URL 必须指向共享仓库；本检出的 `origin` 是上游 DeerFlow，不是部署来源（`deploy-guard.md`）。守卫只保证来源，合同能力另由回滚规则里的四格验证保证。

| 步 | 内容 | 对外 | 谁 |
|---|---|---|---|
| S0 | **合并协调与部署冻结**：0007、models、TR-12 的授权与 D15 的发布授权、TR-34 守卫合进 `feat/trends-radar`，再按仓库现行做法合进 ggwork main。U16：用户转告另一会话 rebase，并约定 S0 到 S4 期间暂停 gateway 部署；双方确认共同 SHA。原因：库升到 0007 之后，不带 0007 的镜像会因迁移头领先而让扩展加载失败，宿主只记日志、`/health/ready` 照样通过，故障是静默的。**合并 main 之后必须重新刷新托管副本**（D21 的 `deerflow extensions upgrade`，只应改 `backend/extensions/sources/ggwork-pick/**` 与 `backend/uv.lock`），并在最终共同 SHA 上重跑：扩展完整套件（两种库，含 `test_managed_copy` 与守卫自己的测试）、前端 `pnpm test` 与 `pnpm typecheck`、守卫的 lint；守卫本身的核对在 S1（前端模式）与 S3（gateway 模式，S2 建好 observer 之后才读得到生产迁移头）照常在这个 SHA 或之后的 main 上跑。`c095fe4` 或任何合并前提交上的结果都不能替代合并后的证据。前提：第 14.3 节标「S0 前」的 G3 处置已合入 `feat/trends-radar` | 是（共享远端） | 代理执行，推送前经用户同意；用户转告 |
| S1 | 前端 TR-16 发 Vercel 生产，对应 7.5 第 1 步。用守卫的前端模式从 `ggwork/main`（含两个会话的提交）`git archive` 出的干净目录部署；部署前在同一提交上跑合同能力检查的前端四格（回滚规则）。前提：S0 在最终共同 SHA 上的重跑已经通过 | 是 | 用户批准后执行 |
| S2 | 在生产库执行 `bootstrap-observer.sql`，用 `\password pick_observer` 设口令，然后用 `pg_roles` 核对 | 是 | 用户（postgres 身份） |
| S3 | 部署带 0007 的 gateway，对应 7.5 第 2 步。从 S0 之后的 `ggwork/main` 干净检出，守卫通过（生产上一次记录的提交是 HEAD 的祖先；gateway 第一次经守卫部署时带 `--first-record`）后执行 `railway up --detach`；部署前在该提交上跑完整套件，不带 `-k` | 是 | 代理，经用户批准 |
| S4 | 核对：日志里没有 `ggwork-pick` 的 `service start() failed`，扩展的启动日志正常；在 gateway 容器里（`railway ssh`，`cd /app/backend`）能导入本阶段已交付的 `ggwork_pick.observe.selfcheck` 与 `ggwork_pick.observe.grants`（`python -c "import ggwork_pick.observe.selfcheck, ggwork_pick.observe.grants"`）。`observe.read` 属于批次 3 的 TR-26，它的导入检查移到 S11，不为过这一步补空壳模块；迁移头 0007；`has_table_privilege('pick_observer',…)`、`has_schema_privilege('pick_board_reader','pick_obs','USAGE')`；anon 与 authenticated 没有 USAGE；在 gateway 里经 `railway ssh` 跑 `observe.admin regrant --check`，有缺项就跑 `regrant`，再 `--check` 到「授权齐全」（`observer-role.md`「S4」）；以 observer 连接在只读事务里读当前镜像版本的 `rs_ids` 后回滚（权限实读）；以登录用户访问 `/api/pick/sync` 返回 200，并打开资料页与一次不带趋势条件的选剧对话（宿主 `/health/ready` 通过不证明扩展加载成功）；只读统计共享批次里 v1 `gsc` 信号的条数（设计 1.2） | 是（只读核对，加一次补授权） | 代理 |
| S5 | 新建 Railway 服务 `pick-obs-trends`：同一仓库、同一项目与环境，不开推送自动部署。**服务设置先写自检配置** `/deploy/pick-obs/trends/selfcheck/railway.toml`：第一次部署就是 S6 的自检，S6 核对通过后才切到 `/deploy/pick-obs/trends/railway.toml`（`packaging.md` 第 3.2 节、第 5 节第 4 步）。Railway 已不读配置路径（2026-09-28 S5 实测，Config as Code 弃用），两份文件由 `scripts/pick-railway-settings.py apply` 写进服务设置、`check` 核对（`packaging.md` 第 3.3 节）。变量名（不写值；全表、谁填与来历见 `packaging.md` 第 5 节）：`PICK_DATABASE_URL`（observer 的 Supavisor session DSN，URL 不带 ssl 参数）、`PGSSLMODE`、`PICK_OBS_STATE_KEY`、`PICK_OBS_EXPECTED_COLLECTOR`、`PICK_OBS_EXPECTED_ROLE`、`PICK_DB_SIZE_CAP_BYTES`、`PICK_OBS_TRENDS_MODE=canary1`；`PICK_OBS_TRENDS_ROUTE` 与 `PICK_OBS_TRENDS_GRANULARITY`（取值由 G2 定，显式写入，不能靠默认值落回 `both`、`H`；2026-09-29 G2 定为 `b_only`、`H`，S5 时写入的是暂定值 `both`、`H`，切 cron 之前把 ROUTE 改成 `b_only`，第 8.1 节）；`PICK_OBS_TRENDS_PACE`（默认 `user`，即阶段 0 第二天用的桶 4、每分钟补 2；由 G3 的节奏修复加入，名字与可选值以实现为准）；`PICK_OBS_PUBLISH`（不设）；`PICK_OBS_EGRESS_ECHO_URL`（U13 批准前不设；旧稿写的 `PICK_OBS_EGRESS_URL` 是错名，代码不读，只在 stderr 点名提示，不会开出口测量）。ROUTE、GRANULARITY、PACE 在金丝雀期间都不改。确认 healthcheck 关闭、重启策略 NEVER。守卫通过后（首次带 `--first-record`）从干净检出执行 `railway up --detach --service pick-obs-trends`；后续顺序见 S6 | 是 | 服务由代理经批准创建，机密变量由用户填 |
| S6 | **自检，再切回 cron**（与 S5 连着做；步骤、核对项与失败处理以 `packaging.md` 第 5 节第 4 步与第 6 节为准，这里只列顺序）：① 守卫只在开头跑一次（S5）；② 以自检配置 `/deploy/pick-obs/trends/selfcheck/railway.toml` 部署，它立即以 `--selfcheck-only` 自检一次，自检以 0 退出才接着跑 S6a 的 `preflight`，然后退出，这次部署的退出码是最后跑的那一步的（`packaging.md` 第 3.2 节）；③ 读这次部署的日志，核对 `selfcheck ok` 一行里的包摘要（等于在所部署提交的干净检出里算出的 `package_digest`）、角色 `pick_observer`、迁移头（等于守卫刚打印的生产迁移头）与 collector，再按 S6a 核对紧随其后的 `{"preflight": …}` 一行，任一不通过就停；④ 从同一检出、同一提交把服务设置 `apply` 回 `/deploy/pick-obs/trends/railway.toml`，再 `railway up` 一次，用 `check --deployment` 核对这次部署的 cron 计划、重启 NEVER、没有健康检查，并确认没开推送自动部署；⑤ 最后才把守卫这一轮的记录行追加进 progress.md，经用户同意推到 `ggwork/main`。期间 `ggwork/main` 前进或间隔过久，按 `packaging.md` 的规则在第二次部署前原样重跑守卫；被拒就在最新 main 的干净检出里从头重走（提交变了，包摘要要重核）。只在 `packaging.md` 第 7 节写的部署时段内做：时段跟着 G3 金丝雀修复定下的起跑时刻与 cron 触发范围走（`test_railway_config.py` 的 `DEPLOY_WINDOW` 把它与 cron 计划钉在一起），本节不另写时刻。自检只证明配置、角色、迁移链与包，不证明当晚的任务负载合格（S6a） | 是 | 代理 |
| S6a | **不发 HTTP 的金丝雀预检**（G3 接缝 1）：`python -m ggwork_pick.observe.trends preflight` 不另开命令：它是 S6 自检配置启动命令的第二步，同一镜像、同一服务变量，自检通过才跑，只读、不取租约（`packaging.md` 第 3.2 节与第 6 节第 3 步；字段与判据见 `trends-session.md`「金丝雀的负载闸门」）。它读当前共享批次，列出各组对照的匹配数与缺失项、近期剧目数、展开与截断后的计划请求数，不发 HTTP。未达最低负载或缺必要对照时退出 2，不进 S7，服务留在自检配置上；金丝雀期间某一晚出现同样情况，该日记为无效验收日，不计入三天、七天，原因在日志与 `status` 里看得见 | 是（只读） | 代理 |
| S7 | 金丝雀开始，对应 7.5 第 3 步（开关关着）。前提：S6a 通过；所部署的提交含第 14.3 节的金丝雀修复（有效负载准入、生产节奏与排期、首次验证码即终止）；市场对照词表已冻结并记下版本（第 14.3 节两路的 B：小样本验证要访问 Google，另经用户批准；没批准就按现有 `canary_controls.json` 冻结，没有合格基线的 geo 按 `control_unavailable` 处理，不出依赖它的业务结论） | 是（访问 Google） | 已批准；代理监控，TR-30 |
| S8 | 旧页快照导入：在本机用 observer 的 DSN（600 权限文件）执行 `observe.admin import-legacy`；U14 批准时同时 `import-editorial` | 是 | 代理，经批准 |
| S9 | 新建 `pick-obs-gsc` 服务：配置 `/deploy/pick-obs/gsc/railway.toml`，变量 `PICK_DATABASE_URL`、`PGSSLMODE`、`PICK_GSC_SA_EMAIL`、`PICK_GSC_SA_PRIVATE_KEY`、`PICK_GSC_SITE_URL`、`PICK_OBS_EXPECTED_COLLECTOR`、`PICK_OBS_EXPECTED_ROLE`、`PICK_DB_SIZE_CAP_BYTES`。TR-21 到 TR-23b 完成后以影子模式运行；没有快照时它不发布（TR-23b） | 是 | 同 S5 |
| S10 | 金丝雀通过，而且 TR-17、TR-18、TR-20 已部署到 trends 服务之后，改成 stable 模式，开始影子运行 2 周（G2 定为 `b_only` 后：TR-17 取消，改为 TR-18 的缩减部分、TR-19、TR-20 已部署，第 8.1 节） | 是 | 代理，经批准 |
| S11 | 发前端 TR-24、TR-25（资料页能看到 shadow 集合与 shadow 联动）；再部署带 TR-26 至 TR-28 的 gateway，`PICK_OBS_AGENT` 不设（工具 schema 与改动前相同）；gateway 上线后在容器里核对能导入 `ggwork_pick.observe.read`（TR-26 交付，自 S4 移来），并按 S4 的其余各项复核；然后跑 TR-36 | 是 | 代理，经批准 |
| S12a | gsc 服务设 `PICK_OBS_PUBLISH=1`。门槛：GSC 影子抽检通过、TR-36 通过、G5 | 是 | 用户拍板，代理执行 |
| S12b | trends 服务设 `PICK_OBS_PUBLISH=1`，对应 7.5 第 4 步。门槛按第 8 节的去向：阶段 0 至少过一道闸门、金丝雀阶段 2 达标、影子抽检通过、TR-36 通过、G5。G2 定为 `b_only`（第 8.1 节）：影子抽检是发现段的影子抽检，U5 的 CSV 形状比对在这一步之前补，TR-36、G5 保留 | 是 | 用户拍板，代理执行 |
| S13 | gateway 设 `PICK_OBS_AGENT=1`（Railway 改变量即重启，工具 schema 随之打开），对应 7.5 第 5 步。先提醒大家刷新页面（照 P4-1 的做法）。需过 G6。从这一步产生新卡起，gateway 的最低可回滚版本升为 M1，按回滚规则的四格验证核对。G2 定为 `b_only`：`trend_state`、`trend_include_first`、`trend_include_presumed` 不开放，`link_state` 只剩 `site_only`（第 8 节） | 是 | 用户拍板 |

**回滚规则**（设计 3.8）
- **每次发布与回滚都做两类检查，缺一不可**（`rollback-matrix.md`「来源检查与合同能力检查」）：
  - **来源检查**（TR-34 守卫）：提交来自共享仓库的 `ggwork/main`、工作区干净、没有 `.env*`、是生产上一次记录提交的后代（不从旧检出回退）、迁移链认识生产迁移头（cron 还要求等于链头）。守卫**只保证来源，不保证合同能力**：在 main 上 revert 掉 TR-16 的前端解析改动（或 S13 之后 revert 掉 TR-26 至 TR-28），新提交仍是上次生产提交的后代、仍在 main 上，守卫照样放行，解析器却可能已退回 F0（gateway 退回 M0）。守卫也证明不了 Railway 控制台里的配置。
  - **合同能力检查**：旧卡、新卡、混合会话、存量快照四格，在要部署的那个提交上跑，revert 提交也不例外；用例被删、被跳过或找不到，一律按没过处理。前端、gateway 各用哪些现有测试，部署后在生产上怎样手工核对，见 `rollback-matrix.md`「四格验证」。S13 之后的 M1 下限按同样方法验证。
- S1 之后，前端不回退到 F0；前端只从守卫导出的 `ggwork/main` 目录部署。
- S3 之后，gateway 永远不回退到 G0。最低可回滚版本是 M0（带 0007）；产生新卡之后（S13 起）是 M1（认识观测字段）。plan 6.10 的 48 小时回滚不适用于这里。回滚一律用 main 上的 revert 提交，守卫拒绝从旧检出部署；revert 提交照样要过合同能力检查。
- **止损的正确方向**：按顺序关 `PICK_OBS_AGENT`、关 `PICK_OBS_PUBLISH`、停 cron（去掉 cronSchedule 或暂停服务），保留 DB0007 与最低兼容网关（S3 之后 M0，S13 之后 M1）；不回退镜像，不降级库。
- cron 服务可以单独回退，前提是回退后的代码认识生产的迁移头（D5）。
- 任何新迁移（本功能的 0008 或另一会话的迁移）上生产后，同步重部署 gateway 与两个 cron。
- 0007 的 downgrade 不是无损回滚：它删掉观测表、视图与候选集的新增列，观测历史随之丢失。只在测试库上用，生产不降。

**回滚矩阵**（前端 F0=TR-16 之前、F1=TR-16 及以后；gateway G0=不带 0007、M0=带 0007、M1=认识观测字段；库 DB0006=生产迁移头 0006、DB0007=0007 或之后本镜像认识的修订；卡片旧、新、混合会话、存量快照。逐格的测试与手工核对见 `rollback-matrix.md`）

| 时段 | 组合（前端 × gateway × 库 × 卡片） | 允许 | 由谁保证 |
|---|---|---|---|
| S1–S2 | F1 × G0 × DB0006 × 旧卡 | 是，S1 到 S3 之间的实际过渡状态 | TR-16 前端格「F1 x old card」「F1 x stored snapshots」；G0 是当时生产上未改的 gateway |
| S3 部署中 | M0 启动时面对 DB0006 | 升级过程：M0 启动时把库迁到 DB0007，不是计划长期运行的组合 | TR-11 的 0007 升级测试；S4 核对迁移头 |
| S3–S12 | F1 × M0 × DB0007 × 旧卡 | 是，S3 之后本阶段的正常状态 | 前端四格；gateway 的旧卡格（`test_frontend_contract.py` 等）；S4 的认证业务路径 |
| S11 起 | F1 × M1（开关关）× DB0007 × 旧卡 | 是 | TR-27 测试 |
| S13 起 | F1 × M1 × DB0007 × 旧卡、新卡、混合会话 | 是 | TR-16 前端三格、TR-27 后端两格、TR-36 |
| S13 起开关又关 | F1 × M1（开关关）× DB0007 × 新卡 | 是，换一批返回「趋势条件尚未开放」 | TR-27 测试 |
| S3 起 | 任意前端 × G0 × DB0007 | 否：旧扩展不认识迁移头，扩展加载失败；宿主 readiness 仍可能通过，不能靠健康检查判断 | 回滚规则；守卫拒绝迁移头认不出的 gateway 部署；S4 的业务路径核对 |
| 已有新卡后（S13 起） | 回滚到 M0（任意前端 × M0 × DB0007 × 新卡） | 否，不能当安全回滚方案：M0 解析不了新条件 | 回滚规则；合同能力检查（守卫放行 main 上的 revert，拦不住） |
| S1 起，已有新卡后尤甚 | 恢复 F0（F0 × 任意） | 否：新卡的 `observations`、`sort=obs` 等让 strict 解析失败；Git revert 本身不保证解析能力 | 回滚规则；合同能力检查的前端四格；守卫前端模式只管来源 |
| 任何时候 | 生产执行 0007 downgrade | 否，不是无损回滚：删掉观测表、视图与新增列 | 回滚规则 |
| 任何时候 | 停 cron、关开关、保留 DB0007 与最低兼容网关 | 是，正确的止损方向；S3 之后最低 M0，产生新卡后最低 M1 | 回滚规则 |

---

## 11. 用户仍需亲自做的事

| # | 事项 | 何时 |
|---|---|---|
| U1 | 创建 GSC 专用服务账号：GCP 项目，启用 Search Console API，生成 JSON 密钥，本机存成 600 权限文件。推荐**不**复用 RealShort 的服务账号（设计 1.2：两边的钥匙要能分别吊销） | 批次 0 当天 |
| U2 | 以 Owner 身份在 Search Console 的 `sc-domain:dramashortstv.com` 添加这个服务账号，权限选 Restricted（加不了就说明当前账号不是 Owner，请实际 Owner 操作） | 紧接 U1 |
| U3 | 在生产库以 postgres 身份执行 `bootstrap-observer.sql` 并设口令（S2）；把 observer 的 DSN 存进 600 权限文件（守卫与本机导入都用），同时填到 Railway | S2 |
| U4 | 在 Railway 上填机密变量（S5、S9）；告知 Railway 套餐（决定日志留存期与静态出口是否可用） | S5 |
| U5 | 阶段 0：在自己的浏览器里导出 3 个词的 Trends 走势 CSV；审阅阶段 0 报告，选定 H、D 或 H+D。**2026-09-29**：颗粒度已选 `H`；CSV 形状比对经批准豁免到 S12b 之前（第 8.1 节） | 第 4–6 天；CSV 在 S12b 之前 |
| U6 | 审发现段种子清单（闸门 B 通过后） | 批次 2b |
| U7 | 给导出脚本提供 RealShort 生产库的只读 `DATABASE_URL`（或新建只读角色）；审并合并 RealShort 的 PR（只含脚本与测试）；知悉 `verify-legacy-live` 会对自家生产站做约 300 次、每秒约 1 次的只读请求；确认 #68 的防火墙规则不会误伤本机 | 批次 1 |
| U8 | 批准在生产上只读比较两个共享批次的身份变动率（设计 7.2） | 批次 2b |
| U9 | 申请官方 Trends API alpha（不作前提） | 任意时间 |
| U10 | 影子抽检的人工标注；给未兑现率定一个上限（设计第 9 节 #11 需要一个具体数字） | 影子运行期间 |
| U11 | 逐项批准第 10 节里标「对外」的步骤；决定两个开关的打开时点 | 按步 |
| U12 | 批准每周一次的线上合同检查（设计第 10 节标了「需批准」）；可选：批准 TR-32 | 批次 2b |
| U13 | 批准或拒绝出口 IP 回显服务（对第三方的外发请求，D20）；拒绝时出口记 NULL，出口相关结论只看 Railway 区域 | S5 之前 |
| U14 | 批准在同一个 RealShort PR 里加编辑精选历史导出（D33，只读 git 历史，不联网）；不批准时 `eval-rules-v1` 不含「进入编辑精选表」 | 批次 1 |
| U15 | 仅当 TR-08 的探针失败时：批准改动 RealShort 线上跳转代码（附合并前全量比对与预览部署核对） | 视探针结果 |
| U16 | 把部署冻结（S0–S4）与部署守卫的用法转告另一会话，并确认共同 SHA | S0 |

---

## 12. 测试与验收

### 12.1 每批的完成定义
每批都要求：两种库全绿（任务分支 deselect `test_managed_copy`，D21）；ruff 与 format 通过；`ggwork_pick.observe` 覆盖率 ≥80%；集成分支刷新托管副本后完整套件（含 `test_managed_copy`）绿；本批任务归属的反例（第 12.2 节）都有「换成错误实现后变红」的日志附在 PR 里。
- **批次 0**：延迟导入的子进程测试绿；依赖已锁定，托管副本已刷新；合同夹具两端可用。
- **批次 1**：0007 的升级、降级、重跑在两种库上都绿，JSON 列全覆盖测试仍绿；阶段 0 在本机跑完并出报告；GSC 七项实测有结论；RealShort 的 PR 测试绿、线上代码零改动、差分核对方案就绪；通过 G2。
- **批次 2a**：金丝雀执行器用假 transport 加本机 PG 端到端跑通，传输层包络测试绿；自检、Railway 配置、守卫、前端合同测试都绿；通过 G3，第 14.3 节标「S0 前」的处置已合入；上线步骤 S0–S7（含 S6a 预检）完成，金丝雀开跑。
- **批次 2b**：两个采集服务用真实 runner 与假 transport 端到端跑通（GSC 从空库起）；资料页集成测试与本机 e2e 零 skip；通过 G4，影子运行开始。
- **批次 3**：智能体测试绿，合同夹具已重新生成，回滚矩阵的允许格全绿；TR-36 在生产 shadow 数据上通过；通过 G5、G6 之后，才分别执行 S12a/S12b、S13。

### 12.2 设计第 8 节反例表逐行指派

| # | 反例 | 负责任务与测试 |
|---|---|---|
| 1 | 判定换成恒等或常量；非零小时改成推算值；每条序列各用自己的窗口；失败返回全零 | TR-17 `test_rules_distinguish`、`test_nonzero_hours_counted_not_inferred`、`test_window_is_batch_not_series`；TR-14 `test_resume_after_midnight_same_window`；TR-02 `test_failure_has_no_values` |
| 2 | 限速器换成空操作；泛词请求带上裸剧名 | TR-03 `test_noop_pacer_fails_envelope`；TR-14 `test_transport_level_noop_pacer_red`；TR-18 `test_generic_title_no_bare_line` |
| 3 | 保语页面来自多个国家；西班牙点击落在其他语种页面 | TR-22 `test_page_country_not_language` |
| 4 | US 的 Trends 上升、MX 的 GSC 上升；另一国 cooling | TR-10 `test_link_same_country_only`、`test_cooling_blocks_same_country_only`；TR-27 `test_cooling_blocks_country` |
| 5 | HTTP 200、未满额，但 A′ 与明细对不上 | TR-09 `test_site_admission`；TR-23b `test_e2e_counterexample_5` |
| 6 | 修订后少一行；一个国家分片失败 | TR-21 `test_revision_drops_row`、`test_country_shard_fail_keeps_old_no_zero` |
| 7 | 只换旧页解析版本，原始指标不变 | TR-22 `test_mapping_only_change` |
| 8 | 同平台同语种同名两部剧；标题真实修订 | TR-18 `test_same_title_same_platform_not_merged`、`test_title_revision_suggested_only` |
| 9 | 原始数据、规则、别名、联动规则分别升级 | TR-20 `test_sets_immutable`；TR-26 `test_frozen_under_upgrades`、`test_ranking_dispatch_uses_recorded_version`、`test_link_rows_read_for_pinned_pair`（用行为确实不同的测试版本） |
| 10 | 租约过期后旧进程恢复并尝试写入和发布 | TR-13 `test_stale_owner_cannot_write_or_publish`、`test_collectors_write_only_via_lease`；TR-14 `test_takeover_mid_http_trends`；TR-20 `test_publish_after_lease_loss_refused`、`test_takeover_before_publish_commit_trends`；TR-21 `test_takeover_mid_round_gsc`；TR-23b `test_takeover_before_gsc_publish` |
| 11 | 序列整体乘常数；同请求加一条强变体 | TR-17 `test_scale_invariance`、`test_strong_variant_no_flip` |
| 12 | 只改查询组成，平均排名不变而曝光上涨 | TR-09 `test_no_causal_label` |
| 13 | first 很多但始终没有后续事件 | TR-10 `test_leadtime_denominator` |
| 14 | 开启新功能后回滚 gateway | 第 10 节回滚矩阵；TR-16 前端格；TR-27 后端格；TR-34 守卫（只管来源）；每次发布与回滚在要部署的提交上跑四格验证（合同能力，`rollback-matrix.md`）；S4 的认证业务路径核验（`test_bootstrap.py:58-69` 只证明初始化会抛错，不证明业务可用） |
| 15 | 镜像里装的是旧扩展快照 | TR-13 `test_selfcheck`；TR-15 部署验证（S6 的包摘要）；`test_managed_copy`；S0 合并 main 后刷新托管副本并在最终共同 SHA 上重跑 |
| 16 | W−1 漏掉某页的行、W0 出现 20 次曝光 | TR-09 `test_from_zero_needs_v_agreement`；TR-23b `test_e2e_counterexample_16` |
| 17 | A′ 不支持且 A″ 失败 | TR-09 `test_site_admission`（unverifiable 分支） |
| 18 | 规则改版前一天 rising、改版后一天 rising | TR-17 `test_confirmed_comparability` |
| 19 | WW 上升、全站上升，但涨在不同国家 | TR-10 `test_ww_sitewide_parallel_only` |
| 20 | 同一剧 14 天内反复触发；提示未满 14 天 | TR-10 `test_alert_dedupe_14d`、`test_leadtime_denominator`、`test_dedupe_survives_alias_change`；TR-20 `test_alerts_dedupe` |
| 21 | 同平台池外旧作与候选同名 | TR-18 `test_out_of_pool_same_name_needs_confirm`；TR-27 `test_default_eligibility` |
| 22 | 全站缺口 3%，但某剧的页全部漏在明细外 | TR-09 `test_per_identity_gap_blocks`、`test_pageset_independent_of_detail`；TR-23a `test_vh_regex_matches_pages_absent_from_C`；TR-23b `test_e2e_counterexample_22` |
| 23 | 22 日切片完整、A 的水位在 24 日中午 | TR-09 `test_cutoff_ignores_history_day_ends` |
| 24 | 旧页 `/en?id=38000` 与 `/en?id=49020` | TR-08 `test_two_query_ids_distinct`；TR-22 `test_raw_url_exact_key` |
| 25 | 对照序列 B5 全零 | TR-17 `test_control_unavailable` |
| 26 | 过滤请求与明细在 W−1 都无行，但真实有曝光 | TR-09 `test_unobserved_not_zero`；TR-24 `obs-views.test`；TR-23b `test_e2e_counterexample_26` |
| 27 | 7 天 rising 候选的前 7 天超出小时数据范围 | TR-09 `test_7d_needs_vd_14_days`；TR-23a `test_vd_missing_day_blocks_7d`；TR-23b `test_e2e_counterexample_27` |
| 28 | 新版本截断、旧版本仍生效 | TR-21 `test_new_version_truncated_old_active_stale`；TR-09 `test_stale_slice_descriptive_only`；TR-23b `test_e2e_counterexample_28` |

### 12.3 设计第 8 节「验收」各条的归属

| 类 | 条目 → 任务 |
|---|---|
| 失败 | 注入 429 后 30 分钟零请求 → TR-03、TR-14（传输层）；旧值保留并标陈旧 → TR-20；sorry 页当天熄火 → TR-03、TR-02；重启后暂停仍有效、状态读不到当天不跑 → TR-04、TR-13；日志无 cookie → TR-02、TR-04 |
| 窗口 | 22:10 与 01:30 共用 `window_end`、午夜后续跑不重算 → TR-14、TR-17；含 partial 点记 `insufficient_window` → TR-17；carried_over 不进智能体 → TR-20、TR-27 |
| 口径 | 泛词只请求意图变体 → TR-18；裸剧名以外的线不参与判定 → TR-17；shared_title、emerging、未确认的默认不进智能体 → TR-10、TR-27；cooling 只拦同一国家 → TR-10 |
| 版本 | 各项升级后旧候选、换一批、计数、详情都不变 → TR-26；旧卡在 strict 解析下仍能打开 → TR-16 |
| GSC | 同一共同截止、暂定小时不比较 → TR-09；三层覆盖齐全 → TR-23b、TR-24；同一行不重复归因 → TR-22 |
| 回归与门槛 | 编辑精选 12 条 → TR-31；阶段 0 → TR-05、第 8 节；金丝雀 → TR-30；三个日期的分层抽检与身份证据抽检 → TR-31；影子全链路 → TR-36 |

### 12.4 gpt-6-astra 验收节点
每个节点都做两件事：先分层审，再做一次「接缝补漏批判」。M1 的教训是，分层审看不见模块之间的接缝，真正的上线阻塞都出在补漏批判里。

| 节点 | 时点 | 审什么 |
|---|---|---|
| G1 | 本计划定稿、批次 0 开工前 | 计划与设计的对照、四个前提、D1–D43、第 14 节处置、TR-33 合同 |
| G2 | 批次 1 合并后，TR-17 编码前，RealShort PR 合并前 | 阶段 0 报告、trend-rules 全文与去向；GSC 实测结论；RealShort 零改动方案的等价性。**阶段 0 部分结论（2026-09-29，`feat/trends-radar` 的 `fbda69f`）**：codex 只读审一轮「改后可交」（P1 一条：日级闸门 A 不能改判为不过；P2 六条），改稿后用户拍板 `b_only`、`H`、`user`，U5 的 CSV 豁免到 S12b 之前，见第 8.1 节。RealShort 部分已于 2026-09-25 随 TR-08 审过；GSC 实测部分等 U1/U2 之后另审 |
| G3 | 0007 上生产前（S0 之前） | 迁移、授权、视图、预置行、回滚矩阵、租约与唯一写入口、金丝雀执行器、守卫。**本轮结论（2026-09-25，`feat/trends-radar` 的 `c095fe4`）**：分层审有条件放行基础设施（迁移、授权、租约、唯一写入口没有上线阻塞，4 项 P2、2 项 P3），但不批准按当前限速与排期开跑 S7；接缝审暂不通过（4 项 P1：金丝雀有效负载准入、阶段 0 节奏没进生产且排期漏算半速、首次验证码没有跨日终止、S4 导入 S11 才交付的模块；另有 2 项 P2）。逐条处置与门槛见第 14.3 节 |
| G4 | 批次 2b 完成、影子运行开始前 | 采集状态机、切片、准入与逐剧核对、冻结输入、清理、联动配对、反例表 |
| G5 | 打开 `PICK_OBS_PUBLISH` 前（S12a、S12b 各一次） | 金丝雀报告、影子抽检、TR-36、连接账实测 |
| G6 | 打开 `PICK_OBS_AGENT` 前 | 智能体侧、schema 开关、提示词、核对接线、前端混合会话 |

---

## 13. 工作量与日历

| 设计第 8 节的类别 | 设计人日 | 本计划任务 | 本计划人日 |
|---|---|---|---|
| Trends 客户端、限速、熔断、cookie、持久状态、租约代次 | 4 | TR-02 1.25、TR-03 1.0、TR-04 0.5、TR-13 1.5 | 4.25 |
| 阶段 0 执行与报告 | 1.5 | TR-05 | 1.5 |
| 金丝雀汇总与一次修复重跑 | 1.5 | TR-30 | 1.5 |
| 0007、models、角色与授权、网盘手册、连接账 | 2.5 | TR-11 1.75、TR-12 1.0 | 2.75 |
| 打包与部署验证 | 1 | TR-01 0.5、TR-15 0.5、TR-34 0.5 | 1.5 |
| 规则纯函数 | 3 | TR-09 1.5、TR-10 1.0、TR-17 1.0 | 3.5 |
| 采集状态机 | 4 | TR-14 2.0、TR-20 1.5、TR-23b 1.25 | 4.75 |
| 清单、歧义、身份证据、别名建议 | 2 | TR-18 2.0、TR-35 0.75 | 2.75 |
| 发现段 | 1.5 | TR-19 | 1.5 |
| GSC 客户端到冷启动回补、准入与逐剧核对 | 4.5 | TR-06 0.75、TR-07 0.5、TR-21 2.0、TR-22 1.25、TR-23a 1.5 | 6.0 |
| 智能体侧 | 3.5 | TR-26 1.5、TR-27 1.75、TR-28 0.75 | 4.0 |
| 前端合同与回滚矩阵 | 1 | TR-16 | 1.25 |
| 资料页接口与界面 | 3.5 | TR-24 2.5、TR-25 1.25 | 3.75 |
| 手册 | 0.5 | TR-29 | 0.5 |
| 设计没单列 | — | TR-33 0.75、TR-31 1.0、TR-36 0.5、0008 预留 0.5 | 2.75 |
| **工作台合计** | **约 35** | 35 个任务条目 | **42.25** |
| RealShort 旧页解析导出与精选历史 | 约 1.5 | TR-08 | 2.0 |
| RealShort build.py（可选） | — | TR-32 | 0.25 |
| G2–G6 验收修复（另留） | — | — | 3–5 |

- **合计**：工作台 42.25 + RealShort 2.0 = 44.25 人日；加验收修复 47.25–49.25 人日（可选 TR-32 另加 0.25）。
- **与草稿（35.5）的差异 +6.75**：新增 TR-33、TR-34、TR-35、TR-36 与 0008 预留共 3.0；TR-23 拆分并补 A″f、Vd 有效期、提示、编辑精选、端到端 +1.5；TR-09（P、D38）+0.25、TR-10（事实与可行动性、资格、D37）+0.25、TR-11（四张表、网盘清洗划入）+0.5、TR-13（唯一写入口）+0.25、TR-14（传输层测试）+0.25、TR-16 +0.25、TR-20（通用层、清理分组）+0.25、TR-21（A″f、入口、冻结输入）+0.25、TR-24 +0.25、TR-26（两处钉住、回放、分发表）+0.25、TR-27（schema 开关、第七个字段）+0.25；TR-12 −0.25、TR-15 −0.5（网盘与 gsc 配置分别划出）。
- **估算条件**（同设计）：阶段 0 与金丝雀都通过，选了单一颗粒度，RealShort 零改动方案可行。以下情形要重估：选 H+D 时 TR-17 +0.5；第 8 节的其他去向按表增减；TR-08 探针失败 +1.0；直连不稳定、要走后备时新增适配器，每项单独批准。
- **不计人日、单列的日历项**：用户的 U 步骤等待（按每步 1 个工作日计）；阶段 0 本机运行 2 天；金丝雀 10 天（修复重跑再加 3 天）；影子运行 2 周；影子抽检的人工标注（U10，三个日期各约 120 个样本）；提前量复盘从打开发布开关起至少 4 周，且要满 30 件提示。
- **日历**（第 9 节的假设下）：第 1–10 天完成批次 0 到 2a 与 G3；第 10–11 天 S0–S7；批次 2b 第 8–16 天，G4 约第 16–17 天，GSC 影子约第 17 天起；批次 3 第 15–20 天，S11 约第 21 天；金丝雀约第 20 天结束，Trends 影子 2 周到约第 35 天；S12a 可在 GSC 影子与 TR-36 通过后先开（约第 24 天起），S12b、S13 约第 36 天起。

---

## 14. 批判处置

核对方法：每条批判都回到设计原文、草稿行号与仓库代码逐一查证；「核实」列出查到的依据。

### 14.1 Claude 对抗式批判

| # | 级别 | 批判要点 | 处置 | 理由与落点 |
|---|---|---|---|---|
| C-1 | P1 | A″ 被降成 A′ 的后备，7 天全站层准入没有数据来源；`final` 版 A″ 是设计遗留缺口 | 采纳 | 核实：设计 5.2 第 388 行写 A″「每轮」，5.6 第 435 行要求 14 天同 dataState，而 5.2 只写 `all`。D27 每轮取 A″a 与 A″f；TR-07 P6 实测；TR-09 `test_site_admission_7d_datastate_per_day`；TR-23b 空库端到端「A′ 可用时仍出 7 天正式标签」 |
| C-2 | P1 | Vd 每天一次与「V 结果不是本轮取的就只出描述性标签」冲突 | 采纳（选有效期方案） | 核实：草稿第 78、95 行与设计第 394 行确实冲突。每轮全量重取违背设计频率，改为 D26：窗口、切片版本、dataState 都没变才沿用，版本记进 V 状态；Vh 仍每轮重取。TR-09 `vd_valid`、TR-23a |
| C-3 | P2 | 提示记录只写 Trends，GSC 那一半缺失 | 采纳 | 核实：设计 6.2 第 483 行去重键含通道。TR-23b 写 GSC 提示，`test_gsc_alerts_written` |
| C-4 | P2 | 里程碑「进入编辑精选表」被删 | 采纳 | 核实：RS `src/lib/pick` 里没有精选数据，精选在 `editorial-picks.ts` 随代码走。D33 用 git 历史导出（不可变，可随时补）；U14 批准范围扩展；不批时 eval-rules-v1 明确不含 |
| C-5 | P2 | 过度离散检验与 BH 没人实现，`test_quality_note_not_gate` 会空绿 | 采纳 | D38 写死检验方法、族划分与 q；TR-09 `test_quality_note_values`、`test_bh_family_per_set` |
| C-6 | P2 | 阶段 0 四种去向只有一个映射函数 | 采纳 | 第 8 节去向表：每行写任务增删、开关、门槛与人日；「只过 A」的歧义第三道改人工落到 TR-18 |
| C-7 | P3 | 设计 1.2 要求核实 v1 `gsc` 信号是否一直为空，没有任务 | 采纳 | S4 加只读统计；结果写进 TR-28 的提示词口径 |
| C-8 | P1 | GSC 集合冻结漏了来源剧库批次与别名版本 | 采纳 | 核实：草稿第 574 行清单与设计第 509 行对比确实缩小。D36 统一 `FrozenInputs`；TR-23b `test_frozen_inputs_complete` |
| C-9 | P2 | `observations` 存在哪里没定 | 采纳 | 核实：`routes.py:127` 的 `status_view` 返回 `result_view(record)`。D28 存进 `obs_as_of_json`；TR-26 `test_observations_in_result_view_frozen` |
| C-10 | P2 | 回放要全体匹配行的映射，与「映射只写进前 limit 条证据」矛盾 | 采纳 | 核实：`selection.py:158-183` 对全部行重跑。TR-26：回放用同一纯函数按冻结别名版本重算；集合已清理返回 410；`ranking_reproducible` 经分发表认 obs-v1 |
| C-11 | P2 | `use_latest` 重钉路径没带 ObsPin | 采纳 | 核实：`tools.py:53-56`。TR-26 两处调用点都改用 `current_pins()`，`test_use_latest_repins_obs_atomically` |
| C-12 | P2 | 人工确认的冻结点没定 | 采纳 | D24：`decisions_version` 冻结进集合，下一个集合生效；TR-35 |
| C-13 | P2 | 联动两处计算、以什么为「现在」不同；影子期没有联动；TR-18 没声明依赖 GSC 状态形状 | 部分采纳 | 影子同模式配对、智能体读物化行不重算、TR-18 依赖 TR-33 合同：采纳（D13、D30、TR-18）。「时效以两个集合各自的发布时刻为准」只用于配对内部的事实；可行动性改在判定时刻用显式 `now` 计算，否则停采后已物化的「双涨」永不撤下（与 gpt-6-astra #15 一致） |
| C-14 | P2 | TR-20 与 TR-23 同批并行都写 `store.py` | 采纳 | TR-20 拥有 `store.py`；TR-23b 依赖 TR-20 并移到 2b-iii |
| C-15 | P3 | `PickTask.trend_checked` 没有归属 | 采纳 | TR-26 改 `context.py` 加 `obs_pin`、`trend_checked` |
| C-16 | P1 | 页面集合 P 若取自明细，反例 22 假绿 | 采纳 | D25；TR-09 `test_pageset_independent_of_detail`；TR-23a `test_vh_regex_matches_pages_absent_from_C` |
| C-17 | P1 | 反例 2 只在限速器单元层，执行器可绕过 pacer 与预算 | 采纳 | TR-14 `test_transport_level_envelope`、`test_transport_level_noop_pacer_red`，并断言请求数 = 预算扣减数 = 请求行数 |
| C-18 | P2 | 分母测试分不出取大取小 | 采纳 | 核实：100/91、100/89、2/5 三组在两种分母下结论相同。加「90 对 100」双向用例 |
| C-19 | P2 | 反例 1「每条序列各用自己的窗口」挂错测试 | 采纳 | TR-17 `test_window_is_batch_not_series` |
| C-20 | P2 | 前提 1 只扫文字，挡不住数值零 | 采纳 | TR-09、TR-23a `test_unobserved_stored_null`；TR-24 `obs-spark.test`；TR-27 `test_evidence_null_not_zero` |
| C-21 | P2 | 反例 10 只测了 Trends | 采纳 | TR-21 `test_takeover_mid_round_gsc`、TR-23b `test_takeover_before_gsc_publish` |
| C-22 | P3 | GSC 反例只在纯函数层 | 采纳 | TR-23b 对反例 5、16、22、26、27、28 各做一轮端到端夹具 |
| C-23 | P2 | TR-11 过不了自己的验收（JSON 列全覆盖测试会红） | 采纳 | 核实：`t/test_pan_runbook_sql.py:444-450` 与 `t/pan_runbook.py:34-47`。网盘清洗分类与脚本划给 TR-11 |
| C-24 | P2 | TR-24 与 TR-25 同批并行，要求互相矛盾 | 采纳 | TR-25 移到 2b-i 先做，断言改为「只允许登记过的 client 组件」，登记表归 TR-25 |
| C-25 | P3 | `admin.py` 被多个任务依次追加 | 采纳（换做法） | D35 用子命令模块自动发现，免去共享文件；reset-disable 归 TR-13 |
| C-26 | P3 | 任务分支上 `test_managed_copy` 必红 | 采纳 | D21、12.1 写明任务分支 deselect |
| C-27 | P1 | 预算与熔断按 UTC 日，会话跨午夜清零 | 采纳 | D23 对 Trends 按 target_date；TR-03 `test_cross_midnight_same_budget_day`、TR-13 `test_cross_midnight_budget_same_target_date`。这是对设计 3.5 表「UTC 日」的细化 |
| C-28 | P1 | RealShort 改线上跳转且上线后才验证 | 采纳 | D32 零改动方案，线上代码一行不改；探针失败才走 U15，并在合并前做全量等价比对与预览部署核对 |
| C-29 | P2 | canary2 的量与时窗对不上 | 采纳 | 核实：600 ÷ 2.9 ≈ 207 分钟，22:00 起跑只剩 18 分钟余量；设计第 261 行计划约 427。第 9 节计划约 430、上限 600；TR-03 `test_mode_plan_fits_window`。G3 按生产节奏重排为 18:30 起跑、约 300、上限 450，窗口检查换成 `capacity.py`（第 9 节） |
| C-30 | P2 | 自检要求迁移头完全相等，新迁移会让 cron 停摆；0008 无余量 | 采纳 | D5 改为「镜像认识且不早于 0007」；手册写新迁移上线的重部署流程；工作量留 0008 预留 0.5 |
| C-31 | P2 | 前端没有回滚下限，也没定从哪个检出部署 | 采纳 | 第 10 节回滚规则与矩阵；TR-34 前端模式用 `git archive` 导出 main |
| C-32 | P2 | S3 的部署来源有歧义 | 采纳 | S3 写明从 S0 之后的 main，守卫核对生产上一次提交是祖先 |
| C-33 | P2 | 跨会话防护只靠人转告 | 采纳 | 新增 TR-34 部署守卫；U16 |
| C-34 | P2 | 工具 schema 在开关打开之前就暴露 | 采纳 | 核实：`tools.py:67` 以 `PickConditions` 为参数类型。D11 开关同时控制 schema；TR-27 `test_tool_schema_unchanged_when_off` |
| C-35 | P3 | 旧页快照导入排在 GSC 影子之后 | 采纳 | 调换为 S8 导入、S9 建 GSC 服务；无快照时 GSC 不发布（TR-23b） |
| C-36 | P3 | TR-08 依赖 TR-07 的 URL 清单没写 | 采纳 | TR-08 标注导出运行依赖 TR-07 |
| C-37 | P3 | 出口 IP 回显是对第三方的外发 | 采纳 | D20 默认关闭；U13 |
| C-38 | P2 | 35.5 人日偏乐观，约 41–44，另加修复 3–5 | 采纳 | 第 13 节重估：工作台 42.25、RealShort 2.0、修复 3–5 另留 |

### 14.2 gpt-6-astra 批判

| # | 级别 | 批判要点 | 处置 | 理由与落点 |
|---|---|---|---|---|
| A-1 | P1 | GSC 7 天准入缺实际取数任务；Vd 复用未定；要有空库多轮的请求计划级测试 | 采纳 | 同 C-1、C-2；TR-23b `test_seven_day_formal_label_from_empty_db`，测试里不预塞 A″、Vd |
| A-2 | P1 | 人工决定只定义了追加写入，没有生效链；改标题、换别名要重新确认 | 采纳 | 新增 TR-35 与 D24；D43 定下别名类决定由 gsc 服务写成新版本；测试覆盖确认、撤销、改名、换别名、旧集合不变 |
| A-3 | P1 | GSC 冻结清单缩小，新增的镜像身份输入没冻结 | 采纳 | D36 含镜像版本与 `decisions_version`；TR-21 `test_frozen_inputs_read_once`（归因后、V 请求前切换镜像与别名）；TR-22 `test_rs_ids_from_pinned_version` |
| A-4 | P1 | 历史规则只登记版本名，没有按版本执行；link-rules 版本存哪不明 | 采纳 | 核实：`selection.py:13-16` 的 `RANKING_VERSIONS` 是允许值集合。D29 分发表；link-rules 版本存 `obs_as_of_json` 与联动行主键（D28、D13）；TR-26 用行为不同的测试版本验收 |
| A-5 | P1 | 影子运行无法验证双通道联动与智能体链路 | 采纳 | D13、D30 同模式配对；新增 TR-36 在生产 shadow 数据上走通到前端 strict 解析，作为 S12 门槛 |
| A-6 | P1 | 清理不保护当前 live，也不按通道分组；引用竞态无人负责 | 采纳 | D30；TR-20 `test_prune_keeps_current_live_per_channel`、`test_prune_per_channel`、`test_prune_race_with_candidate` |
| A-7 | P1 | 租约只测了工具函数，没保证两条写入链都用；发布接回 cron 入口无人负责 | 采纳 | TR-13 的 `LeasedWriter` 唯一写入口与导入图测试；TR-14、TR-20、TR-21、TR-23b 运行器级接管测试；TR-20 负责把发布接进 `trends/run.py` |
| A-8 | P2 | 没有字段表达「推定对应」；GSC 单通道资格有歧义 | 采纳 | D31 加第七个字段 `trend_include_presumed`（设计 7.3 只列六个，属设计缺口，已在 D31 写明）；TR-33 资格真值表，GSC 正式标签不要求对应确认 |
| A-9 | P2 | 提示与里程碑没形成完整评估链：精选事件缺、池外发现无关联键、别名变化后去重、shadow 是否写提示 | 采纳 | D33、D37；TR-10 `test_dedupe_survives_alias_change`、`test_shadow_alerts_excluded`、`test_pool_entry_ambiguous_counted_separately`；TR-23b 写 GSC 提示与全部里程碑 |
| A-10 | P2 | 阶段 0 四种结论没有可执行的任务分支；选 D 后联动与前端仍按 H 写死 | 采纳 | 第 8 节；S12 拆成 S12a（GSC）与 S12b（Trends）；D39 颗粒度无关的锚点 |
| A-11 | P2 | 批次依赖硬矛盾：TR-15 在 2a 测 gsc 入口；TR-14 没依赖 TR-05；TR-23 与 TR-22 同批 | 采纳 | 核实：草稿第 482–485 行与第 547 行。gsc 入口与配置移入 TR-21；TR-14 依赖 TR-05 的对照清单；新 DAG 同一小批次内无依赖边 |
| A-12 | P2 | 「同批不改同文件」不成立；早发前端没有先冻结 wire schema | 采纳 | 新增 TR-33 共享合同（批次 0）；D35 运维命令与手册分文件；TR-25 先于 TR-24；TR-16 以合同夹具为准 |
| A-13 | P2 | 现存镜像版本没有 observer 授权；运行时状态初始化没落进 S0–S7 | 采纳 | 核实：`mirror/publish.py:115-123` 只在发布时授权。D15 的 `regrant` 覆盖所有已发布版本；S4 以 observer 实读；D34 由 0007 预置运行时行 |
| A-14 | P2 | 跨会话协调与回滚靠口头；回滚矩阵没列拒绝的组合；`/health/ready` 不证明业务可用 | 采纳 | 核实：`gateway.py:575-610` 失败放行、`app.py:1023-1038` 不看扩展。TR-34 守卫与 U16；第 10 节逐格矩阵含禁止格；S4 加日志、导入、认证后的 `/api/pick/sync` 与一次选剧对话 |
| A-15 | P2 | 联动物化后缓存，缺时效重判 | 采纳 | D13：缓存冻结事实，可行动性在判定时刻计算；TR-10 `test_link_actionability_read_time`、TR-24 `obs-link.test` |
| A-16 | P2 | 回答核对可能漏接线；「所有反例变红」只在 2b 是门槛 | 采纳 | 核实：`middleware.py:103` 只传两个参数。D42 必填参数；TR-28 走真实 `_record_checks`；12.1 要求每批都附本批反例的变红日志 |
| A-17 | P2 | 35.5 人日只能算条件预算，日历依据不足 | 采纳 | 第 13 节重估并单列等待、标注与运行时长；第 9 节写明并行人数与用户响应假设 |

### 14.3 G3 处置

评审对象：`feat/trends-radar` 的 `c095fe4`（基线 `1df57d6`），两路：分层审与接缝补漏批判。两路都没有访问 Google、生产库或生产服务，也没有重跑数据库套件（扩展 3417、前端 2747 通过是已有证据）。编号沿用评审原文：「分层 P2-1」是分层审的 P2-1，「接缝 1」是接缝审的第 1 条，A–E 是两路对同一组问题的回答；两路指向同一问题的合成一行，编号写在该行第一格，用顿号隔开。分层审没有核实到 P1；接缝审列了 4 项 P1。

**放行口径**：标「S0 前」的各项合入 `feat/trends-radar` 之后，才开始 S0；S0 把它们合进 `ggwork/main`、在最终共同 SHA 上重跑通过之后，才进 S1（第 10 节 S0、S1）；S7 另需 S6a 通过、市场对照词表已冻结（两路的 B）。标「G4 前」的不阻塞 S0–S7（金丝雀不发布集合），在 G4 之前关闭。本节记的是处置决定与落点；各项是否已经合入，以集成时的提交与测试为准。「负责」列里的「G3 金丝雀修复」「G3 D24 修复」「G3 文档对齐」是本轮 G3 修复的三组工作，「集成者」是把它们合进 `feat/trends-radar` 的那一步。G3 文档对齐另加 `customizations/pick-workbench/tests/observe/test_rollout_plan.py`，把第 10 节的 S0、S1、S4、S5、S6、S6a、S7、S11、S13、部署来源、来源检查与合同能力检查的分工、回滚矩阵（两份一致、含库维度）、`deploy-guard.md` 的 cron 一条、四格验证的命令与点名的测试，以及本节（第一格列全两路的编号、每行有负责的一方、「S0 前」「S7 前」的门槛出现在对应步骤的前提里）都对着代码与文件钉住；CI 的触发路径随之加上本计划与 `api.test.ts`。

| 编号 | 级别 | 要点 | 处置 | 落点 | 负责 | 门槛 |
|---|---|---|---|---|---|---|
| 接缝 1 | P1 | 共享批次存在不等于有效负载：对照身份缺失、近期剧目为零时只跑市场词，也能报 100% 覆盖；缺失清单只写在 `plan_json.notes`，`status` 看不到 | 采纳 | 新增 S6a 不发 HTTP 的预检 `python -m ggwork_pick.observe.trends preflight`（以实现为准）；明确最低负载与必要对照，不满足就拒跑，或把该日记为无效验收日、不计入三天与七天，原因进日志与 `status` | G3 金丝雀修复（预检、准入与 `status`）；G3 文档对齐（第 10 节 S6a） | S0 前 |
| 接缝 2、分层 P2-1、两路的 A | P1、P2 | 阶段 0 的 `--pace user`（桶 4、每分钟补 2）没进生产，`run.py` 固定构造默认 `EnvelopePacer()`（桶 8、每分钟补 4）；容量校验「计划 ÷ 2.9 + 40 分钟」漏算熔断后整晚半速，canary2 的排期装不下一次早期熔断 | 采纳 | 生产节奏经 `PICK_OBS_TRENDS_PACE`（默认 `user`）交付，与计划量、起跑时刻一起冻结；容量校验绑定所选节奏，用真实执行器与假时钟覆盖无熔断、早期 429 后半速、午夜续跑、截止截断，不能只改常数 2.9；优先减量或提前起跑，保留 01:45 硬截止，`ModeLimits`、cron 小时范围、部署时段、测试与手册一起改（第 9 节、`budget.py`、`deploy/pick-obs/trends/railway.toml`、`packaging.md` 第 3.1 节与第 7 节）；target_date 与 D23 不变，`window_end` 随首次建批次的时刻变（改起跑后第一天的窗口间隔见「分层 A（窗口间隔）」一行）；参数在计入验收天数之前固定，中途改就开新的验收周期 | G3 金丝雀修复（节奏、容量校验、`ModeLimits`、cron、部署时段与 `packaging.md`）；G3 文档对齐（S5 的 `PICK_OBS_TRENDS_PACE`；S6 只引用 `packaging.md` 第 7 节的部署时段，不写时刻） | S0 前 |
| 分层 A（窗口间隔） | — | 起跑时刻改动后的第一天，相邻两天 `window_end` 的前移不再是约 24 小时；设计 4.9 第 6 条要求可比的两次观测前移 20–28 小时，不满足时不能自动给 confirmed | 采纳 | TR-17 的可比条件按两个批次实际冻结的 `window_end` 算前移是否在 20–28 小时内，不按起跑时刻推算，不满足只记 first；TR-17 加 `test_confirmed_across_start_change`（第 7 节 TR-17）。每次改起跑时刻（含 canary1 → canary2 → stable）后的第一天，核对这一天的前移量，记进金丝雀汇总或影子运行记录 | TR-17（规则与用例）；TR-30（改起跑后第一天的核对） | G4 前；改起跑时刻后的第一天核对一次 |
| 接缝 3 | P1 | 第 9 节「出现验证码即终止」没有跨日落实：`session_summary.py` 与 `run.py` 只看熄火天数达到 2，第一次验证码之后次日照常采集；`test_trends_run.py:280` 钉住了这一行为 | 采纳 | 「验证码或同意墙出现一次」与「其他原因熄火累计两天」分开判定，依据持久化记录，启动拒跑与收尾同一口径，崩溃后也成立；改掉钉住错误行为的测试 | G3 金丝雀修复 | S0 前 |
| 接缝 4、分层 P2-3 | P1、P2 | S4 要求导入 S11 才交付的 `ggwork_pick.observe.read`，照单执行必卡；扩展入口不依赖它，不是网关启动故障 | 采纳 | 第 10 节 S4 改验 `observe.selfcheck`、`observe.grants` 的导入，保留迁移头、扩展启动日志、权限实读、`regrant --check`、认证后的 `/api/pick/sync` 与普通选剧对话；`observe.read` 的导入检查移到 S11；不补空壳模块 | G3 文档对齐 | S0 前 |
| 接缝 6、分层 P3-1、两路的 E | P2、P3 | S5/S6 与 `packaging.md` 分叉：变量写成错名 `PICK_OBS_EGRESS_URL`，首次部署直接用 cron 配置，S6 没写怎样传 `--selfcheck-only`；计划写的是 `origin/main` | 采纳 | S5/S6 统一引用 `packaging.md` 的双配置流程（先自检配置、核对包摘要／角色／迁移头，再从同一提交切回 cron 配置，最后记守卫结果）；变量名统一为 `PICK_OBS_EGRESS_ECHO_URL`，代码不加旧名别名；S5 补 `PICK_OBS_TRENDS_ROUTE`、`PICK_OBS_TRENDS_GRANULARITY`（G2 定）与 `PICK_OBS_TRENDS_PACE`；D41、TR-34、第 10 节与手册改为经守卫核验的 `ggwork/main`；`deploy-guard.md` 的 cron 一条改指 `packaging.md` | G3 文档对齐；集成者（`packaging.md`、`trends-session.md` 里跟着旧 S5 写的几处，见下文「集成核对」） | S0 前 |
| 分层 P2-4 | P2 | 守卫只保证 Git 来源与迁移链，不保证回滚后仍有 F1、M1 的合同能力：main 上 revert 掉 TR-16 的提交，守卫照样放行 | 采纳 | 第 10 节回滚规则、`rollback-matrix.md`、`deploy-guard.md` 把来源检查与合同能力检查分开写；每次发布与回滚在要部署的提交上跑四格验证：前端用例名必须出现在输出里，gateway 点名的文件在设了 `PICK_TEST_PG_URL` 的 `-v -rs` 输出里逐个出现、skipped 为 0，被删或被跳过按没过；S13 之后的 M1 下限同法验证 | G3 文档对齐 | S0 前 |
| 分层「回滚矩阵应明确数据库维度」 | — | 矩阵没有库维度：F1×G0×DB0006 与 F1×M0×DB0007 的允许格、任意前端×G0×DB0007 的禁止格、已有新卡后回滚 M0 或恢复 F0、0007 downgrade、止损方向 | 采纳 | 第 10 节回滚矩阵与 `rollback-matrix.md` 逐格补齐 | G3 文档对齐 | S0 前 |
| 接缝「S0 合并」 | — | 合并 main 之后没写要刷新托管副本、在最终共同 SHA 上重跑；`c095fe4` 的结果不能替代合并后的证据 | 采纳 | 第 10 节 S0；S1 的前提写上这次重跑已通过 | G3 文档对齐（写法）；集成者（S0 执行时照做） | S0 |
| 分层 P2-2、接缝 5、两路的 C | P2 | 标题 A→B→A 让人工对应确认自动恢复，违反 D24 的「失效后重新确认」；`test_decisions_state.py:122` 钉住了这一行为。空平台拒绝确认可以接受 | 采纳 | 确认绑定对应关系的修订号，标题、平台或有效身份映射变化就推进修订号，只有新确认才能恢复；旧集合按冻结版本读，不改写历史；改掉钉住错误行为的测试。空平台不放宽 `CorrespondenceConfirm.platform`，不用占位值，页面说明「平台未知，先补全来源资料」 | G3 D24 修复；TR-25（空平台的页面说明） | G4 前 |
| 分层 P3-2、两路的 D | P3 | 强停后「不会丢状态」说过了头：预算先提交，请求日志与熔断状态在响应之后才提交，中间被杀会丢未提交的响应并重做未完成单元；没有 SIGTERM 处理 | 手册改写；SIGTERM 收尾列为后续改进 | `packaging.md` 第 7 节「停用」一条改为「已提交的状态保留，预算不退；未提交的响应、最新 cookie 或熔断事件可能丢失，续跑可能重做未完成单元；五分钟是租约失效的上界，不是恢复时间」；有界 SIGTERM 收尾（停发新请求、完成当前响应事务、释放租约）不阻塞 G3 | G3 金丝雀修复（与同一节的部署时段一起改；集成者核对已改）；SIGTERM 收尾另立后续任务 | 手册 S0 前；SIGTERM 后续 |
| 两路的 B | — | 市场对照短语补齐只保证清单完整，不证明该市场、该粒度下有数据 | 采纳；不阻塞 0007，也不阻塞验证出口、限速、预算、租约的金丝雀 | 每个 geo 按用途（可用性正对照、行业大盘对照）选词，按 G2 的粒度小样本验证后冻结，记下词表版本；小样本验证要访问 Google，另经用户批准，没批准就按现有 `canary_controls.json` 冻结；中途换词就开新基线；`no_data` 记为该市场对照不可用，不补零，不为刷出数据重试；没有合格基线时不出依赖它的业务结论（TR-17 `control_unavailable`、TR-30 汇总）；所有正对照都缺、退化成接缝 1 那种小负载时，阻塞有效验收（S6a）。第 10 节 S7 的前提列这一条 | TR-30（S7 前冻结；小样本验证经用户批准） | S7 前冻结 |
| 接缝「共享数据库」 | — | 没有已成立的「采集拖垮网关」路径；NullPool 不证明 Supavisor 端只有两条物理连接，连接余量（约 52–57／上限 60）待实测；`status` 短时多一条连接；0007 的 DDL 会取迁移锁 | 记录 | S0–S4 部署冻结照旧；S7 首晚与 G5 的连接账实测（`supabase.md` 连接账） | TR-30（S7 首晚实测） | G5 |
| 其余逐层与逐接缝判断 | — | 迁移 0007、observer 授权、`db.py`／`lease.py`／唯一写入口、`selfcheck.py`、守卫的迁移防回退、Railway 仓库配置、前端 F1 先于网关上线、S2→S3 授权自举、S3 网关启动、镜像与共享批次链路：可以上线，没有发现阻塞 | 无需处置 | — | — | — |

**集成核对**（跨组的文字同步，不是上表哪一行的缺陷；集成者在三组合入 `feat/trends-radar` 时核对，缺的交 G3 金丝雀修复或 TR-29 补）：
- 第 10 节只由 G3 文档对齐改，G3 金丝雀修复改第 9 节（模式参数与时间线）；两组都不动对方那一节。
- 部署时段以 `packaging.md` 第 7 节为准（第 5 节第 4 步也写了同一时段，两处一起改），数值以 `test_railway_config.py` 的 `DEPLOY_WINDOW` 与 cron 计划为准；按金丝雀最终选定的起跑时刻核一遍。第 10 节 S6 只引用，`test_rollout_plan.py` 钉住第 10 节不写时刻、`packaging.md` 第 7 节写的就是 `DEPLOY_WINDOW`。
- `packaging.md` 第 5 节变量表 `PICK_OBS_EGRESS_ECHO_URL` 一行与 `trends-session.md`「环境变量名写错」一段仍说计划 S5 写的是 `PICK_OBS_EGRESS_URL`：计划 S5 已改用正确名字、旧名只作错名提示，两处改成「旧稿写的」。
- `packaging.md` 第 10 节的 TR-34 与「计划」两条仍说 `deploy-guard.md` 与计划的 S5/S6「要改」：已由 G3 文档对齐改完，两条改成已完成。第 8 节的 CI 触发路径补上本计划与 `api.test.ts`（工作流已加）。
- S6a 的命令名与 G3 金丝雀修复的 `preflight` 子命令一致；它在 Railway 的 cron 服务上怎样带着同一套服务变量运行，现在还没有手册写，由 G3 金丝雀修复写进 `packaging.md` 与 `trends-session.md`，第 10 节 S6a 以那里为准。
- 目录页 `README.md` 里 `packaging.md`、`deploy-guard.md` 的状态仍是「未写」，由 TR-29 更新。
