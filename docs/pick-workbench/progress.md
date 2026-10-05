# 当前发布状态（2026-10-06，PR #33）

当前产品源码为 `148fff7b08d22c1112fd142e62f0076698862fd3`，前端实际版本 `20261006-148fff7`。Gateway 与前端已发布，正式域名、限定源码核验和普通 QA 只读浏览器验收均通过；最终 PR CI 与 main CI 均 SUCCESS。四条已选记录及旧十部候选保持，新单次取消功能及用量观察持久化验收已通过；provider 用量仍诚实标为 unknown。发布身份、守卫原文和验收边界见文末「取消用量观察后续发布」。模型输入格式与雷达范围仍待用户选择，完整后续目标未完成。

---

# 后续落实（2026-10-06，历史发布前验证）

本轮授权为“按照建议去落地”。基于业务 main `8f2311da` 的 `codex/readiness-followup-20261006` 已集成取消用量观测、只读 canary 事实报告、负向引用覆盖和下一阶段规格。本段记录发布前的状态，当时生产版本为下方记录的 `d236f951`；后续发布结果以上方当前状态及文末追加为准。

- **取消计量实现与独立审查完成**：错误回调已知 partial usage 保留，缺失与显式零区分，实际回调生命周期计数通过 server-owned run metadata 持久化。原子 admission、租约丢失、重放、双库、真实本地异步流取消均验证；运行级观测不会复制进线程。前端用量文字明确“已记录”和未记录不等于零消耗。历史 Q19 的供应商 token 仍无法回填，见 [观测合同](usage-observation.md)。
- **中性 canary 报告完成**：独立 Trends CLI 只读最近七个 UTC 日的预算、请求、限流及停止事实，缺日 unknown、当天 pending；qualification 固定 not_evaluated。它没有恢复采集或判定门槛通过，见 [运维说明](observe-runbook/canary-report.md)。
- **团队/外部写入规格已交付并复审**：见 [团队流程](../plans/2026-10-06-pick-team-workflow-spec.md) 和 [明确确认的外部写入](../plans/2026-10-06-pick-external-write-spec.md)。这些是下一阶段可实施合同，不是已上线功能。
- **真实部署前验证进行中**：业务双库 4,351 passed / 21 skipped；blocking-I/O 149 passed；前端常规 2,874 passed，45 个 reader 项另用真实 PG 全部通过；check/build 通过，文字增量定向 8 项通过。宿主全量发现线程 metadata 隔离及指南预算问题，均已修并定向验证；最终以集成 HEAD 的全套 CI 为准，不把失败轮次改写成绿色。
- **两项明确决策待回复**：原字段表示可安全去重到约 8.9%，新模型专用可逆字典实验约 32%～35%，后者改变 Spec 表示合同；雷达还需确认按历史批准的 100 剧全球日级参考表，还是恢复完整 GSC/智能体路线。相关实现没有据等待时间自动选定。完整目标见 [后续计划](../plans/2026-10-06-pick-workbench-followup-plan.md)。

---

# PR #31 发布状态（2026-10-06，历史记录）

