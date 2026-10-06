# GGWork 个人选剧工作台可用性收尾实施规格 v1.1

> 给 Code Agent：按本文逐任务实施，先写能复现行为的测试，再做最小改动，再验证。安装了 `executing-plans` 技能时可以使用它；未安装时直接按本文检查清单执行，不以技能缺失阻塞。本文是实施规格，不是已经完成这些任务的声明。

**Goal:** 在现有个人选剧 MVP 上完成时效语义修正、候选决策信息、模型输入瘦身、回答核对改进与当前模型业务验收，使一次「查询 → 核对 → 换批/追问 → 确认保存 → 恢复」有明确、可验证的结果。

**Architecture:** 沿用 DeerFlow 用户、会话与运行层，业务能力继续归 `ggwork_pick` 扩展。筛选、排序、权限、候选快照和保存事务由服务器确定；模型解释资料并调用受控工具。新增展示说明走 `/api/pick/results/{id}/notes`，模型投影与持久化/HTTP 结果保持分离。

**Tech Stack:** 当前仓库锁定的 Python/FastAPI/SQLAlchemy/Alembic、Next.js/React/TypeScript、Supabase PostgreSQL；Railway gateway、Vercel frontend。不得顺便升级框架、模型、CLI 或数据库版本。

**规格日期:** 2026-10-05，Asia/Shanghai。**状态:** 可交给 Agent 实施；本文编写时只有规格交付，尚未实施下列改动。

**v1.1 修订:** 按综合审计纠正运行预算基线；为验收增加独立意图预期与前置状态；隔离合成/真实 QA；补齐证据投影字段；明确 RD-02 的现有能力与新增工作；前置基线并区分自动检查与人工判读。保留本文件路径，替代 v1.0 执行说明。

**2026-10-06 后续范围:** 用户已明确选定「可逆字典 + 简化雷达」。本文件的 RD-07 只读审计属于此前 readiness 阶段；新的简化雷达实施与部署按 [后续计划](2026-10-06-pick-workbench-followup-plan.md) 和 [简化范围](2026-09-30-trends-radar-simplified-scope.md) 执行。GSC、智能体趋势排序、集合发布及两周影子运行不在所选范围；预算、租约、验证码/同意页停止及三个真实有效夜晚的出口门槛仍保留。

---

## 0. 可以直接转发的执行指令

```text
请在 phananhson733-oss/ggwork 对应的 ggwork-deerflow 项目中，按
docs/plans/2026-10-05-pick-workbench-readiness-spec.md 实施个人选剧工作台可用性收尾。

先确认仓库身份、最新业务 main、工作区状态和 AGENTS.md；从最新业务 main 建独立 worktree。
不要在旧 pick-workbench 仓库实施，也不要直接在旧 feat/pick-mirror 上继续。
执行 RD-01 到 RD-06；RD-07 只做趋势雷达的只读就绪审计，不能改变生产开关或触发采集。
先执行 RD-05a 的验收合同/环境预检和基线，再修改产品代码；最后执行 RD-05b 的集成验收。
记录目标实际生效的执行预算，不把旧的 120 秒回退值作为生产预算。合成 E2E 与真实剧库验收使用不同配置和 QA 身份。
遵循文件分工、依赖顺序、TDD、源目录/托管副本一致性和契约兼容要求。
完成代码、测试、文档和可审查 PR。本文本身不授权合并或生产部署；如当前会话另有明确发布授权，按本文发布门槛完成，不重复询问已授权事项。
真实模型验收按 QA 约束执行；凭据缺失就交付已经完成的代码和验证，准确列出未完成的验收，不能用 mock 替代真实验收结论。
每个结果说明基线 SHA、实际改动、运行命令、通过/失败/跳过数量、剩余风险，附证据位置。
```

### 0.1 仓库身份和基线

| 项目 | 正确值 |
|---|---|
| 本地主仓库 | `/Users/wzb/Code/ggwork-deerflow` |
| 业务 GitHub | `https://github.com/phananhson733-oss/ggwork` |
| 主仓库业务远端 | `ggwork` |
| 上游远端 | `origin` → `https://github.com/bytedance/deer-flow.git` |
| 线上前端 | `https://ggwork-deerflow.vercel.app` |
| 编写规格时的业务 main | `cd58837f7f5c1a1a17a5992610e9d03be692c2f4` |
| 最新业务发布记录 | `ddbf9f14c882052d42dd0217e595a9c90c7694fc`，PR #29 |
| 本地主目录当时状态 | `feat/pick-mirror@1df57d66`，是旧分支，不能当作当前生产能力 |
| 容易混淆的另一项目 | `/Users/wzb/Code/ggwork` → `phananhson733-oss/pick-workbench`，不是实施目标 |

上述 SHA 只用于定位本规格基线。执行时重新读取最新业务 main，不把旧 SHA 写成部署固定值。在一个业务仓库被克隆为 `origin` 的环境中，使用其真实业务远端，不依赖远端名称猜仓库。

建议起步命令（工作树路径与分支名用任务自己的新名字）：

```sh
cd /Users/wzb/Code/ggwork-deerflow
git rev-parse --show-toplevel
git status --short
git remote -v
git fetch ggwork main
git rev-parse ggwork/main
git worktree add -b codex/pick-readiness-rd01 /Users/wzb/Code/ggwork-deerflow-wt/readiness-rd01 ggwork/main
```

不得 reset、clean、stash 或覆盖其他人的未提交工作。若 main 已包含某项实现，先按本规格验收；已满足的项直接记录为已有能力，不重复重写。

### 0.2 必读材料

所有相对路径均以**当前任务 worktree 的仓库根**为准，不能混读旧主目录的文件。

- `AGENTS.md`、`customizations/pick-workbench/AGENTS.md`。
- 前端任务：`frontend/AGENTS.md`、`frontend/src/AGENTS.md`。
- 宿主/测试/脚本任务：相应的 `backend/AGENTS.md`、`backend/tests/AGENTS.md`、`scripts/AGENTS.md`。
- `docs/plans/2026-09-21-pick-workbench-mvp-plan.md`。
- `docs/pick-workbench/progress.md`，尤其最新发布记录，不只读首页历史状态。
- `docs/pick-workbench/realshort-sync.md`、`acceptance.md`、`pick-board-parity.md`。
- `docs/pick-workbench/observe-runbook/deploy-guard.md`、`rollback-matrix.md`、`packaging.md`。

## 1. 当前事实与本期边界

### 1.1 编写时已核对的事实

2026-10-05 线上只读检查看到镜像 v22、全部剧库 75,120 行、智能体候选池 11,584 部、发布记录 313 条；最近五次定时同步成功。这些是当时快照，不是测试常量。

个人 MVP 已有真实剧库查询、规则检索、计数、候选/详情、个人确认保存、备注/移出/恢复/CSV、历史候选与结果引用。最新两批修复已上线，包含换批链排除、notes、零结果诊断、条目事实、重新生成引用和宿主提示裁剪。

最新版本的真实模型完整验收仍需收口。此前的 10 题验收及旧模型结果不能直接替代当前模型和当前代码的验收。

### 1.2 本期必须交付

1. 历史候选与当前运行使用正确的时效参考时间，历史卡不误报当前同步停止。
2. 候选卡提供足够的比较、来源和待核实信息，unknown 清楚可见。
3. 减少传给模型的重复元数据，保护持久化结果与旧客户端兼容。
4. 修正已知回答核对误报，覆盖已返回剧目的无书名号说法，并保持核对范围诚实。
5. 提供可重复、按冻结数据独立核对的业务验收和性能基线。
6. 当前状态文档、验收文档和运维索引与实际代码/发布一致。
7. 单独给出趋势雷达只读就绪结论，明确其能否进入下一阶段。

### 1.3 本期不实施

团队共享、跨人审批、选剧池外部写入、排期、自动发布、飞书写操作、偏好自动学习、向量库、通用工作流、多 Agent runtime、模型切换、新地区评分体系、趋势雷达从 canary/shadow 切 stable/live。

这些不是个人 MVP 的漏项。需要做时另开规格，不能通过开放跨 owner 查询、加入一个 status 字段或增加模型提示来替代完整设计。

## 2. 全局不变量

以下任一条被破坏，都不能验收通过。

