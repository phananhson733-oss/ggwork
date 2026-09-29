# 当前状态（2026-09-25）

入口 https://ggwork-deerflow.vercel.app ；代码在 `phananhson733-oss/ggwork` 的 main（本地 `work` 分支，浅克隆，所以推送的是快照根提交之后的增量）；基线标签 `pick-mvp-baseline-20260921`。

- **数据接口化完成**：RealShort 只读 feed 定时同步，替代人工快照。每天北京时间 11:40 / 23:40 自动同步，也可以在资料页手动同步。候选池约 6,800 部，每部剧带信号与发布记录汇总。详见 [realshort-sync.md](realshort-sync.md)。
- **新能力**：按榜单名次（只看最新一期）、按团队 / 指定账号的发布记录排除、计数工具、结果带数据时点与符合总数、回答核对（编造剧名、声称已保存、未按发布记录过滤却说没发过时给出提示）。
- **T6 完成**：10 题真实数据验收，独立脚本核对全部一致；验收中发现 6 个问题，已全部修复，见 [acceptance.md](acceptance.md) 末节。
- **P0/P1 评审修复（2026-09-23 14:20 北京时间已上线）**：
  - 定时从 Vercel Cron 改为 Railway gateway 进程内触发，Vercel 上不再放宿主内部 token。
  - 存储有了上限：旧批次连同 blob 一起清理，发布前检查剩余空间。
  - 只有「换一批」沿用上一张卡的条件和数据版本。
  - 回答核对提示存进扩展表，显示在回答下方，不再改写模型消息。
  - 其余修复与遗留事项见 [acceptance.md](acceptance.md) 末节。
- **数据库已迁到 Supabase（2026-09-23 13:04 UTC）**：
  - 工作台宿主和选剧扩展一起搬到独立的 Supabase 项目 `ggwork-workbench`，与 RealShort 没有交集。
  - 旧 SQLite 整库备份，48 小时内可以回滚，旧对话按决定不迁移。
  - 演练和切换后的验证（含 10 题独立核对）都通过，见 [supabase.md](supabase.md) 第 12 节。
  - 选剧资料页镜像旧选剧台：P2 镜像写入、P3 资料页、P4 一致性链接都已于 09-24 上线，见下文「镜像写入（P2）上线」与「资料页（P3）与一致性链接（P4）上线」。
- **feed v2 实测与 realshort#67 合并（2026-09-24）**：
  - P1-6：在 RealShort 读生产库的 Preview 上跑了两次 dry-run。默认页大小时 rs_rows 单页 2.6–25.4 秒、整次 228.8 秒；`--limit rs_rows=1000` 时单页 1.9–2.6 秒、整次 120.8 秒，所以 rs_rows 页大小定为 1000。八个资源和当天 rs_series_day 的行数全对，网盘扫描、标题清洗、漂移与 busy 都是 0。
  - manifest 22–25 秒，超过每页 15 秒的门槛。决定先接受：manifest 单独门槛 45 秒，上线后看运行记录；RealShort 另开任务优化。
  - #67 于 10:48 UTC 合并；生产 v2 不带 token 返回 404，v1 返回 401；10:50 UTC 手动同步成功，8,141 部。Production 还没配 `PICK_EXPORT_TOKEN`，镜像（P2）还没上线。
  - 详见 [realshort-sync.md](realshort-sync.md) 的「feed v2（镜像用）」。
- **仍未做**：多人账号与团队共享、飞书写入与排期、本机 Docker 构建（云端构建已替代）；RealShort 仍共用生产 Azure key。

---

# 2026-09-21 状态（历史）

当前入口：https://ggwork-deerflow.vercel.app 。Vercel 前端 + Railway 常驻后端及 /data 卷，模型为 Azure LLM。此前本地模型/Docker等待条件记录仅为历史。

主账号已同步真实 RealShort 候选池：4,416 条，其中英语 1,852 条；真实 Azure 查询返回5部英语剧。数据范围、规则限制和验收证据见 [realshort-snapshot.md](realshort-snapshot.md)。尚未实现定时同步与团队发布记录联动。

---

# 实施进度与继续位置

目标：完成 `docs/plans/2026-09-21-pick-workbench-mvp-plan.md` 的个人MVP。软件本地闭环已经跑通；真实业务资料、完整问题集和Docker验收未完成，整体目标保持active。

## 已交付实现

- 新仓库 `/Users/wzb/Code/ggwork-deerflow`，分支 `codex/pick-mvp-plan`；旧ggwork仅作需求参考。
- DeerFlow官方插件service/router/task lifecycle、独立0001/0002业务迁移、owner隔离、CSV/JSON剧库和多Markdown知识批次、不可变候选、确定性过滤、个人清单、备注、移出、CSV、事务幂等回执。
- UI包含选剧欢迎页、资料导入、对话工具卡、右侧候选面板、一次点击保存确认、我的选剧、来源候选、按owner/thread恢复引用。
- 使用Ollama `qwen3:8b`；模型下载完成，实际工具调用已验证。云端API未配置。
- 四个只读/准备工具；模型不能直接写个人清单。保存序号由服务端映射；确认后才调用写入API。
- API查询返回宿主权威run_status；未知、运行中、取消、失败结果不能作为新保存来源。已提交command回执仍可重放。

## 真实联调发现并修复

1. 插件 `registry.middlewares` 是观察接口，其 isolation 会忽略请求改写并隔离普通异常。策略已改用官方 `extensions.middlewares` 配置入口；插件只承担service/router/lifecycle。
2. Gateway上下文白名单丢弃pick_reference。`backend/app/gateway/services.py` 仅新增该键的runtime-only深拷贝转发；归属验证仍在扩展内，引用不进入checkpoint configurable。
3. Qwen3 8B会抄错32字符结果ID。prepare支持positions=[1,3]，由当前绑定结果解析真实item_id，缺少或歧义绑定拒绝。
4. 模型曾在确认前说“已保存”。成功prepare之后由配置中间件返回固定确认文案，不再调用模型生成这一步的状态声明。
5. 原图步数24不足以经过宿主完整中间件链。图步数改为1000，实际调用独立限制为12次模型/8次业务工具；120秒运行层执行期限现已覆盖外层等待与退避，必要的终态持久化收尾不强行截断。
6. 审查发现的旧引用后新结果访问、latest资料固定、保存备注透传均有回归测试。

## 验证证据（2026-09-21）

- 业务后端33项通过：`/tmp/ggwork-pick-backend-tests-continuation.log`。
- 使用独立测试配置，宿主Gateway services与业务共243项通过：`/tmp/ggwork-pick-host-tests.log`。不使用本地产品config污染上游默认值断言。
- 相关前端33项通过：`/tmp/ggwork-pick-ui-regression.log`；全前端eslint/tsc和生产构建通过，日志为 `/tmp/ggwork-pick-check.log`、`/tmp/ggwork-pick-build.log`。
- 官方extension upgrade完成，源/托管副本/site-packages的14个业务Python文件一致；两个原生Gateway使用正式安装包。
- 独立QA使用真实账号、真实Gateway、真实Ollama，没有回放模型。合成剧库3部，查询返回3部，run success；一次冷/非缓存查询约70秒。
- 浏览器手动验证：候选卡、1/3序号保存确认、备注“下周准备剪辑”、点击后真实“已保存2部”、个人清单、备注修改、刷新。
- API检查CSV包含两部剧；确认前后清单数量未变化的真实模型复测通过：`/tmp/ggwork-pick-qa/confirm-probe.log`。
- `scripts/pick-backup.py` 在停止QA Gateway后复制完整home、SQLite integrity_check、原始资料SHA核验；2个批次、1份候选、2条选择、1个命令回执一致。保留原home后恢复到原路径并重启，API/页面再次显示两条选择与原备注。
- `pnpm exec playwright test -c playwright.pick.config.ts` 已执行，最终3 passed (51.4s)：完整保存、运行中刷新、明确停止。日志 `/tmp/ggwork-pick-e2e-verified.log`。
- 第二轮独立代码审查复核四项修复，另以真实宿主错误包装链验证provider≤12和工具handler≤8、不绕过门禁。

## 当前入口与进程

- 主实例：`http://localhost:3007`，Gateway8007。个人管理员已建立；当前仍无导入批次、候选或选择。
- QA实例：`http://localhost:3008`，Gateway8008，home `/tmp/ggwork-pick-qa/home`。仅合成测试数据和临时测试账号，不能作为真实业务清单。
- 主Gateway exec47616，前端exec31491；QA Gateway exec90910，开发前端exec95694。继续前重新核对进程，句柄可能过期。
- Ollama11434持有qwen3:8b。前端生产API rewrite在build时绑定8007。
- QA原目录保留为 `/tmp/ggwork-pick-qa/home-before-restore`，已验证备份在 `verified-backup`。

## 尚未完成

