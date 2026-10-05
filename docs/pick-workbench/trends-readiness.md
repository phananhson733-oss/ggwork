# RD-07 趋势雷达只读就绪审计

观察时间：2026-10-05 13:22–13:29 UTC（北京时间 21:22–21:29）。代码基线：`5c39bdf2266f57a9898dbd4e2c09c1c7d6fdadaf`。目标：业务仓库 `phananhson733-oss/ggwork`，Railway `ggwork-deerflow / production`。

**结论：`BLOCKED`。** Trends 已部署 canary collector，但两晚都因限流熄火，随后三天明确拒跑 `canary_terminated`，不满足原计划阶段 1，更不能进入 stable/live。GSC 只有部分实现，生产未部署 collector、未产生观测批次或集合。两条通道分别判断，GSC 的缺口不是从 Trends 失败推导的。

本次仅查询现有服务、变量、日志、包文件与数据库；未触发 Trends/GSC 采集、自检部署、同步、导入、重启、发布或模式变更。数据库补充查询经 `lease.status_reader` 的只读事务执行，不取租约。变量响应在内存过滤，仅输出变量名及模式/开关/版本等非机密配置；没有导出凭据、cookie、出口明细、原始标题或逐剧数据。未消耗真实模型 run。

## 当前证据

以下 E 编号对应文末的可重复读取方式。所有数量都是上述观察窗口的快照。

| 证据 | 渠道/部署 | 读取方式 | 当前指标 | 判断 |
|---|---|---|---|---|
| E1 | Railway production | `status --json` 服务及 deployment manifest | 仅 `gateway`、`pick-obs-trends` 两个服务；没有 `pick-obs-gsc` | 不把 GSC 客户端文件当作已上线 collector |
| E1 | Trends `4d1d6e5b-c62a-4859-96f4-a8b4e30818b1`，创建于 09-30 11:40:16 UTC | 当前服务与部署清单 | cron `*/30 17-23,0-1 * * *`；启动 `python -m ggwork_pick.observe.trends run`；重启 `NEVER`；无 healthcheck；下次触发 10-05 17:00 UTC | cron 配置存在，不能据 SUCCESS 推断采集成功 |
| E2 | 同一 Trends 服务 | 变量白名单读取 | `canary1 / b_only / H`；PACE 未设，代码默认 `user`；expected collector `obs-collector-v1`，expected role `pick_observer`；PUBLISH 未设 | 仍是 canary/shadow；未启用 live |
| E3 | Trends collector | 最新三条既有 `selfcheck ok` 日志，最后一条事件为 10-05 01:30:24 UTC | collector `obs-collector-v1`、head `0007`、role `pick_observer`；包摘要见下表 | 最近自然触发的镜像确已读到对应版本/头/角色；不是本次新执行自检 |
| E4 | 通过当前 gateway 读取同一观测库 | `trends --help` 后执行 `trends status` | 2 个请求预算日、5 个批次；10-03 至 10-05 均 `canary_terminated` | 明确终止，不能标 `CANARY_PENDING` |
| E5 | 数据库只读聚合 | `status_reader` + 显式 SELECT | migration `0007`；Trends 批次 5，均 collector v1；GSC 批次 0；所有模式的观测集合 0 | 当前无可发布/供智能体使用的观测集合 |
| E5 | 来源/身份 | 计划概览与计数聚合 | 两个采集夜晚均冻结共享 catalog；各匹配正对照 11/16、负对照 2/4、地区对照 9/12；缺对照 10；watch、identity_alias、decisions 均 0 | 仅证明 canary 负载对照匹配，不能称业务身份对应已确认 |
| E5 | freshness | raw 聚合 | 最新 `ok` 原始行时间为 10-01 23:14:13 UTC；无 published set | 没有当前有效集合的 freshness/新鲜率验收；不将缺失计为 0% 或成功 |
| E6 | gateway `87a09df4-f27c-4a6c-b481-730b7c973f54`，创建于 10-05 09:47:53 UTC | 镜像内包摘要与文件存在性 | 当前包与基线源码、托管副本摘要相同；`PICK_OBS_AGENT`、`PICK_OBS_PUBLISH` 均未设；GSC `__main__.py` 不存在 | 智能体观测开关未启用；GSC runner 缺失 |
| E5 | GSC 独立数据库证据 | 批次/集合/切片聚合 | GSC 批次 0，GSC 集合 0，GSC slices 0 | 无已运行或通过影子验证的证据 |

### 包与部署对应