- 服务器认证 owner 决定读写范围。模型参数、客户端传入 owner、`confirmed=true` 均不能授权。
- `result_id + item_id` 定位对象；同名标题、列表序号、客户端“最新候选”不能替代服务器归属检查。
- 序号来自已绑定结果的冻结顺序。查询、计数、解释不能写个人清单；准备保存也不等于已经保存。
- 已提交保存按原 request_id/幂等键重放回执；不能为重试生成新请求号。
- result、存储 item、`data_as_of` 是既有严格契约。本期不新增字段、不改编号、不修改旧 JSON、不回填迁移。
- notes 是独立、可加字段的展示接口；缺失、410、网络错误分别处理，不能使已有候选整体不可用。
- 标签、渠道、日期和证据来自该结果自己的批次。不得偷换到最新批次补旧卡。
- `catalog_rows` 及可达的嵌套对象可能被缓存共享；任何投影/拼接都必须新建对象，不原地修改。
- 默认排除明确下架；unknown 不等于 active。渠道 unknown 不等于 allowed。未匹配发布记录不等于从未发布。
- 英语不等于美国需求；`hot_only` 是来源信号筛选，不是收益预测。不同榜、不同日期的名次不混排。
- 每轮模型 12 次、业务工具 8 次、插件 8 次、Lark 8 次的既有调用上限保持；运行期限按目标实际生效配置保留，既不缩短也不提高。默认值、覆盖值与 QA 等待规则见 2.1。
- 不把生产剧库、令牌、cookie、连接串、原始工具日志提交到 Git。原始验收产物写到 ignored/私有目录；仓库仅存合成 fixture 和脱敏汇总。
- 业务表仍归 `ggwp_` 和独立 Alembic 链。本期预期无迁移；若实现确实需要迁移，先说明必需性，不能为了展示说明另建持久化系统。

### 2.1 运行配置与 QA 等待合同

先读取 `backend/app/gateway/pick_entrypoint.py:MODEL_DEFAULTS`、`config.pick.example.yaml` 与目标实例实际覆盖值，形成不含凭据的配置快照。以实际生效值为准；不能只看源码默认值推断生产配置。

| 配置 | 本规格基线的入口默认值 | 作用 |
|---|---|---|
| `PICK_RUN_TIMEOUT_SECONDS` | 600 秒 | 宿主 watchdog 与业务扩展整轮期限 |
| `PICK_LLM_REQUEST_TIMEOUT_SECONDS` | 300 秒 | 单次模型请求期限 |
| `PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS` | 300 秒 | 流式字节之间的最长静默 |
| `PICK_LLM_MAX_OUTPUT_TOKENS` | 32,000 | 单次输出上限，含 reasoning |
| `PICK_LLM_EFFORT_THINKING_ON/OFF` | high / low | 开/关思考时的模型档位 |

`context.py` 的 120 秒仅为缺少环境变量时的回退，正式 pick 入口会设置整轮预算。不得据该回退值把目标改成 120 秒。模型部署名、当前模式、上述实际值、应用 SHA 和读取时间一同进入 QA manifest；未知值不填 0，也不称“已与生产一致”。

真实模型测试的单轮等待上限为 `实际 run_timeout_seconds + 60 秒终态传输/持久化余量`；这只是测试等待，不延长产品执行期限。每个多轮 case 的测试总超时至少为 `计划最多的新 run 数 × 单轮等待上限 + 120 秒登录/交互余量`。新 readiness 配置按此计算，不能继承旧配置的 360 秒总超时或旧用例的 150 秒等待来判断一个 600 秒预算的运行失败。故障注入用例可以使用独立、显式的短期限，但只证明该测试配置的取消/超时机制。

## 3. 任务包与依赖

| ID | 内容 | 优先级 | 依赖 | 交付类型 |
|---|---|---|---|---|
| RD-01 | 历史时效语义 | P1 | RD-05a 基线 | 后端修复 + 双库回归 |
| RD-02 | 现有候选卡的核对效率与时效展示 | P1 | RD-01 notes 契约 | 前端 + 合同/UI 回归 |
| RD-03 | 模型工具投影瘦身 | P2 | RD-01 后端落定 | 后端 + 投影/预算回归 |
| RD-04 | 回答核对误报与覆盖 | P1 | RD-05a 基线；集成时含 RD-03 | 后端 + 语义/复杂度回归 |
| RD-05 | 当前模型验收与基线 | P1 | 05a 前置；05b 依赖 RD-01～04 的集成 SHA | QA 工具、E2E、证据报告 |
| RD-06 | 文档、集成、发布交付 | P1 | RD-01～05；纳入 RD-07 结论 | 文档 + 集成检查/PR |
| RD-07 | 趋势雷达只读就绪审计 | P2 | 可独立 | 状态/证据报告，默认不改功能 |

建议每项一个可审查 PR；也可一个 PR 内按任务独立提交。RD-01 → RD-02 先形成一个最小纵向修复，再集成其余任务，不等所有工作都完成才发现契约冲突。

RD-05a 包括预期合同、QA 隔离预检、合成 baseline 和最多六个关键场景的改前冒烟；RD-05b 是改后的完整矩阵。缺真实 QA 凭据时，05a 的真实部分记 NOT_RUN，先保存合成基线后可以继续产品实现；该缺口不能在最终真实验收中被当作通过。RD-07 独立进行，其 CANARY_PENDING/BLOCKED 结论不阻塞个人选剧修复的代码交付。

## 4. RD-01：历史候选的时效参考时间

### 4.1 问题与目标

打开 9 月 30 日历史候选时，notes 目前按今天计算批次年龄，展示“超过 14 小时没有新批次，同步可能停了”。线上最新批次和定时同步却正常。

目标不是隐藏旧数据，而是分清：**查询发生时资料是否过期、这是一份旧候选、当前同步是否异常**。

### 4.2 文件范围

- 修改：`customizations/pick-workbench/ggwork_pick/freshness.py`。
- 修改：`customizations/pick-workbench/ggwork_pick/selection.py` 的 notes/解释/详情路径；只动本项需要的部分。
- 修改：`customizations/pick-workbench/ggwork_pick/tools.py` 的 `get_drama_detail_tool` 时效接入；当前详情只返回 item 与 data_as_of，不会自动经过查询解释路径，必须补齐本项合同。
- 必要时修改：`customizations/pick-workbench/ggwork_pick/routes.py`，仅为传递可信时间。
- 测试：`tests/test_freshness.py`、`tests/test_result_notes.py`、`tests/test_routes.py`、`tests/test_tools.py`，均位于业务扩展测试目录。
- 托管副本按第 11 节刷新，不手工双份修改。

### 4.3 时间合同

| 路径 | 年龄计算参考时间 | 数据来源 |
|---|---|---|
| 本轮新查询、计数、详情供模型使用的时效提示 | 本轮实际运行时间 | 该轮固定的批次/被绑定结果批次 |
| `GET /results/{id}/notes` 的历史说明 | `record.created_at` | 该结果冻结的批次和 `data_as_of` |
| 榜期是否过期 | 仍以批次采集时刻判断 | 该批次的实际榜期 |
| 同步页/最新资料页的同步健康 | 当前时间与当前可用批次/运行记录 | 当前同步状态 |

不能为 notes 的无效 `created_at` 静默回退到 `datetime.now()`；时间读不出时只保留能确定的证据和榜期说明，说明无法核对查询时点，不推断同步停止。

现有阈值保持：批次 14 小时、剧单 36 小时、日榜 2 天、周榜 14 天；不得顺便改变边界比较符。

`data_notices` 的纯函数只知道调用方给它的数据时间，不知道系统当前最新批次或同步进程是否健康。因此它在任何路径都不能单凭旧批次年龄判断“同步停了”；改成“本轮使用的资料已超过 N 小时，可能不是最新资料”。“同步可能停止”只由读取了当前同步状态的页面/接口表达。这样明确沿用旧批次的模型追问也不会误报系统异常，无需为本项增加查询最新版本的 API。

### 4.4 notes 最小增量

在 notes 中允许新增一个**可选**字段，供 RD-02 使用：

```json
{
  "notices_reference_at": "2026-09-30T12:14:11.000000+00:00",
  "data_notices": ["查询时该榜最新一期已超过两天，只能作历史参考。"],
  "item_facts": {}
}
```