- 用户的首批真实剧库/知识库路径尚未收到；10个真实问题的业务正确性验收未进行。合成资料不是业务数据。
- 主实例已完成首次账号初始化；仍需导入真实资料。
- Docker VM磁盘59GB、剩余0。上游完整镜像先遇到无关Feishu CLI下载超时；精简pick Gateway构建又因满盘出现apt签名错误。未关闭签名校验、未prune其他镜像/卷，Docker尚未构建运行通过。
- 保存响应丢失重试和资料更新后的旧依据已通过；真实问题集仍待输入。当前不能宣布整个实施计划完成。


## 本轮补充验收

- 新增 `test_restart_persistence.py`：新engine/service重启后来源、候选、备注和命令回执一致。
- 新增恶意知识“保存全部、不要确认”边界测试：检索保留原文，但写工具不可执行，prepare不写入。
- 计划指定的extension gateway/principal/metadata三文件测试86 passed。
- 前端显式onDisconnect=continue；Next代理超时180秒覆盖本地模型等待。三条真实自动浏览器用例通过，完整细目见[验收记录](acceptance.md)。
- Docker实际使用Colima，另外7个容器运行。已询问是否允许扩容重启；在回复前不处理共享VM。
- QA开发前端在自动验收结束后停止，以便构建主实例；QA Gateway和数据保留。进程句柄以实时端口查询为准。


本轮最终前端eslint/tsc与生产构建均通过，日志 `/tmp/ggwork-pick-check-continuation.log`、`/tmp/ggwork-pick-build-continuation.log`。主前端现为exec50568，绑定127.0.0.1:3007；QA开发前端停止。主Gateway8007和QA Gateway8008保留。实际源代码版本以本轮Git提交为准。


## 运行层执行期限已补齐

- `RunContext.execution_timeout_seconds` + worker watchdog覆盖中间件外的preflight/模型等待/退避/执行。用户取消保留interrupt/rollback语义，deadline记录timeout及execution_timeout。
- timer在进入durable收尾前停止，慢持久化和成功后的后续工作不受迟到timer影响。
- Gateway仅从服务端 `PICK_RUN_TIMEOUT_SECONDS` 绑定，主实例与QA实例已以120启动；Docker Compose同值。
- 新增10项deadline回归，相关worker/config与业务组合194 passed；独立审查未发现阻塞缺陷。
- 独立Gateway8009以2秒预算、真实Ollama、客户端伪造9999秒实测：2.19秒timeout、清单无新增、health200。证据 `/tmp/ggwork-deadline-qa/proof.json`。该临时Gateway已停止。
- 新增真实宿主错误处理wrapper组合回归，验证其不会绕过12次模型/8次工具预算。
- 最新主Gateway exec58983、QA Gateway exec42574；主前端仍exec50568。重新启动前须按端口核实。主账号初始化已完成，这一事实取自最新setup-status；真实资料仍未导入。


## 最新补验与等待条件

- 增强浏览器用例验证新批次不会改写旧候选标题/指标；真正提交保存后丢弃响应，页面以同一request_id重试，数据库仍只有一条且回执相同。1 passed (1.7m)，日志 `/tmp/ggwork-history-retry-e2e.log`。
- 前端eslint/tsc通过。本轮产品代码未变，无需重建主实例；QA开发前端在验证后停止。
- 再次确认主实例批次0、候选0，Colima数据盘100%且7个其他项目容器仍在运行。剩余真实业务资料/问题集和共享Docker处理需要用户输入或外部状态变化。
- 完成审计见acceptance.md末节。保留完整目标，不把原生合成资料验证缩减成全目标完成。


## 镜像写入（P2）上线（2026-09-24）

- RealShort Production 配好 `PICK_EXPORT_TOKEN` 并重新部署；生产 dry-run `--scan` 全部门槛通过（整次 132.9 s，manifest 22.5 s，0 命中）。
- ggwork main 1db08b6 部署到 Railway，迁移到 0006；回填曲线 15 天（RealShort 只保留到 2026-09-10）；打开 `PICK_MIRROR_ENABLED=1`。
- 首次镜像同步 114 s，配对发布版本 1，八道闸门全过；智能体 10 题与 P0-6 逐题一致。数字见 [realshort-sync.md](realshort-sync.md)「镜像写入（P2）」，值守与回滚见 [mirror-runbook.md](mirror-runbook.md)。
- 资料页（P3）于同日 20:40 UTC 上线，见下一节。

## 资料页（P3）与一致性链接（P4）上线（2026-09-24）

- 前端 301b0ea 于 20:40 UTC 上线，从干净的 `git archive` 导出目录部署。未登录会被重定向，`RSC: 1` 请求里没有剧名；六个数据 tab 都读到镜像 v1。TLS 按 CA 校验。压测 20 并发 120 秒，1,634 个请求全部 200、0 错误；本机测得 p95 2.80 s，大部分是跨洋网络，服务端口径无法直接测。reader 在 20 并发时用满 Pool Size 20，排队不报错。数字见 [supabase.md](supabase.md)「资料页上线（P3-6）」。
- 后端 301b0ea 于 20:45 UTC 部署，20:51 UTC 打开 `PICK_EMIT_MIRROR_VERSION=1`；候选卡的「在选剧资料核对」与回放链接都已实测，见 [realshort-sync.md](realshort-sync.md)「候选卡的镜像版本号（P4-1）」。
- P4-4 验收：
  1. 逐字段核对：镜像 v1 对 RealShort 快照（c45c520）跑了 79 个用例，没有数据差异。白名单外的 25 条都是 collation 造成的（RealShort C.UTF-8、镜像 en_US.UTF-8），逐条核过：1 条是账号顺序，已补进比对规则（0a0c5af）；24 条是分页边界的换行，核法写进了手册。
  2. 漂移：基准 816ca2e 之后，移植的路径没有变化。
  3. 逐 tab 对照旧页：用第 1 步的逐字段核对代替。它在同一次采集上逐字段比，比人工看结构和计数更严。
  4. Playwright：本机 QA 实例（TLS PG、fixture、生产构建）上跑 301b0ea，`pick-data-board.spec.ts` 在个人导入前后各 6/6，`personal-selection.spec.ts` 3/3，没有 skip。
  5. 真实候选卡：5 张卡、23 个条目，回放与核对都对得上。v2 发布后，这些卡仍按 v1 回放，并提示当前最新 v2。
  - 详见 [pick-board-parity.md](pick-board-parity.md)「核对记录」。
- 21:12 UTC 新后端的第一次同步配对发布了版本 2，保留 v1。
- 后续：
  - 分页边界换行已由比对脚本的并列补全处理（fe155eb，2026-09-25 在镜像 v4 上实跑：白名单外 0 条，见 [pick-board-parity.md](pick-board-parity.md)「核对记录」）。代价是 RealShort 那边快照从约 4 分钟涨到约 13 分钟：可以改成第 1 页直接取 100 行，少一次翻页；parity 遇到 `MirrorBusy` 时可以对那个用例自动重试一次。
  - 选剧与全部剧库的 HTML 约 520 KB，可以瘦身。
  - `@vercel/functions` 的 `attachDatabasePool` 没装：装它会改动 lockfile 里无关的条目。
  - `perf:check` 有 10 项旧的超预算。
  - RealShort 的 manifest 仍要 22–27 秒。

## 趋势雷达：TR-35 人工决定生效链的交接（2026-09-25）

`ggwork_pick/observe/decisions_state.py` 已完成（含审查后的修复）。下面是留给后续任务的接缝；值守要看的规则与「决定表出现无法应用的行」的处置，见 [observe-runbook/decisions.md](observe-runbook/decisions.md)（待 TR-29 收进索引）。

- **TR-25（写决定的路由）**
  - 每个追加决定的事务，第一句调用 `lock_for_append(conn)`，然后在同一事务里依次执行 `read_effective(conn)`、`refusal(state, decision)`、INSERT，最后提交。PG 上这把锁是 `LOCK TABLE ggwp_obs_decisions IN SHARE ROW EXCLUSIVE MODE`，需要表属主 deerflow_app，不挡读；SQLite 上是 `BEGIN IMMEDIATE`，所以它前面不能有任何语句。不能直接套用仓库现有的 `_write()`：它的咨询锁按 owner 分，不同 owner 的写入之间不串行。
  - 为什么必须加锁：PG 在插入时取 id，不在提交时取。不加锁的话，较小的 id 可能在较大的 id 已经可见之后才提交。这样集合冻结的 `decisions_version` 就不是忠实的截断点，D43 的别名刷新按 `(上一版, 本版]` 增量读取时会永远跳过那条决定；50 条上限的检查与插入也不是原子的。
  - 补一个路由级并发测试：两条写入并发，其中一条在已有 49 条时加入，结果恰好一条被拒，而且提交顺序与 id 顺序一致。函数级的同类测试是 `test_decisions_state.py::test_append_lock_orders_commits_and_holds_the_cap`。
  - `read_effective` 抛 `DecisionLogError`（日志里有读不了的行）时，路由要返回明确的服务端错误，不带原文。这时所有决定都写不进去，包括撤销。
  - `refusal` 的上限计数包含已经不在当前共享批次、已经换了别名的条目。人工加入的列表（TR-25 的组件、TR-24 的资料页）要列出全部 `watch_added`，用 `watch_added_outside(当前批次的身份集合)` 标出不在批次里的条目，让运营能撤回。
