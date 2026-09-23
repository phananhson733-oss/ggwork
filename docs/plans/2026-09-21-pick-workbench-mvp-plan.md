# 个人选剧工作台 MVP 实施方案

> 执行方式：按本文件逐任务实施；实现时使用 executing-plans 与仓库要求的测试流程。当前交付是源码适配审计与实施计划，不代表应用已经启动或业务功能已经实现。

**Goal:** 在 DeerFlow Web 工作台中实现「提出选剧条件 → 查询知识与剧库 → 展示有依据的候选 → 连续追问 → 保存个人选择 → 刷新/重启恢复」。

**Architecture:** 保留 DeerFlow 的用户、会话、消息、运行事件及模型调用。增加一个本地 Python 业务扩展，承接受控工具、个人业务数据和 HTTP 接口；前端增加候选卡、右侧候选面板、我的选剧与资料页面。一个用户对应一个个人数据范围，MVP 不新增组织与共享权限系统。

**Tech Stack:** 固定版本 DeerFlow、Python/FastAPI/SQLAlchemy/Alembic、Next.js/React/TypeScript、单 Gateway worker + SQLite 持久卷；后续多人并发时再验证 PostgreSQL。模型沿用上游 provider 配置，只接一个提供方。

**Status:** 2026-09-23：T0–T7 个人 MVP 已落地并上线，T6 真实数据 10 题验收通过；数据改为 RealShort 只读接口定时同步（见 docs/pick-workbench/realshort-sync.md）。下文保留原始方案。

## 1. 已确认范围与工程基线

- 用户确认 clone DeerFlow，再嵌入选剧功能；旧 `/Users/wzb/Code/ggwork` 仅为产品需求和规则参考，不是迁移对象。
- 新仓库：`/Users/wzb/Code/ggwork-deerflow`。
- 上游：`https://github.com/bytedance/deer-flow.git`。
- 固定提交：`29d285731b326a728a9df33d3641f73b68bbe48b`；该提交标记 `2.1.0-rc0`，不可当作稳定 release 已验收。
- Extension API 的实际版本为 `0.2.1`，在 `backend/packages/extension-api/pyproject.toml` 与 `deerflow_extension_api/__init__.py` 均已核对；依赖与构建使用该提交的锁文件，不浮动追踪 API 次版本。
- 当前分支：`codex/pick-mvp-plan`。本次不推送、不部署、不复制生产凭据。
- `make check`：Node 24.12.0、pnpm 10.26.2、uv 0.10.8 通过；缺 nginx。Docker 引擎 29.5.2 已响应，Docker 路径是首个启动验证选择；尚未完成容器构建、模型配置和登录验收。
- 参考 `/admin/pick` 本轮未能成功打开，不能声称已审核当前生产页面；既有产品文档用于数据字段与交互参考。

### MVP 包含

1. 一个个人账号；数据均按已认证用户隔离。
2. 新建/恢复选剧对话；右侧结构化候选；明确引用候选的追问。
3. 选定 Markdown 知识、CSV/JSON 剧库快照的手动导入。
4. 剧场、语言、数量、渠道规则、已选排除等可支持条件；不支持的条件明确显示。
5. 我的选剧：保存、查看、备注、移出、CSV 导出。
6. 对话、候选快照和保存结果在刷新、服务重启后保持。

### 后续再做

团队共享、跨人审批、排期执行、飞书写入、自动发布、自动全网采集、向量库、通用工作流编辑器、多 Agent 并行、开放插件市场、多模型路由、偏好自动学习。

## 2. 采用的底座接入方式

选择「本地业务扩展 + 少量前端改动」，不重新实现 Agent loop，不另起 MCP 服务，不复制 runtime 数据库。

### 已核对的上游接入点