| 层 | SHA / 摘要 | 证据范围 |
|---|---|---|
| Trends 已部署包 | `sha256:e2c4a3e3e92f181f9e0f9085302c3fc5def761ba8743f90c436b651c1cec619a` | 当前 collector 自然运行日志；与 Git 提交 `9667ea36e18c9397604a0acb2a2c94ef4fe0d658` 的源码及托管副本独立重算一致 |
| Trends 镜像 | `sha256:9a3c91d1a5de7093060502a8eac2f2f3a7f3d0df5ee1ea6d7c2660e6b92cc346` | Railway 当前部署清单；镜像摘要与包摘要属于不同对象 |
| gateway 已安装包 | `sha256:0b903b81fa5c0c151fc2da17c19b5447111349f5d2edde65bcbed0f7e9310dc4` | 当前容器现场计算；等于审计基线源码及托管副本；只证明业务包相同，不声称完整镜像 SHA 就是审计基线 |
| 对照清单 | `sha256:c2689da2cf597699ca828596b3f238ae5a0ab5789b4a3ada9d64561539f96f64` | 当前 gateway 包现场计算，与 09-30 冻结记录相同；Trends 整包摘要匹配冻结提交 |

## 已提交的 canary 结果

日期为 `target_date`，不是请求所在 UTC 日。覆盖率为 `fetched_units / planned_units`，不是 HTTP 成功率，也不是阶段 2 的 freshness。

| target_date | 模式 | 请求 / 上限 | 单元覆盖 | HTTP 429 | 熔断 | 结果 |
|---|---|---:|---:|---:|---:|---|
| 2026-10-01 | canary1 / shadow | 105 / 220 | 46 / 92 = 50.00% | 3 | 3 | `withheld`；`extinguished_today`；熄火原因 `trips` |
| 2026-10-02 | canary1 / shadow | 68 / 220 | 29 / 92 = 31.52% | 3 | 3 | `withheld`；`extinguished_today`、`canary_terminated`；原因 `trips` |
| 2026-10-03 | canary1 / shadow | 0 | null（未采集） | 无当日预算行 | 批次为 0 | `failed / canary_terminated` |
| 2026-10-04 | canary1 / shadow | 0 | null（未采集） | 无当日预算行 | 批次为 0 | `failed / canary_terminated` |
| 2026-10-05 | canary1 / shadow | 0 | null（未采集） | 无当日预算行 | 批次为 0 | `failed / canary_terminated` |

两个请求日均通过负载准入：planned requests 204 ≥176（220 的 80%）；positive 11 ≥8；reasons 为空；`late_admission=false`；节奏 `user`（桶 4、每分钟补 2）。首个限流分别发生于第 54、24 个已计数请求后。负载准入通过不抵消限流失败。

数据库请求行总计 173：`ok` 121、`no_data` 46、`rate_limited` 6（全部 HTTP 429）。没有已提交的 captcha/consent 分类；这是已存请求记录的事实，不是未来保证，也不能豁免限流。10-01 的 HTTP 200 为 102/105（97.14%），10-02 为 65/68（95.59%）；HTTP 200 内含 `no_data`，不得直接称阶段 2 的可用采集成功率。原始行按 fetch_status 另有 `ok=40 / no_data=46 / rate_limited=7`，行单位不同，不与请求数混算。

运行时 `disabled_at=null`、`paused_until=null`，不表示正常运行：`canary_terminated` 是两天熄火后的独立终止规则。运行时最近更新为 10-05 01:30:25 UTC，租约已过期；本次查询未修改状态。

## 原计划门槛与现状

依据 [设计 4.11](../plans/2026-09-25-trends-radar-design.md) 与 [实现计划第 9–10 节](../plans/2026-09-25-trends-radar-impl-plan.md)，本报告不降低门槛。

| 门 | 原门槛 | 本次判定 |
|---|---|---|
| canary 有效负载 | 计划请求至少 80%；正对照匹配至少一半；非 late-admission | 两晚满足；随后三天没有请求，不能计为通过夜晚 |
| 阶段 1 | 3 个有效日，没有任何限流信号 | **FAIL**：仅两晚实际请求，两晚均 HTTP 429 和熄火 |
| 终止规则 | 首次验证码/同意页立即终止；其他熄火累计 2 天终止 | 已实际触发两天熄火终止，后续批次拒跑 |
| 阶段 2 | 7 天；成功率 ≥98%、熔断 ≤1 次、验证码/熄火 0、新鲜率 ≥95% | **NOT_RUN**：仍 canary1，不存在合格阶段 2 样本；不拿阶段 1 聚合冒充 |
| S10 stable/shadow | canary 通过且 TR-17/18/20 已部署；影子运行 2 周 | **BLOCKED**：canary 失败；基线 `source_for()` 明确拒绝 stable，WatchTaskSource 尚未接入 |
| S12b Trends live | 至少一道阶段 0 闸门、阶段 2、影子抽检、TR-36、G5 | **NOT_RUN**：没有 live 集合或相应验收证据 |
| S9/S12a GSC | TR-21–23b runner/发布链部署；影子抽检、TR-36、G5 | **BLOCKED**：服务与 runner 入口缺失，批次/切片/集合均无；客户端与规则模块不能替代这些证据 |
| S13 智能体开关 | G6 通过后单独开启 | **IMPLEMENTED_NOT_ENABLED 仅适用于已有开关合同**：实际开关未设；完整观测业务链不能因此称已实现 |