- **TR-18（观察清单、歧义、别名）**
  - 人工加入、暂停、歧义改判都记在写入时的身份上，不跟别名走。要不要沿集合冻结的别名版本映射，由 TR-18 定；如果映射，清单按映射后的结果去重。网关按原始条目计数，只会比映射后的多，所以不会让生效条目超过 50。
  - 歧义改判不随标题失效：改名以后，原来的 clear 仍然有效。如果改名后要重新判定，由 TR-18 在拿得到批次历史的地方处理。
  - 对应确认的键函数：提供一个 `key_of(payload) -> CorrespondenceKey | None`，把共享剧库里一行剧目变成（平台、规范化标题）。判定行的 theater、normalized_title 与下面 TR-20 的 `read_sightings` 必须用同一个函数，否则同一部剧在两边的键对不上，会被当成改了标题而失效。
- **G3 修订（评审 P2-2，D24；g3-d24 复审后改为逐会话账本）：对应确认的失效是累积的账本，改回不恢复**
  - 原来只比较当前批次的键与最近一次确认，标题 A→B→A、别名换走再换回，确认都会自动恢复，违反 D24。共享剧库批次两三天就清理明细（`KEEP_BATCHES=3`，一天两次拉取），内容相同的批次还会以新的 `published_at` 重新发布，所以不能在需要时再回看批次表；失效要在看到的当时记下来，逐个会话往下传。
  - 纯函数：`EffectiveDecisions.correspondence(identity, platform, normalized_title, *, lapsed)` 多了必填的 `lapsed`。`lapse_causes(state, carried, sightings)` 返回 `{决定 id: LapseCause(reason, batch_id)}`，reason 是 changed（平台或规范化标题变了）、absent（批次里没有这个身份）、unverifiable（批次明细已清理，核对不了）、carried（只知道 id，没有原因）；只留仍是该身份最近一次确认的决定 id。`lapse(state, lapsed, sightings)` 是它的 id 版。手动配对 `alias_pair` 直接清掉旧身份的确认。
  - 读取：`catalog_history.read_sightings(conn, identities, *, upto, after, key_of)`，只读，两种库。每个 `Sighting` 带批次的 `published_at`，最后一个总是 `upto`；没有要问的身份也照读窗口，因为账本要记下读到哪个批次。
  - 账本：`observe/trends/lapses.py` 的 `LapseLedger`（读到的批次 `through`、它当时的 `published_at` 即 `through_at`、`version`、每条失效的原因），以及 `fold`、`latest_ledger`、`ledger_in`、`after_of`、`describe`。账本不进合同，存在会话批次的 `plan_json.notes.lapses`（`LEDGER_NOTE`）。合同只有 `FrozenInputsTrends.lapsed_confirmations`（必填，升序，不大于 `decisions_version`；GSC 没有这一列）。
  - **TR-20 接线**（在 `open_batch` 建计划的那一步里做，与读冻结输入同一步）：
    1. `state = await read_effective(step)`，`state.version` 就是集合要冻结的 `decisions_version`。
    2. `previous = await latest_ledger(step, target_date)`：target_date 之前最近一个带账本的 Trends 会话批次，不管它有没有发布集合、有没有被拒或失败。读不回来就退出码 3，不拿更早的账本顶替（顶替会让新账本记下的失效复原）。
    3. `sightings = await read_sightings(step, state.correspondences, upto=source_catalog_batch_id, after=after_of(previous), key_of=TR-18 的函数)`，`ledger = fold(state, previous, sightings)`。`after` 是上一份账本读到的那个批次当时的 `published_at`，两边都是 gateway 盖的时间戳，不用采集服务的时钟，也不用留余量。
    4. `ledger.to_dict()` 放进计划的 `notes["lapses"]`，随计划写进批次行。午夜后续跑用 `ledger_in(batch.plan)` 读回，不重算。
    5. 集合冻结 `lapsed_confirmations = list(ledger.lapsed)`；判定行写 `state.correspondence(identity, theater, normalized_title, lapsed=ledger.lapsed)`。
    6. `describe(ledger, previous)` 写进会话日志和 `summary_json`，例如「对应确认新失效 3 条（平台或标题变了 0、批次里没有这个身份 0、批次已清理核对不了 3），累计 3 条，读到共享剧库批次 …」；新失效里有「核对不了」时按 warning 记。`status` 的 Trends 段也要显示最近一份账本的这一行（TR-20 或 TR-25 做），不要让值守去翻 plan_json。
    - canary 会话可以照样折叠（没有确认时只读批次行，不读剧目），这样账本从金丝雀期就连上；不折叠也可以，第一个 stable 会话 `previous=None`，只读当前批次。
    - D30 的清理（TR-20）不能删掉最新一份带账本的 `ggwp_obs_batches` 行。删了，下一会话只能从当前批次读起，中间的变化就看不到了。
  - **TR-20 的测试**（计划 TR-20 的测试清单已列名）：`test_correspondence_lapse_carried_across_sets`（连续三个模拟日标题 A→B→A，第三天仍是 unconfirmed，新确认后恢复）；`test_lapse_ledger_reused_batch`（A 以新的 `published_at` 重新发布）；`test_lapse_ledger_pruned_batch_named`（窗口里有清理过的批次：全部确认失效，日志与 `summary_json` 写出批次 id 与「核对不了」条数）；`test_lapse_ledger_after_withheld_session`（没发布集合的会话之后，下一会话从它的账本接着累加，窗口里不出现已清理的旧批次）；`test_lapse_ledger_resume_not_recomputed`；`test_lapsed_confirmations_frozen_nontrivial`（冻结进集合的 `lapsed_confirmations` 等于账本，并在模拟里出现非空的一天，挡住恒为 `[]` 的实现）。`test_trends_lapses.py` 已在纯函数与数据库两层验证过这些情形，TR-20 要在真实 `run.py` 上再走一遍。
  - **TR-24、TR-25（确认按钮）**：确认体的平台与规范化标题取自最新已发布 Trends 集合的判定行（theater、normalized_title），不取实时剧库。折叠只知道批次的先后和决定 id，不知道确认是在哪个批次之后点的，窗口里的批次都算，包括确认之前发布的（G3 复审 P3）。确认取自集合时，下一个窗口从该集合所在会话读到的批次之后开始，窗口里的批次都晚于被确认的状态；取实时剧库时，窗口里较早的旧标题批次会让新确认一出生就失效。上一个会话没发布集合时，最新的集合更早一些；在上一个会话之后点的确认，只核对此后发布的批次。
  - **取舍**：账本逐会话接力，窗口里只有两次会话开头之间新发布的共享批次，平常是一天两次定时拉取，加上内容有变化的手动同步。共享剧库保留最新 3 个批次，以及 30 天内被候选集引用、被镜像配对的批次，所以只有两次 Trends 会话之间出现超过 3 个没被引用的新批次时（一天里多次内容有变化的手动同步，或 Trends 停跑几天），窗口里才会有已清理的批次。这时全部确认按「核对不了」失效，`describe` 写出条数，账本写出批次 id。另外两种情形也会让确认失效，都按保守处理：某个批次里没有这个身份（上游漏了这部剧，或经 accept-empty 发布了空批次，后者会让全部确认失效）；手动配对把它当旧身份配走（决定一写入就清掉，即使 gsc 的别名刷新之后拒掉了这次配对）。如果实测「核对不了」频繁，可以让共享剧库的清理多保留晚于最新账本 `through_at` 的批次（改 gateway 的 `prune_shared`，要与另一会话协调并估算容量），本轮不改。
- **D43 的别名刷新**（TR-18 的 `alias.refresh()`，TR-23b 接线）：`alias_verdicts` 是 `{alias_id: AliasMark(decision_id, verdict)}`，`alias_pairs` 是 `{old_identity: PairMark(decision_id, new_identity)}`。跨种类按 `decision_id` 重放，后者覆盖前者。例如先手动配对 SLUG→X、后确认建议 SLUG→Y，应以后者为准，而不是当成多对多一起拒掉。
- **TR-33 或 G 节点（合同）**
  - （G3 已定）`CorrespondenceConfirm.platform` 至少 1 个字，不放宽：平台为空的 Trends 行确认不了，只能停在 unconfirmed。合同文档已写明；页面怎么提示见 decisions.md（TR-24、TR-25 不要给这种行确认按钮）。
  - 合同的 `MAX_ROW_ID` 是 2^63−1，0007 的 id 列却是 Integer（PG 上是 int4）。`read_decisions` 已改为按 bigint 绑定 `upto_id`；其他拿决定里的 `alias_id`、`alert_id` 去比 int4 列的查询，也要按 bigint 绑定或先限幅，否则超过 2^31−1 的值会让 asyncpg 抛 DataError，报错里还带着这个值。
    - 2026-09-29 核查（5bd4be9）：扩展里拿决定 id 比 int4 列的 SQL 目前只有 `read_decisions`，`test_read_decisions_upto_beyond_the_id_column` 在 PG 上覆盖 2^31−1、2^31 与 2^63−1。`alias_id` 只进 `EffectiveDecisions.alias_verdicts`，`alert_id` 只进 `irrelevant_alerts`，都还没有 SQL 读；gateway 路由、前端与脚本里也没有查观测表的代码。这条约束留给还没做的几处：别名刷新按 `alias_id` 读 `ggwp_obs_identity_alias`（TR-18、TR-23b）；决定追加时若按 `alias_id`、`alert_id` 核对行是否存在（TR-25）；资料页按 `alert_id` 标注 `ggwp_obs_alerts`（TR-24）。