- 值为服务器结果创建时间的 ISO UTC 字符串；读不到时为 null。
- 前端接受缺失/null/合法时间。字段只属于 notes，不进入 result、item 或 `data_as_of`。
- 不新增 latest-version 查询来实现本项；“看最新资料”沿用现有资料入口，“使用最新数据”由用户新查询或明确 use_latest 决定。
- notes 文案不声称“这就是模型当时收到的完整说明”。旧结果没存过时效文本时，它是**按生成时点重新核对**。
- 历史说明若需警示，使用“查询时资料已过期”；不使用“当前同步可能停了”。模型当前运行使用旧批次时，可以说“本轮沿用旧批次”，也不能仅凭旧批次断言系统没有更新。

### 4.5 必须先写的测试

1. 冻结时钟：9/30 候选、当时资料新鲜、10/5 打开，notes 不出现当前同步停止提示；返回可信参考时间。
2. 当候选生成时资料已超过阈值，历史 notes 仍给出过期说明，不能一律清空历史告警。
3. 同一旧批次用于今天的模型追问，查询、计数、`pick_get_drama_detail` 三条真实工具入口各自收到当前参考时间的旧资料警示；不能只测纯函数或 notes 来替代详情路径。
4. 最新批次真的超过 14 小时，当前查询/同步状态仍保留对应提示。
5. 14h、36h、2d、14d 的等于/超过边界，UTC 与带偏移输入。
6. created_at 缺失/非法，不冒充已完成时间核对；不产生 500。
7. 旧结果无冻结 `data_as_of_json` 时保持已有回退语义，不读取别人的资料。
8. owner 不符/不存在 404、批次清理 410；notes GET 无 INSERT/UPDATE/DDL。

**DoD:** 新旧两个时点的语义有测试证明；SQLite/PostgreSQL 均通过；真实旧卡与当前同步正常的组合不再出现矛盾提示。

## 5. RD-02：现有候选卡的核对效率与时效展示

本期改进是信息可读性和核对效率，不改变候选排序，也不据此宣称推荐质量、投放效果或收益提高。当前已上线标签/上架/渠道、零结果诊断、热门口径、时效说明、结果引用和保存回执；实施者先验收现有行为，只实现下面的增量。

| 已有能力 | 本期增量 | 可观察的验收点 |
|---|---|---|
| reason、折叠证据、来源链接 | 卡片可见区域突出主依据、评级/名次及日期；完整证据仍可展开 | 不逐张展开全部证据，就能辨认主要依据及日期；无日期明确未知 |
| 标签/上架/渠道事实行 | 分清渠道允许与上下架待核实等组合 | allowed + unknown 不被合成“确认可发” |
| warnings、发布记录说明 | 待核实信息靠近相关事实，消除同一事实的重复文案 | 未匹配、已发、账号范围可区分，不出现互相矛盾的短标签 |
| notes 的时效/热门说明 | 使用 RD-01 的参考时间，批次说明集中一次 | 旧卡不误报当前同步；不再展示“回答里说明”等模型指令 |
| 勾选、证据、保存及 notes 容错 | 保持主操作可达与异步加载独立 | 390px 和 1280×720 下均可查看依据、勾选和保存；notes 延迟不阻塞 |

主依据从本结果对应的信号中确定：显式请求榜单时显示该榜匹配证据；否则按现有结果排序口径选可解释的证据，日期并列保留稳定次序，未知日期不伪造。不得新增收益评分、改变服务器返回顺序，或隐藏否定性证据。多个来源不能归成一个跨榜名次。

### 5.1 文件范围

- `frontend/src/core/pick/notes.ts`：notes 字段、纯格式化函数。
- `frontend/src/components/workspace/pick/candidate-view.tsx`：候选信息布局。
- `frontend/src/components/workspace/pick/use-result-notes.ts`：仅必要的加载/缓存调整。
- `frontend/src/core/pick/format.ts`：确有共用格式时修改，不另建通用 UI 框架。
- 测试：`frontend/tests/unit/components/workspace/pick/candidate-view.dom.test.tsx`、`frontend/tests/unit/core/pick/notes.test.ts`。
- 时效、地区措辞使用已有提示约束；若需修订技能只改 `skills/public/pick-drama/SKILL.md` 的相关文字。

### 5.2 每个条目的三类信息

保持候选原顺序和现有保存控件，补齐/整理为：

1. **入选依据**：条件命中；所选榜单/信号、名次（若有）、证据日期。不得称为预测收益或最佳投放。
2. **可核实事实**：标签、上架日期、渠道规则、发布记录匹配状态。继续从 notes.item_facts 和原 item 读取。
3. **待核实事项**：上下架 unknown、目标渠道 unknown/denied、发布记录未匹配、证据日期未知/过期。

复用既有字段和 warnings，不再另造一套 readiness/status 业务状态。不要把“没有警告”显示为“可直接发布”。

### 5.3 明确的呈现规则

| 数据 | 必须表达 | 禁止推断 |
|---|---|---|
| 目标渠道 allowed + availability active | 资料中规则允许、已确认在架 | 已获本次发布授权 |
| allowed + availability unknown | 渠道规则允许；上下架待核实 | 确认可发 |
| channel unknown/缺失 | 渠道待核实/未注明 | 可发 |
| channel denied | 明确禁用目标渠道 | 建议绕过限制 |
| posted.matched=false | 发布记录未对上，不代表从未发布 | 没发过 |
| 日期未知 | 日期未知 | 用导入日代填 |
| 多种榜单信号 | 分别显示依据，不合成跨榜名次 | 综合榜第 N |
| 询问美国但只按 en 筛选 | 英语候选，不能据此确认美国需求 | 美国爆款 |

候选级别的数据说明放在条目列表前，避免每张卡重复大段告警。`notices_reference_at` 存在时显示“按候选生成时点核对的数据时效”；null 显示“查询时点未知”；旧 gateway 无字段时用中性“数据时效说明”。中文运营界面时间显示北京时间并明确时区，内部合同仍 UTC；复用现有格式化函数。

若已有 notices/hot_scope 文字包含“回答里说明”等模型指令，新路径改为用户可读事实描述；不要在产品 UI 展示给模型的操作指令。

### 5.4 容错、性能和测试

- notes 尚未返回时，候选标题、证据、选择/保存可用；不等 notes 才注册结果引用。
- 404 兼容旧 gateway；410 明确旧依据已清理；其他错误显示“依据说明暂不可用”，都不清空原候选。
- 切换用户/线程/卡片，不能短暂展示前一人的 notes。保持 user + resultId 缓存隔离。
- 5/10/20 部、无证据、长标签、多渠道、中文/英文片名均可使用，无不可访问横向溢出。
- 证据默认折叠，折叠不影响引用与保存；不要为每行额外请求详情。
- 增加一次 390px 手机、1280×720 桌面 UI 验证，入口可达、按钮可见；本期不重做宿主响应式导航。
- 保留“已核对/未核对/读取失败”的差别，不扩张“已核对”的文案范围。

**DoD:** 提交上述“已有→新增”逐项对照和同一合成输入的改前/改后界面证据；用户能在卡片可见区域回答“凭什么入选、依据哪天、还需核实什么”。旧卡、失败状态、unknown 的展示有 DOM 测试；合成 E2E 验证 notes 延迟不阻塞主链。仅重新实现或重测已上线字段不能作为本项新增成果。

## 6. RD-03：传给模型的工具投影瘦身

### 6.1 问题与文件范围

候选达到 8～10 部时，原工具结果曾被宿主预算外置为摘要，候选卡解析失败。当前通过 exempt_tools 保住结构，但完整证据元数据重复仍增加 token。

- 修改：`customizations/pick-workbench/ggwork_pick/tools.py`，工具 JSON 输出边界。
- 新增：`customizations/pick-workbench/ggwork_pick/model_projection.py`，仅一个小的纯投影模块，不能发展成通用序列化框架。
- 必要时调整 `selection.py:model_view/detail` 的调用，但必须在 RD-01 之后串行操作同文件。
- 测试：新增 `tests/test_model_projection.py`；扩展 `tests/test_tools.py`、`tests/test_frontend_contract.py`。

### 6.2 投影必须保留