| 用途 | 实际文件 | 结论 |
|---|---|---|
| 扩展打包/注册 | `examples/deerflow-extension-example/pyproject.toml`、`deerflow_extension_example/__init__.py` | entry point + service + routers 可复用；示例并非现成选剧插件 |
| 数据库生命周期 | `backend/packages/extension-api/deerflow_extension_api/contracts.py` | `ExtensionRuntimeDeps.session_factory` 在持久化就绪后可供扩展使用 |
| HTTP 身份 | `backend/packages/extension-api/deerflow_extension_api/auth.py` | 使用 `resolve_principal(request)`；缺失身份必须拒绝 |
| 工具注册 | `backend/packages/harness/deerflow/config/tool_config.py`、`tools/tools.py` | `tools[].use` 支持引用已安装包中的工具对象；工具不是由 routers 自动注册 |
| 工具身份 | `backend/packages/harness/deerflow/runtime/user_context.py`、`tools/builtins/list_uploaded_files_tool.py` | 有 runtime 身份解析入口；选剧工具禁止匿名/default 回退 |
| 业务表迁移 | `backend/packages/harness/deerflow/persistence/migrations/AGENTS.md` | 独立 metadata、`table_prefix`、独立 Alembic version table；不把扩展表混进上游 metadata |
| 入口导航 | `frontend/src/components/workspace/workspace-sidebar.tsx` | 少量增加我的选剧/资料入口 |
| 专用工具卡 | `frontend/src/components/workspace/messages/message-group.tsx` | 现有工具分发可加候选/待保存卡分支 |
| 右侧面板 | `frontend/src/components/workspace/chats/chat-box.tsx` | 复用其布局并加 pick panel；不可重写 stream 或其他面板逻辑 |
| 个人项目归属 | `backend/packages/harness/deerflow/persistence/projects/sql.py` | 项目按 user_id 隔离，符合本期；不提供现成团队共享 |

新增源码目录（设计路径，尚未创建）：

```text
customizations/pick-workbench/
  pyproject.toml
  ggwork_pick/
    __init__.py          # install：service + routers +必要的runtime middleware
    service.py           # 数据库依赖、扩展迁移、应用生命周期
    context.py           # 已认证用户/run/thread 与固定资料版本
    contracts.py         # Pydantic 输入输出合同
    models.py            # 私有 SQLAlchemy metadata，全部 ggwp_ 前缀
    repository.py        # 按 owner 过滤的存储/事务
    imports.py           # 验证、批次发布与可重复导入
    knowledge.py         # 简单检索与来源片段
    selection.py         # 筛选/排序/候选/保存服务
    tools.py             # 4 个模型可调用工具
    routes.py            # 资料、候选、个人选择 HTTP 接口
    migrations/          # 私有 Alembic chain，ggwp_alembic_version
  tests/
  fixtures/              # 明确标注为合成数据
config.pick.example.yaml # 本期配置范例，无密钥
skills/public/pick-drama/SKILL.md
frontend/src/core/pick/   # types、API、query hooks、结果引用
frontend/src/components/workspace/pick/
frontend/src/app/workspace/picks/page.tsx
frontend/src/app/workspace/pick-data/page.tsx
```

扩展通过官方 extension-install 流程安装；工具的 `use` 为 `ggwork_pick.tools:<tool_object>`，与工具组一起写入本地配置。SQL 表前缀声明为 `ggwp_`。首版只注册实际需要的 service/router/middleware，不照抄示例全部贡献类型。

**本地扩展安装不是 editable link。** 上游管理器会把源码复制到 `backend/extensions/sources/<distribution>/`，并修改 `backend/pyproject.toml` 与 `backend/uv.lock`。源目录仍是 `customizations/pick-workbench/`；每次源码改动必须运行官方 extension-upgrade 刷新托管副本、重建 Gateway 镜像/重启，再验证运行版本。首次安装使用绝对 SOURCE 路径；实现交付必须包含锁文件和托管快照变更，不能只提交源目录。业务插件设为 required；插件/迁移失败时明确无法提供选剧，不静默退回通用助手。

## 3. 最小产品交互

### 对话页

- 默认进入「选剧」上下文，保留上游历史会话。
- 输入示例：「选 5 部英语剧，DramaBox，排除我已经选过的」。
- 显示本次生效条件与数据截至时间；缺少影响结果的参数时一次澄清。
- 工具查询成功后生成不可变候选快照；卡片顺序以该快照为准，模型不得在正文重新编号成另一套。
- 右侧：剧名、剧场、语言、证据类型/值/日期、入选说明、待核实项、查看/勾选/保存。
- 每张卡具有 result_id 和 item_id；选中引用由前端随用户消息携带，服务端校验用户、会话、结果归属。
- 「换一批/换语言」创建新结果，旧结果仍可查看；新数据导入不改写旧结果。
- 「为什么第二部」由消息提交时绑定的 result_id 解析：显式引用优先，否则前端绑定当时可见候选。无有效绑定或有多个可能结果时澄清；禁止服务端按“最新列表”猜测，避免多标签页和迟到响应错位。