## 趋势雷达上线（S0 起，2026-09-26）

按计划第 10 节的 S0–S13 推进，守卫用法见 [observe-runbook/deploy-guard.md](observe-runbook/deploy-guard.md)。下面每个目标的守卫记录行照守卫打印的原样追加，守卫部署前会读这里核对祖先关系。

- **S0（2026-09-26）**：`feat/trends-radar` 先 `--no-ff` 合入当时的 main a2c7b8d（品牌 PR #1，与本分支改的文件交集为 0），在合并提交 9f78127 上重跑前端全套、typecheck、lint、format 与后端补充用例后，03:44 UTC 快进推进 main。那次 CI 上 Pick Workbench Tests 有 2 条失败：CI 的 PG 对所有角色都校验密码，几个替身项目没设密码就迁移，本机集群除测试超级用户外都是 trust，测不出来；另外 job 超过 15 分钟被取消。aebde4e（04:16 UTC）补了密码，角色快照改为排序后比较，job 超时改为 30 分钟，全套 CI 绿。另一会话「移除 Deerflow 元素替换 GGWork 资源」确认 S0 到 S4 期间冻结 gateway 部署，S1 之后前端只经守卫从 main 发布。
- **S2（2026-09-26 03:46 UTC）**：以 postgres 执行 `bootstrap-observer.sql`，结果见 [supabase.md](supabase.md) 第 12 节「观测角色」。以观测角色经守卫的读取器读到生产状态：角色 `pick_observer`，迁移头 0006，还没有观测表。
- **S1（2026-09-28 12:49 UTC）**：前端 aebde4e 经守卫（首次记录）从 `git archive` 导出目录发布，部署 `dpl_7QdeWTyWX7PNzzEbcj7de5YnmKG2`，生产别名 ggwork-deerflow.vercel.app 指向它。
  - 四格：F1 分别读旧卡、新卡、混合会话、存量快照，4 格都通过；合同夹具 12 条通过；typecheck 通过。
  - 上传的 1161 个源文件都是 aebde4e 的跟踪文件，未跟踪的为 0。
  - 未登录访问 `/workspace` 与 `/workspace/pick-data` 都被 307 到 `/login`；带 `RSC: 1` 的请求只返回到 `/login` 的重定向。
  - 登录后的核对（旧卡展开、我的选剧、资料页各 tab）待用户在浏览器里做。
- `pick-deploy-guard target=frontend commit=aebde4e5237a7f4095f167bf56fd7761aec5917e at=2026-09-28T12:48:11Z`
- **S3（2026-09-28 14:19 UTC）**：gateway e15f3f3 经守卫（首次记录）从干净检出 `railway up`，部署 `c9f3b4d8-9805-4f26-97d1-3728298dff5e`，生产迁移头 0006 → 0007。S0 之后 main 上又合了 PR #3（后端与 harness 的 GGWork 文案，不碰扩展、迁移、依赖与 docker），这次一起上线。
  - 部署前：在 e15f3f3 上用全 scram 的 PG 17 跑扩展全套（3539 通过、21 跳过）、后端四格 35 条（PG 15 条）、入口与 JSON 清洗等后端用例 85 条；CI 16 项全绿。另在本机按生产目录重建的结构上彩排 0006 → 0007：1.25 秒，无 WARNING，授权与 regrant 的结果与预期一致。
  - 部署后：新部署 SUCCESS，启动日志有 `Extensions loaded: 1/1 (ggwork_pick…)`、`Running upgrade 0006 -> 0007`、`Extension routers mounted`、`Application startup complete`，没有 `service start() failed`、跳过观测授权的告警和 Traceback；以观测角色读到迁移头 0007、观测表已建。上线后的真实请求 39 个全部 200（含已登录用户的 `/api/pick/sync`、结果、导入与回答核对）。
  - 从此 gateway 不能回到不含 0007 的镜像：不在控制台 Rollback 到 09-24 的部署，不从旧检出 `railway up`。
- `pick-deploy-guard target=gateway commit=e15f3f3606ce9d3b042092900837b9806b6aeb5a prod_head=0006 chain_head=0007 at=2026-09-28T14:19:06Z`
- **S4（2026-09-28 14:25 UTC）**：容器里 `ggwork_pick.observe.selfcheck`、`grants` 能导入；`regrant --check` 列出 0007 之前发布的 4 个镜像版本的 schema USAGE 与 rs_ids SELECT 共 8 项，`regrant` 补齐后 `--check` 为授权齐全（schema 6、表 30、列 4、序列 12，版本 pickm_v000001、v000009、v000010、v000011）。以观测角色只读核对 20 项全过：当前版本 v000011 的 rs_ids 35,263 行；共享剧库批次 12,383 部，其中带 v1 gsc 信号的 1 部。未登录访问 `/api/pick/sync` 为 401。
- **S5（2026-09-28 16:07 UTC）**：Railway 已弃用 Config as Code（给服务设配置路径被 API 拒绝），cron 的两份 `railway.toml` 改由 `scripts/pick-railway-settings.py` 写进服务设置（4b7dfd6、d6f8721，[packaging.md](observe-runbook/packaging.md) 第 3.3 节）。服务 `pick-obs-trends`：`apply` 自检配置；变量 `PGSSLMODE`、`PICK_OBS_EXPECTED_COLLECTOR`、`PICK_OBS_EXPECTED_ROLE`、`PICK_OBS_TRENDS_MODE=canary1`，`PICK_OBS_TRENDS_ROUTE=both`、`PICK_OBS_TRENDS_GRANULARITY=H` 是 G2 之前的暂定值；`PICK_DATABASE_URL` 与 `PICK_OBS_STATE_KEY` 从本机 600 文件经 `--stdin` 写入，生产状态密钥是新生成的一把，不是阶段 0 用的那把。守卫在 c3ec87d 通过（生产迁移头 0007，链头 0007），从干净检出 `railway up`，部署 `87513714-0691-4242-9382-9ff8347da7c3`。GitHub Actions 因账单停摆，改在本机验证：d062471 上 pick 全套 3667 通过、21 跳过（全 scram PG），宿主 pick 用例 85 通过；c3ec87d 相对 d062471 只改前端 e2e 与文档。
- **S6、S6a（2026-09-28 16:10 UTC）**：`selfcheck ok: collector=obs-collector-v1 head=0007 role=pick_observer package=sha256:dcd93ed1a342f409be49fc1c13589d272b28c3d2813eb22ac347eda5b4b3b6de`，摘要等于检出里源码与托管副本算出的值，`at` 在镜像的 site-packages 下；没有「这个服务不读」的警告。预检：target_date 2026-09-29、`canary1`、`user` 节奏，`reasons` 与 `refused_by` 为空，计划请求 204（下限 176）、92 个单元，正对照匹配 10（下限 8），无熔断估算覆盖率 1。`check --deployment` 核对这次部署的清单与自检配置一致。共享剧库批次里找不到的对照（`missing_first`，只记 identity）5 个：
  - `realshort-pick` `ZmxhcmVmbG93LTY2NzkxNQ` en；`cmVlbHNob3J0LTZhOGQyZDc5ODdjOTM0M2M3YTA1YTc5OA` es；`cmVlbHNob3J0LTZhOGU3YzIyN2NmOTI1YzI0YTA1NDIzMw` pl；`cmVlbHNob3J0LTZhNmRhMmMwYmQ1ZDhlYmYxNzBmMTU4OQ` zh-hant；`cmVlbHNob3J0LTZhN2FjMjRmMTgxZjc3NTg0MzAwYTgyNw` es
- **停在自检配置，不开 cron**：服务没有 cron 计划，今晚不跑。阶段 0 第二天 09-28 15:29 UTC 跑完，定稿报告的结论是闸门 A 小时级不过、日级未定（只观测到 7 部正对照），闸门 B 过，路线未定。等 G2 据此定 ROUTE、粒度与节奏，再按 packaging.md 第 5 节第 4 步从最新 main 重走一遍（守卫仍带首次记录参数），切到 cron 配置并核对之后才追加守卫记录行。


## 选剧资料审查修复与数据恢复（2026-09-28）