- result_id（沿用工具现有 id 字段）、条目 item_id/identity、冻结顺序、title/theater/language。
- 原 conditions、matched_total、data_as_of（及已有 mirror/observations 合同），不能改字段含义。
- availability、warnings、reason、tags、listed_at、channel_rules。
- 发布记录匹配状态和判断所需的次数、账号数据。
- 每条证据的 `kind、label、observed_at、rank、value、grade、note` 及实际存在的单位/口径字段；`citation_id` 原样保留并可映射回完整证据。保持 null、空串、0 的原含义，不能合并为“缺数据”。`grade` 是评级/确认层级等业务事实，`note` 是说明/限制，不是可裁的展示元数据。
- zero_diagnosis、hot_scope、data_notices 等当前作答需要的说明。

可省略模型无需使用的原始 source_ref、完整 detail_url、重复来源路径等；先检查它们的消费者，不能直接批量删除所有引用字段。证据若缺来源标识就无法区分口径，需保留可辨识的标识；不截断数值、不裁剪片名、不只保留第一条证据。

投影仅作用于 `pick_query_candidates` 与 `pick_get_drama_detail` 的模型返回边界；计数、知识检索、准备保存输出本期保持原合同，不能把同一删字段函数套到所有工具上。特别是知识的 source_ref/citation_id 与保存的 requires_confirmation/item_ids/note 不能被候选瘦身删除。未识别的证据字段默认保留并标记待评审，不使用不完整的白名单丢弃新事实。

**2026-10-06 用户追加批准可逆证据字典。** 上述逐条必留事实允许在模型专用表示中通过 payload 级字典及逐证据引用保留；解码后字段值、类型、顺序、条目和证据数量必须与原内联投影一致。仅提取同 kind 组中重复且类型敏感完全相同的已知事实，保留每条 citation_id/source_ref、独有值、单位/口径及未知字段；命名冲突不得覆盖原字段，模型说明必须明确读取引用。原 display-link 去重仍用小型显式删字段清单；HTTP、快照、缓存和作答核对材料保持完整原合同。原固定 10/20 部字节基准不得替换，至少 20% 目标及真实模型理解验收继续适用。

### 6.3 禁止的捷径

- 不能缩小用户请求数量、把 20 部变成 5 部、改变筛选/排序，以达到 token 目标。
- 不能修改存储 snapshot、结果 HTTP 返回、前端 strict schema 或 source cache。
- 不能为此取消 five pick tools 的预算豁免，或提高模型/工具预算。
- 不能把人工阅读的简短摘要当作候选结果 JSON。
- 不增加 SQL 查询；不为 source_ref 建新持久化索引或新端点。

### 6.4 可验证目标

使用合成数据、固定 ID，构造 5/10/20 部场景，每部包含同样的多来源信号和发布汇总，记录修改前后 JSON UTF-8 字节数与字符数。

投影 fixture 至少包含：只有 `rank` 的榜单；`grade=S` 而 value/rank 都为空的评级；事实只存在于 `note` 的运营备注；带 0/null 与单位说明的数值；已有 obs 证据及其 grade/note。逐字段比较必留字段，而不只检查证据条数；所有合成 obs 用例沿用现有合同，不启用生产观测功能。

- 普通 10/20 部场景，目标减小 **至少 20%**；这是本期工程目标，不是对现状的测量结论。
- 所有必需字段、所有条目和所有证据保留；若字段本身不足以达 20%，报告差额和原因，不能破坏事实来凑指标。
- 20 部结果必须仍通过真实宿主工具预算中间件组合测试，不被摘要替换；前端仍从服务器持久化结果读完整卡片。
- 前端工具卡先从工具 payload 的现有 `id/result_id` 定位结果，再用 `getPickResult` 读取完整卡片；必须保留这条定位路径，并用 `pick-tool-card.dom.test.tsx` 证明瘦身后的工具输出仍能定位、展开、确认保存。
- 同一快照两次投影一致；投影前后源对象深度比较完全相同。
- 回答核对的 known_titles/posted_seen 材料不缩水；详情工具仍能解释同一条目。

**DoD:** 交付合成 benchmark JSON、投影合同测试、真实宿主 wrapper 回归；新旧结果 API 字段对照无变更。不要仅凭“工具输出看起来短了”验收。

## 7. RD-04：回答核对保持准确且克制

### 7.1 文件范围

- `customizations/pick-workbench/ggwork_pick/answer_check.py`。
- 测试：`tests/test_answer_check.py`；确需端到端核对记录时补 `tests/test_routes.py`。
- 如文案范围变化，配套改 `frontend/src/components/workspace/pick/answer-check-note.tsx` 与其测试。
- 不修改历史已经保存的核对记录，不重新扫描所有历史回答。

### 7.2 第一部分：先修已复现误报

已有回答：“这 5 部在团队发布记录里都没有匹配到已发记录，但不能据此断言它们从未发布。”被再次警告不能说没发过。

先写失败测试，覆盖：

- 上述原句及“不能因此断定它们没有发布过”“无法据此确认团队从未发过”等明确否定结论。
- 同一个回答中另一句真正声称“它们都没发过”，仍应提示；不能因为出现一次免责声明就豁免整篇。
- “不是都没发过，有两部已经发布”不能被解释为全部未发。
- 账号未发与团队未发的范围不同；一个账号查询不能为所有账号作结论。
- 带引号的用户原话/问题与助手事实断言分开处理，避免把询问当事实。

### 7.3 第二部分：无书名号的已知剧目

对本轮工具已返回的片名，英文、中文、弯引号、逗号列表、换行/表格中的无《》形式也参与发布说法核对。匹配按已有规范化标题机制，不建立新 alias 数据库。

同名但不同 identity 的记录不能因 title 匹配就互相授权；证据冲突保留较保守的发布状态。未知自由文本不自动判为真实片名：未知剧名检测保留既有范围，本期最多扩到明确的“剧名：…”或候选表格列；不声称可以识别任意文本里的编造标题。

### 7.4 复杂度与持久化

- 不用具有灾难性回溯的正则覆盖整篇文本。
- 延续现有线性扫描方式；用构造长文本测试 1×/2×/4× 输入，记录耗时与算法路径，不能靠一次很宽的 timeout 掩盖退化。
- 核对失败不能使已生成答案整轮失败；干净核对仍存 `notes=[]`，工具调用回合仍不存空核对。
- “已核对”只说明剧名、保存与发布说法等实际检查范围，不表示所有数字、渠道或市场需求已被事实验证。

**DoD:** 原句不再误报，真实错误说法仍拦；无书名号的已返回片名覆盖有测试；旧核对记录保持可读，当前记录按 owner/run/message 正确归属。

## 8. RD-05：独立业务验收与性能基线

### 8.1 要交付的文件

- 新增：`scripts/pick-readiness-verify.py`，独立 checker；不修改历史专用 `scripts/pick-acceptance-verify.py` 的旧验收结论。
- 新增：`customizations/pick-workbench/tests/test_readiness_verify.py`，只用合成 fixture。
- 新增：`frontend/tests/e2e-pick/readiness.spec.ts`，复用现有真实 QA 登录和归属验证方式。
- 新增：`frontend/playwright.pick.readiness.config.ts`，只收集 readiness，按已核对运行配置计算等待时间；不收集合成导入和本地镜像测试。
- 如本地合成 E2E 使用真实模型，修改 `frontend/playwright.pick.config.ts` 与 `frontend/tests/e2e-pick/personal-selection.spec.ts` 中的模型终态等待/多轮测试超时，使其同样符合 2.1；仅调整测试预算，保留原有导入、身份和幂等断言。纯资料页 fixture 不依赖模型配置。
- 更新：`docs/pick-workbench/acceptance.md`，追加当前版本验收，不删旧记录。
- 合成输入/输出 fixture 放 `customizations/pick-workbench/tests/fixtures/readiness/`，必须注明 synthetic。

### 8.2 独立 checker 的输入合同

checker 只读取本地文件，不登录、不联网、不触发同步、不调用模型。它分别核对**预先定义的用户意图、独立捕获的前置状态、实际执行结果**；actual_conditions 是待测对象，不能替代 expected_conditions。

验收产物分为两份：运行前锁定并计算 SHA-256 的 expectations manifest，以及运行中/运行后追加的 captures。manifest 的预期来自固定题目和独立复核，不得根据模型输出倒写；复核者可以是人或评审 Agent，须记录身份和依据。CLI 约定为 `--expectations <manifest.json> --captures <captures.json> --out <report.json>`；所有引用文件相对各自 manifest 目录解析，并核对 hash。