09-30 的 progress 记录引用了“简化版范围”中只验证出口的三夜目标。本次遵守 RD-07 要求，仍逐项按原计划门槛审计；即使只看简化目标，目前也只有两个实际请求夜晚且已因两天熄火终止，不能声称已完成三夜验证。

## 缺口与下一道门的范围

1. Trends 当前实际阻塞是已有限流失败及终止状态，连接和读权限可用。重新验证前需要另行确定失败处置与重新开始条件；本次没有清除终止状态、重设日期、改节奏、出口、预算、模式、cron 或词表。
2. 原计划阶段 1/2、生产级任务来源、集合发布、两周影子抽检、身份确认与 TR-36/G5/G6 证据仍需按各自门槛补齐。正对照 identity 匹配不等于短剧搜索实体确认，市场对照有效性也不能由匹配数量替代。
3. GSC 需要单独交付并部署 runner/快照/发布链，提供真实影子批次与抽检。未在无关机器寻找或申请 Google 凭据；本次未访问 Google。已有 RealShort 镜像信号不等同本观测通道完成。
4. 没有 published set，当前 fresh/live/source matching 的业务验收无法完成；历史空白保持原样。本文审计交付完成不代表趋势产品 ready，也不阻塞 RD-01～06 的代码交付。

## 读取方式与验证边界

CLI 首先执行 `railway --help`，并读取 status/list/ssh/variable/deployment/logs 的帮助。SSH 使用项目 UUID（当前 CLI 用项目名 SSH 报 project not found，换 UUID 后成功）；没有执行 `railway link` 或改本地项目关联。

- **E1**：`railway status -p ggwork-deerflow -e production --json`；只读服务、最新 deployment、manifest 与下次 cron。服务清单同时由 `railway list --json` 核对。
- **E2**：`railway variable list -p ggwork-deerflow -e production -s pick-obs-trends --json`，响应直接送入过滤器，仅保留名称与文中安全配置；不保存原始响应。敏感变量只确认名称存在：`PICK_DATABASE_URL`、`PICK_OBS_STATE_KEY`。
- **E3**：`railway logs -p 449df38f-9bce-40ad-90c8-8feac355881d -e production -s pick-obs-trends --lines 3 --filter 'selfcheck ok' --json`。只取既有自检行，不读取任意 HTTP 正文或原始采集词。
- **E4**：`railway ssh -p 449df38f-9bce-40ad-90c8-8feac355881d -e production -s gateway -- /bin/sh -c 'cd /app/backend && python -m ggwork_pick.observe.trends --help'`，确认现有命令后将最后 `--help` 换成 `status`。输出中过滤 `missing_first`、lease owner、reset operator，报告只留日期和聚合。
- **E5**：gateway 现有 Python 环境中 `async with status_reader('trends') as step` 执行 SELECT：迁移头；请求按 budget_day/fetch_status/status_code 聚合；批次按 channel/collector_version 聚合；集合按 channel/mode 聚合；raw 按 fetch_status 聚合；watch/alias/decisions/GSC slices 的 count。未读取 params/data JSON、标题或 cookie，未执行 DML/DDL。
- **E6**：gateway 容器调用既有 `package_digest()`；读取对照文件字节计算 SHA-256；读取两个开关的是否设置与 GSC 入口文件是否存在。源码侧按 `selfcheck.package_digest` 的相同路径/长度/字节算法，以 `git ls-tree`、`git show` 重算两个 SHA 的源码及托管副本，不 checkout、不导入或运行旧采集代码。

静态依据还包括 [观测合同](observe-contract.md)、[打包](observe-runbook/packaging.md)、[会话](observe-runbook/trends-session.md)、[GSC 客户端](observe-runbook/gsc-client.md)、[租约与自检](observe-runbook/lease-and-selfcheck.md)、[回滚矩阵](observe-runbook/rollback-matrix.md)，以及 `observe/{cron,cron_status,lease,selfcheck}.py` 和 `trends/__main__.py`。

本项只有文档改动，验证为实时只读查询、摘要一致性核对和 `git diff --check`；没有为文档重跑产品测试或触发任何真实业务 QA。
