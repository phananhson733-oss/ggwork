# 当前状态（2026-09-23）

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
  - 选剧资料页镜像旧选剧台（P2 镜像写入、P3 资料页）还没做；RealShort 的导出接口在 realshort#67，还没合并。
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
