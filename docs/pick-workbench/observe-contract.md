# 趋势雷达共享数据合同（TR-33）

两个采集服务（trends、gsc）、gateway、智能体与资料页共用的 wire 与数据库合同。实现计划 `docs/plans/2026-09-25-trends-radar-impl-plan.md` 的 TR-33 定义它，依据是 D8、D11、D13、D24、D26、D27、D28、D31、D36、D37 与设计第 7 节。

- **代码**：`customizations/pick-workbench/ggwork_pick/observe/` 下的四个模块，只有数据形状与常量，不判定、不读表、不外发。
  - `contract.py`：开关、标量、枚举、观测条件、结果级 observations 与 obs_as_of_json、证据拍平、冻结输入（第 1 至 5 节）。
  - `contract_rows.py`：判定行、V 状态行、总量行、联动行、提示行、集合摘要（第 6 至 10 节）。
  - `contract_views.py`：八个 pick_obs 视图（第 11 节）。
  - `contract_api.py`：人工决定的请求体与 /sync 的 obs 键（第 12、13 节）。
- **夹具**：`customizations/pick-workbench/tests/fixtures/obs_contract/`，每项都有正例与反例；前端（TR-16、TR-24）读同一批文件。
- **测试**：`customizations/pick-workbench/tests/observe/test_contract.py`。它核对三件事：正例能解析、反例被拒；本文每张字段表、视图表、枚举行与代码逐项相等（四个模块里每个公开模型都要有字段表）；资格真值表与联动用例和夹具自带的规则说明一致。
- **改合同**：按计划第 6 节，只能在 TR-33 或经 G 节点批准的后续 PR 里改，Python 与 TS 两端的测试同时改。本文、代码与夹具任何一处不一致，上面的测试就会变红。

---

## 1. 通用约定

- **两类模型**。存储与 wire 形状继承 `Frozen`：键集合封闭、严格类型（不做 `"1"`→1 这类转换）、不可变、文本原样保存，并拒绝 NUL 与孤立代理项。来自人和模型的输入（条件、决定请求体）继承 `StrictInput`，与 `PickConditions` 同一写法（去首尾空白）。
- **时间戳**：`repository.stamp()` 的形状，UTC、六位小数、`+00:00`，例如 `2026-09-25T01:52:10.000000+00:00`。`Z` 结尾或缺小数都不算。日期是 `YYYY-MM-DD`。GSC API 自带的 metadata 字符串（`first_incomplete_hour` 等）原样保存，不改写。
- **标识**：集合 id 与 GSC 轮次 id 是 32 位小写十六进制（`uuid4().hex`）。判定行、联动行、V 状态行、总量行、提示行、决定行的 id 是自增正整数。身份是 `DramaInput.identity` 的 JSON 串，最长 512。
- **地区**：Trends 的 geo 是 `WW` 或两位大写国家码；GSC 的国家是三位大写国家码，全站合计记 `ALL`。GSC API 返回的小写国家码（含未知国家 `zzz`）进入判定行、V 状态行与联动行之前转成大写；明细表可以按 API 原样存。
- **版本串**：规则版本形如 `trend-rules-v1`、`gsc-rules-v1`、`link-rules-v1`、`watch-rules-v1`、`eval-rules-v1`；市场表形如 `market-map-v1`。当前值由 `observe/versions.py`（TR-01）与各规则模块定义，合同只定格式。
- **「未观测到」（前提 1）**：没有行就是空值（`null`），计数从不写 0 来代替；证据里的空值是空串加说明文字。措辞常量 `UNOBSERVED`（「未观测到」）、`UNOBSERVED_TRENDS`（「在 Google Trends 返回的数据里未观测到」）、`UNOBSERVED_GSC`（「在 GSC 返回的数据里未观测到」）定义在合同里；禁用词清单归 TR-10 的 `wording.py`。
- **两个开关（D11）**：cron 上的 `PICK_OBS_PUBLISH`，等于 `1` 时集合发成 live，否则发成 shadow。gateway 上的 `PICK_OBS_AGENT`，等于 `1` 时工具 schema 带七个观测字段并按它们执行，否则工具参数与改动前逐字相同。常量是 `PUBLISH_SWITCH`、`AGENT_SWITCH`、`SWITCH_ON`。

#### 枚举 `CHANNELS`
取值：`trends`、`gsc`

#### 枚举 `MODES`
取值：`live`、`shadow`

#### 枚举 `SET_STATUSES`
取值：`published`、`pruned`

---

## 2. 观测条件（D31、设计 7.3）

TR-27 用 `PickConditionsObs(PickConditions, ObsConditionFields)` 把七个字段加进工具参数，并写字段说明（说明就是给模型的提示，D11）。前端的 `pickConditionsSchema` 把七个键都声明为可选（TR-16）。

#### `ObsConditionFields`

| 字段 | 类型 | 说明 |
|---|---|---|
| `trend_state` | `TREND_STATES` 之一，或 null | 要求 Trends 判为上升（rising）或从零起量观察（emerging） |
| `trend_geos` | geo 列表，最多 10 个 | 只看这些 geo；空列表表示不限。每个 geo 单独判定，任一满足即命中 |
| `trend_include_first` | 严格布尔 | 接受只成立一天的 first；默认只要 confirmed |
| `trend_include_presumed` | 严格布尔 | 接受强证据、尚未人工确认的推定对应（D31 的第七个字段） |
| `gsc_state` | `GSC_STATES` 之一，或 null | 要求 GSC 正式标签：rising 为 7 天上升，surge 为曝光飙升，from_zero 为从零起量，high_ctr 为高点击率，rank_push 为排名冲顶，present 为在该国家（或全站）有过两层准入的观测、不要求标签 |
| `gsc_countries` | 国家列表，最多 30 个 | 只看这些国家，`ALL` 指全站合计；空列表表示不限 |
| `link_state` | `LINK_STATES` 之一，或 null | 要求同一国家的联动标签（第 8 节） |

- **写入规则**：`conditions_json` 与请求哈希用同一口径，值为假的观测字段（null、空列表、false）一律不写。不带观测条件的结果与改动前逐字相同。
- **排序（D19）**：带观测条件且没指定 sort 时，有效排序是 `obs`（排序版本 `obs-v1`）；指定 `sort=obs` 却没有观测条件，拒绝。
- **由 TR-27 在查询时拒绝的组合**：`rank_push` 只作全站证据，配了不含 `ALL` 的国家列表时拒绝；钉住的是个人批次时拒绝。
- **`unmappable_conditions` 的顺序**：现有七项（tags、posted_account、channel、confirmed_eligible_only、query、exclude_selected、exclude_previous）不变，观测字段按 `UNMAPPABLE_OBS_ORDER`（与上表同序）接在后面，值为真时才列出。资料页的文字见 `OBS_CONDITION_LABELS`，用例见 `unmappable_cases.json`。

#### 枚举 `TREND_STATES`
取值：`rising`、`emerging`

#### 枚举 `GSC_STATES`
取值：`rising`、`surge`、`from_zero`、`high_ctr`、`rank_push`、`present`

#### 枚举 `LINK_STATES`
取值：`both_rising`、`trends_lead_page`、`trends_lead_distribution`、`site_only`、`cooling`

#### 枚举 `SORTS`
取值：`evidence_date`、`rank`、`obs`

---

## 3. 结果级 observations 与 obs_as_of_json（D28、设计 7.4）

`ggwp_candidate_sets.obs_as_of_json` 存 `ObsAsOf`，只在带观测条件的结果上有。`result_view` 只在它存在时加 `observations` 键，内容是它的投影：先放四个引用键（`OBS_AS_OF_REF_KEYS`：trends、gsc、link_rules_version、judged_at），再放内层 `observations` 对象的键（coverage、excluded）。这样 `test_mirror_frozen` 的 `query == result_view(record)` 照样成立。观测时点绝不写进 `data_as_of_json`。