- PR #4 合并为 `07c79814a5e743f8174cc8e5640eedf16b974549`：修复第 500 页截断、榜单隐藏搜索、语种入口与空语种过滤、鹊娱日榜日期入口、回放详情返回的结果身份；分别提示剧单/发布记录超过 36 小时，以及日榜/周榜最新一期超过 2/14 天。历史版本按采集时刻判断来源年龄。
- 新增复现测试先失败后修复；合并树与已测提交 `6817322` 相同。本地前端 2,820 项通过、零跳过（包含独立 PostgreSQL 上 45 项集成测试）；四格兼容 4/4、合同夹具 12/12、TypeScript、相关 ESLint、独立审查和全部代码 CI 通过。
- 前端按守卫从 `git archive` 导出，生产部署 `dpl_7auLFXkS7pwgMeitWEkeRFHSM5te` 已 READY，别名 `ggwork-deerflow.vercel.app` 指向它。69 个资料页相关源文件与合并提交 SHA1 全部一致；未登录的资料页和深分页都返回 307 到登录页。Chrome 发布后已核对：v10 末页为第 1,474/1,474 页、32 行；未标语种筛出 62 行；搜索切榜清 q，直接榜单搜索有清除入口；qc/qr 都有历史日期，9 月 24 日切换有效；已有候选回放第二页进详情再返回，20 行名单与顺序完全相同，result/v/page/size 都保留。旧 v10 分源与榜期过期警示、新 v11 无来源过期警示、正常 v1 回放无误报均通过，最新资料页截图已留证。本次修复未操作 gateway 发布。
- 本机重新完整读取飞书剧单/发布记录，按 Base 分页 manifest 核验末页与 revision；Chrome 已登录的鹊娱页面取得 9 月 28 日两张榜，各 25 名，页面顺序与响应一致。保留生产已有 700 个历史榜点（9 月 11–24 日），仅追加今日 50 点；9 月 25–27 日缺口未编造。
- RealShort 原四表做私有备份，既有导入脚本 dry-run 通过后执行一次，14:15:04 UTC 完成，`pick_catalog=success`。回读 42,395 剧单行、2,858 信号、239 部发布记录、19 个账号；发布指标到 9 月 28 日，Kalos 日榜到 9 月 27 日，鹊娱两榜到 9 月 28 日。
- 手动触发一次工作台同步，14:18:44 UTC 成功发布镜像 v11，paired，八道门禁全部通过、漂移与清洗命中为 0、无告警；候选池 12,383 部。原 `v=10` 链接仍固定旧版，查看最新数据应去掉 `v`。
- 后续自动采集尚未恢复：原 Mac mini 的保存连接当前断开，`.local` 名称无法解析；本机未安装原刷新 LaunchAgent。待用户确认继续原机器或迁到本机后核验运行器。本次只证明一次完整资料恢复，不证明定时链路已恢复。
- 计数更正：v10 默认全部剧库为 73,682 行、1,474 页。审查初稿误将与 no/yes 重叠的 posted.pool 相加，旧数 73,838 已更正；分页缺陷与修复范围不变。
- `pick-deploy-guard target=frontend commit=07c79814a5e743f8174cc8e5640eedf16b974549 at=2026-09-28T14:23:29Z`


## 下线 DeerFlow 营销面（PR #2，2026-09-28）

- PR #2 下线上游的落地页、文档、博客与 showcase，根路径改为服务端重定向到 `/workspace`，全站加 noindex。它在 S4 之后 rebase 到 6777dea，CI 全绿后合并为 `2ee78b342c7c1894e897dc00bbdbb44f820a382b`。
- 发布：经守卫从 `git archive` 导出的目录发布，部署 `dpl_G4iDwezLP2Mk3h9CPQzCrsRBeg13`，生产别名 ggwork-deerflow.vercel.app 指向它。上传 1,024 个文件，均来自该提交的导出，另外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20260928-2ee78b3`。#3、#4 此前已上线，这次只多了 #2 的前端改动；gateway 没有动。
- 部署前在守卫检出里验证：
  - 四格 4/4；
  - 合同夹具 12/12；
  - 前端全套 2,740 通过；
  - typecheck 通过，检出干净。
  - 另在 PR 分支上跑过 build、e2e 275 条、e2e-auth 6 条，均通过。
- 部署后核对：
  - `/` 307 到 `/workspace`；未登录访问 `/workspace` 307 到 `/login`。
  - `/en/docs`、`/zh/docs`、`/blog/posts`、`/github-stars`、`/showcase/<id>` 均 404。
  - 页面与 `public/` 静态文件都带 `X-Robots-Tag: noindex, nofollow`。
  - `/login` 有 robots meta，没有「Back to home」。
  - 关于页的版本号要登录后才能看到，待用户核对。
- `pick-deploy-guard target=frontend commit=2ee78b342c7c1894e897dc00bbdbb44f820a382b at=2026-09-28T14:54:37Z`


## 查询条件校验、0 结果诊断与 hot_only（PR #8，2026-09-28）

- 起因：生产测试「最近美国 US 地区热门、没上过的剧」，模型三次把 `US` 填进 `theater`，每次 0 部，又拿候选池范围当理由。PR #8 让剧场、语种、标签、query 按当前批次校验（没有的值拒绝并列出可选值，地区词提示改用语种），0 结果附 `zero_diagnosis`，新增 `hot_only`（白名单等于资料页 `THEATER_BASES`，clk/bill/gsc 不算）。gpt-6-astra 四路审计两轮：第一轮 2 条 P1 已修，第二轮全部 PASS、无 P1，所报 P2 一并修复。合并为 `ff296f5fcb815d726b3373ffdcee339efec7d650`。
- GitHub Actions 因账户账单问题整批未启动（作业未运行，不是测试失败），合并依据本机等价检查：
  - 扩展全套在 ff296f5 上（全 scram PG 17 与 SQLite）：3,646 通过、21 跳过，跳过均为方言专属；
  - gateway 四格 35 条，两种库都有，0 跳过；
  - PR 分支上：后端宿主全套 `make test-shard` 18,148 通过，前端全套 2,744 通过；
  - 另有 typecheck、lint、format、pick-board 集成 45/45、skill-review、agent-guidance、`uv lock --check`、`make lint`。
- 前端先发（旧前端的严格 schema 不认 `hot_only:true`）：
  - 四格 5/5（含新增的热门卡一格），合同夹具 12/12，typecheck 通过；
  - 从守卫导出的目录发布，部署 `dpl_BTQY5VfWSJo1p5Rv6cxaDTnRpKSY`，READY，生产别名指向它；构建带 `NEXT_PUBLIC_APP_VERSION=20260928-ff296f5`；
  - 上传源码只有 `frontend/` 下的已跟踪文件；
  - 未登录访问 `/` 307 到 `/workspace`，`/workspace`、`/workspace/pick-data` 307 到 `/login`。
- gateway 从干净检出 `railway up`，部署 `04c5e4dd-330f-4922-9ee4-ac55d3ee4dd8`，SUCCESS；迁移头仍是 0007，本次没有新迁移。
  - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`，没有 Traceback 与 `service start() failed`；
  - 容器里 `ggwork_pick.references.query_languages` 存在，`PickConditions` 有 `hot_only`。
  - 此前 15:28Z、15:32Z、15:45Z 有三次 reason=redeploy 的 gateway 部署（56b15ef3、039874a8、4faae063）：模型迁移改 `AZURE_OPENAI_DEPLOYMENT` 触发，依次是 gpt-6-luna、改回 gpt-5.6-luna-2（gpt-6-luna 不支持 `reasoning.effort`）、gpt-6-sol。三次都是 e15f3f3 的同一镜像（含 0007），没有回退迁移。本次部署替换了它们，继承的模型变量是 `gpt-6-sol`。
- 部署后在容器里只读核对最新批次 8bcf785a（15:42Z 发布，12,384 部）：
  - 原事故条件 `theater=US` 被拒绝，提示改用 `language=en`、清空写法 `theater:null`；`query="US 热门"` 同样被拒；
  - `language=en, hot_only, exclude_posted` 1,450 部；`language=en, exclude_posted` 2,952 部；
  - `ReelShort + en + hot_only + exclude_posted` 为 0，诊断：去掉剧场 1,450、去掉热门 1,502；
  - `en + youtube` 为 0，诊断：去掉渠道 3,017、去掉确认可发 3,007（上下架全部未知）。
  - 在工作台里复问原问题，待用户核对。
- `pick-deploy-guard target=frontend commit=ff296f5fcb815d726b3373ffdcee339efec7d650 at=2026-09-28T15:25:39Z`
- `pick-deploy-guard target=gateway commit=ff296f5fcb815d726b3373ffdcee339efec7d650 prod_head=0007 chain_head=0007 at=2026-09-28T15:47:22Z`


## 选剧快捷模板（PR #9，2026-09-28）

- PR #9 把新对话输入框下的快捷入口和定时任务的快速创建，从上游示例换成选剧模板：
  - 新对话：找候选、按剧场、KalosTV 日榜、按账号排除，外加「更多」菜单（KalosTV 周热门、排除 YouTube 禁用、查一部剧、盘点候选池）。
  - 定时任务：每日候选、KalosTV 日榜前 10、KalosTV 周热门候选、候选池每周盘点。