### 保存操作

- 按钮保存：勾选后点击保存，POST 业务接口，返回真实保存回执。
- 自然语言保存：「保存第1、3部」先解析为明确候选项并显示简短待保存卡；用户点击一次确认，走同一个 POST 接口。
- 这张卡不是多人审批，也不要求二次登录；它解决模型自由文本没有可靠写入授权的问题。
- MVP 的模型工具不直接修改最终选剧清单；`pick_prepare_selection` 只返回已验证的目标项。界面中的确认是确定性的写入入口。
- 若后续希望自然语言一句话直接保存，需要补充服务器可信意图与作用域验证，不能把工具参数 `confirmed=true` 当授权。
- 只有事务提交成功才显示已保存；失败/处理中/已存在明确区分。

### 我的选剧

剧目、保存时间、理由、个人备注、来源对话与快照；支持移出和导出 CSV。无排期、已发布或飞书已同步的虚构状态。

### 资料

展示知识/剧库批次、来源、导入时间、来源数据时间、条数、校验失败原因。一次只激活每类一个完整批次；导入失败不替换现有批次。

## 4. 数据合同与业务口径

### 4.1 剧库最小输入

必需：`source`、`source_id`、`language`、`title`。

可选：`theater`、`tags[]`、`listed_at`、`availability`（active/delisted/unknown）、`signals[]`、`channel_rules`、`detail_url`。

- identity = source + source_id + language；来源 ID 本身包含版本时仍保留原值。不按 title 合并实体；同名仅为线索。
- signals 每条有 kind、source_ref、observed_at（可空）、原始值与单位说明。无日期写未知，不用剧库导入日期填充。
- 欠缺字段保持 unknown；导入页列出该批次可支持的筛选。
- 不以旧研究文档直接证明现行平台规则。结构化硬规则带来源、版本、核对日期；未知限制不能被展示成确认可发。
- “我没选过”查本系统个人选择；“账号没发过”需要发布记录数据，本期没有接入时明确拒绝该断言，可让用户改为排除已选。
- 默认排除已确认下架；未知上下架状态可展示为待核实。严格要求“确认可发”时，unknown 不满足。
- 默认先做硬过滤，再按最近证据时间排序，同日用稳定 identity 排序；这是浏览顺序，不叫预测收益或爆款评分。仅在同来源同口径时比较榜单名次。
- 返回数量不足时给真实数量及排除摘要；零结果分清无匹配、无数据、读取失败。

### 4.2 知识输入

- 首批选定少量 Markdown：剧场规则、术语、选剧方法、账号说明。仅导入指定文件，不扫描整个个人目录。
- 内容副本有 source_path/source_url、hash、版本、更新时间；引用保存具体片段与版本。
- MVP 用标题/剧场/关键词匹配与固定规则读取，不引入向量数据库或知识自动改写。
- CSV/JSON 剧库进入结构化存储，不靠向量相似度证明某剧存在或允许分发。
- 资料内容是数据；不能覆盖工具白名单、身份或保存权限。

### 4.3 持久化表（业务侧 6 张）

| 表 | 核心内容与约束 |
|---|---|
| `ggwp_import_batches` | id、owner_id、kind、content_hash、raw_blob_path、status、source_as_of、created_at、published_at、validation_json；原文件保存在受控持久卷；同用户同类同 hash 的重复导入可复用；完整成功后发布 |
| `ggwp_drama_versions` | batch_id、identity、原始关键字段、signals/rules JSON；UNIQUE(batch_id, identity)；快照不可变 |
| `ggwp_knowledge_versions` | batch_id、document_id、hash、来源、text、元数据；快照不可变 |
| `ggwp_candidate_sets` | id、owner_id、thread_id、run_id、tool_call_id、parent_result_id、catalog_batch_id、knowledge_batch_id、rule_version、ranking_version、conditions_json、ordered_items_json、created_at；UNIQUE(owner_id,run_id,tool_call_id)；每项有稳定 item_id、identity、证据/说明快照 |
| `ggwp_selections` | id、owner_id、identity、source_result_id、source_item_id、snapshot_json、note、state、version、timestamps；UNIQUE(owner_id,identity) |
| `ggwp_selection_commands` | owner_id、request_id、payload_hash、receipt_json、created_at；UNIQUE(owner_id,request_id)；与 selections 在同一事务提交 |