#### `TrendsSetRef`

| 字段 | 类型 | 说明 |
|---|---|---|
| `set_id` | 集合 id | 钉住的 Trends 集合 |
| `published_at` | 时间戳 | 集合发布时刻 |
| `latest_block_end` | 时间戳 | 数据截至：方案 H 是 B6 终点，方案 D 是最近完整日的日末（D39） |

#### `GscSetRef`

| 字段 | 类型 | 说明 |
|---|---|---|
| `set_id` | 集合 id | 钉住的 GSC 集合 |
| `published_at` | 时间戳 | 集合发布时刻 |
| `cutoff` | 时间戳 | 数据截至，即共同截止 H_c |

#### `ObsCoverage`

| 字段 | 类型 | 说明 |
|---|---|---|
| `pool_identities` | 非负整数 | 钉住的共享剧库批次里的身份数 |
| `trends_observed` | 非负整数 | 其中在 Trends 集合里有判定行的身份数（跨批次映射之后） |
| `gsc_observed` | 非负整数 | 其中在 GSC 集合里有判定行的身份数 |

#### `ObsSummary`

| 字段 | 类型 | 说明 |
|---|---|---|
| `coverage` | `ObsCoverage` | 身份覆盖 |
| `excluded` | 原因 → 正整数 | 满足本次非观测条件、却因资格被排除的身份，按原因计数；一个身份有几条原因就各计一次；只列计数至少为 1 的原因 |

#### `Observations`

| 字段 | 类型 | 说明 |
|---|---|---|
| `trends` | `TrendsSetRef` 或 null | 没有钉住 Trends 集合时为 null |
| `gsc` | `GscSetRef` 或 null | 没有钉住 GSC 集合时为 null |
| `link_rules_version` | link-rules 版本或 null | 两个集合都在时的联动规则版本 |
| `judged_at` | 时间戳 | 查询时刻，也就是计算可行动性用的 now（D13） |
| `coverage` | `ObsCoverage` | 同上 |
| `excluded` | 原因 → 正整数 | 同上 |

#### `ObsAsOf`

| 字段 | 类型 | 说明 |
|---|---|---|
| `trends` | `TrendsSetRef` 或 null | 至少钉住一个集合 |
| `gsc` | `GscSetRef` 或 null | 同上 |
| `link_rules_version` | link-rules 版本或 null | 两个集合都在时必填，否则为 null |
| `judged_at` | 时间戳 | 查询时刻 |
| `observations` | `ObsSummary` | 冻结的覆盖与排除计数 |

#### 枚举 `EXCLUSION_REASONS`
取值：`trend_first_only`、`emerging_not_requested`、`title_ambiguous`、`shared_title`、`correspondence_unconfirmed`、`correspondence_presumed`、`stale`、`carried_over`、`b_tier`、`unstable`、`control_unavailable`、`gsc_descriptive_only`、`migration_suspect`、`mapping_changed`、`set_batch_mismatch`

原因的含义见第 14 节。`set_batch_mismatch` 来自跨批次映射（TR-26，设计 7.1），其余来自资格函数（TR-10）。

---

## 4. 证据拍平（D8、前提 1）

观测证据沿用现有 evidence 的九个键，不加新键，也不加 payload；`rank` 恒为 null。结构化数据留在判定行，按 `source_ref` 回查。每条候选最多 6 条 obs 证据（TR-27）。

#### `ObsEvidence`

| 字段 | 类型 | 说明 |
|---|---|---|
| `citation_id` | 字符串 | `<item_id>:<序号>`，接在现有来源信号之后编号 |
| `kind` | `EVIDENCE_KINDS` 之一 | 三种 |
| `source_ref` | `obs:<set_id>:<row_id>` | 判定行或发现行的编号 |
| `observed_at` | 时间戳 | 数据截至：Trends 为判定行的 latest_block_end，GSC 为 W0 终点，发现为集合的 latest_block_end |
| `value` | 字符串或正整数 | 见下表；未观测到时是空串 |
| `label` | 字符串，最长 200 | 以 geo 或国家码加「 · 」开头 |
| `rank` | null | 观测证据不排名次 |
| `grade` | 字符串 | 见下表 |
| `note` | 字符串，最长 1000 | 以「规则版本；截至 T」开头，其余段落用全角分号分隔 |

| kind | value | grade | label 开头 | note 开头 |
|---|---|---|---|---|
| obs_trends | rising、emerging、cooling；未观测到或低量时为空串 | confirmed、first；cooling 与空值时为空串 | geo | trend-rules 版本 |
| obs_gsc | W0 的曝光数（正整数，取哪一份计数由 TR-27 定）；未观测到时为空串 | formal、descriptive | 国家或 ALL | gsc-rules 版本 |
| obs_discovery | 发现词原文，不能为空 | 身份证据等级 strong、medium、weak | geo | trend-rules 版本 |

- **空值**：value 为空串时，note 必须写明 `UNOBSERVED_TRENDS` 或 `UNOBSERVED_GSC`。value 从不是 0，也不是 `"0"`。
- **Trends 不给热度数值**：value 是状态码，不是指数。
- **联动段**：联动事实挂在该 geo 的 obs_trends 条目上；该国没有 Trends 判定行时（site_only）挂在 obs_gsc 条目上。写成 note 里的一段 `联动：<中文标签>（<联动标签码>；可行动|不可行动：<原因,原因>；判定于 <时间戳>）`，标签码取自 `LINK_LABELS`，原因取自 `LINK_ACTIONABILITY_REASONS`，判定时刻就是 `judged_at`。智能体读钉住那一对集合的物化事实行，不自己重算事实（D13）。

#### 枚举 `EVIDENCE_KINDS`
取值：`obs_trends`、`obs_gsc`、`obs_discovery`

---

## 5. 冻结输入（D36）

`ggwp_obs_sets.frozen_inputs_json`：每轮（GSC）或每个会话（Trends）开头读一次，整轮使用，随集合冻结。两种按 `channel` 区分。

#### `RulesRef`

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | 规则版本 | 本集合用到的规则，例如 trend-rules-v1、watch-rules-v1、gsc-rules-v1 |
| `params` | 对象 | 该规则的全部参数，原样冻结 |

#### `SliceRef`

| 字段 | 类型 | 说明 |
|---|---|---|
| `dataset` | `SLICE_DATASETS` 之一 | 明细数据集 C、D、E 或查询 Q |
| `pt_date` | 日期 | 太平洋时间的日期 |
| `version_id` | 行 id | 本轮开头时生效的切片版本 |
| `shape` | `SLICE_SHAPES` 之一 | 整日或按国家拆分 |
| `status` | `SLICE_STATUSES` 之一 | 该版本的请求状态 |
| `stale` | 严格布尔 | 新版本失败或截断、旧版本继续生效时为真 |
| `first_incomplete_hour` | 字符串或 null | API metadata 原样 |
| `first_incomplete_date` | 日期或 null | API metadata 原样 |
| `usable_until` | 时间戳或 null | 可用区间终点；不可用时为 null |
| `watermark_absent` | 严格布尔 | 跨过 A 的水位却缺 metadata（设计 5.3） |

#### `TotalsRef`

| 字段 | 类型 | 说明 |
|---|---|---|
| `a` | 行 id 列表 | 本轮 A 的总量行 |
| `a_prime` | 行 id 列表或 null | 本轮 A′ 的总量行；小时数据不支持 byPage 时为 null |
| `a2_all` | 行 id 列表 | 本轮 A″a（all）的总量行 |
| `a2_final` | 行 id 列表 | 本轮 A″f（final）的总量行 |