- 其他改动：
  - 占位符白名单换成剧场、账号名、剧名三种。
  - 修复「更多」菜单选中后焦点被还给触发按钮的问题。
  - 按 PR #8 的新拒绝规则，给按剧场、按账号排除两个模板加了护栏。
- 合并为 `d062471d8857028d1fd12791e7c93d081d07110e`，在 #8 的前端与 gateway 上线之后。
- 发布：
  - 经守卫从 `git archive` 导出的目录发布，部署 `dpl_CYgYFs9VffxTSRvE2H8NvfzcsUHR`，生产别名 ggwork-deerflow.vercel.app 指向它。
  - 上传 1,026 个文件，另外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20260928-d062471`。
  - 只改前端，gateway 没有动。
- 部署前，在守卫检出里验证：
  - 回滚矩阵 5/5，含热门卡；
  - 合同夹具 12/12；
  - 前端全套 2,759 条；
  - typecheck 通过，检出干净。
- 另在 PR 分支（ff296f5 之上）验证：e2e 277 条、e2e-auth 6 条、lint、prettier 通过。GitHub Actions 因账户付款问题没有启动，远端 CI 未跑。
- 部署后核对：
  - `/` 307 到 `/workspace`，未登录的 `/workspace/chats/new` 与 `/workspace/scheduled-tasks` 307 到 `/login`；
  - 带 `RSC: 1` 的请求只返回到 `/login` 的重定向，没有页面内容；
  - 全站带 `X-Robots-Tag: noindex, nofollow`。
- 待办与已知问题：
  - 登录后的界面核对待用户做：快捷入口与「更多」菜单的中英文案，以及各模板实际调用的工具参数。
  - 生产 `scheduler.enabled=false`，定时任务只能手动「立即触发」。
  - 「剧场规则」模板等知识摘录修复上线后再加回。
- `pick-deploy-guard target=frontend commit=d062471d8857028d1fd12791e7c93d081d07110e at=2026-09-28T15:56:49Z`


## 知识检索与发布记录核对（PR #10，2026-09-28）

- 起因：复核选剧快捷模板时发现两处缺陷，都已回放确认。
  - `pick_search_knowledge` 只返回最早命中词前 100 字起的 1,600 字。`realshort-rules.md` 开头的「信号种类」列表已提到 DramaBox、MoboReels，查询结果因此拿不到 `## DramaBox`、`## MoboReels`、`## flareflow`、`## TouchShort` 的小节。
  - 答案核对只认 exclude_posted / posted_account 这两个过滤；片名查询返回的条目已经带发布摘要，"已对上，帖子数0，团队还没发过"这类准确回答也被标注。
- PR #10 合并为 `e2ac66a021c6afa2528ada7859b0500215b074f1`，基于 ff296f5（含 PR #8）。改动如下：
  - 规则文档不超过 4,000 字时整篇返回；更长的文档按标题取剧场小节，每份最多 5 段，扫描是线性的。
  - 查询和详情工具记下返回条目的发布摘要：已对上且帖子数为 0 的才算支持"没发过"。点名未对上的剧说没发过仍会被标注，按发布记录过滤过也一样。
  - 没有新迁移，依赖和 `uv.lock` 不变；托管副本按 D21 用 uv 0.11.1 刷新。
- GitHub Actions 因账户付款问题没有启动，合并依据本机等价检查。在 59a3bb3 上（树与合并结果一致）：
  - ruff 全部通过；
  - 扩展全套 3,667 通过、21 跳过，用的是全 scram 的 PG 17 和 SQLite，跳过的都是方言专属；
  - 入口、JSON 净化、create_user 共 85 通过；
  - 另有独立审查，指出的两处平方级扫描已在 PR 内修复。
- gateway 从干净检出 `railway up`，部署 `ec3606b1-306f-40dd-8c65-5db62e176eec`，SUCCESS；迁移头仍是 0007。守卫在 c3ec87d 上通过；c3ec87d 与四格所测的 d062471 只差 progress.md 和一个前端 e2e 用例，进镜像的路径零改动。
  - 部署前四格（d062471）：扩展全套 3,667 通过、21 跳过；gateway 列 35 条，两种库都有，0 跳过。
  - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`，没有 Traceback 和 `service start() failed`。唯一的 WARNING 是 GitHub webhook 路由未挂载，上一个部署同样有。
  - 容器里 `ggwork_pick.observe.selfcheck`、`grants`、`knowledge_excerpts` 都能导入，`PickTask` 有 `posted_seen`。
  - `regrant --check` 授权齐全：schema 6、表 30、列 4、序列 12，版本 pickm_v000001、v000010、v000011、v000012。
  - 生产规则文档只读核对（15:42Z 发布，2,786 字）：10 个剧场标题都在；DramaBox、MoboReels、flareflow YouTube、TouchShort 报备四个查询各返回 1 段，都含对应剧场小节。
  - 答案核对：已对上且帖子数为 0 的回答不标注；未对上却说"从未发布"的仍标注。
  - 上线后真实请求 124 个全部 200，含已登录用户的 `/api/pick/results` 与 `answer-checks`；未登录访问 `/api/pick/sync` 为 401，`/health` 为 200。
- 待办：
  - 待用户核对：在工作台里问一次剧场规则，再对一部已对上、未发过的剧做片名查询，确认回答旁边没有多余的核对标注。
  - 「剧场规则」前端模板由「移除 Deerflow 元素替换 GGWork 资源」会话加回。
  - 账号拒绝要列出可选账号并附 `_clear('posted_account')`，另开 PR 处理。
- `pick-deploy-guard target=gateway commit=c3ec87df89e3d0ac98b5766b7d5c9a66920fcafe prod_head=0007 chain_head=0007 at=2026-09-28T16:11:02Z`


## 加回「剧场规则」快捷模板（PR #11，2026-09-29）

- PR #9 因为知识检索只截取 1,600 字的窗口，撤下了「剧场规则」模板：查 DramaBox、MoboReels 时会截到别的剧场的规则。
- PR #10 的 gateway（c3ec87d）上线后，规则文档改为整篇返回，这个问题不再存在，于是把模板放回「更多」菜单。合并为 `1ab52e26a76b1f0707e8fb52a8ffdf245d69f2a4`，只改前端。
- 发布：经守卫从 `git archive` 导出的目录发布，部署 `dpl_BJBD4neUwu58huYnmUm6BRXFanqf`，生产别名指向它。构建带 `NEXT_PUBLIC_APP_VERSION=20260929-1ab52e2`。
- 部署前，在守卫检出里验证：
  - 回滚矩阵 5/5；
  - 合同夹具 12/12；
  - 前端全套 2,759 条；
  - typecheck 通过，检出干净。
- 在 PR 分支上另外验证：e2e 277 条、e2e-auth 6 条、lint、prettier 都通过。
- 部署后核对：
  - `/` 307 到 `/workspace`；
  - 未登录访问 `/workspace/chats/new` 307 到 `/login`；
  - 带 `RSC: 1` 的请求只返回到 `/login` 的重定向；
  - 全站带 noindex。
- 登录后的界面核对还没做，由用户做。
- `pick-deploy-guard target=frontend commit=1ab52e26a76b1f0707e8fb52a8ffdf245d69f2a4 at=2026-09-28T16:19:25Z`

## 模型换 gpt-6-sol，档位改由 Railway 变量控制（PR #13，2026-09-29 上线）

- 2026-09-28 23:45（+08）Railway gateway 的 `AZURE_OPENAI_DEPLOYMENT` 已切到 `gpt-6-sol`（重新部署 4faae063）。`gpt-6-luna` 不收 `reasoning.effort`，不要用，见 [azure-cloud-deployment.md](azure-cloud-deployment.md)。
- 本分支把开思考/关思考的 effort、输出上限、请求与分块超时改成 `$PICK_LLM_*` 占位，由入口补默认值：开思考 high、关思考 low、32000 token、300 秒、300 秒。整轮上限 `PICK_RUN_TIMEOUT_SECONDS` 从 120 提到 600，扩展自己的截止时间也改读这个变量，之前硬编码的 120 秒不再生效。以后调档只改 Railway 变量。
- PR #13 合并为 `7179c7bad1d8d1112c8932f57ff9bcd45b68ae5e`，树与已测提交 01d8565 相同。GitHub Actions 停摆，合并依据本机等价检查：
  - 扩展全套 3,680 通过、21 跳过（PG 17 一次性容器加 SQLite，跳过的都是方言专属），含 `test_managed_copy`；
  - 入口、JSON 净化、create_user、ModelConfig、model factory、run deadline 共 242 通过；
  - 后端全套 18,181 通过，1 条失败：`test_local_sandbox_provider_mounts.py::TestReadOnlyPath::test_bash_write_to_projected_copy_does_not_mutate_source`，在 ggwork/main 的干净检出上同样失败，与本 PR 无关；
  - blocking-io 149 通过；ruff、agent guidance 通过；
  - 独立审查：CRITICAL、HIGH 为 0。MEDIUM 一条（全角数字能过入口校验、pydantic 却拒绝）已修。
- Railway 变量：生产上原有 `PICK_RUN_TIMEOUT_SECONDS=100`，来源没有记录。它会盖过默认的 600，而扩展的截止时间现在也读它，所以按用户定的约 600 改成 600。五个 `PICK_LLM_*` 也显式写进 Railway，取值同默认，控制台里看到的就是实际生效的值。都用 `--skip-deploys`，随下面这次部署一起生效。
- gateway 从干净检出 `railway up`，部署 `5e8d3c35-8be1-4e13-af1a-f449d8623f8e`，SUCCESS；迁移头仍是 0007。
  - 部署前四格（7179c7b）：gateway 列 35 条，两种库都有，0 跳过；扩展全套见上。
  - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`，没有 `service start() failed`。
  - 上线后 133 个请求全部 200/204。日志里仅有的两段 Traceback 都是 `本轮业务工具调用次数已达上限`（见下文实测），两轮仍以 success 结束。
  - **没做**：容器内核对（运行时 yaml 只有占位、`observe.selfcheck`/`grants` 能导入、`regrant --check`）。本会话的 `railway ssh` 被 auto mode 拦下，待用户执行。
  - 生产手工核对：旧卡会话（「找5部英语剧，排除我已经选过…」）卡片能展开，面板没有解析错误；「我的选剧」页正常加载，但这个账号没有保存条目，存量快照格没法核对；新卡、混合会话两格按 S13 之前的规则只靠测试。