不新增自有 users/messages/runs 表，不让“workspace_id”成为客户端可随意指定的租户参数；MVP owner_id 即个人空间范围。后续共享要显式迁移，不能把跨 owner 查询解禁当团队功能。

删除/归档会话不级联删除个人选择。已选行保留独立的剧目与依据快照；来源会话已删除时显示对应状态，不能丢失选剧结果。移出使用 state/version，不物理删除历史 command receipt。

### 4.4 一次 run 的一致性

- 开始运行时固定本次资料版本：新查询读取当前已发布版本；基于父结果的追问/条件调整沿用父结果版本，除非用户明确要求更新。后续工具同用一组 catalog/knowledge batch 和规则/排序版本。
- 延续旧候选解释默认使用旧结果和当时资料；“用最新数据重新选”才创建新快照。
- 个人已选是可变化工作状态：查询时取一次快照；保存时用唯一约束再校验。
- 结果引用至少带 result_id；“前3部”由服务端在该结果的固定顺序上取项，不让模型直接提交任意数据库 ID。当前结果游标在提交消息时固定；没有引用时不自动选取服务器最新候选。
- 接收到未完成或已取消 run 的候选时不得当最终交付自动选择；页面区分运行状态，并通过业务读接口恢复已持久化的完整快照。

## 5. 工具与 HTTP 合同

### 模型可用工具

1. `pick_search_knowledge(query, theater?)`：返回文档版本、片段和 citation_id。
2. `pick_query_candidates(filters, limit, parent_result_id?)`：过滤参数由 schema 校验，服务端注入身份和资料批次；先保存完整有序结果，再返回 result_id 和卡片数据。
3. `pick_get_drama_detail(result_id, item_id)`：返回该候选快照与其依据；当前状态另列，不覆盖历史依据。
4. `pick_prepare_selection(result_id, item_ids, note?)`：确认目标均属于当前用户可见结果，返回待保存卡；不写 selections。

所有工具不接受模型传入 owner_id、任意 SQL、任意本地路径、confirmed 或角色字段。缺少可信 runtime 身份即失败；run/thread 参数也取自服务器 runtime。

### 新增 HTTP API（同源、沿用身份/CSRF机制）

| 方法与路由 | 用途 |
|---|---|
| `POST /api/pick/imports` | 上传指定知识/剧库批次，限制类型/大小并校验；全部成功才激活 |
| `GET /api/pick/imports` | 当前用户的批次与就绪信息 |
| `GET /api/pick/results?thread_id=...` | 恢复当前用户可读会话的候选结果 |
| `GET /api/pick/results/{id}` | 候选详情；越权与不存在统一404 |
| `POST /api/pick/selections` | 确认保存，body={request_id,result_id,item_ids,note} |
| `GET /api/pick/commands/{request_id}` | 当前用户提交后断线时查询原回执；查不到代表尚无已提交回执，不由模型推断成功 |
| `GET /api/pick/selections` | 当前个人清单 |
| `PATCH /api/pick/selections/{id}` | 备注/移出，携带 expected_version 和 request_id |
| `GET /api/pick/selections/export.csv` | 导出；处理 CSV 公式前缀与引用转义 |

保存事务：解析真实身份 → 校验快照归属与 item_id → 检查 request_id → 同键不同 payload 返回409 → 写/复用个人选择 → 写 command receipt → 提交。网络超时重发原 request_id 返回原 receipt；不会复活后来已移出的记录。新的再次保存需新 request_id；同剧已存在时回 existing，不覆盖已有备注。

## 6. 运行配置与版本策略