#### `VcheckSummary`

| 字段 | 类型 | 说明 |
|---|---|---|
| `requests` | 非负整数 | 本轮逐剧核对的请求数 |
| `succeeded` | 非负整数 | 成功 |
| `failed` | 非负整数 | 失败 |
| `truncated` | 非负整数 | 截断 |
| `stale` | 非负整数 | 用到陈旧切片 |
| `reused` | 非负整数 | 按 D26 沿用的 Vd |
| `regex_overflow` | 非负整数 | 正则分块溢出 |

#### `FrozenInputsTrends`

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | `trends` | 区分字段 |
| `collector_version` | 采集版本 | 换源就换版本，不同来源的集合不互相确认 |
| `source_catalog_batch_id` | 批次 id | 来源共享剧库批次 |
| `alias_version` | 非负整数 | 别名版本（D43），0 表示还没有别名 |
| `decisions_version` | 非负整数 | 读到的人工决定最大 id（D24），0 表示没有 |
| `market_map_version` | 市场表版本 | market-map-v1 |
| `link_rules_version` | link-rules 版本 | 发布时物化联动用的版本 |
| `rules` | `RulesRef` 列表，至少 1 个 | trend-rules 与 watch-rules 及其全部参数 |
| `granularity` | `GRANULARITIES` 之一 | 阶段 0 选定的颗粒度 |
| `target_date` | 日期 | 目标发布日（D23） |
| `window_end` | 时间戳 | 批次创建时算好的窗口终点 |

#### `FrozenInputsGsc`

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | `gsc` | 区分字段 |
| `collector_version` | 采集版本 | 同上 |
| `source_catalog_batch_id` | 批次 id | 同上 |
| `alias_version` | 非负整数 | 本轮第 0 步刷新后的别名版本 |
| `decisions_version` | 非负整数 | 同上 |
| `market_map_version` | 市场表版本 | 同上 |
| `link_rules_version` | link-rules 版本 | 同上 |
| `rules` | `RulesRef` 列表，至少 1 个 | gsc-rules 及其全部参数 |
| `round_id` | 轮次 id | 本轮编号 |
| `legacy_snapshot_id` | 字符串 | 旧页快照；没有快照时 GSC 不发布（TR-23b） |
| `mirror_version` | 正整数或 null | `rs_ids` 所在的镜像版本；没有镜像时为 null |
| `slices` | `SliceRef` 列表 | 各切片的生效版本、状态与 metadata |
| `totals` | `TotalsRef` | A、A′、A″a、A″f 的总量行 |
| `vcheck_summary` | `VcheckSummary` | 逐剧核对汇总 |

#### 枚举 `GRANULARITIES`
取值：`H`、`D`、`HD`

#### 枚举 `SLICE_DATASETS`
取值：`C`、`D`、`E`、`Q`

#### 枚举 `SLICE_SHAPES`
取值：`whole`、`by_country`

#### 枚举 `SLICE_STATUSES`
取值：`fetched`、`truncated`、`failed`

---

## 6. 判定行（`ggwp_obs_states`，视图 `pick_obs.states`）

资料页详情与智能体只读判定行（设计 7.1）。Trends 一行是身份 × geo；GSC 一行是身份 × 国家（或 ALL）× 窗口，`state` 取主标签，没有标签时为 present，`labels` 列出全部命中的标签及其命中条件与原始计数。

#### `LabelHit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `label` | `GSC_LABELS` 之一 | 标签 |
| `formal` | 严格布尔 | 是否过了该标签的正式门槛（设计 5.8） |
| `condition` | 字符串 | 命中条件原文 |
| `counts` | 名称 → 非负整数或 null | 原始计数；null 表示未观测到 |

#### `QualityNote`

| 字段 | 类型 | 说明 |
|---|---|---|
| `tested` | 严格布尔 | 基线没有行或为 0 时不检验（D38） |
| `rate_ratio` | 浮点或 null | 率比 RR |
| `dispersion` | 浮点或 null | 离散度 φ |
| `z` | 浮点或 null | 检验统计量 |
| `p_value` | 0 到 1 或 null | 单侧 p 值 |
| `bh_adjusted` | 0 到 1 或 null | BH 调整后的值 |
| `bh_q` | 0 到 1 | BH 的 q，gsc-rules-v1 取 0.10 |
| `bh_passed` | 严格布尔或 null | 是否通过 BH |
| `note` | 字符串 | 注记原文，例如「基线未观测，不检验」 |

做了检验时六个数值字段都有值，没做检验时都为 null。质量注记只作注记，不作门槛。

#### `PasteRow`

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | 字符串 | 剧名 |
| `url` | 现行剧目页 URL | 形如 `https://<主机>/<locale>/drama/<slug>-<24 位十六进制>`，满足 RealShort `editorial-sheet.ts` 的解析 |
| `impressions` | 非负整数或 null | 展示 |
| `clicks` | 非负整数或 null | 点击 |
| `window_kind` | `GSC_WINDOW_KINDS` 之一 | 数字所属的窗口 |
| `top_query` | 字符串或 null | 主要查询词 |
| `verified_on` | 日期 | 核实日期，取集合日期 |
| `note` | 字符串 | 规则版本、Trends 状态与 geo |

#### `StateRow`

| 字段 | 类型 | 说明 |
|---|---|---|
| `row_id` | 行 id | 判定行编号，证据 source_ref 用它 |
| `set_id` | 集合 id | 所属集合 |
| `channel` | `CHANNELS` 之一 | 通道 |
| `mode` | `MODES` 之一 | 所属集合的模式 |
| `identity` | 身份 | 集合来源批次里的身份 |
| `title` | 字符串 | 判定时的剧名 |
| `language` | 字符串 | 语种 |
| `theater` | 字符串 | 剧场（平台） |
| `scope` | geo 或国家 | Trends 为 geo，GSC 为国家或 ALL |
| `window_kind` | `TRENDS_WINDOW_KINDS` 或 `GSC_WINDOW_KINDS` 之一 | Trends 为颗粒度，GSC 为窗口 |
| `state` | `TRENDS_ROW_STATES` 或 `GSC_STATES` 之一 | 判定结果 |
| `confirmation` | `CONFIRMATIONS` 之一或 null | 只有 Trends 的 rising、emerging 带 |
| `admission` | `ADMISSIONS` 之一或 null | GSC 两层准入的结果，GSC 必填 |
| `labels` | `LabelHit` 列表 | GSC 命中的标签；Trends 为空列表 |
| `tier` | `TIERS` 之一或 null | Trends 必填 |
| `correspondence` | `CORRESPONDENCES` 之一或 null | Trends 必填，冻结时按 decisions_version 的有效状态写；GSC 为 null（页面归属由 URL 决定） |
| `id_evidence` | `ID_EVIDENCE_LEVELS` 之一或 null | Trends 必填 |
| `ambiguity` | `AMBIGUITIES` 之一或 null | Trends 必填 |
| `flags` | 标记列表 | Trends 取 `TRENDS_FLAGS`，GSC 取 `GSC_FLAGS` |
| `carried_over` | 严格布尔 | Trends 没刷新到、沿用上次状态；GSC 恒为假 |
| `stale` | 严格布尔 | 沿用超过 3 天；GSC 恒为假（切片陈旧记在 flags） |
| `window_end` | 时间戳 | Trends 为该行自己的窗口终点，GSC 为 W0 终点 |
| `latest_block_end` | 时间戳或 null | Trends 必填（D39），GSC 为 null |
| `metrics` | 对象 | 全部中间量与原始计数；未观测到记 null |
| `quality_note` | `QualityNote` 或 null | 只属于 GSC 的 7 天窗口 |
| `paste_row` | `PasteRow` 或 null | 只属于有现行剧目页的 GSC 行 |
| `created_at` | 时间戳 | 写入时刻 |

