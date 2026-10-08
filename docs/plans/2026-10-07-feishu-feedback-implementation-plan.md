# Feishu Feedback for Pick Agent — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **For other code Agents:** 阅读目标仓库AGENTS.md及配套spec，按依赖顺序执行；本次交付只含文档，未授权启动实现或生产启用。

**Goal:** 让选剧 Agent 使用最新飞书运营反馈，并能追溯到数据版本、来源记录及判断边界。

**Architecture:** 飞书只读同步到现有数据库的独立反馈版本，经完整性检查原子发布；Agent通过两类反馈工具和候选附证据使用数据。同轮固定版本，旧候选保留历史；第一版不新增综合评分或自动重排。

**Tech Stack:** 目标仓库现有Python / SQLAlchemy / Alembic / Pydantic / DeerFlow工具及服务生命周期；现有Next.js/React/Zod前端；SQLite和PostgreSQL测试；飞书只读来源适配器。

---

## 交接入口

先读 [Spec v1](2026-10-07-feishu-feedback-spec.md) 和 [完整数据盘点](../reference/2026-10-07-feishu-feedback-inventory.md)。冲突时以最新用户指令、目标仓库约束、spec明确契约为准。

**路径规则：** 以下均相对于 `<deerflow-repository>` 或它的隔离worktree。当前存放文档的 `<legacy-worktree>` 是旧版仓库，不能用作实现目标。实施前将三份文档携带到目标分支的同名目录。

本地观察基线：DeerFlow `1df57d66`，不是生产SHA保证。所有“Modify”路径已在该本地版本发现；“Create”是建议新文件，若新版本已有同职责模块则复用。迁移编号必须按执行时最新head顺延，不硬编码0007。

**接手续做：** 已发现 `<feedback-worktree>` 有本功能实现。先读该工作区 `docs/pick-workbench/feedback-acceptance.md`、检查diff及最新AGENTS.md，给每个任务标记“已有且已验收 / 已有待验收 / 缺失”，再执行剩余项。当前交接只核对文档和文件存在，不重述历史测试为本轮验证结果。严格候选结构不可加键；反馈应通过不可变附属证据及 `/results/{id}/notes` 接入。

## 默认交付边界

- v1完成spec A01–A15；反馈辅助查询和证据进入候选，但不改变既有排序。
- 开关默认关闭，生产调度不自动启用，飞书保持只读。
- 地区分析返回明确不支持；没有已核验逐剧收益时仍可完成播放反馈与收入分组展示。
- 不创建新数据库、向量库、Agent runtime、全新数据驾驶舱。
- 本计划不是再次要求用户确认已经明确的开发细节。实施被授权后，普通可逆开发和测试连续完成；外部授权/部署边界按实际用户授权判断。

## 通用测试与交付规则

每个代码任务按“先写业务失败用例→确认失败原因→最小实现→相关用例通过→差异检查”推进。测试缺依赖或环境错误不算业务红灯。一个任务可以拆为多个小提交；只有后续实施授权覆盖提交时才提交。每个checkpoint记录SHA、变更路径、命令、通过/跳过及剩余风险。

从目标仓库root运行：

```bash
backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q
backend/.venv/bin/ruff check customizations/pick-workbench/ggwork_pick customizations/pick-workbench/tests
backend/.venv/bin/ruff format --check customizations/pick-workbench/ggwork_pick customizations/pick-workbench/tests
```

数据库测试复用 `tests/conftest.py` 的 `pick_db_url` 和 `tests/engines.py`，验证SQLite及临时PostgreSQL。缺 `PICK_TEST_PG_URL` 时明确标PG未验证；不得连生产库充当测试库。

涉及前端时先读 `frontend/AGENTS.md`、`frontend/src/AGENTS.md`，按执行时package scripts运行对应单测和 `pnpm check`，最后运行针对性E2E。不要为本计划升级依赖。

## Task 0：核验目标与来源契约（先做，不写业务实现）

**Read:**
- `AGENTS.md`、`customizations/pick-workbench/AGENTS.md`
- `docs/pick-workbench/local-run.md`、`progress.md`、`mirror-runbook.md`
- `customizations/pick-workbench/ggwork_pick/{tools,middleware,selection,models,context,pin,service,routes}.py`
- 宿主现有飞书集成；进入backend模块前读对应AGENTS。

**Create:** `docs/pick-workbench/feedback-source-contract.md`（仅schema和契约，不写业务记录/秘密）。