- 首先用固定 commit、单 worker、SQLite、持久卷、localhost 入口完成运行验证。
- 显式配置统一 database.backend=sqlite 并移除互相矛盾的旧 checkpointer/store 配置；上游 legacy checkpointer 优先于统一 database，缺配置可能回落内存。验收必须检查实际 backend/路径并重启 Gateway，不能仅检查页面刷新。
- 一个本地账号，关闭自助注册；保留底座认证。多个用户独立空间后仍按 owner 查询，不共用“default”账户。
- 选剧配置只开放本期工具和必要澄清能力；关闭子代理、定时、IM、自动记忆写入、任意shell/浏览器等非本期能力。实际允许集合以工具发现与执行测试验证，隐藏 UI 不等于禁用工具。
- `tools/tools.py` 在 config tools 外还会追加 BUILTIN_TOOLS、上传工具，以及条件启用的子代理/图像/MCP等工具。配置和最终装配后的 allowlist 必须一致，执行端再次校验；仅声明四个工具不足以证明隔离。每轮初始预算为最多8次业务工具调用、12次模型调用、120秒执行时间；通过本期中间件计数和宿主取消机制实施，超限返回已有结果/明确失败，不能后台继续调用。数值是MVP设计默认值，非上游已实现的默认保证。
- 业务数据库与知识原始文件不暴露给模型的文件/shell工具；由业务工具返回授权范围内数据。
- 模型密钥使用服务端环境变量。选择提供方和模型需在启动阶段配置；不得复用或复制其他项目凭据作为默认动作。
- Docker 引擎可用不代表应用已启动。先核对生成的 compose/config，再启动自己的容器与端口；不要执行会影响其他项目的全局清理。
- `config.yaml`、实际知识/剧库、`.deer-flow` 不提交；可审查范例与合成测试数据提交。
- 上游升级由专门变更进行：备份 DB/文件 → 运行合同测试与真实后端冒烟 → 才切换；不部署浮动 latest。

## 7. 实施任务与验收

以下路径以新仓库根目录为基准；Create 为未来实现文件，Modify 为已存在的接入点。对影响行为的任务先写失败用例，再实现，再运行该任务用例；通过后再进入下一项。

### T0：跑通固定底座（里程碑 A 的前置）

**Files:** Create `config.pick.example.yaml`、`docs/pick-workbench/local-run.md`；本地生成 gitignored `config.yaml` 与环境配置。

1. 根据固定 commit 的 `config.example.yaml` 收敛个人选剧配置，不复制整套默认开放能力。
2. 按 root Makefile 的 Docker 路径构建/启动，仅本机端口；不要求安装本机 nginx。
3. 配置一个模型与个人账号，完成普通对话、工具调用、刷新及重启。
4. 记录 commit、配置版本、容器、入口、模型名称和实际结果；未通过时不开始大规模前端定制。

**Verify:** 健康检查成功；网页真实模型回复；刷新和重启保留历史；两条相邻消息的运行状态合理。预计 0.5–1.5 工程日，不含提供方开户。

### T1：业务扩展与独立迁移

**Create:** `customizations/pick-workbench/pyproject.toml`、`ggwork_pick/{__init__,service,context,models,repository}.py`、`ggwork_pick/migrations/{env.py,versions/0001_initial.py}`、`tests/test_bootstrap.py`、`tests/test_owner_scope.py`。
**Managed changes:** 官方扩展管理器更新 `backend/pyproject.toml`、`backend/uv.lock`、`backend/extensions/sources/ggwork-pick/`；不能手工改托管副本后忘记改源目录。

1. 先写测试：空库安装、第二次启动幂等、迁移失败阻止业务路由就绪、另一用户无法读取。
2. 注册 service/routers；取得 host session_factory，建立私有 metadata 和六张表。
3. 设置 table_prefix，使用独立 ggwp_alembic_version；不改上游迁移链。
4. 缺身份时拒绝；工具上下文只使用服务器身份与run/thread。

**Verify:** 扩展相关测试通过；上游 metadata 无 ggwp_ 表；重启迁移无重复；缺身份/伪造owner均拒绝；升级本地源后运行中插件版本确实改变，Docker构建可复现。

### T2：导入与知识/剧库查询

**Create:** `ggwork_pick/{contracts,imports,knowledge}.py`、`tests/test_imports.py`、`tests/test_knowledge.py`、`fixtures/catalog.json`、`fixtures/knowledge/*.md`。