#### 枚举 `TRENDS_ROW_STATES`
取值：`rising`、`emerging`、`cooling`、`flat`、`sparse`、`insufficient_window`、`ambiguous`、`failed`

ambiguous 指泛词或被更长剧名包含、没有请求裸剧名，结果「歧义，不判定（设计如此）」；failed 指没有成功值可用。沿用与陈旧不是状态，记在 carried_over、stale。

#### 枚举 `GSC_LABELS`
取值：`surge`、`from_zero`、`high_ctr`、`rank_push`、`rising`

#### 枚举 `TRENDS_FLAGS`
取值：`shared_title`、`unstable`、`control_unavailable`

#### 枚举 `GSC_FLAGS`
取值：`migration_suspect`、`mapping_changed`、`gap_exceeded`、`unverifiable`、`detail_gap`、`stale_slice`、`short_history`

#### 枚举 `AMBIGUITIES`
取值：`clear`、`generic`、`contained`、`unresolved`、`manual_required`

#### 枚举 `CONFIRMATIONS`
取值：`confirmed`、`first`

#### 枚举 `ADMISSIONS`
取值：`formal`、`descriptive`

#### 枚举 `TIERS`
取值：`A`、`B`

#### 枚举 `CORRESPONDENCES`
取值：`confirmed`、`unconfirmed`

未确认而身份证据为强的，资料页显示「推定对应」。

#### 枚举 `ID_EVIDENCE_LEVELS`
取值：`strong`、`medium`、`weak`

#### 枚举 `TRENDS_WINDOW_KINDS`
取值：`H`、`D`

#### 枚举 `GSC_WINDOW_KINDS`
取值：`24h`、`7d`

---

## 7. V 状态行与总量行（D26、D27）

#### `SliceDay`

| 字段 | 类型 | 说明 |
|---|---|---|
| `pt_date` | 日期 | Vd 覆盖的 PT 日 |
| `version_id` | 行 id | 该日当时生效的 D 或 E 切片版本 |

#### `VcheckRow`

`ggwp_gsc_vchecks`：身份 × 核对种类 × 窗口 × 国家 × dataState。Vh 每轮重取、从不复用；Vd 在 D26 的三个条件（14 个 PT 日相同、各日切片版本相同、各日 dataState 相同）都满足时沿用。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | 行 id | 编号 |
| `round_id` | 轮次 id | 本轮 |
| `identity` | 身份 | 被核对的身份 |
| `check_kind` | `VCHECK_KINDS` 之一 | Vh 或 Vd |
| `window_label` | `WINDOW_LABELS` 之一 | W0 或 W−1 |
| `window_start` | 时间戳 | 窗口起点 |
| `window_end` | 时间戳 | 窗口终点 |
| `country` | 国家或 ALL | 比较的国家 |
| `data_state` | `DATA_STATES` 之一 | Vh 为 hourly_all，Vd 为 all 或 final |
| `impressions` | 非负整数或 null | X_flt；响应里没有行时为 null |
| `clicks` | 非负整数或 null | 同上 |
| `row_count` | 非负整数 | 响应行数；为 0 时两个计数都是 null |
| `request_status` | `VCHECK_STATUSES` 之一 | 请求状态 |
| `chunk_count` | 非负整数 | 正则分块数 |
| `slice_versions` | `SliceDay` 列表 | Vd 覆盖的日期与切片版本；Vh 为空列表 |
| `fetched_at` | 时间戳 | 取数时刻 |
| `reused` | 严格布尔 | 是否沿用 |
| `reused_from` | 行 id 或 null | 沿用时指向被沿用的行 |

#### `TotalsRow`

`ggwp_gsc_totals`：A、A′ 按小时（hourly_all），A″a、A″f 按 PT 日（all、final）。final 还没有数据的日子记 null，request_status 仍是 fetched，表示「无行」。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | 行 id | 编号 |
| `round_id` | 轮次 id | 本轮 |
| `kind` | `TOTALS_KINDS` 之一 | 请求编号 |
| `data_state` | `DATA_STATES` 之一 | 由 kind 决定 |
| `hour` | 时间戳或 null | A、A′ 的小时 |
| `pt_date` | 日期或 null | A″ 的 PT 日 |
| `impressions` | 非负整数或 null | 无行为 null |
| `clicks` | 非负整数或 null | 与曝光同时为 null |
| `request_status` | `TOTALS_STATUSES` 之一 | A′ 不支持时为 unsupported |
| `watermark` | 字符串或 null | A、A′ 为 first_incomplete_hour，A″ 为 first_incomplete_date，原样 |
| `fetched_at` | 时间戳 | 取数时刻 |

#### 枚举 `DATA_STATES`
取值：`hourly_all`、`all`、`final`

#### 枚举 `VCHECK_KINDS`
取值：`vh`、`vd`

#### 枚举 `WINDOW_LABELS`
取值：`w0`、`w_minus_1`

#### 枚举 `VCHECK_STATUSES`
取值：`fetched`、`truncated`、`failed`、`regex_overflow`

#### 枚举 `TOTALS_KINDS`
取值：`a`、`a_prime`、`a2_all`、`a2_final`

#### 枚举 `TOTALS_STATUSES`
取值：`fetched`、`truncated`、`failed`、`unsupported`

---

## 8. 联动（D13、D39、设计 6.1）

任一采集服务发布集合时，对「新集合 × 对方通道最新的同模式集合」按 link-rules 版本物化事实行。只冻结事实；可行动性在判定时刻用显式的 now 计算（智能体取查询时刻并写进证据，资料页取请求时刻）。

#### `LinkFact`

`link_facts` 对一个身份的输出，每个国家至多一行。

| 字段 | 类型 | 说明 |
|---|---|---|
| `country` | 国家、ALL 或 null | different_markets 为 null，global_parallel 为 ALL |
| `trends_geo` | geo 或 null | 与 country 配对的 geo |
| `label` | `LINK_LABELS` 之一 | 联动标签 |
| `trends_row_id` | 行 id 或 null | 用到的 Trends 判定行 |
| `gsc_row_id` | 行 id 或 null | 用到的 GSC 判定行 |
| `trends_anchor` | 时间戳 | Trends 判定行的 latest_block_end，没有行时取集合的 |
| `gsc_anchor` | 时间戳 | GSC 判定行的 W0 终点，没有行时取集合的 cutoff |
| `pair_gap_minutes` | 非负整数 | 两个锚点相隔的整分钟数 |
| `timely` | 严格布尔 | 相隔不超过 48 小时 |
| `published_gap_minutes` | 非负整数 | 两个集合发布时刻相隔的整分钟数 |
| `stale` | 严格布尔 | Trends 判定行是 carried_over 或 stale |

#### `LinkRow`