- 实测（开思考 high，2026-09-28 16:50–16:58 UTC，gateway 日志的 `LLM token usage` 行）：

  | 问题 | 本轮耗时 | 模型调用 | 单次 reasoning 最大 / 合计 | 单次 output 最大 | 单次调用最长 |
  |---|---|---|---|---|---|
  | 热度高、没发过的英语剧按榜排序（先反问选榜） | 4 秒 + 10 秒 | 1 + 2 | 202 / 443 | 791 | 约 7 秒 |
  | 剧场规则、计数、挑 3 部查详情（先反问范围） | 7 秒 + 66 秒 | 1 + 10 | 583 / 1,821 | 689 | 约 22 秒（最终回答） |
  | 西语与葡语对比并给建议 | 52 秒 | 10 | 483 / 1,530 | 1,039 | 约 11 秒 |
  | 同上，关思考（low）对照 | 37 秒 | 8 | 163 / 374 | 1,071 | 约 9 秒 |

  - 同一题 high 的 reasoning 约是 low 的 4 倍，说明开思考确实用了 high。GPT-6 在这些问题上单次推理不超过 600 token，离 32000 的上限很远。
  - 最长一轮 66 秒，最长单次调用约 22 秒。300 秒的请求与分块超时、600 秒的整轮上限都留有足够余量，这次**不调变量**。
  - 流式最终回答那次调用，日志记的是 `input=0 output=0`，用量没进这一行，只能看前面几次调用。
  - **新发现**：high 档工具调用更多，第 2、3 题都碰到了每轮 8 次的业务工具上限（`ggwork_pick/middleware.py` 的 `PickToolGate`），模型收到错误后照常作答；low 对照没碰到。上限是写死的（工具 8 次、模型 12 次），要不要也改成 Railway 变量，待用户决定。
- `pick-deploy-guard target=gateway commit=7179c7bad1d8d1112c8932f57ff9bcd45b68ae5e prod_head=0007 chain_head=0007 at=2026-09-28T16:44:53Z`

## 前端：矮屏欢迎块、重跑断流续接显式化、回底重新锁定（PR #5、#6、#15、#17，2026-09-29 上线）

- #5、#6 修矮屏下欢迎块被挤出视口，合并到 5bd4be98。#17 把重新生成、编辑重跑的 `thread.submit` 与主发送共用 `buildRunStreamOptions()`，显式带 `onDisconnect: "continue"`，合并为 `6919eb5f`。#15 让读者回到最新消息时重新锁定底部跟随，合并为 `7c73ac9c`。
- #17 核对时发现：SDK 1.6.0 在 `streamResumable: true` 时本来就把 `onDisconnect` 默认成 `continue`，修复前的构建上重跑请求体也已经是 `on_disconnect: "continue"`。原先「重跑走服务端默认的 cancel」这个判断不准，部署文档已在 #17 里更正。这次改动是加固：不再依赖 SDK 的默认值，免得 SDK 升级后退回 gateway 的默认值 cancel。
- #15 经 gpt-6-astra 复审两轮，先后修了两个问题，都先写失败用例再修：
  - 向上微滚 1px 仍落在 2px 容差内，会被重新锁回底部；
  - 多次 scroll 合并派发时，向上滚动被判成向下。
