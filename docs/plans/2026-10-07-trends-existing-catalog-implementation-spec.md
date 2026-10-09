# Google Trends：复用现有剧库的实施 Spec

2026-10-08 UX 补充：前端展示须遵循 [Google Trends 标签与重点信息 UX SPEC](../pick-workbench/trends-ux-spec.md)，区分趋势信号、涨跌数值、历史窗口与数据异常的语义色，并完成明暗主题及移动端视觉验收。规范更新不代表页面已实现或发布。

2026-10-07 最新确认：用户已批准全球日级、仅曲线的 10 → 30 → 100 部分晚恢复，替代本文第 8 节的旧 canary 路线。执行合同见 [日级分阶段恢复](2026-10-07-trends-daily-recovery.md)。下文的“尚待确认”是批准前记录。

状态：可交给 Code Agent 的实施草案。2026-10-07 根据用户“没有 API，参考旧 HTML 逻辑，复用选剧台已有剧集”编写。本次交付是规格，不表示采集已经恢复。

实施更新：A/B 已随 PR #39 发布，产品提交 `723ba4fa`，线上普通 QA 的 100 行候选与 API、刷新、移动端及原清单保持验收通过。C/D 尚未完成，collector 仍停止；调整恢复验收为日级分阶段的提议尚待用户确认。以 [当前发布记录](../pick-workbench/progress.md) 和 [验收记录](../pick-workbench/acceptance.md#2026-10-07-google-trends-候选预览发布验收) 为准，不把本 Spec 的待做项视作已实现。

业务仓库：`phananhson733-oss/ggwork`，本地项目为 ggwork-deerflow。代码核对基线：`79bb8e1cd87a1864113f2365d8420da38e8c2517`。该检出中的 `ggwork` remote 是业务仓库，`origin` 是上游 DeerFlow；实施前重新核实 remote 和当前主线，不向上游提交业务代码。

## 1. 目标与边界

让选剧运营在现有资料页看到：从我们已有热门剧中选出的待查剧集、入选依据，以及实际采集成功后的 Google 搜索走势。无需重新抓取各短剧平台，也不依赖官方 API key。

采用现有匿名网页客户端。没有官方 API 权限是已确认条件，不再把申请 key 作为实现前置条件；网页接口能否在生产出口持续使用仍须实测。

固定首版范围：最多 100 个剧集语言版本、全球、近一个月日级序列、仅时间序列、每天一个计划。延续 [简化版批准范围](2026-09-30-trends-radar-simplified-scope.md)；本文新增候选预览并细化恢复与验收。历史旧方案的未实现事项不自动成为本期任务。

本期不做团队制作流程、飞书多维表格写入、GSC、智能体趋势排序、全库轮询、相关搜索发现、小时级活跃时长、跨语言自动归并、代理采购或官方 API 接入。不新增数据库迁移或前端数据库权限。Trends 仍是辅助参考，不改变原有选剧和个人清单行为。

## 2. 已有数据与已有代码

剧集资料与 Google 观测是两个数据层。有剧集、榜单和收入资料，不等于有该剧的新鲜 Google 曲线。

| 数据/能力 | 当前来源或实现 | 本期处理 |
|---|---|---|
| 剧集身份、剧名、语种、平台、榜单信号 | 最新 published 的 shared catalog，`ggwp_import_batches` / `ggwp_drama_versions.payload_json` | 直接复用，不从 HTML 导入 |
| 三个榜各自最新一期、名次 | `top_dramas.py` 的 `shared_catalog`、`board` | 保留日期和各榜顺序 |
| ReelShort 收入补足 | 最新 published mirror 的 `rs_ids` 与 `pick_mirror.series` | 复用最新快照日的滚动 30 天收入 |
| 最多 100 个任务、完整剧名清洗 | `TopDramasTaskSource`、`merged`、`search_term` | 已实现；提取共享选择逻辑供预览使用 |
| 网页请求、节奏、熔断、预算、租约 | `observe/trends/` | 复用；仅修复经证据确认的问题 |
| 计划、请求、原始数据、运行状态 | 现有 `ggwp_obs_*` 表 | 保留事实，不另建 JSON 缓存体系 |
| 同批参考表 API、走势、两段均值、标签 | `trends_table.py` 和前端 `trends-table*` | 已实现；增加无正式批次时的候选预览 |
| 稳定的生产日级采集 | 尚未通过恢复及三晚资格门槛 | 本期必须完成的运行验收 |