`ggwp_obs_links` 与视图 `pick_obs.links`：先是编号与配对，再是事实，最后是写入时刻。可行动性从不存储。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | 行 id | 编号 |
| `link_rules_version` | link-rules 版本 | 物化所用版本，是主键的一部分 |
| `trends_set_id` | 集合 id | 配对的 Trends 集合 |
| `gsc_set_id` | 集合 id | 配对的 GSC 集合 |
| `mode` | `MODES` 之一 | 同模式配对：shadow 只配 shadow，live 只配 live |
| `identity` | 身份 | 联动的身份 |
| `country` | 国家、ALL 或 null | 同 LinkFact |
| `trends_geo` | geo 或 null | 同 LinkFact |
| `label` | `LINK_LABELS` 之一 | 同 LinkFact |
| `trends_row_id` | 行 id 或 null | 同 LinkFact |
| `gsc_row_id` | 行 id 或 null | 同 LinkFact |
| `trends_anchor` | 时间戳 | 同 LinkFact |
| `gsc_anchor` | 时间戳 | 同 LinkFact |
| `pair_gap_minutes` | 非负整数 | 同 LinkFact |
| `timely` | 严格布尔 | 同 LinkFact |
| `published_gap_minutes` | 非负整数 | 同 LinkFact |
| `stale` | 严格布尔 | 同 LinkFact |
| `created_at` | 时间戳 | 写入时刻 |

### 8.1 link-rules-v1 的事实规则

- **只在同一国家内联动**。geo 与国家按 market-map-v1 配对：WW↔ALL、US↔USA、GB↔GBR、ES↔ESP、DE↔DEU、FR↔FRA、IT↔ITA、MX↔MEX、BR↔BRA、BG↔BGR。配不上的国家（菲律宾、印度等）不参与联动，只进全站合计。
- **排除**：Trends 判定行带 unstable 时，这个国家整行不出事实。GSC 行带 migration_suspect、mapping_changed、gap_exceeded、unverifiable 的丢弃。GSC 降级（`[hour,page]` 加日级国家）时，按国家的 24 小时行视同不存在，只用 7 天行。
- **记号**：up(t) 为 Trends 行 rising 且 confirmed；low(t) 为没有行，或状态是 flat、sparse；gsc_up 为某个两层准入为 formal 的 GSC 行带正式的 surge 或 from_zero（24 小时）或 rising（7 天）标签；gsc_low 为没有丢弃任何行、剩下的行都没有任何标签。
- **按序取第一个成立的**：cooling(t) → cooling；up 且 gsc_up → both_rising；up 且 gsc_low 且本站有现行剧目页 → trends_lead_page；up 且没有页面 → trends_lead_distribution；low 且 gsc_up → site_only；否则不出事实。first、emerging 既不算 up 也不算 low。
- **全球**：WW 为 up 且 ALL 为 gsc_up，出 global_parallel（「全球同向」），不给动作。
- **不同市场**：没有任何国家是 both_rising，但有一国 up、另一国 gsc_up 时，加一行 different_markets（「不同市场信号」）。
- **引用的 GSC 行**：第一个 gsc_up 行，否则第一个剩下的行，按 24 小时在前、7 天在后排。
- **时效**：pair_gap_minutes 取两个锚点之差的绝对值，按整分钟向下取；timely 为不超过 48 小时。published_gap_minutes 同样算。事实按国家排序，null 在最后。

### 8.2 可行动性

`link_actionable(fact, trends_published_at, gsc_published_at, now)` 返回是否可行动与原因。原因按以下顺序全部列出：label_not_actionable（global_parallel、different_markets 从不给动作）、untimely_pair（配对内部时效不符）、stale_row（用到沿用或陈旧的 Trends 行）、trends_set_too_old（now 距 Trends 集合发布超过 26 小时）、gsc_set_too_old（now 距 GSC 集合发布超过 6 小时）。恰好 26 小时或 6 小时仍可行动。资料页把任一时效原因显示成「时效不符」。

#### 枚举 `LINK_LABELS`
取值：`both_rising`、`trends_lead_page`、`trends_lead_distribution`、`site_only`、`cooling`、`global_parallel`、`different_markets`

中文标签依次是：双涨、站外先行·补页、站外先行·仅分发、站内独涨、退潮、全球同向、不同市场信号。前五个也是 `link_state` 的取值。

#### 枚举 `LINK_ACTIONABILITY_REASONS`
取值：`label_not_actionable`、`untimely_pair`、`stale_row`、`trends_set_too_old`、`gsc_set_too_old`

#### 枚举 `LINK_EXCLUDED_FLAGS`
取值：`migration_suspect`、`mapping_changed`、`gap_exceeded`、`unverifiable`、`unstable`

---

## 9. 提示行（D37、设计 6.2）

`ggwp_obs_alerts` 与视图 `pick_obs.alerts`，保留 180 天，不随集合清理。

#### `AlertRow`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | 行 id | 编号；人工标无关按它引用 |
| `mode` | `MODES` 之一 | shadow 提示不进提前量指标，也不占 live 的去重窗口 |
| `channel` | `CHANNELS` 之一 | 通道 |
| `set_id` | 集合 id | 触发提示的集合 |
| `state_row_id` | 行 id | 触发提示的判定行 |
| `identity` | 身份 | 判定时的身份 |
| `root_identity` | 身份 | 沿冻结别名版本追到最早的身份 |
| `state` | 提示状态 | Trends 取 `TRENDS_ALERT_STATES`，GSC 取 `GSC_ALERT_STATES` |
| `scope` | geo 或国家 | Trends 为 geo，GSC 为国家或 ALL |
| `dedupe_key` | 字符串 | `dedupe_key_of(root_identity, channel, state, scope, mode)`：五项的 JSON 数组，写法与身份相同 |
| `published_at` | 时间戳 | 集合发布时刻，即提前量的起点 |
| `eval_rules_version` | eval-rules 版本 | 评估口径 |
| `alias_version` | 非负整数 | 追溯 root_identity 所用的别名版本 |
| `evidence` | 对象 | 判定行全部中间量的快照 |
| `created_at` | 时间戳 | 写入时刻 |

#### 枚举 `TRENDS_ALERT_STATES`
取值：`rising_first`、`rising_confirmed`、`emerging`

#### 枚举 `GSC_ALERT_STATES`
取值：`surge`、`from_zero`、`rising`

---

## 10. 集合摘要（`ggwp_obs_sets.summary_json`，视图 `pick_obs.sets` 的 summary）

资料页常驻的覆盖数字、未覆盖名单与状态码都从这里读，不回查明细表。按 `channel` 区分两种。

#### `UncoveredUnit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `identity` | 身份 | 未覆盖的身份 |
| `geo` | geo | 未覆盖的 geo |
| `reason` | `UNCOVERED_REASONS` 之一 | 预算截断、熔断跳过或截止 |

#### `SetSummaryTrends`

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | `trends` | 区分字段 |
| `planned_units` | 非负整数 | 计划的查询单元数 |
| `fetched_units` | 非负整数 | 实际取到的单元数 |
| `uncovered_units` | `UncoveredUnit` 列表 | 资料页「未覆盖 N 个单元」的名单 |
| `a_tier_coverage` | 0 到 1 | A 档覆盖率；低于 0.8 不发布 |
| `breaker_events` | 非负整数 | 熔断次数 |
| `all_zero_rate` | 0 到 1 或 null | 全零率 |
| `user_types` | 字符串列表 | 本会话见到的 userType |
| `ambiguous_undecided` | 非负整数 | 「歧义，不判定（设计如此）」的计数 |
| `carried_over` | 非负整数 | 沿用行数 |
| `stale` | 非负整数 | 陈旧行数 |
| `rule4_no_data` | 严格布尔 | 规则 4 还没有同模式的 GSC 集合可读 |
| `status_codes` | 状态码列表 | 本集合的状态码 |

#### `CoverageLayers`

设计 5.6 的三层中前两层，按指标各一份。

| 字段 | 类型 | 说明 |
|---|---|---|
| `metric` | `METRICS` 之一 | 曝光或点击 |
| `received` | 非负整数 | 收到的明细合计 |
| `attributed` | 非负整数 | 已归因到剧的部分 |
| `unattributed` | 种类 → 非负整数 | 各类未归因；第一层要求已归因加各类未归因等于收到的明细合计 |
| `site_total` | 非负整数或 null | A′（或 A″）的全站总量；两种都拿不到时为 null |
| `detail_gap` | 整数或 null | 第二层：全站总量减收到的明细；没有总量时为 null |