下面是一个查询 case 的结构示例；路径、hash、synthetic 名称为占位，实际运行必须替换为可验证文件。默认条件在 checker 自己的 case fixture 中显式记录，不 import 产品条件归一化函数；actual 缺省字段按这份已审核的合同归一化后比较。

```json
{
  "spec_version": "pick-readiness-v1.1",
  "environment": "synthetic-local",
  "runtime": {
    "app_sha": "<tested-sha>",
    "model": "<verified-model>",
    "mode": "thinking",
    "run_timeout_seconds": 600,
    "request_timeout_seconds": 300,
    "stream_chunk_timeout_seconds": 300,
    "config_evidence_file": "private/runtime.json",
    "config_evidence_sha256": "<sha256>"
  },
  "sources": {
    "catalog-a": {
      "catalog_batch_id": "synthetic-batch-a",
      "source_type": "synthetic",
      "shared": false,
      "rows_file": "private/catalog-a.json",
      "rows_sha256": "<sha256>",
      "metadata_file": "private/catalog-a-metadata.json",
      "metadata_sha256": "<sha256>"
    }
  },
  "states": {
    "state-a": {
      "selections_before_file": "private/selections-before-a.json",
      "selections_before_sha256": "<sha256>",
      "parent_chain_file": "private/parent-chain-a.json",
      "parent_chain_sha256": "<sha256>"
    }
  },
  "cases": [{
    "case_id": "Q01",
    "case_type": "query",
    "prompt": "找 5 部英语剧，排除我已经选过的。",
    "planned_max_runs": 1,
    "source_key": "catalog-a",
    "state_key": "state-a",
    "expected": {
      "allowed_actions": ["pick_query_candidates"],
      "allowed_condition_sets": [{
        "theater": null,
        "language": "en",
        "channel": null,
        "query": null,
        "tags": [],
        "limit": 5,
        "exclude_selected": true,
        "confirmed_eligible_only": true,
        "exclude_previous": false,
        "signal_kind": null,
        "sort": "evidence_date",
        "exclude_posted": false,
        "posted_account": null,
        "hot_only": false
      }],
      "expected_outcome": "query_success",
      "semantic_rubric": ["不把个人未选说成团队未发"]
    }
  }]
}
```

captures 必须回指 expectations 文件的 SHA，并记录 case_id/step_id、QA 身份标识、thread/run/tool_call ID、工具名、原始参数、服务端有效条件、actual batch/source/shared、返回状态、结果 ID、冻结有序条目、总数/分项、原始回答及本次提交/回执引用。多次工具调用不能只留下最后一次成功结果。所有真实原文与 ID 留在私有产物；入仓仅存合成样本和脱敏报告。

#### 预期与状态规则

- 每个 case 的允许动作、完整有效条件集合、期望结果类型、允许的澄清分支先固定。不能只存一句自然语言题目或模型自己的实际参数。
- 新增未请求的 theater/tags/hot_only 等限制，也必须使意图检查失败；只有预期明确允许的归一化/默认值可以接受。
- Q08 的排除集从该 QA 身份在查询前的选择清单独立计算。Q14 另外从实际绑定的父卡及祖先卡的有序条目推导，核对同 owner/thread 和链关系，不直接信任待测系统输出的 excluded_json。
- 查询前无选择/无父链时，相应快照文件明确保存空数组；缺文件、读失败、hash 不符不能当空集。
- 准备/保存 case 还要固定绑定结果、期望 item_ids、用户备注和确认前状态。API 专用测试可预定 request_id；浏览器幂等验收用 `request_id: {"capture_before_dispatch": true}` 预先锁定策略，在首次 UI 请求发送前封存原始 request_id/result_id/item_ids/note、捕获时刻和 hash，再原样放行。重试必须与首次原始请求一致，禁止拦截后替换 request_id 来制造幂等。确认后读独立清单与回执验证；仅重算名单不能证明保存幂等。
- 多步 case 的每一步都有 source_key/state_key。前一步生成的父卡及变动后的个人状态在下一步提交前捕获并封存 hash，不能到整题结束才补造“运行前”状态。
- Q18 使用多个 source 条目分别引用旧/新批次；禁止将两版结果都对照单一“最新 source”。合法定时更新导致预期批次与实际批次不符时，记录该次为 UNVERIFIED_DATA_VERSION，准备新的一致快照后另开有记录的尝试，不覆盖原尝试。

#### 数据捕获和独立计算

通过既有授权的只读数据库/原始 blob 导出路径取得**实际冻结批次**的完整行，核对批次、来源、owner/shared 权限和 hash。导出 SQL 必须带目标批次条件并使用只读事务；不新增可匿名导出剧库的产品 API，不读取其他个人用户批次。没有获授权的精确批次读取路径时，该题保持未验证，不能改用验收结束后的 live feed。

source metadata 同时保存独立读取的 source_as_of/published_at/freshness 和适用的规则/排序版本；历史卡的 created_at 与冻结 data_as_of 单独保留。不能用待测 notes 的文案反推“正确时效”。生产捕获时间不倒填；阈值边界用冻结时钟的合成案例验证。

独立重新实现过滤、排序、计数与排除，不 import `ggwork_pick.selection`、matcher/ranking/helper/条件归一化，也不 import 前端 formatter 作为判据。预期结果由已锁定的 expected 条件与独立前置状态计算；actual 条件仅用于比较“模型是否做对了”。手算合成 fixture 检验 checker 自身，数量、榜期和名字不硬编码为历史生产值。

必须加入 checker 反例：要求 en 却查询 ko；偷偷增加剧场过滤；遗漏一个已选 identity；换批漏排祖父卡；把读失败当空集；返回正确名单但 matched_total 错；使用另一批次；只记录重试成功而丢掉前次失败。以上均不能 PASS。

#### case 类型、结果与退出码

`case_type` 明确为 query/count/detail/prepare_save/clarification/refusal/recovery/knowledge，复合题按 steps 组合；不得用同一空 rows 结构代表计数、拒绝和成功零结果。count 验证 total/分项，clarification 验证缺失参数及后续分支，refusal 验证业务状态与原因，prepare_save 验证事务前后状态及回执。每种类型的必填字段用 schema 和正反例固定；缺 result_id 合法的类型不要求它。

复合工具链在运行前锁定 `tool_contracts`（逐工具 case_type/expected）及允许的调用顺序/次数；同一次 run 的 count→query 或 query→detail 各调用分别核对，不能套用一个查询 outcome 或丢弃中间调用。运行中产生的结果 ID 可用已锁定的 `from_tool`/`occurrence` 关系引用，须与权威调用顺序、同 owner/thread、真实冻结结果及其批次交叉核验，不按模型输出倒写预期。无工具的澄清/取消/恢复用 `record_kind: "terminal"` 与预定 `terminal_contract` 捕获，保留权威 run 终态和回答 hash；不得伪造 tool_call_id，也不能用带工具结果的空数组冒充终态证明。

拒绝结果的来源也必须可核对：只有完成授权读取后，query/count 的业务拒绝才附本次实际使用的 `catalog_batch_id` 和 `data_as_of`（actualPin）；换批沿用父结果冻结 pin，明确 use_latest 才按现有规则更新。无剧库、身份/绑定校验先行失败等尚未完成授权读取的拒绝不附来源，不能拿请求中的任意 ID 或验收后的最新批次补填。checker 对照独立冻结 source 判断此来源，而不将拒绝当作成功零结果。

逐检查状态为 PASS/FAIL/NOT_RUN/UNVERIFIED。已观察到违背预期为 FAIL；必需检查未做为 NOT_RUN；材料无法核对为 UNVERIFIED，报告 reason_code（如 UNVERIFIED_DATA_VERSION）。只有该题全部必需检查 PASS 才能标整题 PASS。退出码：0=本次要求的所有 case/step/检查均完成且通过；1=存在 FAIL；2=输入/版本/配置无效，或没有 FAIL 但存在 NOT_RUN/UNVERIFIED。无待测 case 也不能以 0 退出。

### 8.3 必须执行的业务矩阵