1. 记录Git状态、目标SHA、实际扩展加载路径；查现有feedback实现避免重复。
2. 以用户现有授权验证服务器侧能否读取目标Base；不能从浏览器登录推断服务端可用。
3. 在已授权的只读范围内枚举15张表schema，核验field_id、类型、link目标、公式、每表分页/版本元数据能力和API限额。
4. 记录表级allowlist、映射及哪些字段仍未核验；收益金额、账号资料只进入受控临时artifact。
5. 核验业务样本：SD↔来源剧目ID↔catalog identity；CPS账号与发布账号；自动/手动明细关系。
6. 测量一次完整扫描耗时/大小；确认是否能在20秒内完成，以及跨表变化检测可用方法。
7. 在契约文档写清来源适配选择、授权范围、失效处理、一致性级别、实际分页边界；不能写虚构field_id。

**完成条件：** 目标与权限可核验；或者明确live阻塞，后续允许fake驱动开发。没有数据归因规则时保留unknown，不阻塞无依赖部分。

## Task 1：定义类型、状态及合成fixtures

**Create:**
- `customizations/pick-workbench/ggwork_pick/feedback/__init__.py`
- `customizations/pick-workbench/ggwork_pick/feedback/contracts.py`
- `customizations/pick-workbench/ggwork_pick/feedback/source.py`
- `customizations/pick-workbench/tests/feedback/fakes.py`
- `customizations/pick-workbench/tests/feedback/test_contracts.py`

1. 为spec状态、metric null、Decimal、source_scope拒绝模型输入写失败用例。
2. 定义source分页协议、版本manifest、各表规范化模型、证据/覆盖/工具输出契约。
3. 构建15表合成数据，包含同名不同语种、重复快照、已测0、缺失、负回调、账号级收入、两个币种、自动/手动重叠和汇总副本。
4. 验证未知原始字段可保留但不进入模型事实；任意工具SQL/URL/owner被拒绝。

**运行：** `backend/.venv/bin/python -m pytest customizations/pick-workbench/tests/feedback/test_contracts.py -q`

**预期：** 严格输入与输出通过；合成fixtures不含真实账号邮箱、订单或凭据。

## Task 2：版本存储、迁移和权限隔离

**Modify:** `ggwork_pick/models.py`、`ggwork_pick/migrations/versions/<next>_feedback.py`（新迁移）。

**Create:** `ggwork_pick/feedback/repository.py`；`tests/feedback/test_repository.py`、`test_migrations.py`、`test_authorization.py`（上述路径均在customizations/pick-workbench下）。

1. 写失败用例：两个owner访问隔离、相同ID不跨scope碰撞、旧候选可读、新字段nullable、Decimal精度。
2. 创建spec最小实体及候选附属证据表，沿用private migration链和timestamp规范；不改变严格候选快照结构。
3. 实现版本/记录不可变读取、暂存、原子current更新和条件更新防迟到发布。
4. 完成SQLite/PG迁移与约束测试；不向所有登录者共享财务来源。
5. 验证老候选/旧API客户端和关闭开关的行为。

**预期：** A02/A06/A12基础通过；迁移可在有旧数据的测试库升级，不重建或删除原业务表。

## Task 3：真实只读适配器与完整读取

**Create:** `ggwork_pick/feedback/feishu.py`、`tests/feedback/test_feishu_source.py`。

**依赖：** Task0的真实API/宿主授权契约，Task1。

1. 写请求级fake测试：多页、空表、重复/循环cursor、读权限不足、限流、token失效、字段改名和类型变化。
2. 实现一种已核验的来源适配器；固定Base/table allowlist；分页不依赖用户UI视图。
3. 设置请求超时、有限重试和安全错误码；鉴权错误不能盲目重试或切换身份。
4. 验证所有15表均记录完成凭据，响应裁剪/漏字段不是完整成功。
5. 同步日志只记table、计数、时间、错误码；不记录token、来源链接中的秘密或整行收入数据。

**预期：** A01/A03/A12；读取路径不存在创建/编辑/删除飞书请求。

## Task 4：同步作业、并发、完整性与发布

**Create:** `ggwork_pick/feedback/sync.py`、`tests/feedback/test_sync.py`、`test_concurrency.py`。

**Modify:** `ggwork_pick/service.py`（仅生命周期及作业入口）。

1. 写失败用例：第15表失败、旧scan迟到、分页中源变化、源业务partial但读完整、跨进程同时触发。
2. 建立按source_scope持久租约；获取租约后顺序执行读取→校验→投影→发布。
3. 实现一致性策略及最多一次整批重读；不能承诺来源没有的跨表事务快照。
4. 同内容复用版本，但记新成功验证时间；不同内容生成新版本。
5. 模拟发布前crash、恢复重试、任务取消；旧版本保持可用，shared job不受单个等待者取消影响。