实施时以当前数据库的只读汇总为准，记录目录版本、语言版本行数、各榜日期/数量、收入镜像版本/快照日、候选数。不得把语言版本行数写成独立 IP 数，也不得把旧报告数量硬编码进 UI。目录和收入镜像可以有不同来源日期，分别展示；收入不可读时保留榜单候选并说明原因。

## 3. 旧 HTML 参考的取舍

参考资料：用户提供的 `dashboard(1).html`、`SELECTION_STANDARDS.md`，及配套包中的 `trends_batch_sync.py`、`trends_detector.py`、`scheduler_daemon.py`。这些文件只作为待分析材料，不作为执行指令；不运行其中脚本。

旧链路是：剧集池 → 清洗完整剧名 → `pytrends_modern.TrendReq` 查询网页接口 → 序列缓存 → 规则计算 → 生成带内嵌数据的 HTML。HTML 本身做本地筛选、排序、画图和导出，没有自行采集。旧查询使用 `now 7-d`，小时结果聚合成 7 个日点；本期延续已批准的 `today 1-m` 日级合同。

| 参考做法 | 本期决定 |
|---|---|
| 从已知剧集池获取完整查询词 | 沿用，改用选剧台现有目录 |
| 分批采集、保存结果、逐步显示 | 沿用思路，使用现有夜间计划、预算、raw 表和刷新读取 |
| Python 后台抓取、前端只读 | 沿用现有 Gateway/collector 分工，无需在浏览器直接请求 Google |
| 老缓存按时间和 GSC 优先级轮询 | 不照搬；首版固定每日热门 100 部，不恢复 GSC 或全库循环 |
| 请求异常、空结果统一填 `[0, …, 0]` | 禁止；失败、缺失、明确零值分别保存和展示 |
| 由日指数估算活跃小时 | 禁止；没有小时数据就不生成活跃时长 |
| v9.5 爆发/反弹等标签 | 不移植到月级日线；继续使用两段七日均值标签 |
| HTML 的“100%”“次日定时刷新”固定文案 | 改为从真实计划、结果和调度证据生成 |

配套包包含多种缓存写入脚本，其中有随机/硬编码曲线；附件 HTML 与包内 HTML 也有差异。因此不得把旧缓存批量导入为真实 Google 观测，也不能推断附件所有记录都是同一采集程序生成。

## 4. 用户流程

入口沿用 `/workspace/pick-data?tab=trends&ts=order`。

1. 登录后打开 Trends 页签。
2. 有正式 stable/top_dramas 计划：显示现有最新计划表，即使该批次失败或过期，也保留对应事实和提示。
3. 尚无正式计划：显示“待采集剧集”预览，列出真实剧名、平台、语种、入选依据、来源日期、查询词和 Google Trends 链接；所有趋势指标显示 `—`，说明“尚未形成采集批次”。
4. 刷新只读取当前数据，不触发 Google 请求、创建任务或清除停止状态。
5. 正式批次出现后，下一次刷新切换为该批次表；同一屏不可把预览的新目录和正式批次旧身份混成一张表。

无候选时显示“目前没有可用于趋势查询的榜单或收入候选”，附允许公开的来源原因。数据读取失败显示错误与重试；不显示“剧库为空”。采集停止提示在预览状态下仍保留。

## 5. 选剧和查询合同

顺序固定：`qc` 鹊娱转化榜 → `qr` 鹊娱收入榜 → `kd` Kalos 日榜。每榜只取自己的最新一期，内部按名次、identity 稳定排序；不同榜的名次不比较。

按完整 identity 去重，同一 identity 的多个入选依据合并。不同语种/不同来源身份即使清洗后的剧名相同，也保持不同记录，首版不新增跨身份共享 Google 请求优化。

不足 100 时，按最新 published 收入镜像的快照日数值补足；收入是滚动 30 天快照，不是 7 天收入，不累加不同快照日。少于 100 就保留实际数量，不能用无依据记录填满。

清洗继续调用现有 `search_term`：Unicode NFKC、去配音标记、标点处理、空白合并、保留词内撇号；保持完整标题，不截成泛词、不翻译、不擅自追加平台名。无有效查询词的记录排除并计数。

查询上下文固定：内部地区 `WW`、Google 全球参数沿用现有转换、`today 1-m`、日级 D、route `a_only`、每剧 explore + multiline。100 剧基础查询为 200 请求；预热、探测及重试仍计入现有独立预算项，不能声称整晚总请求一定等于 200。

## 6. 新增候选预览 API