| 编号 | 用户操作/问题 | 机器验收点 |
|---|---|---|
| Q01 | 5 部英语候选 | 条件、identity、真实数量、冻结顺序一致 |
| Q02 | 10 部英语候选 | 卡片 10 部完整；不被工具摘要替换 |
| Q03 | 20 部候选 | 20 部或真实不足；无裁剪/补齐虚构项 |
| Q04 | 最新 kd/qc/qr 某一期按名次 | 同类同一期；名次缺失规则正确；稳定 tie-break |
| Q05 | kw 要按名次 | 无名次时明确拒绝/澄清，不降成 ID 排序 |
| Q06 | “热门”，不指定榜 | hot_only；bill/clk/gsc 不当热门来源 |
| Q07 | 候选池计数与剧场分项 | 用 count；总数和每项独立核对 |
| Q08 | 排除个人已选 | 排除集来自 QA 自己的清单，不替代发布记录 |
| Q09 | 排除团队已发 | 返回记录过滤正确；未匹配不说从未发布 |
| Q10 | 排除指定账号已发 | 仅该账号范围；未知账号按可选值拒绝 |
| Q11 | 确认 YouTube 可发 | allowed 且 active；unknown 不通过严格条件 |
| Q12 | 明确地区“美国/US” | 说明 en 是近似；不制造 geo 过滤或美国需求结论 |
| Q13 | 确定零结果 | diagnosis 单项放宽计数正确；null 不等于 0 |
| Q14 | 换批 A→B→C | 同冻结条件/版本，沿链排除，无跨批返回 A/B 条目 |
| Q15 | 追问第二部 | 绑定 result_id/item_id；详情对应第二部冻结事实 |
| Q16 | 保存 1、3，确认前后 | prepare 不写；确认后真实回执；失响应重试不重复 |
| Q17 | 重新生成/编辑重发/分支 | 同线程复用原引用；分支不盗用父线程引用；失败明确 |
| Q18 | 历史卡和资料更新 | 旧结果不改写；历史时效正确；回放链接保持 result/v/page/size |
| Q19 | 断线/刷新/停止 | 恢复到权威终态；取消、超时不是成功保存来源 |
| Q20 | 剧场规则问答 | 带来源与核对日期；缺标签/限制明确未知；知识不授权写入 |

每题分开判：工具参数/过滤正确、返回对象正确、卡片呈现正确、模型解释正确、运行终态正确。不能只看 run success；有真实业务错误就 FAIL。真实数据里缺反例时，以 synthetic 集成测试完成机制验收，真实题记“该数据无此反例”，不能算验证了不存在的样本。

模型可能调用 count 后只回答计数，或先澄清再查询，capture 必须保留实际工具类型及其返回状态；不得强迫每题都有 result_id。无候选的拒绝、计数、澄清题使用独立 case 类型，字段不适用就显式 null/省略；checker 分类型验证，不能把业务拒绝当成空成功候选。

验收由以下五层组成，报告逐层状态；checker 不得从结构性 PASS 自动推导语义 PASS。语义复核由未生成该被测回答的评审者（人或评审 Agent）按 rubric 判读，无须把所有本地实现都停在等待用户逐题确认上：

| 层 | 判据与执行方式 | 必需证据 |
|---|---|---|
| 意图与工具选择 | 自动比较预先锁定的动作/条件/澄清分支 | expectations hash、实际工具序列与实参 |
| 数据与状态 | 独立 checker 重算并核对回执 | 冻结 source、前置状态、名单/数量/保存后状态 |
| 浏览器交互 | E2E 与必要的视觉检查 | 卡片、勾选、引用、恢复、保存的断言与 trace |
| 模型解释 | 独立评审者逐题按 rubric 判读，标出判读者 | 原回答引用、对应事实、每个 rubric 的判定理由 |
| 性能与成本 | 在可比输入、配置、环境下测量 | 各次 token/耗时/调用次数、样本条件 |

“未把语种说成地区需求”“未匹配不等于未发”等语义可用规则辅助，但正则未命中、被测 answer_check 返回空数组均不是语义正确的独立证明。判断不清的项记 UNVERIFIED，不自动放行；严重事实/身份/保存错误必须 FAIL。计数、拒绝等不适用候选卡的项目，其必需检查在运行前按 case_type 确定，不用随意 skip 凑整题 PASS。

### 8.4 QA 和执行边界

#### 两套隔离的执行环境

| | 合成集成 E2E | 真实剧库 readiness |
|---|---|---|
| 目标 | localhost 的隔离 QA 实例、一次性 PG/SQLite；真实模型或显式固定模型须在报告区分 | 独立远程 QA 普通用户、只读共享 RealShort 批次、实际配置模型 |
| 身份/状态 | 合成测试专用，允许在其个人空间导入合成剧库 | 另一 QA 身份，不得有新于目标共享批次的个人 catalog/knowledge 导入 |
| 配置 | `playwright.pick.config.ts`，命令只选两个原有合成文件 | 新 `playwright.pick.readiness.config.ts`，`testMatch` 只匹配 `readiness.spec.ts` |
| 批次写入 | 仅一次性本地 fixture 和合成用户空间 | 禁止导入、同步、改变共享批次；只允许本题明确的个人保存动作 |
| 前置检查 | 本地 URL、fixture 与数据库归属正确 | HTTPS、`PICK_E2E_REMOTE_QA=1`、qa-azure- 普通用户、source/批次/运行配置核验 |

现有 `personal-selection.spec.ts` 会发布个人合成剧库，`pick-data-board.spec.ts` 要求本地镜像 fixture。不能把它们与 readiness 放在同一次远程全目录命令里，也不能复用它们刚导入资料的账号。只改测试文件名顺序不能实现隔离。

新配置读取私有 `PICK_READINESS_MANIFEST`，按 2.1 的预算和每题 planned_max_runs 设置测试/等待超时。manifest 缺失、目标环境不符时发布验收命令必须非 0 退出；普通未配置开发收集可以说明未运行，但不能出具成功验收报告。

以下命令分别在各自干净环境的 frontend 目录执行；环境变量从各自私有配置加载，不在命令中展开凭据：

```sh
# 本地合成实例：仅这两个文件，URL 必须为 localhost/127.0.0.1。
pnpm exec playwright test -c playwright.pick.config.ts personal-selection.spec.ts pick-data-board.spec.ts

# 另一远程 QA 身份：新配置只收集 readiness，不运行合成导入测试。
pnpm exec playwright test -c playwright.pick.readiness.config.ts
```

#### 真实来源预检与捕获

- `PICK_E2E_EMAIL/PICK_E2E_PASSWORD/PICK_E2E_URL` 由私有环境提供，不写进命令输出、Git 或报告；先验证实际登录 owner/email/system_role，再建会话/保存。
- 真实验收前检查 catalog 与 knowledge 的当前来源，必须为本次批准使用的共享 RealShort 批次；个人导入覆盖、source 不明、配置不明时在新 run 前停止，不自动删除个人批次或触发同步修正环境。
- 每轮工具实际返回后，验证该次 catalog_batch_id、knowledge_batch_id（用到知识时）、`data_as_of.shared`、来源类型与已锁定 manifest 一致；核对完整行 hash。一次预检不能代替逐轮验证。旧卡题按该步骤明确的旧 source 比较，不强制它等于今天最新版。
- 本规格的真实环境 source_type 固定为 `realshort_shared`；synthetic 或 personal_import 只能进入对应机制测试报告。多个批次分别捕获、分别标识，不能只判断候选数量“大于 1”来猜数据真实。
- 私有 evidence 目录 0700、文件 0600。原始请求/响应捕获须在持久化前移除认证 header、cookie、密码和连接串；不能把未脱敏的浏览器网络 trace 上传 CI artifact。原文题目/身份/完整剧库只留私有证据，入仓报告只含 case ID、脱敏指标、SHA 与结论。
- 不调用 `mirror:sync`、生产导入、Trends/GSC 采集。等待自然更新或使用已固定批次；清理只针对本次 QA 创建的可恢复资源。

#### RD-05a → RD-05b 的执行顺序与预算