PR [#31](https://github.com/phananhson733-oss/ggwork/pull/31) 已合并，生产业务源码为 `d236f951d76d011bc2e704a772b2382345b8335b`。Gateway 部署 `457941b3-0f1d-49d8-8ccf-2b0b6d93b735`，Vercel 部署 `dpl_CKFUdQYs4ofv8TeYEoWGSxQKLKoW`；生产 alias `ggwork-deerflow.vercel.app` 已指向该 READY 部署，前端构建版本 `20261006-d236f95`。

- 合并提交上的复跑：业务双库 4,326 passed / 21 方言或重复计时 skip；后端兼容矩阵 35 passed；宿主 116 passed；真实 PG reader 45 passed / 0 skip；前端全套 2,874 passed，45 reader 项另行通过；旧/新/混合/hot/存量快照矩阵五格通过，check/build 通过。合并提交的 GitHub CI 也已成功。
- Gateway 实际 1,141 个源码树文件及 installed 145 个业务包文件与合并提交一致；关键模块加载、认证 API、数据库/checkpointer readiness 正常，生产 applied migration head 仍为 0007，没有新迁移或观测 cron 发布。
- 普通 QA 原两条已保存记录与旧 10 项候选在 Gateway 更替后保持，历史 notes 的 reference 与原 created_at 一致。
- 上线后以专用普通 QA 身份完成 Q01～Q20 的真实模型执行，共 26 个改后应用 run；连同改前本地/远程基线累计 36/40。新查询使用真实 mirror v23，旧 QA 卡仍冻结 v22；换批、详情、确认保存、丢响应幂等重试、重新生成/编辑/分支、历史翻页及刷新恢复均有实际证据。
- Q19 主动取消的精确 POST 请求返回 202，运行终态 interrupted，个人清单不变；取消调用缺少完整 provider 用量，仍记 UNVERIFIED。不能把功能通过写成“全部五层全通过”，也没有推算费用改善。
- 最终独立五层检查：20 题中 19 PASS、1 UNVERIFIED（Q19），0 FAIL、0 NOT_RUN；27 条记录中 26 PASS、1 UNVERIFIED。97 条语义标准全部通过，26 次运行的 130 个性能指标槽位中 127 个有证据，缺失三项未填 0。
- 原固定投影 baseline 的字节降幅仍为 7.79%，未达 20% 工程目标；RD-07 Trends/GSC 仍为 BLOCKED，未启用雷达下一阶段。两项限制保留。
- 本次发布与业务验收分别记账；下方 2026-10-05 的“未发布”和基线 FAIL/NOT_RUN 是历史记录，不覆盖本段已验证部署状态。改后五层结果见 [验收记录](acceptance.md#2026-10-06-readiness-生产发布与改后验收)。后续文档和 QA 工具提交不改变上述实际产品部署 SHA。

- `pick-deploy-guard target=gateway commit=d236f951d76d011bc2e704a772b2382345b8335b prod_head=0007 chain_head=0007 at=2026-10-05T16:20:29Z`
- `pick-deploy-guard target=frontend commit=d236f951d76d011bc2e704a772b2382345b8335b at=2026-10-05T16:27:02Z`

---

# 当前状态（2026-10-05，readiness 集成中）

业务仓库：`phananhson733-oss/ggwork`。本期实现基线 `5c39bdf2`，交付分支 `codex/pick-readiness`，见 [PR #31](https://github.com/phananhson733-oss/ggwork/pull/31)。业务源码完整回归后，仅追加模型字段说明及验收驱动修复；这些增量分别执行定向检查，最终提交以 PR HEAD 和对应 CI 为准。生产仍是前后端 `ddbf9f14`（前端版本 `20261005-ddbf9f1`），已有部署证据见下方历史记录；本期 readiness 改动尚未发布。

- **已实现并有代码验证**：RD-01 历史 notes 按候选创建时点核对，当前模型工具按本轮时点核对；RD-02 可见主证据、事实/待核实信息及北京时间展示；RD-03 只在查询/详情模型边界移除可恢复的重复链接，HTTP 与持久化完整证据保留；RD-04 发布说法的否定范围、已知片名与中英文混排列表核对。上述代码已集成并经过独立审查；单测与局部浏览器证明不等于真实业务验收。
- **已上线但仍待本期验收**：此前的个人查询、候选引用、保存回执、资料页、notes/零结果诊断/热门口径。不能把这些已有能力计作本期新增成果，也不能用旧批次验收替代 RD-05。
- **RD-05 仍待完成**：独立 checker 已集成，真实只读来源及初始状态已私下封存；独立真实基线已实际执行 Q02、Q11、Q13 两步、Q14-A、Q16，共 6 个 Azure run。Q01～Q20 的改后完整五层验收尚未完成；基线 Q16 的五层预锁检查通过；其余基线暴露的默认条件偏差、采集器缺口及未执行部分均保留，不能计为改后矩阵通过。
- **本地验证进度**：扩展全套 SQLite/PostgreSQL 4,312 通过、21 跳过、0 失败；默认条件说明后追加定向双库及托管副本检查 101 通过。宿主回归 416 通过；前端 check 通过，常规套件 2,848 通过，45 个 reader 集成项另用真实 PostgreSQL 全部通过。资料页真实本地 E2E 6 通过；本地模型刷新/取消通过，保存丢响应重试在保留两次旧测试定位失败后，用已有结果、无新增模型调用完成独立恢复验收。
- **投影工程目标未全面达到**：原固定合成 baseline 字节降幅为 7.79%，未达 20% 目标；另一个重复链接密集样本约 26%，单独记录，不能替代原 baseline。完整事实优先，不把字节降幅推算成 token、成本或时延改善。
- **RD-07 审计结论为 BLOCKED**：Trends canary 未达到原阶段门槛，GSC 没有生产 collector/批次证据，见 [趋势雷达就绪审计](trends-readiness.md)。这阻止雷达进入下一阶段，不阻止已有个人选剧功能；本期未开启观测开关或触发采集。
- **计划但未实施**：雷达下一阶段、团队协作/排期、外部写入不属于本期实现范围。手工 CLI 部署的现有证据不保证推 main 自动发布。
- **下一步**：完成 PR 最终 CI 与证据整理；发布授权后再将集成源码部署到指定验收目标，执行改后真实矩阵并独立判读。代码交付不等于日常使用收尾或生产验收完成。

详细状态见 [本期验收增量](acceptance.md#2026-10-05-readiness-集成验收增量未发布) 和 [readiness 规格](../plans/2026-10-05-pick-workbench-readiness-spec.md)。历史记录保留各自日期、版本和当时限制，不代表当前完成状态。

---

# 2026-09-25 状态（历史）

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
- **切 cron 未完成（2026-09-29 15:46–16:24 UTC）**：G2 定了 `b_only`、`H`、`user`（计划第 8.1 节），按 packaging.md 第 5 节第 4 步重走，两轮都在第二次部署前重跑守卫时被拒，服务仍停在自检配置，没有 cron 计划，今晚 canary1 不跑，S7 没有开始。
  - 变量：`PICK_OBS_TRENDS_ROUTE` 由暂定的 `both` 改为 `b_only`（`--skip-deploys`，随下面的部署生效）；`PICK_OBS_TRENDS_GRANULARITY=H` 显式保留；`PICK_OBS_TRENDS_PACE` 不设（即 `user`）；其余变量未动。
  - 市场对照词表：用户没有批准小样本验证，按现有 `canary_controls.json` 冻结（计划第 14.3 节两路的 B），sha256 `c2689da2cf597699ca828596b3f238ae5a0ab5789b4a3ada9d64561539f96f64`，源码与托管副本相同。
  - 第一轮：fbda69ff，守卫 15:46Z 通过（生产迁移头 0007，链头 0007），`apply` 自检配置，部署 `fb8a2cc4-f645-436a-afd4-4b582558e70b`，`check --deployment` 一致，自检与预检两行都过。之后重跑守卫被拒：`ggwork/main` 前进到 7d33058（本文件补记 gateway fbda69f 上线）。
  - 第二轮：7d33058，守卫 16:09Z 通过，部署 `3acd3d97-29b2-414e-996d-8b97a5216e28`，`check --deployment` 一致，两行都过。之后重跑守卫又被拒：`ggwork/main` 前进到 98a683d（本文件补记前端 7d33058 上线）。离 17:00 只剩约 35 分钟，按第 7 节不再重走。
  - 两轮的自检行相同：`selfcheck ok: collector=obs-collector-v1 head=0007 role=pick_observer package=sha256:811dafd9f45a20f49a5e9c7007b954e673abca5eddaaf8c83dbe01c7ab7b3e94`，摘要等于检出里源码与托管副本算出的值，`at` 在镜像的 site-packages 下，没有「这个服务不读」的警告。预检也相同：target_date 2026-09-30、`canary1`、`user` 节奏，`reasons` 与 `refused_by` 为空，计划请求 204（下限 176）、92 个单元，正对照匹配 9（下限 8，09-28 是 10），共享剧库批次 12,508 部、近期 227 部，无熔断估算覆盖率 1。`missing_first`（只记 identity）5 个：
    - `realshort-pick` `ZmxhcmVmbG93LTY2NzkxNQ` en；`cmVlbHNob3J0LTZhOGQyZDc5ODdjOTM0M2M3YTA1YTc5OA` es；`cmVlbHNob3J0LTZhOGU3YzIyN2NmOTI1YzI0YTA1NDIzMw` pl；`cmVlbHNob3J0LTZhNzk0YjFkNGYwM2ZmNGM1YzA2OTk0OQ` es；`cmVlbHNob3J0LTZhNmRhMmMwYmQ1ZDhlYmYxNzBmMTU4OQ` zh-hant
  - 下一步：在 UTC 02:00–17:00 内从最新 main 的干净检出重走一遍，守卫仍带首次记录参数；两次部署之间约 20 分钟，这段时间里别的会话推 main（哪怕只改本文件）都会让第二次部署前的守卫被拒，要先和在推 main 的会话约好这段时间不推。切到 cron 并核对之后才追加守卫记录行；这两轮守卫打印的记录行都不追加。
- **切 cron 完成（S5/S6 收尾，2026-09-30 11:21–11:41 UTC）**：前一天两轮没切成（上一条）。这次从 `ggwork/main` 9667ea36 的干净检出重走 packaging.md 第 5 节第 4 步，守卫带首次记录参数通过（生产迁移头 0007，链头 0007）。开工前请两个可能推 main 的会话暂停到 12:20 UTC，两个都回复照办。变量沿用前一天：`PICK_OBS_TRENDS_MODE=canary1`、`PICK_OBS_TRENDS_ROUTE=b_only`、`PICK_OBS_TRENDS_GRANULARITY=H`、`PICK_OBS_TRENDS_PACE` 不设（即 `user`）；市场对照词表 sha256 `c2689da2cf597699ca828596b3f238ae5a0ab5789b4a3ada9d64561539f96f64` 不变。
  - 自检配置部署 `ec8366f5-36bc-444a-ae24-4a959f60977f`：`check --deployment` 一致；`selfcheck ok: collector=obs-collector-v1 head=0007 role=pick_observer package=sha256:e2c4a3e3e92f181f9e0f9085302c3fc5def761ba8743f90c436b651c1cec619a`，摘要等于检出里源码与托管副本算出的值，`at` 在镜像的 site-packages 下，没有「这个服务不读」的警告。预检：target_date 2026-10-01、`canary1`、`user` 节奏，`reasons` 与 `refused_by` 为空，计划请求 204（下限 176）、92 个单元，正对照匹配 9（下限 8），共享剧库批次 12,519 部、近期 191 部，无熔断估算覆盖率 1。`missing_first`（只记 identity）5 个：
    - `realshort-pick` `ZmxhcmVmbG93LTY2NzkxNQ` en；`cmVlbHNob3J0LTZhOGU3YzIyN2NmOTI1YzI0YTA1NDIzMw` pl；`cmVlbHNob3J0LTZhOGU3YzIyN2NmOTI1YzI0YTA1NDIzNQ` ro；`cmVlbHNob3J0LTZhNzk0YjFkNGYwM2ZmNGM1YzA2OTk0OQ` es；`cmVlbHNob3J0LTZhNmRhMmMwYmQ1ZDhlYmYxNzBmMTU4OQ` zh-hant
  - 切 cron 的前两次 `railway up`（`ea950856-80a9-4fcc-9260-c2e8903de5af`、`1dfc5db3-f0f4-42b3-89a0-0f21388c8068`）都在 Railway 生成代码快照时失败（部署元数据 `configErrors`：「Failed to create code snapshot … try again」），没有构建、没有运行，CLI 报 `operation timed out`；Railway 状态页当时全部正常。每次失败后都把服务设置 `apply` 回自检配置。第三次重试成功，所以是 Railway 的瞬时故障，与 cron 配置无关。
  - 其间试过用 API `deploymentRedeploy`（`usePreviousImageTag`）重部署 ec8366f5（新部署 `8b309b63-d98b-4d20-aa79-7d67e24ae5a7`），想不重新上传就换上 cron 配置。结果不行：重部署沿用原部署的清单（自检启动命令、没有 cron 计划），只是把自检与预检又跑了一遍（两行都过）。**重部署拿不到新的服务设置**，切换配置只能靠新的 `railway up`。
  - cron 配置部署 `4d1d6e5b-c62a-4859-96f4-a8b4e30818b1`（同一检出、同一提交；上传前守卫 11:39:46Z 重跑通过）：`check --deployment` 一致，cron 计划 `*/30 17-23,0-1 * * *`、重启 `NEVER`、没有健康检查、Dockerfile 是 gateway 那一份；部署后没有立即运行（没有日志）；镜像 config 摘要与 ec8366f5 相同（`sha256:1b0572f90b71…`），今晚跑的就是上面核对过包摘要的镜像。服务的 source 为空、没有 repoTriggers，没开推送自动部署。
  - 金丝雀的用途按 [简化版范围](../plans/2026-09-30-trends-radar-simplified-scope.md) 第 10 节缩成只验证出口：累计 3 个有效夜晚（通过负载闸门并实际发出请求），没有验证码或同意页，没有因限流被停用；出现验证码或同意页就停 cron。
  - canary1 首晚：2026-09-30 21:00 UTC 起跑（target_date 2026-10-01），S7 开始。17:00 那次触发的日志应是「还没到 canary1 的起跑时刻」；21:00 起的 `selfcheck ok` 一行的 `package=` 应与上面相同。首晚之后在 gateway 容器里看 `python -m ggwork_pick.observe.trends status`。
- `pick-deploy-guard target=cron:trends commit=9667ea36e18c9397604a0acb2a2c94ef4fe0d658 prod_head=0007 chain_head=0007 at=2026-09-30T11:39:46Z`


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
  - PR #16 合并后再经守卫部署一次 gateway：2026-09-30 随 PR #24 一起上线（见文末「答案核对与知识检索修复」），两个 PR 都没改前端；
  - Vercel 项目 `ggwork`：2026-09-30 按用户要求先断开 Git 集成，再用 `vercel project rm` 删除，ggwork-nine.vercel.app 现在返回 404。本团队里已经没有项目接着这个仓库，推 main 不会再触发任何 Vercel 构建。
- `pick-deploy-guard target=gateway commit=fbda69ff3cea78be5bbcbff6f2131dfbe7dfa8d5 prod_head=0007 chain_head=0007 at=2026-09-29T15:20:55Z`
- `pick-deploy-guard target=frontend commit=7d33058427c457446a8a676e317da391aa824144 at=2026-09-29T16:09:56Z`

## 答案核对与知识检索修复（PR #16、#24，2026-09-30 上线）

- PR #16（9667ea36）与 PR #24（3040bd87）一起上线。
  - PR #16：答案核对跨句借主语、账号查询误报；知识检索多文档互相挤掉。
  - PR #24：#16「已知局限」里的 MEDIUM。答案核对认全逗号分隔的片名列表、排除说法与更多"没发过"说法，构造的长回答也按线性时间核对；知识检索每条按自己的元数据计费，带 `truncated` 与 `omitted`，并修掉 casefold 后切片的偏移。
  - 两个 PR 都只改 `ggwork_pick` 与托管副本，没有新迁移，迁移头仍是 0007；依赖、`uv.lock`、Dockerfile 不变。前端没改，不用发布；cron 不用重部署。
- gateway 经守卫后，从 `git archive` 导出的目录 `railway up`，部署 `fd2cf0eb-2530-4319-90e1-629cafe3dc10`，SUCCESS。导出目录是 3040bd87 的 3,845 个跟踪文件。
  - 第一次上传（11:56Z）连接被对端重置，只留下一个没有构建、没有运行的 FAILED 部署 `bb0efe00-c93c-4cf6-b59f-56e2e6949df6`，生产不受影响。重跑守卫后第二次上传成功，下面的记录行是这次的。
- 部署前在守卫检出（3040bd87，检出自己的 venv）里做四格的 gateway 一列：
  - 扩展全套 3,758 通过、21 跳过，含 `test_managed_copy`。测试库是一次性的全 scram PG 17 加 SQLite，跳过的都是方言专属，没有 `PICK_TEST_PG_URL is not set`；
  - gateway 四格的 4 个文件 35 条通过，两种库都有，0 跳过。
- 部署后核对：
  - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`。没有 Traceback，没有 `service start() failed`，也没有 `Running upgrade`。唯一的 WARNING 仍是 GitHub webhook 路由未挂载。
  - 容器内（`railway ssh`）：
    - `observe.selfcheck`、`observe.grants` 能导入；
    - site-packages 里的 `answer_check`、`knowledge_excerpts` 是新代码；
    - `regrant --check` 授权齐全：schema 7、表 31、列 4、序列 12，已发布镜像版本 5 个（pickm_v000001、v000010、v000012、v000013、v000015），与上次相同。
  - 守卫读到的生产迁移头是 0007。
  - 以登录用户在用户的 Chrome 里核对（12:10–12:20 UTC）：
    - `/api/pick/sync` 返回 200，字段 configured、current、runs、mirror；「选剧资料」正常打开（选剧 12,508、全部剧库 74,295、发布记录 309）。
    - 旧卡格：打开已有候选卡的会话「帮我看看最近美国 US 地区热门…」，「查看候选」展开 5 部，控制台没有错误。
    - 存量快照格：「我的选剧」为空，`/api/pick/selections` 返回 200、0 条，没有可展开的旧条目。
    - 选剧对话：按 KalosTV 日榜找 5 部英语剧并核对团队发布记录，run 正常，5 部都"未匹配到发布记录"。答案核对给了一条"发布记录没有对上"的提示，是误报：回答写的是"不能据此断言它们从未发布"。
      - 原因是 `_NEGATING_PREFIX` 只认"不能"之后 6 字以内的免责，"不能据此断言它们从未发布"隔了 7 字。fbda69f 起就这样，#16 开始按全部记录判断"它们/这 5 部"之后才显出来。留作后续小修。
    - 「剧场规则」模板问 DramaBox：`pick_search_knowledge` 返回规则文档整篇（第 1 行起、134 行、2,786 字，没有 `truncated`、`omitted`，整条结果 3,347 字），回答列出 YouTube、报备、标签（未知）与核对日期。
    - 核对时新建了两个会话（KalosTV 那一问与 DramaBox 规则），留在用户的会话列表里。
- `pick-deploy-guard target=gateway commit=3040bd877a5cb34f9105756680e3ab0b8e0d3e02 prod_head=0007 chain_head=0007 at=2026-09-30T11:58:46Z`

## 飞书个人授权（lark-cli）第一期：只读文档与消息（PR #22，2026-09-30 上线）

- 起因：PR #20 把飞书 CLI 标成 `hidden`，个人授权单独做。设计见 [lark-personal-auth.md](lark-personal-auth.md)，定的是方案 A：gateway 内一个专用的只读 `lark_cli` 工具，第一期开放文档类与消息。
- 分支 `feat/lark-personal-auth` 20:24 从 ggwork/main 6919eb5 切出，22:05 变基到 #20 合并后的 fbda69f，变基没有冲突。推送前又变基到 b5c9dd9，main 上只多了 #20 gateway 上线的记录。和 #20 的整合（插件放行规则、目录 `hidden`）放在变基之后补。写中间件的会话 22:06 留下两个未提交文件后就不在了，所以先把整个分支打包，再动历史。包里有一个 WIP 提交，还有变基前的原提交。包存在 `~/.gstack/projects/ggwork-deerflow/artifacts/lark-personal-auth-2026-09-29.bundle`。
- 改动：
  - 镜像：`Dockerfile.pick-gateway` 内置 lark-cli v1.0.96，版本和两种架构的 sha256 写死；新增无特权用户 `larkrun`。设置了 `DEER_FLOW_LARK_CLI_RUN_AS` 时，入口会去掉 `DEER_FLOW_HOME` 的其他用户权限（755→750）。
  - 上游 `lark_cli.py` 加固定版本模式：不调 npm，不查 latest，技能用二进制内置的，子进程用精简环境。另加 `peek_lark_app_config`，只读不写。
  - `lark_cli` 工具：argv 白名单，只放行 `Risk: read`，以 `larkrun` 身份运行，凭据只给副本。
  - 选剧对话：
    - 只有已在能力中心连接飞书的用户能看到 `lark_cli`，每轮单独 8 次；
    - 调用过它就算本轮读过外部内容，按 #20 的读后拦截处理。lark-cli 自带的帮助、schema 和技能文本不算；
    - 运行配置注册了 `lark_cli`，工具组 `lark`。
  - 能力中心：飞书 / Lark 取消 `hidden`，文案改成第一期的只读范围。生产前端的目录从 gateway 读，所以这一项跟着 gateway 部署生效。
- 独立审查：CRITICAL 0。
  - HIGH 1，已修：值前加空白、`-q` 错位，这两种写法能让 lark-cli 读本地文件。审查用本机 lark-cli 1.0.93 复现过。现在值去掉首尾空白后再检查，jq 表达式只有写成 `--jq=` 一个参数时才豁免。
  - MEDIUM 3，都已修：
    - 连接检查原来在每次模型调用时都写一遍凭据目录，现在不写了；
    - 飞书命令改在工具自己的 4 个线程上排队，不再占网关共用的默认线程池；还没开始就被取消的调用不会再执行；
    - 先限时拿用户自己的凭据锁，再拿全局槽。某个用户正在授权时，只有他自己的命令在等，到本轮截止就放弃；其他人不受影响。上游 `lark_credential_lock` 因此加了可选的 `deadline`，不传时行为不变。
  - LOW 7，都已修：
    - 目录文案原来承诺了日历，改成第一期的只读范围；
    - `skills read` 这类指南命令原来会误触发读后拦截，现在不算外部内容；
    - 运行锁文件打不开时，原来抛原始异常，现在返回「不可用」；
    - 「不可用」时的说明里带着网关路径、权限和用户名。现在只给模型一句通用说明，原因写进网关日志；排队超时、该用户正在授权这两种情况仍把原话告诉模型；
    - 输出原来整段读进内存再截断。现在每个流最多读入约 160 KB，其余读出后丢弃，退出码照实；超时时杀掉整个进程组；
    - 执行层透传的代理变量补上了小写形式；
    - 名字以 `pick_` 开头的 MCP 工具原来计入选剧额度，还能绕过读后拦截（#20 起就这样）。现在只有配置里的选剧工具计入选剧额度。
- 测试（最后一轮，含全部审查修复；三段按顺序跑，没有并发负载）：
  - 扩展全套：SQLite 加全 scram 的 PG 17，3,923 通过、21 跳过，跳过的都是方言专属，含 `test_managed_copy`；
    - 前一轮在并发负载下，mirror 有一条计时用例超了门槛：0.76 秒对 0.65 秒。本分支没碰 mirror，单独重跑 5 次都通过，这一轮也通过了；
  - 新增的并发用例（限时凭据锁、授权中不拖累他人、专用线程排队与取消）在负载下连跑 10 次，都通过。进程组用例还做了反证：只杀子进程时孙进程会活下来；
  - 入口、JSON 净化、create_user：SQLite 和 PG 共 108 通过；
  - 后端全套：18,504 通过、80 跳过，1 条失败：`test_local_sandbox_provider_mounts.py::TestReadOnlyPath::test_bash_write_to_projected_copy_does_not_mutate_source`，ggwork/main 上原本就失败（见 PR #13 一节），本 PR 没碰沙箱；
  - blocking-io 149 通过；ruff、`uv lock --check`、agent guidance 通过；
  - 前端：
    - format、lint、typecheck、build 通过；
    - 单测 2,797 通过、45 跳过；
    - e2e 默认套件 282 通过，auth 套件 6 通过。修复后重跑了能力中心、集成、插件图标三个文件，23/23；最后两轮都没改前端。
- 合并与上线（2026-09-30）：
  - PR #22 于 12:22Z 合并，合并提交 a6b8bcb1。合并前两次把 main 并进分支（先是 #16 的 9667ea36，再是 #24 与文档到 39d4d94a），都没有强推。
  - 前端只改了静态演示用的目录快照和测试，生产前端的目录从 gateway 读，所以不用发布前端。合并也不会触发 Vercel 构建。没有新迁移，迁移头仍是 0007，cron 不用重部署。
- gateway 经守卫部署 `6f8dece5-b45a-4e74-ac34-e6a001e88c57`，SUCCESS：12:33:15Z 开始构建，12:34:03Z 上线。生产此前是 3040bd87（含 #16、#24），这次只多出本 PR 的改动。
  - 前三次上传都失败了：从完整的 `git archive` 导出目录上传，压缩后约 27MB。两次上传超时，一次 Cloudflare 524。每次各留下一个没有构建、没有运行的 FAILED 部署（`775483cb-4cd6-4372-9eb8-817d790d02fd`、`343f6341-6b50-492d-8493-976e4479f356`、`402e5f6b-a442-4c97-a96a-f321605624e9`），生产不受影响。每次重传前都重跑了守卫。
  - 第四次只导出镜像用到的路径：`backend`、`docker`、`config.pick.example.yaml`、`skills/public/pick-drama`、`railway.toml`、`.dockerignore`。共 2,088 个文件，压缩后 7.3MB，每个路径都和完整导出比对过，逐字节相同。Dockerfile 只 COPY 这些路径，所以镜像内容与完整导出一样。上传时设了 `RAILWAY_HTTP_TIMEOUT=600`，29 秒传完。下面的记录行来自这次上传前的守卫。
  - 构建日志里，lark-cli 发布包的 sha256 校验通过（`/tmp/lark-cli.tar.gz: OK`）。
- 部署前在要部署的树上做四格的 gateway 一列：
  - 用的是分支 5baebfc0 的树，它和 a6b8bcb1 只差 progress.md。差的这部分，在守卫检出 a6b8bcb1 里重跑了读 progress.md 的用例（守卫、cron 部署流程、上线计划）和托管副本用例，135 通过、0 跳过；
  - 扩展全套 3,982 通过、21 跳过，含 `test_managed_copy`。测试库是一次性的全 scram PG 17 加 SQLite，跳过的都是方言专属，没有 `PICK_TEST_PG_URL is not set`；
  - gateway 四格的 4 个文件 35 条通过，两种库都有，0 跳过；
  - 入口、JSON 净化、create_user 108 通过；
  - GitHub Actions 已恢复，5baebfc0 上 pick-workbench-tests 与 pick-board-integration 都通过。
- 部署后核对：
  - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`。没有 Traceback，没有 `service start() failed`，也没有 `Running upgrade`。唯一的 WARNING 仍是 GitHub webhook 路由未挂载。
  - 容器内（`railway ssh`）：
    - `lark-cli version 1.0.96`，`larkrun`（uid 999）存在；`DEER_FLOW_LARK_CLI_PINNED_VERSION=v1.0.96`，`DEER_FLOW_LARK_CLI_RUN_AS=larkrun`；
    - `/data` 已由入口收成 750（root:root）；
    - 运行配置 `/data/pick-runtime.yaml` 注册了 `lark_cli` 与工具组 `lark`；
    - `lark_tool`、`lark_runner`、`lark_policy`、`lark_credentials`、`observe.selfcheck`、`observe.grants` 都能导入，来自镜像的 site-packages；
    - 固定版本探针认出 `/usr/local/bin/lark-cli` 1.0.96，能列出内置技能 28 个；能力目录可见 13 项，含飞书 / Lark；
    - `regrant --check` 授权齐全：schema 7、表 31、列 4、序列 12，已发布镜像版本 5 个，与上次相同。
  - 守卫读到的生产迁移头是 0007。
  - **待用户以登录用户核对**：
    - 能力中心的飞书 / Lark 显示「已安装版本：v1.0.96」，且不提示新版本；
    - 用自己的账号走「连接飞书 → 授权」（PersonalAgent 应用注册可能要租户管理员放行），再在对话里读一篇自己的文档，或搜一次消息；
    - 读完飞书内容后让助手发飞书群通知，应该先要你确认，而不是直接发；
    - `/api/pick/sync` 返回 200，一次选剧对话正常；四格手工格：有旧卡的会话能展开，「我的选剧」里改动前保存的条目能展开。
- 后续：写操作放第二期。
- `pick-deploy-guard target=gateway commit=a6b8bcb1f7871f32c8bba0e5993f87a5d60466f4 prod_head=0007 chain_head=0007 at=2026-09-30T12:32:02Z`

## 选剧 Agent 第一批修复（2026-09-30）

- 起因：2026-09-30 对「对话框敲一行需求后整套 Agent 如何运作」做了端到端评估（基线 `c91b647a`，gpt-6-astra 五路并行审计交叉核实），结论是当作候选池筛选加个人保存工具基本符合，当作每天的主力工作台还不符合。评估列了五件「改动小、先堵住会误导人的口子」的事，本批先修这些。评估报告没进仓库。
- 评估期间 `ggwork/main` 前进到 `9667ea36`（PR #16）。按新基线重新核对：第 3 项「发布核对整轮标记」（做过一次发布筛选后，没用书名号点名的「没发过」不再核对）已由 PR #16 重写的 `answer_check.py` 修掉，总括性的「都没发过」按本轮返回的每条记录判断；本批不再动它。其余四项仍成立。
- 改动（一个 PR，四个改动各一个提交，另有托管副本一个提交）：
  - 选剧工具豁免宿主工具输出预算。宿主 `ToolOutputBudgetMiddleware` 默认把超过 12,000 字符的工具结果外置并换成文本摘要（30,000 以上无论如何截断），候选结果 8 到 10 部就超过：候选卡解析失败显示「选剧查询未完成」，模型只看到摘要并被指引用被闸门拒绝的 `read_file`。用宿主函数实跑确认过（10 部 13,242 字符被替换），线上没复现。`config.pick.example.yaml` 新增 `tool_output.exempt_tools`，保留宿主默认的 `read_file` 一对，加五个选剧工具；`pick_entrypoint` 直接读这份模板，生产同步生效。给模型的 JSON 瘦身（去掉 `source_ref`、`citation_id`、`detail_url` 等）留到后面。
  - 查询与计数返回 `data_notices`。资料页有四类过期提示，Agent 一条没有。新模块 `ggwork_pick/freshness.py` 用同样的阈值给出句子：批次采集超过 14 小时、剧单导入超过 36 小时、问到某张榜（`signal_kind` 是 kd/qc/qr/kw 或 `hot_only`）时该榜最新一期在采集时已超过 2 天（周榜 14 天）。只在有提示时出现，只给模型看，不进 `data_as_of`（前端 strict schema）也不进存储快照；提示词与技能各加一句要求如实转述。详见 `realshort-sync.md` 的「给模型的数据时效提示」。
  - 候选卡不再被步骤折叠，最新候选按线程读取。一轮里的多个工具调用合成一组，默认只渲染最后一个；候选卡不是最后一个调用时不挂载，也就不登记为「最新候选」，下一条追问绑错卡或不带引用，对比类提问只露最后一张卡。`message-group.tsx` 把选剧卡当作助手文本一样始终可见；`pick-context.tsx` 新增 `useObservePickThread`，用此前没有调用方的 `listPickResults` 按线程读服务端候选并登记，回答结束时重新读取，`ChatBox` 调用它。
  - 换一批排除整条候选链。排除集只含个人已选和父卡条目，A → B → C 时 C 又给回 A。现在沿 `parent_result_id` 向上走整条链并入各级条目；只并条目不并各级 `excluded_json`（那里混着当时的个人已选）；链在另一线程的祖先处停下，最多 100 级。
- 验证（本机，GitHub Actions 状态见推送后的检查）：
  - 扩展全套 SQLite：2,942 通过、822 跳过（跳过均为 PostgreSQL 专属），托管副本刷新前只有 `test_managed_copy` 失败，刷新后通过。PostgreSQL（`initdb --auth=scram-sha-256 -E UTF8` 的一次性 PG 17）：3,744 通过、21 跳过。第一次 PG 跑用 `--no-locale` 建库，服务端编码成了 SQL_ASCII，35 失败 12 错误全是环境问题（psycopg 返回 bytes、ICU 排序规则不存在），重建 UTF8 集群后全过。rebase 到 `3040bd87`（PR #24）后两种库合跑一次：3,777 通过、21 跳过；再 rebase 到 `a6b8bcb1`（PR #22）后：4,001 通过、21 跳过。
  - 宿主 `test_pick_cloud_entrypoint.py` 与 `test_compose_default_bind_host.py` 67 通过；`ruff check`、`ruff format --check` 通过；agent guidance 检查 0 错误 0 警告。
  - 前端 `pnpm check` 通过；`pick`、`messages` 目录单测 297 通过；改动文件 prettier 通过。
  - 托管副本 `diff -rq` 为空，`uv.lock` 不变。
- 上线后由用户在工作台核对：问一次「给我 10 部英语剧」看卡片是否完整；问一次 KalosTV 日榜看回答是否转述时效提示；连续换两批看第三批是否还回到第一批。e2e-pick 需要真实模型和数据库，本批没跑。
- 后续（第二批候选）：条件摘要补渠道、确认可发、标签、关键词；0 结果诊断和 `data_notices` 上卡；重新生成和编辑重发带 `pick_reference`；候选条目投影保留 tags、listed_at、channel_rules；回答核对扩到书名号以外的剧名。
- 上线（2026-10-05，UTC）：PR #26 合并为 `4c4a3853`，gateway 与前端都从这个提交经守卫上线。
  - 守卫之前，在部署检出（`4c4a3853`）上做完四格的测试部分：
    - gateway 一列：扩展全套 4,001 通过、21 跳过（一次性全 scram、UTF8 的 PG 17 加 SQLite，跳过原因里没有 `PICK_TEST_PG_URL is not set`）；四个点名文件 35 通过、0 跳过，每个文件两种库都有。
    - 前端一列：rollback matrix 5 行 ✓（四格加 09-28 的热门卡格），合同夹具 12/12，全量 2,801 通过、45 跳过，typecheck 通过。
  - gateway 经守卫后，从 `git archive` 导出的目录 `railway up`，部署 `501f3edc-2b5d-4783-b79f-226b961d3d73`，SUCCESS，一次上传成功。导出只含镜像用到的路径（`backend`、`docker`、`config.pick.example.yaml`、`skills/public/pick-drama`、`railway.toml`、`.dockerignore`，2,091 个文件）。迁移头仍是 0007，没有新迁移。
    - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`，没有 Traceback，没有 `service start() failed`。
    - 容器内（`railway ssh`，只读）：
      - site-packages 里有 `ggwork_pick.freshness`（阈值 14、36，榜单 kd、qc、qr、kw），`CHAIN_LIMIT` 是 100，`PICK_INSTRUCTIONS` 含 `data_notices`；
      - 运行配置 `/data/pick-runtime.yaml` 的 `tool_output.exempt_tools` 含五个选剧工具和 `read_file` 一对；
      - `observe.selfcheck`、`observe.grants` 能导入；
      - `regrant --check` 授权齐全：schema 8、表 32、列 4、序列 12，已发布镜像版本 6 个。
  - 前端经守卫从 `git archive` 导出的目录发布，部署 `dpl_AbuRYui81moWRUkViBhkkRqQ4Rxw`，READY，生产别名 ggwork-deerflow.vercel.app 指向它。导出目录里是 `frontend/` 的 1,034 个跟踪文件，另外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20261005-4c4a385`。
    - 未登录：`/` 307 到 `/workspace`；`/workspace`、`/workspace/chats/new` 307 到 `/login`；带 `RSC: 1` 的请求只返回到 `/login` 的 `NEXT_REDIRECT`；`/login` 带 `X-Robots-Tag: noindex, nofollow`。
  - **待用户以登录用户核对**：
    - 关于页的版本号是 20261005-4c4a385；
    - 四格手工格：有旧卡的会话能展开；「我的选剧」里改动前保存的条目能展开；
    - 本批三问：「给我 10 部英语剧」卡片完整；问 KalosTV 日榜，回答转述时效提示；连续换两批，第三批不回到第一批。
- `pick-deploy-guard target=gateway commit=4c4a385339d8f1959ce9b9329f4061f5feff48aa prod_head=0007 chain_head=0007 at=2026-10-05T08:24:46Z`
- `pick-deploy-guard target=frontend commit=4c4a385339d8f1959ce9b9329f4061f5feff48aa at=2026-10-05T08:27:50Z`
- 飞书授权误报修复上线（2026-10-05，UTC）：PR #28 合并为 `c11d7212`，gateway 与前端都从这个提交经守卫上线。
  - 起因：能力中心飞书授权弹出 `lark-cli exited with code 3`。lark-cli 在已保存 token、但申请的 scope 没有全部授予时，在 stdout 打出 `authorization_complete` 事件并以退出码 3 静默退出，gateway 把它当成失败。生产上该用户 08:19:53Z 已授权成功（223 个 scope）。修复后部分授予按成功处理，响应带 `missing_scopes`，前端改为黄色提醒。
  - 守卫之前，在与 `c11d7212` 代码树相同的 `35500150`（PR 分支头，`git diff` 为空）上做完四格的测试部分：
    - gateway 一列：扩展全套 4,001 通过、21 跳过（一次性全 scram、UTF8 的 PG 17 加 SQLite，跳过原因里没有 `PICK_TEST_PG_URL is not set`）；四个点名文件 35 通过、0 跳过，每个文件两种库都有。
    - 前端一列：rollback matrix 5 行 ✓，合同夹具 12/12，全量 2,801 通过、45 跳过，typecheck 通过。
  - gateway 经守卫后，从 `git archive` 导出的目录（镜像用到的路径，2,091 个文件）`railway up`，部署 `27e27a61-399e-4712-ba37-e270a871cd8f`，SUCCESS，一次上传成功。迁移头仍是 0007。
    - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`、`Application startup complete`，没有 Traceback，没有 `service start() failed`。
    - 容器内（`railway ssh`，只读）：`LARK_CLI_EXIT_AUTH`、`_run_lark_cli_json(allow_missing_scopes)`、`LarkAuthCompleteResult.missing_scopes`、响应模型的 `missing_scopes` 都在；lark-cli 1.0.96；`observe.selfcheck`、`observe.grants` 能导入；`regrant --check` 授权齐全：schema 8、表 32、列 4、序列 12，已发布镜像版本 6 个。
  - 前端经守卫从 `git archive` 导出的目录发布，部署 `dpl_3qq6URyqK9osmNkYPeXrHoG2NCeY`，READY，生产别名 ggwork-deerflow.vercel.app 指向它。导出目录里是 `frontend/` 的 1,033 个跟踪文件，另外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20261005-c11d721`。未登录时 `/` 307 到 `/workspace`，`/workspace` 307 到 `/login`。
  - **待用户以登录用户核对**：关于页版本号是 20261005-c11d721；在能力中心勾选业务域重新授权，部分授予时看到黄色提醒（列出缺失数量与前 3 个 scope），对话框关闭、卡片显示已连接；四格手工格同上一批。
- `pick-deploy-guard target=gateway commit=c11d72121f975f4de9bda4815f138a739818825d prod_head=0007 chain_head=0007 at=2026-10-05T09:19:36Z`
- `pick-deploy-guard target=frontend commit=c11d72121f975f4de9bda4815f138a739818825d at=2026-10-05T09:20:15Z`

## 选剧 Agent 第二批修复（2026-10-05）

- 起因：第一批（PR #26）上线后，按 09-30 端到端评估的第二批清单接着修，外加三件顺手的小事。第 11 项（在 gpt-6-sol 上重跑验收）放在本批上线后单独做；第三批（模型 JSON 瘦身、回答核对扩到书名号以外的剧名等）还没开始。
- 改动（一个 PR）：
  - 新接口 `GET /api/pick/results/{id}/notes`：从结果自己冻结的剧库批次读出每个条目的 `tags`、`listed_at`、`channel_rules`（`item_facts`），以及查询工具当时给模型的 `zero_diagnosis`、`hot_scope`、`data_notices`。别人的结果和不存在的结果 404，批次被清理 410，存储的条件这版代码跑不了时只给 `item_facts`。结果本身的形状不变（前端 strict 解析），存储快照也不变。查询与详情工具给模型的条目同样多了这三个字段；提示词与技能各加一句「题材、上架日期、渠道能不能发按这三个字段答，没有就说资料里没有」。
  - 干净的最终回答也记一条空核对（`notes=[]`），工具调用回合仍不记。前端据此分清「核对过没问题」「没有核对记录」和「核对读取失败」。
  - 宿主系统提示词裁剪（`ggwork_pick/host_prompt.py`）：PickModelGate 在追加 `PICK_INSTRUCTIONS` 前，整段去掉 `skill_system`、`working_directory`、`subagent_system`，并从 `thinking_style`、`critical_reminders` 删掉提到 `read_file`、`present_files`、`/mnt/`、并行调用、委派子代理的条目。按选剧配置渲染真实提示词测：13,033 字符剩 9,194，开子代理时 21,227 也剩 9,194。
  - 前端候选卡：条件摘要补上渠道（只要确认可发 / 只排除明确禁用）、关键词、标签、换一批；读 notes 显示零结果逐项诊断（替换原来笼统的「放宽条件」）、热门口径、数据时效提示，以及每部剧的标签、上架日期、渠道规则。notes 404（含旧 gateway 没有这个接口）不显示，410 说明批次已清理，其他错误显示「依据说明暂不可用」，卡片其余部分照常。
  - 前端回答核对：有提示照旧显示警示框；空核对显示一行克制的「已核对剧名、保存与发布说法……其他内容以候选卡为准」；读取失败显示「回答核对暂不可用」；没有核对记录的旧回答不显示。
  - 重新生成和编辑重发沿用该轮发送时绑定的候选引用：发送时把 `pick_reference` 连同发送时的线程存进人类消息的 `additional_kwargs`（与知识范围快照同一做法，网关不剥这个键）；重新生成从该回答之前最近的、网关重放时也认作输入的人类消息取回（跳过 goal 续跑、摘要这类隐藏控制消息，人工输入卡的回复照旧算），编辑重发从被编辑的消息取回并写进替换消息。线程对不上（分支复制来的消息）或旧回合没存过，照旧不带。
  - 保存回执分清新存入、恢复（之前移出过，备注用这次的）和已在清单（备注未改）。
  - 选剧工作台不再提供 Pro/Ultra：选剧 Agent 拿不到计划模式的待办工具，也拿不到子代理，默认 Pro 却让每次运行都带 `is_plan_mode`（宿主还注入一段用不了的待办提示）。InputBox 新增 `planModes`（默认 true，上游行为不变），chat-page 在 PickProvider 里传 false：菜单隐藏 Pro/Ultra，输入框显示 Thinking，运行上下文里把存着的 Pro、Ultra 换成 Thinking。存着的偏好不改写，其他 Agent 的对话仍读到用户自己选的模式（选剧模型没开 `supports_reasoning_effort`，`reasoning_effort` 本来就不生效）。
- gpt-6-astra 审计（`codex exec -s read-only`，后端与前端各一轮）：
  - 后端：一个可复现问题，提示词用 `\r\n` 换行时裁剪全部失效（`>$` 匹配不上 `\r`），已修并加 LF/CRLF 两种用例；当前宿主只产生 LF，线上不触发。另指出空核对的新测试只跑 SQLite，已在 `test_routes.py` 的两种库用例里补上空核对的读写。notes 的 owner 隔离、批次清理、条件校验失败、identity 缺失、换一批、缓存行不被修改、GET 只有 SELECT 均核过没问题。
  - 前端：四个真实问题，均已修并补测试。① 分支对话复制了带 `pick_reference` 的消息，在分支里重新生成会把父线程的结果交给后端，后端按线程归属拒绝，整轮失败：引用改为连同发送线程一起存，线程对不上就不带。② goal 自动续跑插入的隐藏人类消息截断了引用查找：改为与网关 `_is_regenerate_human_message` 同样跳过控制消息。③ 第一版把选剧页限制后的 Thinking 经 `resolveThreadContext` 写回共享设置，之后进别的 Agent 对话也成了 Thinking：改为只在显示和运行上下文里换。④ 零结果诊断把数不出来（null）当 0，下了「单放宽一项都没有结果」的结论：改为每项都是 0 才说。notes 的缓存键、换卡与切换用户、404/410/网络错误/schema 失败的显示、工具调用回合不会挂空核对、模式 effect 不会循环，均核过没问题。
- 验证（本机，GitHub Actions 状态见推送后的检查）：
  - 扩展全套（一次性全 scram、UTF8 的 PG 17 加 SQLite）：4,020 通过、21 跳过（跳过原因里没有 `PICK_TEST_PG_URL is not set`）。托管副本刷新后 `diff -rq` 为空，`uv.lock` 不变，版本仍 0.3.0。
  - 宿主 `test_pick_cloud_entrypoint.py` 与 `test_compose_default_bind_host.py` 69 通过；`ruff check`、`ruff format --check` 通过；agent guidance 检查 0 错误 0 警告。
  - 前端 `pnpm check` 通过；全量单测 2,828 通过、45 跳过。
- 上线后由用户在工作台核对：问一个筛不出结果的条件看逐项诊断；候选卡每部剧下有标签、上架、渠道一行，问「第 1 部能不能发 YouTube」；勾选后发「保存第 2 部」再点重新生成，仍按同一张卡；模式菜单只有闪速和思考。e2e-pick 需要真实模型和数据库，本批没跑。
- 上线（2026-10-05，UTC）：PR #29 合并为 `ddbf9f14`，gateway 与前端都从这个提交经守卫上线。合并前 main 前进到 `8b0d8374`（PR #28 飞书授权修复的上线记录），在 PR 分支上合并 main 解决了 progress.md 的冲突（两边都保留），合并后 CI 两项全绿。
  - 守卫之前，在与 `ddbf9f14` 代码树相同的 `ec0c875a`（PR 分支头，`git diff` 为空）上做完四格的测试部分：
    - gateway 一列：扩展全套 4,020 通过、21 跳过（一次性全 scram、UTF8 的 PG 17 加 SQLite，跳过原因里没有 `PICK_TEST_PG_URL is not set`）；四个点名文件 35 通过、0 跳过，每个文件两种库都有。
    - 前端一列：rollback matrix 5 行 ✓，合同夹具 12/12，全量 2,828 通过、45 跳过，typecheck 通过。
  - gateway 经守卫后，从 `git archive` 导出的目录（镜像用到的路径，2,095 个文件）`railway up`，部署 `87a09df4-f27c-4a6c-b481-730b7c973f54`，SUCCESS，一次上传成功。迁移头仍是 0007，没有新迁移。
    - 启动日志有 `Extensions loaded: 1/1`、`Extension routers mounted`（含 `/api/pick/results/{result_id}/notes`）、`Application startup complete`，没有 Traceback，没有 `service start() failed`。
    - 容器内（`railway ssh`，只读）：`ggwork_pick.host_prompt`（三段、两张列表）、`ggwork_pick.item_facts`（tags、listed_at、channel_rules）都在，PickModelGate 调用 `pick_system`，`PICK_INSTRUCTIONS` 含 `channel_rules`，routes 里有 notes 路由；`observe.selfcheck`、`observe.grants` 能导入；`regrant --check` 授权齐全：schema 8、表 32、列 4、序列 12，已发布镜像版本 6 个。
  - 前端经守卫从 `git archive` 导出的目录发布，部署 `dpl_4UggWbpuuSXkfwTu4FGLWdgzypus`，READY，生产别名 ggwork-deerflow.vercel.app 指向它。导出目录里是 `frontend/` 的跟踪文件，另外只放了 `.vercel/project.json`。构建带 `NEXT_PUBLIC_APP_VERSION=20261005-ddbf9f1`。未登录时 `/` 307 到 `/workspace`，`/workspace`、`/workspace/chats/new` 307 到 `/login`，`/login` 带 `X-Robots-Tag: noindex, nofollow`，`/api/pick/results/x/notes` 返回 401。
  - **待用户以登录用户核对**：关于页版本号是 20261005-ddbf9f1；四格手工格同上一批；本批五问见上一节「上线后由用户在工作台核对」。
- `pick-deploy-guard target=gateway commit=ddbf9f14c882052d42dd0217e595a9c90c7694fc prod_head=0007 chain_head=0007 at=2026-10-05T09:47:41Z`
- `pick-deploy-guard target=frontend commit=ddbf9f14c882052d42dd0217e595a9c90c7694fc at=2026-10-05T09:50:34Z`


## 取消用量观察后续发布（2026-10-06）

- [PR #33](https://github.com/phananhson733-oss/ggwork/pull/33) 已合并，产品源码为 `148fff7b08d22c1112fd142e62f0076698862fd3`，与最终 CI HEAD `21c7d4d6` 的文件树一致。PR CI `37371039191` 的第 2 次执行和合并后 main CI `37375741963` 均 SUCCESS；第 1 次 hosted runner 获取失败记录仍保留，没有计为测试通过。
- Gateway 部署 `22828dc0-562a-4349-a177-b254c37d2c45` SUCCESS；健康检查通过，启动完成，无应用 ERROR 或 `service start() failed`。只读核对生产迁移头 `0007`，本次没有新增迁移；部署前后均无 pending/running 用户任务。限定源码核验中 1,144 个源码文件与 146 个已安装业务包文件全部匹配，新增用量 metadata 接口、模块导入路径和脚本迁移头核对通过。此证明不等同于整个镜像、供应商账单或其他服务的证明。
- 前端部署 `dpl_BLhV1GhZf4eFjr6wTT8CGCjKQv1C` READY；正式域名 `ggwork-deerflow.vercel.app` 指向该部署，普通 QA 浏览器 About 显示 `20261006-148fff7`。部署只上传 `git archive` 中的已跟踪前端文件及 `.vercel/project.json`。
- 最终本地宿主 offline 18,449 passed / 166 skipped / 3 deselected；blocking-I/O 149 passed。业务双库 4,351 passed / 21 skipped；前端 2,874 passed，另行真实 PostgreSQL reader 45 passed / 0 skipped，check/build 通过。早期失败轮次保持原记录。
- 专用普通 QA 只读快照与浏览器 canary PASS：旧十部候选的条目、来源时点与历史核对保持，四条已选记录的 identity、快照、备注、状态和版本保持，刷新后正常；新的「已记录 Token 用量」说明可读。所有导航 HTTP 200，About 首次可操作 5.66 秒、已选刷新首次可操作 2.525 秒，console/page errors 均为 0，没有新增模型 run 或业务保存。
- 验收脚本初轮因默认守卫拦住只读 threads/search POST 而 UNVERIFIED；独立核实端点与 store 均只读，加入严格路径、字段和 SDK 读取参数白名单后复验通过。原失败保留，不归为产品故障，也不放宽其他写入或模型调度。
- 新独立 manifest 下唯一一次取消运行已实际执行，Playwright 1 passed（21.5 秒）：Stop 前记录 `calls_started=1`，精确 POST cancel 返回 202，权威状态 interrupted；观测 finalized，刷新后字段不变，四条清单完整不变，运行用量未进入线程 metadata。供应商没有返回用量，known token 三字段均 null，coverage=unknown，原因保留 `provider_usage_missing` / `call_terminal_callback_missing`；没有终态模型回调，因此不制造 `calls_cancelled=1`。这是功能与本地观测持久化通过，不是 provider 用量或账单通过。统一账本现在 37/40，前 36 次 attempts 和原 reservations 原样保留；旧 Q19 不回填。
- 模型输入格式与雷达范围仍等待用户选择；团队协作和外部写入交付的是下一阶段规格，功能未实现。本节记录实际产品版本；后续文档提交不作为已部署产品 SHA。
- `pick-deploy-guard target=gateway commit=148fff7b08d22c1112fd142e62f0076698862fd3 prod_head=0007 chain_head=0007 at=2026-10-05T21:30:00Z`
- `pick-deploy-guard target=frontend commit=148fff7b08d22c1112fd142e62f0076698862fd3 at=2026-10-05T21:34:05Z`

- 私有 producer 初次在 ESM 模块 collection 阶段失败，尚无认证、reservation 或模型派发。补私有 `package.json` 的 module 边界后，真实 CLI `--list` 独立通过，再执行上述唯一一次 run；初次启动失败保留，不计为模型重试。