新增 `GET /api/pick/obs/trends-candidates`，与现有 `/obs/trends-table` 相同的登录校验和 shared 只读权限。无用户可配置的 owner、SQL、目标数、查询词或网络目标参数。未登录沿用宿主认证错误；读取异常返回固定安全错误 503。

使用单个一致的只读数据库事务捕获选择所需版本及记录。将 `TopDramasTaskSource.units` 中的选择步骤提取为同模块共享函数：输入只读访问器和 target，输出 picks、catalog_batch_id、来源说明和无效标题数量。collector 和预览都调用它。预览不得直接启动 collector、调用 preflight、获取租约或写 obs 表。不要为这两个调用方新建通用插件体系。

正式采集仍由现有任务源把共享选择结果转成 QueryUnit 和不可变 plan notes。预览刷新后入选名单允许随目录变化；不承诺与未来某一晚计划一致。两者在相同数据库快照下必须得到相同顺序和依据。

新增响应合同（下面是结构定义，非真实数据）：

```text
TrendsCandidates {
  checked_at: UTC timestamp,
  kind: "candidate_preview",
  selection_state: "ready" | "empty",
  catalog_batch_id: string | null,
  target: 100,
  selected: integer 0..100,
  unusable_titles: nonnegative integer,
  sources: TableSources,
  rows: CandidateRow[]
}
CandidateRow {
  order: integer 1..100,
  identity: existing Identity,
  title: string <=500,
  platform: string <=100,
  language: string <=100,
  term: string 1..200,
  geo: "WW",
  time_range: "today 1-m",
  basis: TableBasis[]
}
```

`selected == rows.length`；order 连续，identity 唯一。复用现有 Basis/Sources 字段和校验。预览没有 batch_id、unit 采集结果、series、planned、成功计数、采集时间；不得复用 `pending` 表示尚未建批次。`checked_at` 是预览读取时间，不是上游更新时间。

前端另加严格 schema 和服务端 Gateway reader，不改原 `TrendsTable` 合同。只有原 table 成功返回 `batch=null` 时才加载预览；table 请求失败不能靠预览掩盖。预览失败保留主表的无计划状态和采集横幅，并显示独立读取错误。先部署兼容的 Gateway，再部署消费新端点的前端。

## 7. 正式结果、缓存与计算

继续把一个正式计划批次作为展示边界：只读取该批次 raw，不拿其他夜晚的成功曲线填补失败。已有原始记录就是本期缓存；不建立第二套本地 JSON 文件或跨批次拼接曲线。断点/幂等继续遵守现有租约和 session 规则，成功单元不得因页面刷新重复查询；不新增绕过“一晚一个运行”限制的续跑命令。

| Google/任务事实 | 正式表 result | 数值处理 |
|---|---|---|
| 合法序列，含明确的 0 | `data` | 保留 0；指数 0 不等于绝对搜索次数为 0 |
| 成功但无可用序列 | `no_data` | `series` 无有效值，无趋势结论 |
| HTTP 429/403、超时、坏响应、未到达、预算截断 | `not_fetched` | 保留允许公开的状态原因，不填 0 |
| 实际运行且尚未到截止时刻的待执行单元 | `pending` | 无曲线；超时后不能永远 pending |
| 某日缺失或 hasData=false | 依现有序列合同 | null，曲线断线，不纳入均值 |
| isPartial 或未结束 UTC 日 | partial=true | 不纳入完整日计算 |

沿用 `frontend/src/core/pick/trends-table.ts`：以批次 window_end 的前一完整 UTC 日为终点，近 7 个日历日与此前 7 日分别仅对有效完整日求均值；展示有效天数。近期非零天数不足 3 或前期没有有效日优先判“数据太少”；前期均值 0 且近期正数为“新出现”，不算百分比；随后按 ±25% 判上升/回落，其余持平。短标题保留歧义提示。不得按不同剧各自 0–100 指数比较绝对热度。

## 8. 生产采集恢复：独立于页面交付的门槛

已确认的历史事实见 [恢复计划](2026-10-07-trends-recovery-plan.md)：两晚共 173 次请求、6 次 HTTP 429，执行过约 30/60 分钟退避；未发现既定请求包络超发，具体服务端限流原因未知。最近检查仍有 canary_terminated。不能把“改成日级”“换个 pytrends 包”当作已证明的修复。

按以下顺序推进，每步保留失败证据：