1. 产品修改前：锁定预期、记录实际配置、检验 checker 的手算正反例、验证两套 QA 环境隔离；保存 5/10/20 部合成 payload 和 UI baseline。
2. 在原业务基线上做最多六个关键场景的真实冒烟，优先 Q02、Q11、Q13、Q14、Q16、Q18；多轮场景每个 run 分别计数。它用于定位现状，不要求已知待修缺陷先通过。
3. A～D 集成后：基于一个 SHA 执行完整 Q01～Q20，数据计算使用各题自己的冻结 source；性能对照使用相同合成输入。实时数据已自然变化时，不把前后不同批次名单差异称为代码回归。
4. 缺凭据/授权读取路径时保留具体 NOT_RUN/UNVERIFIED，不改用 mock 出具真实验收结论；可在合成 baseline 完成后继续代码工作。

改前、改后、重试和本期新增真实模型 QA 共用 **40 个新 run** 的总预算，由 E/集成负责人统一计数；不是每位 Agent 各 40 次。每个失败 case 最多重试一次，首次失败仍保留。按 planned_max_runs 预留整题预算，不够则不启动该题，记录剩余题清单；当前会话有更低配额时以它为准。已经完成的相同 SHA、相同运行配置与相同 QA 证据可复用，不因换 Agent 重跑。

真实模型保持目标配置，不以调高预算/换模型换取通过。本地超时与取消故障测试、已有合成 E2E 的结果单列；它们不能证明实际模型在生产预算内完成。

### 8.5 性能记录

记录普通筛选、10 部候选、规则问答、复杂比较四类任务的：模型/档位、输入/输出 token、模型/业务工具次数、工具输出字节、总耗时、状态、预算拒绝次数。单次样本不能称 p95；少量真实样本报告各次值和中位数。大量时延分布先用本地固定模型测试。

usage 缺失或日志未采到的值标为 unknown，并注明证据缺口；不得把缺失当 0 计算“节省率”。耗时、token 和字节分别报告，JSON 字节减少不能直接宣称等比例成本或延迟改善。

前端对隔离 QA 生产构建测候选页和资料页：HTML 字节、JS/CSS 资源、主要接口请求数、首次可操作耗时，记录冷/热条件与网络位置。不直接沿用“520KB”“10 项超预算”等旧记录当当前测量。

本期不承诺模型时延固定值。门槛：不新增与条目数成比例的详情请求；不新增预算误截断；无功能丢失。固定模型的合成基准在相同机器、配置、数据、生产构建模式下，冷启动独立记录，预热 2 次后采集 5 次，比较中位数；超过基线 10% 时在相同条件再测一组，若两组都超过则记性能回归，不靠单次噪声判失败。真实模型少量样本只报告观察值，不套用该 10% 确定性门槛。已有 `perf:check` 超预算区分基线与本期新增，不调大阈值掩盖。

**DoD:** checker 正例通过、规定反例被拒；05a 基线和 05b 结果分开记录；Q01～Q20 每题按五层检查给出 PASS/FAIL/NOT_RUN/UNVERIFIED、配置/批次/预期 hash 与证据。核心查询/换批/保存/恢复的 FAIL 为发布阻塞；需要语义判读的项没有判读证据时不能 PASS。未完成真实验收只能称“代码验证完成，业务验收待补”。

## 9. RD-06：状态文档、集成与交付

### 9.1 文件范围和维护规则

- `docs/pick-workbench/progress.md`：顶部换成当前状态摘要，历史按日期保留。
- `docs/pick-workbench/acceptance.md`：追加本期验收矩阵与证据，旧结果标历史，不删。
- `docs/pick-workbench/observe-runbook/README.md`：按文件实际存在与功能状态更新索引，“有文件”和“实现/上线/验收”分开。
- `docs/pick-workbench/realshort-sync.md`：历史时效/当前时效语义、notes 与 model-view 区别。
- 需要时更新根 README 的选剧入口与当前范围，不重写上游全部 README。

顶部摘要固定列出：更新时间、业务仓库、基线/已部署 SHA、前后端版本证据、已实现且已验收、已上线待验收、计划但未实现、下一步。动态数据只写“观测于某时间”，不写永远正确的数量。

文档也需说明：手工 CLI 发布项目不保证推 main 自动部署；确认当前集成设置后再写。未发布的代码不填成生产完成。

### 9.2 集成前后必须做

1. 汇总任务 PR 和实际变更路径；检查重叠函数、契约、提示词及 fixture。
2. 在集成后的单一 SHA 跑第 11 节完整检查，不能拼各分支的绿色报告。
3. 源目录、托管副本一致；前端能读旧卡、新卡、混合会话、已保存快照、hot 卡。
4. 检查无冲突标记、无凭据、无生产数据、无偶然依赖升级。
5. 实施代码必须有独立代码审查；后端 Python、前端与身份/输入边界使用相应审查角色。只报告可达错误路径，区分 bug 与建议。

### 9.3 Agent 的最终交付格式

```text
任务 ID：
基线 SHA / 最终 SHA / PR URL：
修改文件及目的：
契约变化与兼容证明：
测试命令、通过/失败/跳过数量及原因：
真实 QA：模型、档位、批次、Q01～Q20 状态、证据位置：
QA 隔离与意图基准：manifest/captures hash、前置状态、各层检查结论及语义判读者：
性能：可比基线、修改后、样本限制：
独立审查：问题及处置：
发布状态：未发布 / 已授权并发布 / 验证完成：
未完成项、实际阻塞和下一步：
```

报告中不附真实连接串、cookie、密码或私人业务数据。PR 描述围绕最终行为和有效验证，不粘贴聊天历史。

## 10. RD-07：趋势雷达只读就绪审计

这是单独的审计任务，不是“把已有代码开关打开”。编写本规格时进度记录包含 canary 启动，但不能据此断言完整稳定验证已经通过。

### 10.1 读哪些证据

- `docs/plans/2026-09-25-trends-radar-design.md`、`2026-09-25-trends-radar-impl-plan.md`。
- `docs/pick-workbench/observe-contract.md` 与 observe-runbook 内的 packaging、trends-session、gsc-client、lease-and-selfcheck、rollback-matrix。
- 当前已部署 collector/包摘要/迁移头、cron 实际配置、最近批次及模式、覆盖、失败、熔断、freshness、source/identity matching 状态。
- 在已有授权与连接可用时，只读执行文档已有的 `python -m ggwork_pick.observe.trends status` 等状态命令；调用前查看当前 `--help`，不猜存在的 admin 子命令。

### 10.2 产出

新增 `docs/pick-workbench/trends-readiness.md`，以观察时间、渠道、部署版本、读取方式、指标、结论组成证据表。真实标题、cookie、出口明细和密钥不进文档。

结论只能是：

- `IMPLEMENTED_NOT_ENABLED`：有实现，当前未启用。
- `CANARY_PENDING`：金丝雀验证正在进行/未完成。
- `READY_FOR_NEXT_GATE`：现有阶段全部满足原计划门槛，可申请下一道门。
- `BLOCKED`：列出实际缺失的配置、权限、样本或失败证据。

用原计划门槛，不自行降低成功率/覆盖率/对照要求。单次 selfcheck、一次采集成功、能导入模块均不是 stable/live 完成证据。

**禁止：** 更改 `PICK_OBS_AGENT/PICK_OBS_PUBLISH`、调整 cron/模式/预算、补发采集、修改对照词表、将历史空白回填为今天的数据。审计后需要功能修复或下一阶段发布时，另列范围和验收门槛。

## 11. 测试、托管副本和发布检查

### 11.1 每项最小检查

在当前任务根目录，用可用的后端 venv；不能借一个装了旧插件的解释器证明新业务代码已生效。测试会把业务源码加入 sys.path，但集成仍须验证实际安装包。

```sh
backend/.venv/bin/python -m pytest customizations/pick-workbench/tests/test_freshness.py customizations/pick-workbench/tests/test_result_notes.py -q -rs
backend/.venv/bin/python -m pytest customizations/pick-workbench/tests/test_answer_check.py customizations/pick-workbench/tests/test_tools.py customizations/pick-workbench/tests/test_frontend_contract.py -q -rs
backend/.venv/bin/ruff check customizations/pick-workbench
backend/.venv/bin/ruff format --check customizations/pick-workbench
git diff --check
```

前端（frontend 目录）：

```sh
pnpm exec rstest run tests/unit/core/pick tests/unit/components/workspace/pick
pnpm check
```

新增文件的定向测试随任务运行，不能只跑上面的旧文件。