#### `Unknowable`

| 字段 | 类型 | 说明 |
|---|---|---|
| `truncated_slices` | 非负整数 | 截断的切片数 |
| `failed_slices` | 非负整数 | 失败的切片数 |
| `stale_slices` | 非负整数 | 陈旧的切片数 |
| `hours` | 非负整数 | 这些切片覆盖的小时数 |

#### `SetSummaryGsc`

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | `gsc` | 区分字段 |
| `cutoff` | 时间戳或 null | 共同截止；本轮不出正式 24 小时窗口时为 null |
| `layers` | `CoverageLayers` 列表 | 第一、二层 |
| `unknowable` | `Unknowable` | 第三层 |
| `site_admission_24h` | `SITE_ADMISSIONS` 之一 | 24 小时窗口的全站层准入 |
| `site_admission_7d` | `SITE_ADMISSIONS` 之一 | 7 天窗口的全站层准入 |
| `vcheck_summary` | `VcheckSummary` | 逐剧核对汇总 |
| `requests` | 非负整数 | 本轮请求数 |
| `quota_errors` | 非负整数 | 配额错误数 |
| `status_codes` | 状态码列表 | 本集合的状态码 |

#### 枚举 `UNCOVERED_REASONS`
取值：`truncated`、`skipped_breaker`、`deadline`

#### 枚举 `UNATTRIBUTED_KINDS`
取值：`legacy_unmapped`、`delisted`、`noncanonical`、`locale_mismatch`、`site_level`、`editorial`、`offsite`、`blog`

依次是旧页未收录、目标已下架、非正典、locale 不符、站点级、编辑页、站外、已转博客（设计 5.5、5.6）。

#### 枚举 `SITE_ADMISSIONS`
取值：`usable`、`gap_exceeded`、`unverifiable`

#### 枚举 `METRICS`
取值：`impressions`、`clicks`

---

## 11. pick_obs 视图（设计 3.4、3.6）

0007 在 PG 上建 `pick_obs` schema（TR-11），只放下面八个视图，列名与类型以本节为准，`pick_board_reader` 只读。运行时表（cookie 罐、请求日志、租约）不进视图。类型是合同类型，`VIEW_PG_TYPES` 给出 `information_schema.columns.data_type` 可以是什么：text 对应 text 或 character varying，int 对应 integer 或 bigint，bool 对应 boolean，json 对应 json，float 对应 double precision。json 列的内部形状见各节模型。

#### 视图 `pick_obs.sets`

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `set_id` | `text` | 否 | 集合 id |
| `channel` | `text` | 否 | 通道 |
| `mode` | `text` | 否 | live 或 shadow |
| `status` | `text` | 否 | published 或 pruned |
| `published_at` | `text` | 否 | 发布时刻 |
| `as_of` | `text` | 否 | Trends 为 latest_block_end，GSC 为 cutoff |
| `source_catalog_batch_id` | `text` | 否 | 来源共享剧库批次 |
| `collector_version` | `text` | 否 | 采集版本 |
| `rules_version` | `text` | 否 | trend-rules 或 gsc-rules 版本 |
| `link_rules_version` | `text` | 否 | 物化联动所用版本 |
| `alias_version` | `int` | 否 | 别名版本 |
| `decisions_version` | `int` | 否 | 人工决定版本 |
| `target_date` | `text` | 是 | Trends 的目标发布日 |
| `window_end` | `text` | 是 | Trends 的窗口终点 |
| `round_id` | `text` | 是 | GSC 的轮次 |
| `frozen_inputs` | `json` | 否 | `FrozenInputsTrends` 或 `FrozenInputsGsc` |
| `summary` | `json` | 否 | `SetSummaryTrends` 或 `SetSummaryGsc` |

#### 视图 `pick_obs.states`

列与 `StateRow` 逐一相同（第 6 节）。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `row_id` | `int` | 否 | 判定行编号 |
| `set_id` | `text` | 否 | 所属集合 |
| `channel` | `text` | 否 | 通道 |
| `mode` | `text` | 否 | 模式 |
| `identity` | `text` | 否 | 身份 |
| `title` | `text` | 否 | 剧名 |
| `language` | `text` | 否 | 语种 |
| `theater` | `text` | 否 | 剧场 |
| `scope` | `text` | 否 | geo 或国家 |
| `window_kind` | `text` | 否 | 颗粒度或窗口 |
| `state` | `text` | 否 | 判定结果 |
| `confirmation` | `text` | 是 | 确认级别 |
| `admission` | `text` | 是 | GSC 准入 |
| `labels` | `json` | 否 | `LabelHit` 列表 |
| `tier` | `text` | 是 | 档位 |
| `correspondence` | `text` | 是 | 对应确认 |
| `id_evidence` | `text` | 是 | 身份证据等级 |
| `ambiguity` | `text` | 是 | 歧义判定 |
| `flags` | `json` | 否 | 标记列表 |
| `carried_over` | `bool` | 否 | 沿用 |
| `stale` | `bool` | 否 | 陈旧 |
| `window_end` | `text` | 否 | 窗口终点 |
| `latest_block_end` | `text` | 是 | 最新块终点 |
| `metrics` | `json` | 否 | 中间量与原始计数 |
| `quality_note` | `json` | 是 | `QualityNote` |
| `paste_row` | `json` | 是 | `PasteRow` |
| `created_at` | `text` | 否 | 写入时刻 |

#### 视图 `pick_obs.links`

列与 `LinkRow` 逐一相同（第 8 节）。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | `int` | 否 | 编号 |
| `link_rules_version` | `text` | 否 | 版本 |
| `trends_set_id` | `text` | 否 | Trends 集合 |
| `gsc_set_id` | `text` | 否 | GSC 集合 |
| `mode` | `text` | 否 | 模式 |
| `identity` | `text` | 否 | 身份 |
| `country` | `text` | 是 | 国家 |
| `trends_geo` | `text` | 是 | geo |
| `label` | `text` | 否 | 联动标签 |
| `trends_row_id` | `int` | 是 | Trends 判定行 |
| `gsc_row_id` | `int` | 是 | GSC 判定行 |
| `trends_anchor` | `text` | 否 | Trends 锚点 |
| `gsc_anchor` | `text` | 否 | GSC 锚点 |
| `pair_gap_minutes` | `int` | 否 | 锚点相隔分钟 |
| `timely` | `bool` | 否 | 配对内部时效 |
| `published_gap_minutes` | `int` | 否 | 发布相隔分钟 |
| `stale` | `bool` | 否 | 用到沿用或陈旧行 |
| `created_at` | `text` | 否 | 写入时刻 |

#### 视图 `pick_obs.discoveries`

发现段的命中（设计 4.8），唯一匹配且剧名清楚的进 A 档，其余进发现队列，池外命中只在资料页显示。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `discovery_id` | `int` | 否 | 编号，证据 source_ref 用它 |
| `set_id` | `text` | 否 | 所属 Trends 集合 |
| `mode` | `text` | 否 | 模式 |
| `geo` | `text` | 否 | 查询的 geo |
| `seed` | `text` | 否 | 种子词 |
| `property` | `text` | 否 | `DISCOVERY_PROPERTIES` 之一 |
| `term` | `text` | 否 | 相关查询原文 |
| `normalized_term` | `text` | 否 | 去掉种子词、平台词、意图词后的规范化剧名 |
| `language` | `text` | 是 | 语种；判断不了时为 null |
| `match_status` | `text` | 否 | `DISCOVERY_MATCHES` 之一 |
| `route` | `text` | 否 | `DISCOVERY_ROUTES` 之一 |
| `matched_identity` | `text` | 是 | 唯一匹配到的身份 |
| `breakout` | `bool` | 否 | 相关查询标为 Breakout |
| `first_seen_at` | `text` | 否 | 首次命中时刻 |