1. 先写坏批次、重复导入、同名不同 ID、未知规则、引用版本的失败测试。
2. 完成 CSV/JSON/Markdown 限定格式导入；固定 UTF-8，来源字段保留，坏批次不激活。
3. 完成硬过滤、稳定排序、知识片段来源；先用小型合成数据核对已知结果。
4. 另行导入用户指定的真实资料，保存在 gitignored 数据区。

**Verify:** 重导入相同内容不会生成第二份活跃相同批次；新批次失败时旧批次可查；同名剧不合并；未知权限不输出“确认可发”。

### T3：候选快照与工具接线

**Create:** `ggwork_pick/{selection,tools}.py`、`tests/test_candidates.py`、`tests/test_tools.py`、`skills/public/pick-drama/SKILL.md`。
**Modify:** `config.pick.example.yaml`（tools/group/plugin/skill配置）。

1. 先写条件继承、结果顺序、run 重试同 call_id 去重、不足数量的用例。
2. 查询成功后先持久化完整候选快照再返回；工具注册通过 upstream tools[].use。
3. 工具输出结构化卡片数据和来源，模型正文仅解释，不决定卡片事实。
4. 增加 prepare-selection，只生成待保存目标。

**Verify:** 实际 Agent 能调用业务工具；没有随机/剧本推荐；每个 identity/citation 都来自快照；模型返回未知 ID 时拒绝；枚举最终工具集并验证越过白名单的调用被拒，预算上限生效。

### T4：候选卡、右侧面板与引用

**Create:** `frontend/src/core/pick/{types,api,queries,references}.ts`、`frontend/src/components/workspace/pick/{candidate-card,candidate-panel,pick-context,selection-confirmation}.tsx`、对应 `frontend/tests/unit/core/pick/` 与 `tests/unit/components/workspace/pick/`。
**Modify:** `frontend/src/components/workspace/messages/message-group.tsx`、`frontend/src/components/workspace/chats/chat-box.tsx`、`frontend/src/components/workspace/chats/use-thread-chat.ts`。

1. 先测试候选解析、引用准确性与陈旧响应不可覆盖当前结果。
2. 专用工具卡识别结果 ID，卡片从授权业务 API 读取；未完成数据不当完整成果。
3. 增加右侧候选面板，保留现有 artifacts/sidecar 功能和 stream 实现。
4. 切换结果时引用显式绑定；刷新通过 thread-scoped 业务结果 API 恢复。

**Verify:** “第二部”与所选快照一致；切对话不串结果；刷新后仍是原顺序；取消/失败与最终完成区分。

### T5：保存、我的选剧、备注与导出

**Create:** `ggwork_pick/routes.py`、`tests/test_selection_commands.py`、`tests/test_pick_routes.py`、`frontend/src/app/workspace/picks/page.tsx`、`frontend/src/components/workspace/pick/my-selections.tsx`。
**Modify:** `repository.py`、`selection.py`、`frontend/src/components/workspace/workspace-sidebar.tsx`。

1. 先测试重复 request、同键不同 payload、越权、历史重放、重复 identity、备注版本冲突。
2. 保存与 command receipt 在一个事务；UI只读实际回执。
3. 对话 prepare 卡与按钮都调用同一个保存接口；不要从聊天文字推断已保存。
4. 完成备注、移出、CSV 导出；移出后旧保存请求重放不得复活。

**Verify:** 双击只一条选择；请求提交后响应丢失重发返回原receipt；另一用户不可读写；保存后刷新/重启可恢复。

### T6：资料入口与真实业务试用（里程碑 B）

**Create:** `frontend/src/app/workspace/pick-data/page.tsx`、`frontend/src/components/workspace/pick/data-imports.tsx`。

1. 用上游 UI 组件增加批次导入/状态页；避免再造文件管理器。
2. 用指定的一批真实剧库、选定规则文档，执行 10 条人工预期明确的选剧请求。
3. 确认数据支持哪些条件；发布记录缺失时不宣称“账号未发”。

**Verify:** 10 条真实问题可复核；能解释、换条件、保存；来源及日期可见；空结果有准确原因。

### T7：运行恢复与验收（里程碑 C）