1. 离线检查现有客户端与配套包的请求/解析差异，使用现有 HTTP fixtures 和本地 fake Google 验证；不安装整个旧包。只对确认影响当前合同的差异提出最小改动。
2. 在既有请求记录路径补齐诊断信息：阶段、状态、耗时、可解析且有上限的 Retry-After、userType 是否存在、运行位置与部署标识。优先复用已有字段/JSON 扩展位；不存在安全持久化位置时使用私有结构化产物，不为遥测单独加表。缺历史信息保留 unknown。禁止记录 cookie、token、授权头或原始错误正文。
3. 准备并审查一份单次诊断执行单：生产 collector 的实际运行位置、一个公开对照词、WW、today 1-m、最多 3 次 HTTP（预热/explore/multiline）、无 related、无重试。诊断必须记账，不能伪装成只读报告；不删除终止状态、不改变 since/mode/cron、不更换出口或 UA。执行前核对既有停止后恢复规则；已有授权未覆盖的停止状态例外需要明确决定，不能由等待时间推定。
4. 任意 429/403、验证码或同意页立即停止。失败时交付具体阶段和下一项待决策，不自动启动整晚任务，不循环探测。一次诊断成功只证明该次请求可用。
5. 有证据支持恢复后，按现有手册建立可追溯的新验收起点，保留旧失败及变更原因。任何恢复操作必须通过已有部署守卫；不能仅重置 since 使报告变绿。
6. 按已批准 canary 路线取得累计三个有效夜晚：负载闸门通过、同批有实际请求、没有验证码/同意页、没有因限流停用。报告的 qualification=not_evaluated 或退出码 0 均不是通过结论。原 canary 路线保持至资格通过，不能提前切 stable 绕门槛。
7. 再发布 collector 的 `stable + a_only + D`，最多 100 部；保留既有租约、预算、节奏和截止规则。三晚 canary 资格不等于日级 100 剧负载通过，首个真实 stable 批次另行验收。

既有时间安排为 UTC 17:30 起跑、次日 01:45 截止；部署前核对调度实参并明确 target_date 归属，避免跨午夜误算。UI 使用真实批次时点，不复制旧 HTML 的 07:30 固定承诺。

## 9. 工作包与文件责任

以下为顺序工作包，不要求多个 Agent 并行。

| 工作包 | 文件/责任 | 可验收输出 |
|---|---|---|
| A. 复用目录与候选预览 | `customizations/pick-workbench/ggwork_pick/observe/trends/top_dramas.py`；新增 `observe/trends_candidates.py`；`ggwork_pick/routes.py` | 相同快照预览与正式任务一致；认证只读 API |
| B. 页面接入 | `frontend/src/server/pick-board/` 新 reader；`frontend/src/core/pick/` 新 schema；`frontend/src/app/workspace/pick-data/trends-route.tsx`；`frontend/src/components/workspace/pick-board/views/trends-table*` | 无计划时真实候选可见；有计划时保持原表；预览和趋势证据明确区分 |
| C. 采集恢复 | `observe/trends/client.py`、`executor.py`、请求记录路径，仅按证据修改；`docs/pick-workbench/observe-runbook/` | 离线合同测试、有限诊断、恢复记录、三晚资格判定 |
| D. 运行交付 | collector 部署配置、`docs/pick-workbench/progress.md`、`acceptance.md` | stable 真批次与 UI 对账；首周真实覆盖记录 |

A → B 可独立交付；C 的离线部分可在期间完成。D 依赖 C 资格和 A/B 可用。若采集仍受限，允许交付“目录预览已完成，实时采集阻塞”，不得写“Google Trends 功能已全部完成”。

估算：A/B 约 1.5–2.5 人日，C 离线诊断及小范围修复约 0.5–1.5 人日，D 集成与验收整理约 0.5–1 人日；总计约 2.5–5 人日开发工作，另需至少三个有效夜晚和首周观察。外部限流未定位，不能承诺恢复日期。

## 10. 验收矩阵