**预期：** A01–A05；业务partial可带质量发布，读取不完整不能发布；失败不清空当前数据。

## Task 5：剧集映射与单剧事实

**Create:** `ggwork_pick/feedback/identity.py`、`normalize.py`、`tests/feedback/test_identity.py`、`test_normalize.py`。

1. 写失败用例：同名异剧/同剧异语、账号域不同、同帖两快照、同一发布关联多个不确定收入。
2. 只接受确证ID/link映射；名称候选保留ambiguous/unmatched。
3. 规范化唯一帖子及最新指标，保留metric_as_of/采集状态/null/0/负值。
4. 收益按source lane、currency、grain、口径分开；自动/手动/RS/汇总不合并。
5. 冻结映射依据到version/候选证据；修改当前映射不重写历史。

**预期：** A07–A09；账户总收益不能出现在单剧实绩里；没有匹配不是没发过。

## Task 6：全量范围的描述性分析

**Create:** `ggwork_pick/feedback/analytics.py`、`tests/feedback/test_analytics.py`。

1. 用超过20个候选的fixture证明统计不受展示limit影响。
2. 实现genre/language/theater分组、明确日期区间、样本数、覆盖、观察时长及证据查询引用。
3. 定义多标签多重归组并返回non_additive提示；计算每帖均值/中位数的分母仅含有效指标。
4. 金额分来源/币种/粒度；不返回未核验合并收入或转化率。
5. 对country/实际付费人数/D7缺数据情况返回结构化不可支持结果。

**最小业务测试样例（测试意图，接口以Task1实际命名为准）：**

```python
def test_latest_post_value_and_missing_denominator():
    observations = [
        {"post": "p1", "at": "2026-10-01", "views": 100},
        {"post": "p1", "at": "2026-10-02", "views": 150},
        {"post": "p2", "at": "2026-10-02", "views": None},
        {"post": "p3", "at": "2026-10-02", "views": 0},
    ]
    result = aggregate_latest(observations)
    assert result.views_total == 150
    assert result.unique_posts == 3
    assert result.measured_posts == 2
    assert result.mean_views_per_measured_post == 75
```

**预期：** A08–A11；不存在先limit后聚合，也不补造历史。

## Task 7：刷新协调与run版本固定

**Create:** `ggwork_pick/feedback/refresh.py`、`tests/feedback/test_refresh.py`、`test_pin.py`。

**Modify:** `ggwork_pick/context.py`、`pin.py`、`selection.py`、`repository.py`（按需小改）。

1. 写失败用例：20秒等待后pending、run预算不足、共享作业仍继续、一次run多个工具只刷新一次。
2. 给run增加独立反馈pin；不要破坏现有Pin tuple解包兼容，可先使用独立字段/对象。
3. 实现新判断刷新、历史详情和换一批沿用旧版本、新评估产生新结果的契约。
4. 候选冻结feedback_version、验证时间、evidence与映射/聚合版本。
5. 失败/pending不生成“最新反馈推荐”；纯榜单路径可用但显式没有反馈。

**预期：** A04–A06/A13/A15；旧候选编号及确认保存准确无变化。

## Task 8：Agent工具、流程和解释边界

**Create:** `ggwork_pick/feedback/tools.py`、`tests/feedback/test_tools.py`、`test_agent_flow.py`。

**Modify:** `ggwork_pick/tools.py`、`middleware.py`、`answer_check.py`及执行时发现的工具注册入口。

1. 写工具schema与身份、result归属、数量上限、prompt injection测试。
2. 注册 `pick_get_feedback` / `pick_analyze_feedback` 到真实最终白名单，不能只定义函数未接线。
3. 新候选查询自动批量附直接反馈；题材策略问题先调用全量统计，不依赖模型自行数卡片。
4. 提示规则区分实绩/类比/榜单/未知，显示数据时点与partial；不自动加用户未要求的硬过滤。
5. scripted模型测试必须捕捉：模型调用工具、证据进入最终输出、未知收入不编造、国家问题拒绝错误推断。
6. 保持固定候选编号；不在自然语言中另排一套次序。明确本版本没有反馈排序。

**预期：** A06–A13/A15；不仅API smoke通过，还能在完整Agent流程看到反馈依据。

## Task 9：状态端点、后台调度与UI