### 11.2 集成门槛

- `PICK_TEST_PG_URL` 只指向一次性测试 PostgreSQL，不用生产库；相关双库用例不得因缺变量全跳过。
- 业务扩展全套：`backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q -rs`。
- 宿主入口/JSON/身份回归：`backend/tests/test_pick_cloud_entrypoint.py`、`test_json_body_sanitizer.py`、`test_create_user_cli.py`、`test_compose_default_bind_host.py`。
- 前端全套 `pnpm exec rstest run`、`pnpm check`、`pnpm build`。
- `frontend/src/server/pick-board` 的真实 PostgreSQL 集成，按现有 `pick-workbench-tests.yml` fixture/reader 流程执行；没有 reader URL 的跳过不能算通过。
- 旧卡/新卡/混合会话/存量快照/hot 卡兼容矩阵及前后端合同 fixture。
- 按 8.4 的两条独立命令分别运行本地合成 E2E 与远程 readiness；不得使用全目录命令混跑。报告目标实例、QA 身份别名、实际来源/批次、用例和 skipped 原因；新 readiness 使用与已核对目标期限匹配的等待值。
- rebase/merge 之后在新的集成 SHA 重跑受影响门槛，旧 SHA 的 build/test 不能沿用为新 SHA 证明。

### 11.3 刷新托管副本

修改源码后，按官方 extension manager 的 **upgrade** 流程刷新 `backend/extensions/sources/ggwork-pick`，先看当前命令帮助，再使用绝对源码路径；不直接改托管副本。

比较时排除 `__pycache__` 等生成缓存：源码、托管副本业务文件相同；`test_managed_copy` 通过。实际镜像/site-packages 的实现和本次 SHA 对齐。锁文件/版本只在 manager 的真实需要下更新，不为本期顺便升版本。

### 11.4 如果当前会话授权生产发布

1. 先完成代码审查与 PR；按授权完成合并，读取新的业务 main。
2. 使用干净、无额外 `.env*` 的部署检出；源/托管副本和迁移链一致。
3. 部署前运行现有 `scripts/pick-deploy-guard.py`，退出码非 0 不部署；不能删除守卫或借 `--first-record` 绕过既有记录。
4. backend 改动先发 gateway；验证真实扩展加载、路由、迁移头、日志和只读 API，再发前端。无后端改动不重复部署 gateway。
5. frontend 按守卫生成的 `git archive` 导出目录发布；不直接上传日常脏目录。
6. 不重部署两个观测 cron，除非本期真的改了它们的包/合同且有明确授权；本规格预期无需动 cron。
7. 记录 SHA → gateway 部署 → frontend 部署/alias → app version → QA/浏览器证据。
8. 登录后核对旧卡、10/20 部新卡、notes 语义、换批、保存回执、重新生成/恢复；写验收使用专用真实 QA 身份，仍执行 8.4 的来源和预算检查。达到本期真实 run 总预算时保留未完成项，不另起不计数的发布验收。未登录跳转/401 只是鉴权冒烟，不是业务验收。
9. 守卫记录和实际结果追加进 progress；READY/SUCCESS 只能说明部署状态，不能直接填业务验收完成。

回滚按现有 rollback-matrix：优先在 main 上做可审查 revert，保护 0007 及以后迁移能力和新旧卡兼容；不在平台回滚到不认识当前数据库迁移的老镜像。

## 12. 多 Agent 分工和并行边界

| Agent | 主责 | 可写路径 | 并行注意 |
|---|---|---|---|
| A | RD-01 后端时效 | freshness、selection 的 notes/解释/详情、tools 的详情时效接入、必要 routes、对应测试 | 05a 基线后实施；冻结 notes 合同；selection/tools 与 C 串行 |
| B | RD-02 前端卡片 | core/pick/notes、format、candidate-view/use-result-notes、对应单测 | 收到 A 的合同再接字段；不改通用聊天/保存协议 |
| C | RD-03 模型投影 | tools、model_projection、新投影测试 | A 的 selection/tools 改动合并后开始；不写 answer_check |
| D | RD-04 回答核对 | answer_check、相应测试；必要时核对提示 UI | 提示 UI 与 B 协调；保留 C 的 posted 材料 |
| E | RD-05 QA | 新 checker、synthetic fixture、独立 readiness 配置/E2E/测试；旧 E2E 中预算相关最小改动 | 先完成 05a 合同与基线，统一真实 run 账本；05b 基于集成 SHA；不自行部署 |
| F/集成负责人 | RD-06 文档/集成 | progress、acceptance、同步文档、运维索引；PR/发布协调 | progress 和 acceptance 由一人统一写，各 Agent 交证据 |
| G | RD-07 雷达审计 | 只读检查，trends-readiness 文档 | 不改采集代码、模式、配置或生产数据 |

每位 Agent 的任务指令都包含：**你不是唯一在代码库工作的 Agent；不要回退其他人的改动；在自己的 worktree/分支工作；只修改分配路径；发现重叠先通知集成负责人。**

推荐顺序：E 先完成 05a 的合同、环境预检和可获得的基线，G 同时只读审计；之后 A 与 D 实施正确性修复；A 契约完成后 B 做界面增量；C 在 A 的共享后端路径合并后做投影。F 在单一集成 SHA 上汇合 A～D，由 E 执行 05b，F 汇总五层验收与发布状态。A～G 是职责分工，不要求同时启动七个 Agent。

不要把创建用户拥有的新聊天当作内部子任务机制。需要多 Agent 时使用执行环境支持的子 Agent/独立 worktree，并保持文件所有权。

## 13. 最终验收标准与后续阶段

### 本期完成必须同时满足

- RD-01～04 的行为和契约测试通过，无未处置的真实阻塞缺陷。
- 原个人选剧流程不退化；快照、权限、幂等和缓存不变量成立。
- RD-05 同时提供改前基线、预先锁定的意图/状态合同、QA 隔离证明、改后五层证据；缺证据的题清楚列出，不能计入完成率。
- 新旧 gateway/frontend 的兼容状态有记录，真实双库/reader 检查没有被跳过替代。
- RD-06 文档准确区分代码完成、QA 完成、发布完成和生产验收完成。
- RD-07 给出有证据的阶段结论；该项不以强行开启雷达来“完成”。

**代码交付完成**：可审查 PR、测试、文档、全部实际限制已交付。

**日常使用收尾完成**：集成版本的真实模型核心链路验收通过，用户可看懂证据、旧数据和保存状态。

**生产发布完成**：仅在有发布授权时，再加部署、alias、版本、登录后业务验收证据。三种状态分别报告。

### 另行制定的下一阶段

1. 地区需求/趋势：根据 RD-07 结论推进现有原计划，不新增一套平行雷达，不把 en 近似当地区证据。
2. 团队流程：个人选择、团队池、负责人/制作、排期、发布回填、复盘的对象/权限/状态分别定义。
3. 外部写入：飞书等写操作有确定意图、作用域、确认、幂等、回执、失败核对和补偿。

执行 Agent 不实现这三项。交付时只报告它们的需求边界与已经掌握的阻塞，供下一份规格使用。

## 14. v1.1 审计问题闭环索引

| 审计问题 | 修订位置 | 实施验收时必须看到 |
|---|---|---|
| 旧 120 秒预算误作生产不变量 | 2.1、8.4 | 实际配置快照；600/300 仅为当前入口默认；测试等待覆盖真实目标期限 |
| 仅用模型实参自证正确 | 8.2、8.3 | 预先锁定 expected、独立前置状态、en→ko/漏排等反例被拒 |
| 合成导入污染真实验收 | 8.4、11.2 | 两套配置/身份与命令；每轮来源、shared、batch/hash 匹配 |
| 投影遗漏评级/说明 | 6.2、6.4 | grade/note/citation_id 保留；五类证据逐字段回归 |
| 已上线字段被重复计作新成果 | 5 | 已有→增量对照、主依据与待核实信息的可见性证据 |
| 缺少改前基线与依赖次序 | 3、8.4、12 | 05a 先于产品修改；05b 基于集成 SHA；共用真实 run 账本 |
| 自动检查代替语义判读 | 8.3、9.3 | 五层分别报告；独立 rubric 判读者与理由；未验证项不判 PASS |

本表表示规格已修订，不表示业务代码或验收已经完成。