| ID | 用例 | 通过条件 |
|---|---|---|
| GT-01 | 三榜日期不同、名次并列、重复身份、同标题不同身份 | 每榜最新一期；固定榜序；身份去重且依据合并；不同身份保留 |
| GT-02 | 不足 100、无收入镜像、收入快照不同日 | 只按单日滚动 30 天值补足；无法补足时数量和原因真实 |
| GT-03 | Unicode/配音后缀/词内撇号/无效标题 | 预览与 collector 复用同一清洗结果和排除数量 |
| GT-04 | 相同快照分别生成预览和任务 | identity、顺序、查询词、依据逐项相同；预览不写表、无外网、无租约 |
| GT-05 | 未登录、目录读取失败、目录有效但无候选 | 认证拒绝/503/合法 empty 三种情况不同，不泄露内部错误 |
| GT-06 | 从无计划到实际 stable 计划 | 无计划显示候选且指标为 —；新计划出现后整表切换，不混合版本 |
| GT-07 | table 失败、预览失败、历史批次过期 | 不用候选掩盖正式表错误；采集停止及过期横幅保留 |
| GT-08 | 明确零值、空响应、429、超时、缺日、partial | 按第 7 节逐项区分；断线且不伪造均值或成功时间 |
| GT-09 | 均值阈值、前期为零、有效天不足 | 保留现有边界测试；不发生除零或把缺日填零；显示有效天数 |
| GT-10 | 页面多次刷新、采集中增量返回、截止后退出 | 不增加 Google 请求；同一批结果更新；超时 pending 正确结束 |
| GT-11 | 受控诊断触发 429/403/wall | 达到停止条件即停；记录真实请求数；无自动重试/出口替换 |
| GT-12 | 三晚 canary 资格 | 每晚有负载和请求证据；资格由批准规则判定，零请求不充数 |
| GT-13 | 第一个 stable 日级批次 | 来源计划、最多 100 行、同批 raw、四种结果计数与页面逐项对应 |
| GT-14 | 部署后连续观察 | 下一次计划运行有实际证据；首周逐日记录运行/成功/无数据/失败/漏跑，不用一次手动成功代替调度成功 |

测试执行：扩展定向 pytest 覆盖 top_dramas、trends_candidates、trends_table、client 与请求记录；数据库测试使用仓库的 SQLite/PostgreSQL 双库 fixture。前端严格 schema、reader、均值、组件状态切换测试，普通 QA 浏览器验证 GT-06/07/10/13；运行 frontend `pnpm check`，变更提交完整 CI。离线测试禁止联系 Google，使用现有 fixtures/fake Google。仅文档交付无需重跑产品测试。

不得增加真实应用模型运行；现有模型验收预算已用尽，本功能的数据读取和曲线展示也不需要模型。Google 请求单独遵守 observer 预算。QA 不保存到管理员个人清单，不进行飞书写入。

## 11. 发布、回滚与交付物

所有业务 Python 修改在 `customizations/pick-workbench` 源码中进行，托管快照通过官方 extension manager 更新，禁止把 `backend/extensions/sources` 当独立源码修。先读根与作用域 AGENTS，再实施。

发布前记录业务 SHA、CI、Gateway/前端/collector 的实际部署身份。A/B 不修改 collector 的模式和定时任务。新 Gateway endpoint 先于前端发布；恢复后的 collector 单独通过部署守卫和第 8 节门槛。

回滚：候选预览出错可回退前端至原无计划空状态，保留兼容 Gateway；采集异常停 cron、保留终止事实和数据库原始记录。不删除历史批次、不回退收入镜像、不清理用户个人清单。无新迁移，无数据回滚步骤。

Code Agent 最终交付必须包含：

- 变更清单、复用/新增边界及最终源码 SHA。
- GT-01～GT-14 的 PASS/FAIL/BLOCKED/NOT_RUN 及证据位置，禁止把未运行写成通过。
- 目录/榜单/收入版本和日期的安全汇总，真实计划与 raw/UI 对账结果。
- 有限诊断及实际请求账本、三个有效夜晚的逐晚资格证据、首个 stable 批次、下一次调度运行证据。
- 首周观察记录；如仍有限流，写明已知现象、未知原因和最小待决策，不以合成曲线填补。

只完成 A/B 可以验收“候选预览完成”；C/D 和首周记录完成后才可宣称本期 Google Trends 闭环完成。

## 12. 给 Code Agent 的执行指令

> 在业务仓库 ggwork-deerflow 中执行本 Spec。先核对业务 remote、主线和工作区，保留他人改动。先实现 A/B，让现有真实剧集在无 Trends 计划时可见，并完成对应只读、身份、同快照及前端测试；已有选剧算法、曲线计算和同批结果 API 直接复用。然后按 C/D 的证据门槛恢复实际采集，不能通过换模式、重置 since、替换出口或导入旧缓存绕过停止状态。团队流程、飞书写入、GSC 和应用模型调用不在范围内。每个工作包分别记录代码完成、部署完成、运行验收是否通过，最后按 GT-01～GT-14 交付证据与遗留事项。