**Create:** `frontend/playwright.pick.config.ts`、`frontend/tests/e2e-pick/pick-workbench.spec.ts`、`customizations/pick-workbench/tests/test_restart_persistence.py`、`docs/pick-workbench/acceptance.md`。

1. 合成 fixture 可用于确定性测试；真实后端测试不得仅以 page.route mock 代替。已核对的 `playwright.real-backend.config.ts` 使用真实 Gateway 但模型为 ReplayChatModel，且默认关闭认证，不能当作本期真实模型/身份验收。新增 pick 配置保留认证、使用临时测试账号和临时数据目录；不修改原有测试的匿名契约。
2. 验证运行中刷新、终态刷新、服务重启、失败重试、资料更新后旧结果仍可解释。
3. 测试知识里出现“保存全部”等指令时，工具权限与最终保存不受影响。
4. SQLite/文件完整备份后在临时目录恢复，检查同一候选和选择可读。

**Verify:** 浏览器 → Gateway → 业务数据库 → 页面回执完整通过；保存行和 UI 内容一致；记录实际模型、数据批次、耗时和失败项。

## 8. 实现时的验证命令

依赖安装和文件创建后再执行；本次仅文档审计未运行这些业务测试。

```bash
# 新扩展已安装到 backend 环境后，工作目录 backend/
uv run pytest ../customizations/pick-workbench/tests -q
uv run pytest tests/test_extension_gateway_wiring.py tests/test_extension_route_principal.py tests/test_persistence_migrations_env.py -q

# 工作目录 frontend/
pnpm check
pnpm rstest run pick
pnpm build
pnpm exec playwright test -c playwright.pick.config.ts
```

T7 的 `playwright.pick.config.ts` 借鉴上游启动方式，但保留身份、独立端口和临时数据库。离线真实后端+确定性模型与真实provider验收分别记录；默认 `pnpm test:e2e` 的网络 mock 和既有 replay 套件都不能独自证明实际选剧可用。

## 9. 三个可演示里程碑与工期

| 里程碑 | 交付 | 门槛 |
|---|---|---|
| A：可用外壳 | 固定底座、个人账号、模型、样例候选与数据库基础 | 第一次对话可用，刷新/重启不丢历史，候选读取真实业务库 |
| B：真实选剧 | 真资料导入、条件查询、证据、换条件、保存 | 10个真实问题可人工复核，保存结果稳定 |
| C：个人日常使用 | 我的选剧、导出、异常恢复、备份恢复 | 端到端真实后端验收，连续使用无状态混淆 |

建议预算：A 2–3 工程日，B 3–4 工程日，C 2–3 工程日，总计约7–10工程日；资料清洗、模型权限、上游缺陷另计。原型演示可早于此时间，不能以样例演示冒充真实数据闭环完成。

开工顺序固定为 T0 → T1/T2 → T3 → T4/T5 → T6 → T7。T0 接口/启动检查发现上游缺陷时先记录和修正估时；不再展开其他框架横向选型。

## 10. 第一轮验收脚本

1. 用户登录，导入指定剧库和规则，看到批次时间。
2. 输入“找5部英语剧，排除我已经选过的”；候选每行有依据。
3. 指定某份结果问“第二部为什么推荐”；回答引用该结果的第二项。
4. 改剧场或换一批；出现新结果，旧结果仍可查看。
5. 勾选三项保存；再用同一 request_id 重放，数据库仍只有三项。
6. 在对话说“保存第1、3部”；目标明确显示，确认后真实保存。
7. 修改备注、移出一项；重放旧请求不会撤销后来的移出。
8. 刷新、重启，继续读取对话、候选和个人清单。
9. 导入新批次；旧依据保留，“用最新数据重新选”生成新快照。
10. 输入“这个账号没发过的”；未接发布数据时显示无法核对，不伪造未发状态。

## 11. 当前交付与待配置事项

已完成：clone固定源码、核对接口与目录、检查本机运行依赖、通过用户指定 ChatGPT Pro 完成独立审计、逐项交叉核对后形成实施合同。

待实施：T0–T7；运行配置、模型提供方/凭据、实际导入资料范围、真实剧库导出格式。它们不会阻止方案和离线合同准备，但真实模型/真实数据验收必须具备这些输入。
