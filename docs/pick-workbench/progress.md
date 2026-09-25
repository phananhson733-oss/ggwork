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
- **G3 修订（评审 P2-2，D24）：对应确认的失效是冻结的累积，改回不恢复**
  - 原来只比较当前批次的键与最近一次确认，标题 A→B→A、别名换走再换回，确认都会自动恢复，违反 D24。共享剧库批次两三天就清理明细（`KEEP_BATCHES=3`，一天两次拉取），内容相同的批次还会以新的 `published_at` 重新发布，所以不能在需要时再回看批次表；失效要在看到的当时记下来，逐个集合往下传。
  - 纯函数：`EffectiveDecisions.correspondence(identity, platform, normalized_title, *, lapsed)` 多了必填的 `lapsed`；`lapse(state, lapsed, sightings)` 返回新的失效集合（只留仍是该身份最近一次确认的决定 id）；手动配对 `alias_pair` 直接清掉旧身份的确认。读取：`catalog_history.read_sightings(conn, identities, *, upto, after, key_of)`，只读，两种库。合同：`FrozenInputsTrends.lapsed_confirmations`（必填，升序，不大于 `decisions_version`；GSC 没有这一列）。
  - **TR-20 接线**（会话开头，与读冻结输入同一步）：`state = effective(await read_decisions(step, k), k)`；取最新一个已发布的 Trends 集合（不分 mode），它的 `frozen_inputs.lapsed_confirmations` 作 `lapsed`，它所在会话批次的 `ggwp_obs_batches.started_at` 作 `after`（发布事务先盖 `published_at` 再提交，可往前留几分钟余量，多读一个旧批次只会更保守）；没有上一个集合时 `lapsed=[]`、`after=None`。`sightings = await read_sightings(step, state.correspondences, upto=source_catalog_batch_id, after=after, key_of=TR-18 的函数)`，`frozen = sorted(lapse(state, lapsed, sightings))` 冻结进 `lapsed_confirmations`；判定行写 `state.correspondence(identity, theater, normalized_title, lapsed=frozen)`。午夜后续跑沿用已冻结的值，不重算。
  - TR-20 的测试：连续三个模拟日标题 A→B→A，第三天仍是 unconfirmed，新确认后恢复；复用批次（A 重新发布）与清理批次各一例；一个会话没发布集合时，下一个会话从上一个已发布集合接着累加。
  - 取舍：清理掉的批次一律按「核对不了」处理，让它能反驳的确认全部失效。Trends 连续多天不发布集合、窗口里出现已清理的批次时，所有确认都要重新点一次；换来的是不会有确认在看不到的变化之后被当成仍然有效。
- **D43 的别名刷新**（TR-18 的 `alias.refresh()`，TR-23b 接线）：`alias_verdicts` 是 `{alias_id: AliasMark(decision_id, verdict)}`，`alias_pairs` 是 `{old_identity: PairMark(decision_id, new_identity)}`。跨种类按 `decision_id` 重放，后者覆盖前者。例如先手动配对 SLUG→X、后确认建议 SLUG→Y，应以后者为准，而不是当成多对多一起拒掉。
- **TR-33 或 G 节点（合同）**
  - （G3 已定）`CorrespondenceConfirm.platform` 至少 1 个字，不放宽：平台为空的 Trends 行确认不了，只能停在 unconfirmed。合同文档已写明；页面怎么提示见 decisions.md（TR-24、TR-25 不要给这种行确认按钮）。
  - 合同的 `MAX_ROW_ID` 是 2^63−1，0007 的 id 列却是 Integer（PG 上是 int4）。`read_decisions` 已改为按 bigint 绑定 `upto_id`；其他拿决定里的 `alias_id`、`alert_id` 去比 int4 列的查询，也要按 bigint 绑定或先限幅，否则超过 2^31−1 的值会让 asyncpg 抛 DataError，报错里还带着这个值。