#### 视图 `pick_obs.alias_queue`

别名表里待人工处理的行（设计 7.2），别名与发布模式无关，所以没有 mode 列。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `alias_id` | `int` | 否 | 编号，决定请求用它 |
| `alias_version` | `int` | 否 | 产生这条别名的版本 |
| `old_identity` | `text` | 否 | 消失的身份 |
| `new_identity` | `text` | 否 | 新增的身份 |
| `status` | `text` | 否 | `ALIAS_STATUSES` 之一 |
| `platform` | `text` | 否 | 平台 |
| `language` | `text` | 否 | 语种 |
| `old_title` | `text` | 否 | 旧剧名 |
| `new_title` | `text` | 否 | 新剧名 |
| `evidence` | `json` | 否 | 建议的依据 |
| `created_at` | `text` | 否 | 写入时刻 |

#### 视图 `pick_obs.confirm_queue`

判出 rising 或 emerging、对应关系尚未人工确认的身份（设计 4.7），按 (身份、平台、规范化标题) 确认。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `identity` | `text` | 否 | 身份 |
| `title` | `text` | 否 | 剧名 |
| `platform` | `text` | 否 | 平台 |
| `normalized_title` | `text` | 否 | 规范化标题 |
| `language` | `text` | 否 | 语种 |
| `geo` | `text` | 否 | 判出上升的 geo |
| `state` | `text` | 否 | rising 或 emerging |
| `confirmation` | `text` | 是 | confirmed 或 first |
| `id_evidence` | `text` | 否 | 身份证据等级 |
| `set_id` | `text` | 否 | 所属集合 |
| `mode` | `text` | 否 | 模式 |
| `since` | `text` | 否 | 首次进入队列的时刻 |

#### 视图 `pick_obs.alerts`

列与 `AlertRow` 逐一相同（第 9 节）。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | `int` | 否 | 编号 |
| `mode` | `text` | 否 | 模式 |
| `channel` | `text` | 否 | 通道 |
| `set_id` | `text` | 否 | 集合 |
| `state_row_id` | `int` | 否 | 判定行 |
| `identity` | `text` | 否 | 身份 |
| `root_identity` | `text` | 否 | 根身份 |
| `state` | `text` | 否 | 提示状态 |
| `scope` | `text` | 否 | geo 或国家 |
| `dedupe_key` | `text` | 否 | 去重键 |
| `published_at` | `text` | 否 | 集合发布时刻 |
| `eval_rules_version` | `text` | 否 | 评估版本 |
| `alias_version` | `int` | 否 | 别名版本 |
| `evidence` | `json` | 否 | 中间量快照 |
| `created_at` | `text` | 否 | 写入时刻 |

#### 视图 `pick_obs.run_status`

每次采集批次（Trends）或轮次（GSC）一行，状态横幅与 `run_missed` 从这里判断。

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `channel` | `text` | 否 | 通道 |
| `batch_id` | `text` | 否 | 批次或轮次编号 |
| `mode` | `text` | 否 | 这次运行会发布成的模式 |
| `target_date` | `text` | 是 | Trends 的目标发布日 |
| `round_id` | `text` | 是 | GSC 的轮次 |
| `started_at` | `text` | 否 | 开始时刻 |
| `finished_at` | `text` | 是 | 结束时刻；还在跑时为 null |
| `outcome` | `text` | 否 | `RUN_OUTCOMES` 之一 |
| `requests` | `int` | 否 | 请求数 |
| `published_set_id` | `text` | 是 | 发布出的集合 |
| `status_codes` | `json` | 否 | 状态码列表 |

#### 枚举 `VIEW_TYPES`
取值：`text`、`int`、`bool`、`json`、`float`

#### 枚举 `DISCOVERY_PROPERTIES`
取值：`web`、`youtube`

#### 枚举 `DISCOVERY_MATCHES`
取值：`unique`、`multiple`、`out_of_pool`

#### 枚举 `DISCOVERY_ROUTES`
取值：`a_tier`、`queue`、`display_only`

#### 枚举 `ALIAS_STATUSES`
取值：`auto`、`suggested`、`confirmed`、`rejected`

#### 枚举 `RUN_OUTCOMES`
取值：`running`、`published`、`withheld`、`failed`

withheld 指跑完但没有发布（例如 A 档覆盖率不足 80%，上一个集合继续生效）。

---

## 12. 人工决定（D12、D24）

`POST /api/pick/obs/decisions` 的请求体，按 `kind` 区分九种，写进追加式表 `ggwp_obs_decisions`（TR-25）。操作人来自认证，不在请求体里；缺失或为 default 就拒绝。TR-35 的 `effective(decisions, upto_id)` 按 id 顺序应用，后者覆盖前者。每种都带 `kind`、`request_id`（1 到 128 字）与可选的 `note`（最多 500 字）。

#### `AliasConfirm`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `alias_confirm` | 确认一条建议别名 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `alias_id` | 正整数 | 别名队列里的编号 |

#### `AliasReject`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `alias_reject` | 拒绝一条建议别名 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `alias_id` | 正整数 | 别名队列里的编号 |

#### `AliasPair`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `alias_pair` | 标题真正修订时手动配对，由 gsc 服务写成新的别名版本（D43） |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `old_identity` | 身份 | 消失的身份 |
| `new_identity` | 身份 | 新增的身份，不能与旧身份相同 |

#### `CorrespondenceConfirm`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `correspondence_confirm` | 确认命中的剧与这一行是同一部 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `identity` | 身份 | 确认的身份 |
| `platform` | 字符串 | 确认时的平台 |
| `normalized_title` | 字符串 | 确认时的规范化标题；平台或标题变了、经别名换了身份，确认失效 |

#### `CorrespondenceRevoke`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `correspondence_revoke` | 撤销对这个身份的对应确认 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `identity` | 身份 | 撤销的身份 |

#### `WatchAdd`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `watch_add` | 人工加入观察清单，生效条目最多 50 条 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `identity` | 身份 | 加入的身份 |
| `geo` | geo | 查询的 geo |
| `active` | 严格布尔 | 为假时撤回这次人工加入 |

#### `WatchPause`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `watch_pause` | 暂停或恢复观察 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `identity` | 身份 | 暂停的身份 |
| `geo` | geo 或 null | null 表示全部 geo |
| `paused` | 严格布尔 | 为假时恢复 |

#### `AmbiguityOverride`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `ambiguity_override` | 歧义改判，处理 `manual_required` 与误判 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `identity` | 身份 | 改判的身份 |
| `verdict` | `AMBIGUITY_VERDICTS` 之一 | 剧名清楚或歧义 |

#### `AlertIrrelevant`

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | `alert_irrelevant` | 把一条提示标为无关，计入人工忽略率 |
| `request_id` | 字符串 | 请求编号 |
| `note` | 字符串 | 备注 |
| `alert_id` | 正整数 | 提示编号 |

#### 枚举 `DECISION_KINDS`
取值：`alias_confirm`、`alias_reject`、`alias_pair`、`correspondence_confirm`、`correspondence_revoke`、`watch_add`、`watch_pause`、`ambiguity_override`、`alert_irrelevant`

#### 枚举 `AMBIGUITY_VERDICTS`
取值：`clear`、`ambiguous`

---

## 13. /sync 的 obs 键与状态码（D10、TR-25）