**Create:**
- `ggwork_pick/feedback/routes.py`、`schedule.py`
- `tests/feedback/test_routes.py`、`test_schedule.py`
- `frontend/src/core/pick/feedback-schema.ts`
- `frontend/src/components/workspace/pick/feedback-status.tsx`
- `frontend/tests/unit/core/pick/feedback-schema.test.ts`
- `frontend/tests/unit/app/feedback-status.dom.test.tsx`

**Modify:**
- `ggwork_pick/routes.py`、`service.py`（入口注册及enabled/configured边界）
- `frontend/src/core/pick/{api,types}.ts`
- `frontend/src/components/workspace/pick/{sync-status,candidate-view,pick-tool-card}.tsx`
- 资料页实际挂载处（先从现有sync-status调用点定位，避免重做pick-board）。

1. 测试status/sync的权限、202幂等作业、token错误脱敏。
2. 默认关闭feature和schedule；开启后60分钟调度使用Task4同一锁，重启不遗留假running。
3. UI状态覆盖关闭/未配置/刷新中/失败/历史/partial/成功；不混淆目录和反馈同步。
4. 候选摘要显示来源类别及指标日期；历史结果显示当时版本；金额不显示假0。
5. 测试新旧payload兼容，不让Zod静默丢字段导致虚假成功。

**预期：** A04/A05/A12/A13/A15。开关关闭时现有UI与功能不退化。

## Task 10：完整回归、只读live验证与交付

**Create:**
- `frontend/tests/e2e-pick/pick-feedback.spec.ts`
- `docs/pick-workbench/feedback-runbook.md`
- `docs/pick-workbench/feedback-acceptance.md`

**Modify:** `docs/pick-workbench/progress.md`；现有安装/部署说明中新增必要配置说明。

1. 跑完整业务扩展测试、ruff、前端检查及针对性E2E；PG不可用时标记未过，不能报全通过。
2. E2E覆盖：刷新→候选反馈→单剧详情→来源回查→刷新后旧结果不变→新评估更新；另测pending/partial/auth失效。
3. 按目标仓库local-run说明更新托管扩展副本，运行：

```bash
backend/.venv/bin/python -m pytest customizations/pick-workbench/tests/test_managed_copy.py -q
```

4. 在已有权限覆盖的受控环境完成一次真实只读完整同步，核对15表、schema、计数和结束分页凭据；对成功/零/缺失/账号级收入/手动出单/未匹配各选可回查样本。
5. 如果无法稳定同步、field_id未证实或授权不够，清楚列出A14未满足，其余开发结果照常交付。
6. 运行独立代码审查（遵循目标仓库/当前会话对review agent的要求），修复本功能高优先级问题。
7. 交付diff、测试证据、配置字段名、错误码、启停与回滚步骤、未决项；不包含秘密。
8. 生产迁移/部署/开关/定时启用仅在用户授权覆盖时执行；如果启用调度，还须观察下一次实际触发，单次成功不能当作定时稳定证据。

## 里程碑与退出标准

| 里程碑 | 任务 | 可以声称什么 |
|---|---|---|
| M0 | 0–1 | 来源契约和类型明确；若凭据缺失注明未live验证 |
| M1 | 2–5 | 版本化同步/映射/单剧事实在合成测试及PG通过 |
| M2 | 6–8 | Agent完整流程可查询反馈，历史结果不漂移 |
| M3 | 9–10 | UI/E2E、真实只读验收完成；开关仍可关闭 |
| M4（需授权） | 生产启用及下一次调度 | 生产可用与定时运行分别有证据 |

## 回滚原则

关闭feedback feature/schedule，停止新同步进入；已开始任务按服务生命周期安全结束或标失败，未完成版本不发布。保持旧剧库/榜单/保存功能；保留历史反馈版本和证据，不能为回滚删除用户结果。数据库迁移优先向前兼容，不默认执行破坏性downgrade。

## 给执行 Agent 的启动指令

> 先读本计划、配套spec和来源盘点。核对实现目标为DeerFlow版pick-workbench，当前docs载体可能是旧GGWork。先完成Task0，再按依赖顺序实现v1。不要改飞书字段，不新增综合权重或地区推断。默认开关关闭；所有事实统计可追溯，同轮固定版本，旧候选不漂移。无法拿到真实权限时继续完成fake驱动开发，并准确标出live验收未完成。未经新的授权不启动生产迁移、部署和调度。交付按A01–A15逐项列证据，不把单测或API调用成功描述成完整上线。