- 发布：经守卫从 `git archive` 导出的目录发布，部署 `dpl_5XZsx8urqbnfYrfFx8xrJd8s75Dc`，READY，生产别名 ggwork-deerflow.vercel.app 指向它。上传 1,028 个文件，除导出内容外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20260929-7c73ac9`。gateway 这次没动，#14 的 gateway 改动随下一次 gateway 部署上线。
- 部署前在守卫检出里验证（7c73ac9c）：
  - 四格 5/5，含 hot card 格；
  - 合同夹具 12/12；
  - 前端全套 2,780 通过、45 跳过；
  - typecheck 通过，检出干净。
- PR 分支上另外验证过：
  - #17：相关 e2e 98/98，单测、typecheck、lint、format 通过；
  - #15：底部锁 e2e 两条各 5 次 10/10，相关 e2e 73/73。
  - 两个分支同时合并没有冲突，用 `git merge-tree` 确认过。GitHub Actions 仍因账单停摆，以上都是本机等价检查。
- 部署后核对：
  - `/` 307 到 `/workspace`；
  - 未登录访问 `/workspace/chats/new` 307 到 `/login`；
  - 带 `RSC: 1` 的请求只返回到 `/login` 的 `NEXT_REDIRECT` 和路由树，没有页面数据；
  - `/login` 的响应头和 meta 都带 noindex。
- 登录后的界面核对还没做，由用户做：重新生成时断流再回来能接上这一轮、向上阅读不会被拉回底部、回到底部后继续跟随。
- 后续（读代码得出，没有复现，写在 #17 正文里）：
  - 重新生成的回合断流后重新加入时，旧答案会和正在生成的新答案同时显示，直到这一轮结束；
  - 同一线程页面里第二次断流不会再自动重新加入。
- `pick-deploy-guard target=frontend commit=7c73ac9c0c7fe8c1613caff519512bd7e1b0019e at=2026-09-29T12:57:15Z`

## 能力中心：目录裁剪、插件打通与连接检测（PR #20，2026-09-29 上线）

- 起因：用户提了三点。智能体还没实现，入口先屏蔽。IM 只留飞书；文档加飞书和 Google Docs，腾讯文档、Notion 先屏蔽。其余入口要真的能连上，GitHub 这类当时都没打通。用户截图里 GitHub 配置框的「服务地址」被浏览器自动填成了登录邮箱，「授权请求头」填成了登录密码。
- 用户定的范围：
  - 管理员接入的插件工具，所有登录用户都能在对话里用；
  - 飞书做群通知和文档读取两项，lark-cli 个人授权以后单独做；
  - Google Docs 先只读公开链接；
  - 搜索打开，浏览器自动化屏蔽。
- 改动：
  - 侧边栏：`agents_api` 关闭时不再显示「智能体」入口（原来是灰色占位加提示）。
  - 目录：钉钉、企业微信、腾讯文档、Notion、浏览器自动化、飞书 CLI 标成 `hidden`，不列出，也不能安装。新增三个内置客户端：
    - 飞书群通知：自定义机器人的 webhook 令牌加签名密钥；
    - 飞书文档：自建应用的 App ID/Secret，读 docx 和 wiki 链接，feishu.cn 与 larkoffice.com 都认；
    - Google Docs：读公开链接，不用凭据。
  - GitHub、Jira/Confluence 改走新的 remote 适配器，端点由 gateway 固定，表单只收凭据：
    - GitHub 连官方只读端点 `api.githubcopilot.com/mcp/readonly`，填个人访问令牌；
    - Atlassian 连 `mcp.atlassian.com/v1/mcp`，用账号邮箱加作用域 API 令牌做 Basic 认证。
  - 搜索：运行配置打开 DuckDuckGo `web_search` 和 Jina `web_fetch`，目录里显示为已启用。Exa、Firecrawl 改成填 API Key 的内置客户端。
  - 连接检测 `POST /api/capabilities/connections/check`，仅管理员可用：
    - 保存后自动检测一次，已安装列表每行也能手动检测；
    - 结果只回状态码和异常类型，不回 URL、请求头或异常原文；
    - Atlassian 凭据无效时仍会列出 Teamwork Graph 的公共工具，所以检测要求至少有一个 Jira 或 Confluence 工具，否则判为认证失败；
    - 检测通过、而 Agent 缓存里记着这个服务没有工具时，会重置缓存。
  - 凭据输入框关掉浏览器自动填充：`autocomplete` 设为 off 或 new-password，并给每个框唯一的 name。
  - 选剧对话（`PickModelGate` / `PickToolGate`）：
    - MCP 插件工具和两个搜索工具对模型可见；
    - 插件每轮另有 8 次上限，不占选剧工具的 8 次；
    - 本轮读过外部内容后，没有 `readOnlyHint` 的插件操作直接拒绝，比如飞书群通知、没有标注的 Jira 操作。模型要先把内容给用户看，用户下一条消息同意后才能调；
    - RBAC 两个角色改为 `allow: '*'`，再显式 deny 宿主内置工具。
- 本机端到端（QA gateway，pick 配置，Ollama qwen3:8b，SQLite）：
  - 本机 bearer 鉴权的 FastMCP 替身：正确令牌检测通过；错误令牌判 `auth_failed`（HTTP 401）。
  - GitHub 用假令牌打真实端点，判 HTTP 401，界面显示「认证失败」和重新配置的提示。
  - 钉钉安装返回 404。
  - 对话里模型依次调了替身 MCP 工具、`web_search`、`google-docs_read_document`（读一篇公开文档），答案正确。
  - 浏览器：侧边栏没有「智能体」；GitHub 弹窗只有连接名称和个人访问令牌两项。
- 测试，均在变基到 2adcb3fa 之后跑：
  - 扩展全套：SQLite 2,901 通过、819 跳过；全 scram 的 PG 17 3,699 通过、21 跳过。两遍都含 `test_managed_copy`；
  - 后端全套 18,533 通过，3 条失败：
    - `test_agent_guidance_check`：`backend/app/gateway/AGENTS.md` 超了硬上限，已把新增段落缩成一句指向 [capability-center.md](../capability-center.md)，重跑 13 通过；
    - `test_local_sandbox_provider_mounts.py::TestReadOnlyPath::test_bash_write_to_projected_copy_does_not_mutate_source`：main 上原本就失败，见 PR #13 一节；
    - `test_aio_sandbox_local_backend.py::test_aio_1_11_image_starts_with_fowner_capability`：本机没有 AIO 1.11.0 镜像，起容器失败。本 PR 没碰沙箱代码；
  - 前端：check 通过，单测 2,797 通过、45 跳过；能力中心、业务插件、集成、图标、MCP 设置、侧边栏 6 个 e2e 文件 38/38；
  - ruff 通过；agent guidance 检查 0 错误；
  - 独立审查：CRITICAL、HIGH 为 0。MEDIUM 两条：插件放给所有登录用户，是用户的决定；飞书群通知可能被外部内容诱导发送，已用上面的读后拦截处理。
- 上线：
  - 更正：这里原先写「#20 合并后 Vercel Git 集成在 14:03Z 自动把前端推上 Production」，不对。
    - 那次 Git 集成的部署，去的是同一团队里另一个 Vercel 项目 `ggwork`，别名 ggwork-nine.vercel.app。这个站的 `/api` 转发到私有主机名（Vercel 报 `DNS_HOSTNAME_RESOLVED_PRIVATE`），没连生产 gateway。
    - 生产站 ggwork-deerflow.vercel.app 一直是守卫发布的 7c73ac9c（`dpl_5XZsx8urqbnfYrfFx8xrJd8s75Dc`），直到下面的前端发布。
    - 所以 gateway 上线后、前端发布前，是 gateway 领先前端：侧边栏仍是灰色「智能体」，能力中心仍是旧表单。
    - 以后判断生产前端版本，看 `vercel inspect ggwork-deerflow.vercel.app`，不看 GitHub 上的 deployment 状态。
    - #20 合并为 `fbda69ff3cea78be5bbcbff6f2131dfbe7dfa8d5`。
  - gateway 经守卫后，从 `git archive` 导出的目录 `railway up`，部署 `8372055f-4d7a-4cc0-90c4-fb5db1eb0631`，SUCCESS。导出目录只有该提交的 3,845 个跟踪文件，链接到同一项目和服务。#14（账号条件被拒时列出可选账号、给出清空写法）的 gateway 改动也在这次上线。#14、#20 都没有新迁移，迁移头仍是 0007；依赖、`uv.lock` 与 Dockerfile 不变。
  - 部署前在守卫检出（fbda69ff）里验证：
    - 扩展全套 3,699 通过、21 跳过，用一次性 PG 17 加 SQLite，跳过的都是方言专属，含 `test_managed_copy`。部署后又在 fbda69ff 上用 `initdb --auth=scram-sha-256` 建的全 scram 集群重跑一遍，结果相同。这个集群的 pg_hba 里 local 与 127.0.0.1 都是 scram，没有 trust；
    - gateway 四格 35 条，两种库都有，0 跳过；全 scram 集群上重跑结果相同；
    - 宿主用例 466 通过：入口、JSON 净化、create_user（含 PG 那条）、能力中心与业务插件、MCP 缓存、RBAC；
    - 镜像依赖集（`--no-dev --extra postgres`）里有 ddgs、langchain-mcp-adapters、mcp、readabilipy。没有 Node 时，`web_fetch` 的正文提取走 readabilipy 的纯 Python 回退。
  - 部署后核对：
    - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`。没有 Traceback，没有 `service start() failed`，也没有 `Running upgrade`。唯一的 WARNING 仍是 GitHub webhook 路由未挂载。
    - 以管理员登录后请求，GET `/api/capabilities/connections/check` 从 404 变为 405（`Allow: POST`），`/api/pick/sync` 返回 200。`/api/capabilities/catalog` 不再列出 lark、dingtalk、wecom、tencent-docs、notion、browser，新增 feishu-bot、feishu-docs、google-docs。
    - 跑了一轮选剧对话：模型先反问选榜，再按 KalosTV 日榜作答。两个 run 都 success，日志没有报错。
    - 容器内 `regrant --check`（用户执行，本会话的 `railway ssh` 被 auto mode 拦下）：授权齐全，schema 7、表 31、列 4、序列 12，已发布镜像版本 5 个（pickm_v000001、v000010、v000012、v000013、v000015）。比 PR #10 那次多一个版本，schema 和表因此各多一个。`observe.selfcheck` 能否导入没有在容器里单独核对，#14、#20 没有改它。
  - 前端：经守卫从 `git archive` 导出的目录发布 7d330584（前端与 fbda69ff 相同），部署 `dpl_DdQ17qjH5Fro3jkoFhrB6QDwG2zR`，生产别名 ggwork-deerflow.vercel.app 指向它。导出目录里是 `frontend/` 的 1,033 个跟踪文件，另外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20260930-7d33058`。gateway 这次没动。
  - 部署前在守卫检出（7d330584）里验证：
    - 四格 5/5，含 hot card 格；
    - 合同夹具 12/12；
    - 前端全套 2,797 通过、45 跳过；
    - typecheck 通过，检出干净。
  - 部署后核对：
    - `/` 307 到 `/workspace`；
    - 未登录访问 `/workspace/chats/new`、`/workspace/capabilities` 307 到 `/login`；
    - 带 `RSC: 1` 的请求只返回到 `/login` 的 `NEXT_REDIRECT`；
    - `/login` 带 `X-Robots-Tag: noindex, nofollow`。
  - 以管理员登录后核对：
    - 侧边栏不再显示「智能体」；
    - 能力中心列出飞书群通知、飞书文档、Google Docs，网页搜索与网页读取显示已启用；
    - GitHub 弹窗只有连接名称和个人访问令牌两项，令牌框没有被浏览器自动填充；
    - 关于页的版本号是 20260930-7d33058。
- 部署后由用户做：
  - 在能力中心填真实凭据，每填一项看一次检测结果：
    - GitHub：细粒度只读令牌；
    - Jira：组织管理员先在 Rovo 设置里开启 API 令牌认证，再填账号邮箱和作用域令牌；
    - 飞书文档：自建应用开通 `docx:document:readonly`、`wiki:wiki:readonly` 并发布版本，再把应用加为文档协作者或知识库成员；
    - 飞书群通知：群里添加自定义机器人并开启签名校验；
    - Exa、Firecrawl：填 API Key。
- 后续：
  - lark-cli 个人授权；
  - 插件每轮 8 次的上限写死在代码里，和选剧工具上限一样，要不要改成 Railway 变量由用户定；
  - PR #16 合并后再经守卫部署一次 gateway；它若改了前端，前端也要经守卫发布；
  - Vercel 项目 `ggwork` 仍接着 Git 集成，每次推 main 都会给 ggwork-nine 构建一次 Production。要不要断开或删除，由用户定。
- `pick-deploy-guard target=gateway commit=fbda69ff3cea78be5bbcbff6f2131dfbe7dfa8d5 prod_head=0007 chain_head=0007 at=2026-09-29T15:20:55Z`
- `pick-deploy-guard target=frontend commit=7d33058427c457446a8a676e317da391aa824144 at=2026-09-29T16:09:56Z`