`GET /api/pick/sync` 加 `obs` 键：正常时是 `ObsSyncStatus`；gateway 读不到时是 `ObsSyncError`，只写异常类名，与 mirror 键同一做法。前端在 `sync-schema.ts` 里声明为 `.nullable().optional().catch(undefined)`，键缺失、为 null 或形状不对都不影响其余字段。横幅级别由 TR-10 的 `status_rules` 计算，Python 与 TS 共用 `obs_status_cases.json`。

#### `ObsBanner`

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | `STATUS_CODES` 之一 | 状态码 |
| `level` | `BANNER_LEVELS` 之一 | 横幅级别 |

#### `ObsChannelStatus`

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | `CHANNELS` 之一 | 通道 |
| `live_set_id` | 集合 id 或 null | 当前生效的 live 集合 |
| `live_published_at` | 时间戳或 null | 它的发布时刻 |
| `latest_set_id` | 集合 id 或 null | 最新发布的集合，不论模式 |
| `latest_published_at` | 时间戳或 null | 它的发布时刻 |
| `latest_mode` | `MODES` 之一或 null | 它的模式 |
| `last_run_at` | 时间戳或 null | 最近一次运行的开始时刻 |
| `banners` | `ObsBanner` 列表 | 该通道当前的横幅 |

#### `ObsSyncStatus`

| 字段 | 类型 | 说明 |
|---|---|---|
| `checked_at` | 时间戳 | 计算横幅所用的 now |
| `channels` | `ObsChannelStatus` 列表 | 依次是 trends、gsc，两个都在 |

#### `ObsSyncError`

| 字段 | 类型 | 说明 |
|---|---|---|
| `error` | 类名 | `type(exc).__name__`，不含任何文本内容 |

#### 枚举 `STATUS_CODES`
取值：`stale_26h`、`not_published_low_coverage`、`extinguished_today`、`disabled_7d`、`canary_terminated`、`usertype_changed`、`all_zero_jump`、`legacy_unmapped_2pct`、`legacy_snapshot_missing`、`gsc_gap_exceeded`、`gsc_unverifiable`、`run_missed`、`shadow_mode`、`db_size_cap`、`parse_error`

前十四个是 TR-10 的清单（含 `legacy_snapshot_missing`）。`run_missed` 指 02:30 UTC 仍没有当天的 Trends 批次，或 GSC 超过 4 小时没有新轮次。`parse_error` 是 TR-14 每周线上合同检查的告警码，解析失败只写这个码，不写值。

#### 枚举 `BANNER_LEVELS`
取值：`red`、`warn`、`info`

---

## 14. 资格真值表（D31、设计 4.7、第 9 节 #4）

`agent_eligibility(row, conditions)`（TR-10）决定一条判定行能不能作为智能体的依据，返回全部适用的排除原因，按 `EXCLUSION_REASONS` 的顺序；没有原因才算合格。只对 state 为 rising、emerging 的 Trends 行与 GSC 行提问；状态、geo、国家是否匹配由筛选另行处理。用例见 `obs_eligibility_cases.json`。

- **Trends**：confirmed，或 `trend_include_first` 时放宽到 first（否则 `trend_first_only`）；emerging 只在 `trend_state=emerging` 时进入（否则 `emerging_not_requested`）；ambiguity 为 clear（否则 `title_ambiguous`）；不是 shared_title；对应已人工确认，或身份证据为强且 `trend_include_presumed`（强证据未确认且没放宽记 `correspondence_presumed`，中、弱证据未确认记 `correspondence_unconfirmed`）；不是 stale、carried_over；A 档（B 档记 `b_tier`）；不是 unstable；对照可用（否则 `control_unavailable`）。
- **GSC**：两层准入为 formal（否则 `gsc_descriptive_only`）；没有 migration_suspect、mapping_changed。**不要求对应确认**，页面归属由 URL 决定。
- **`set_batch_mismatch`**：集合与候选集的剧库批次不同且按冻结别名版本映射不到（TR-26），不由这个函数给出。

---

## 15. 夹具

#### 夹具清单 `obs_contract/`

- `conditions.json`：七个观测条件字段（`ObsConditionFields`）。TR-16、TR-27。
- `unmappable_cases.json`：`unmappable_conditions` 的顺序与资料页文字。TR-16、TR-27。
- `observations.json`：结果级 observations。TR-16、TR-26。
- `obs_as_of.json`：`obs_as_of_json` 与它投影出的 observations。TR-26。
- `evidence.json`：三种 obs 证据的拍平。TR-16、TR-27。
- `result_obs.json`：一份完整的带观测条件的结果；TR-16 由它生成 `backend-result-obs.json`，TR-27 再用真实同步重新生成。
- `frozen_inputs.json`：两种冻结输入。TR-20、TR-21、TR-23b。
- `state_rows.json`：两个通道的判定行。TR-17、TR-20、TR-23a、TR-24。
- `vcheck_rows.json`：V 状态行。TR-23a。
- `totals_rows.json`：总量行。TR-21。
- `link_rows.json`：联动事实行。TR-20、TR-24。
- `alert_rows.json`：提示行。TR-10、TR-20、TR-23b。
- `set_summaries.json`：集合摘要。TR-20、TR-23b、TR-24。
- `views.json`：八个视图各一行正例与若干反例。TR-11、TR-24。
- `decisions.json`：九种决定请求体。TR-25、TR-35。
- `sync_obs.json`：/sync 的 obs 键。TR-25。
- `status_codes.json`：状态码与横幅级别。TR-10、TR-24、TR-25。
- `obs_eligibility_cases.json`：资格真值表。TR-10、TR-27。
- `obs_link_cases.json`：联动事实与可行动性。TR-10（Python）、TR-24（TS 的 `obs-link.ts`，只用可行动性部分）。

状态码到横幅级别的用例文件（obs_status_cases.json）归 TR-10，不在本目录。

---

## 16. 计划未写死、由本合同推导的地方

1. **observations 与 obs_as_of_json 的关系**：D28 把 `observations` 放在 `obs_as_of_json` 里，TR-33 又要求 observations 带集合 id 与截至时刻。本合同让内层只存覆盖与排除计数，`result_view` 按固定规则投影（第 3 节），这样同一信息不存两份。
2. **联动标签挂在哪条证据上**：D8 只给三种 kind，联动写成 obs_trends（没有 Trends 行时 obs_gsc）note 里的一段（第 4 节）。
3. **link-rules-v1 的细则**：设计 6.1 没写的「低量」「曝光低」、被排除行的处理、降级时的 24 小时行、引用哪一行、锚点取法、different_markets 何时出现，都按第 8 节写死；判定时刻的时效在边界上取「不超过」。
4. **GSC 判定行的粒度**：身份 × 国家 × 窗口一行，state 取主标签，labels 列全部命中；`present` 指有过两层准入的观测、不要求标签。
5. **Trends 状态词表**：ambiguous 表示「歧义，不判定」；shared_title、unstable、control_unavailable 是标记，不是状态；沿用与陈旧是两个布尔列。
6. **国家码大小写**：判定行、V 状态行、联动行一律大写，`ALL` 表示全站合计。
7. **决定的九种**：别名确认、拒绝、手动配对，对应确认、撤销，人工加入（带撤回）、暂停（带恢复），歧义改判，提示标无关。每种都带 `request_id`。
8. **集合摘要的形状**：计划只写了视图列名与类型，本合同另把 `summary` 的内部形状定下来（第 10 节），让 TR-20、TR-23b 写的与 TR-24 读的是同一份。
9. **状态码**：在 TR-10 的十四个之外加了 TR-14 提到的 `parse_error`。
10. **alias_queue 视图没有 mode 列**：别名与发布模式无关。
