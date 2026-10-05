# 选剧工作台后续实施与验收计划

> 执行方式：按 `superpowers:executing-plans` 分批实施，代码修改先有失败测试，再做最小修复；每批独立审查，最终按实际授权完成集成、发布和真实验收。

**Goal:** 按用户“按照建议去落地”的授权，修复取消用量记录，完成原固定样本的输入瘦身决策与实现，推进用户选定的雷达范围，交付团队协作与外部写入的下一阶段规格，并补齐相应验证。

**Architecture:** 沿用宿主运行生命周期、现有 owner/lease 隔离、业务扩展和独立迁移链。取消计量通过现有运行 JSON 元数据存储；模型投影只影响查询/详情返回给模型的表示；雷达复用已有计划与实现，不新增平行采集系统。

**Tech Stack:** 当前锁定的 Python、LangChain/LangGraph、SQLAlchemy、PostgreSQL/SQLite、Next.js、Railway/Vercel；不升级依赖来换取通过。

## 1. 权威基线与完成边界

- 业务仓库：`phananhson733-oss/ggwork`，业务 remote `ggwork`；`origin` 为上游 DeerFlow。
- 本轮代码基线：`8f2311da2dc011067f9a5bec631c9f9d2c2bbdc9`。线上产品仍是 `d236f951` / `20261006-d236f95`；不得用文档或 QA-only SHA 冒充实际部署版本。
- 工作树：`/Users/wzb/Code/ggwork-deerflow-wt/readiness-spec-20261005`，分支 `codex/readiness-followup-20261006`；原主目录及历史分支保留。
- 原 baseline、锁定 expectations、原 captures 和失败记录保持原样，新证据单独追加。
- 原应用模型运行预算仍为 40，本轮开始已使用 36。新增真实验收必须先登记，最多使用剩余 4 个 run；不足时请求明确的新预算，不能另建不计数账本。
- 本计划记录完整目标。中间提交、测试通过或等待用户决策均不等于目标完成。

## 2. 取消用量记录

### 2.1 已复现原因与设计

`RunJournal.on_llm_error()` 忽略 LangChain 提供的 `kwargs.response`，已接收的 partial usage 会丢失；旧 `llm_call_count` 实际只计算带正 usage 的完成调用。供应商未返回的最终用量不能从调用已开始或 API 默认 0 推算。

采用 `metadata.deerflow_usage_observation`，包含版本、finalized、调用生命周期计数、缺失/部分用量计数、coverage、已知 token 合计和有限原因码。它描述已观测回调事实，不能解释为供应商计费请求数或账单。显式 0 与未返回的 null 区分。旧整数统计字段维持兼容。

### 2.2 文件与步骤

修改 `backend/packages/harness/deerflow/runtime/journal.py`、`runtime/runs/manager.py`、`runtime/runs/store/{base,memory}.py` 和 `persistence/run/sql.py`；测试在 `backend/tests/`，遵循相应 AGENTS。

1. 先测试取消回调给定 7/3/10 token 却被丢失；测试已开始/无 usage、完整显式零、混合完整与取消、重复终态回调和 closed journal。
2. 以实际 LangChain 调用 UUID 去重。错误路径仅读取 usage，不调用完整回答写入逻辑，不制造 AI 消息、工具调用或保存事实。
3. 在共享运行创建入口清除客户端伪造的保留键；初始观察未 finalized。历史无键、关闭跟踪和没有 journal 的情况保持未知。
4. manager 在已有锁内合并保留子键，保留 trace/replay 等其他元数据；store 的 progress/completion 在现有受状态限制的同一个 UPDATE 中写元数据。不得新增无 fence 的写入或覆盖整行 lease/status。
5. 覆盖延迟 progress、重复取消、丢失 lease、行恢复和双库持久化。finalized 仅表示终态观察已形成，partial 数值不因此成为最终计费值。

验证命令：宿主 `make test` 与 `make test-blocking-io` 改前/改后均运行；新增 journal/manager/store/worker 测试定向执行；SQLite 与真实临时 PostgreSQL 分开记账。

完成证据：已知 partial usage 保留；调用生命周期可读；缺失字段有原因且为 unknown；API owner 隔离、回调重放和租约边界测试通过；发布后用专用普通 QA 身份验证取消记录。旧 Q19 的不可恢复 token 不补造。

## 3. 模型输入瘦身

原固定 10/20 部样本分别是 17,416 / 34,116 B，当前紧凑输出 16,060 / 31,460 B。没有长 detail_url 可删；证据字段占主要体积。

**等待合同选择：**

- 保持原内联字段表示：仅删除与 root 完全同值、同类型的 item matched_total 副本，原样本约减少 8.9%；详情没有 root 值时保持。诚实保留 20% 目标差额。
- 若用户明确允许模型专用字典编码：将重复且类型敏感相等的已知事实提取为公共字典，逐证据保留引用、独有值和未知字段；以独立解码逐字段证明事实保持。只读实验原样本约减少 32%～35%，尚非批准实现或真实模型验证。

修改范围仅为 `customizations/pick-workbench/ggwork_pick/model_projection.py`、必要的 `tools.py` 表示说明与序列化、对应模型投影/工具/前端合同测试及固定合成 benchmark。HTTP、持久化、缓存、排序、回答核对材料、知识检索和准备保存合同保持各自原语义。

1. 原固定样本及 hash 已纳入 `tests/fixtures/model_projection` 合成回归。执行 `backend/.venv/bin/python scripts/pick-model-projection-benchmark.py` 分开报告 whitespace 与结构贡献；加 `--require-target` 时，任一原样本未达到 20% 即退出 2。当前普通 10/20 部仍约 7.79%、结构缩减 0，严格目标未通过；重复长链接样本不能替换该分母。
2. 按批准方案先写失败测试；覆盖 null/空串/0、bool/int 类型差异、评级、note-only 事实、未知字段、obs 合同和命名碰撞。
3. 保留源对象不变、引用可定位、5/10/20 部完整候选和证据数量；新字典方案还需无损解码与模型读取说明。
4. 经过宿主工具预算及前端真实定位/展开/确认合同验证，使用专用 QA 做 20 部查询、详情解释和准备保存的真实验收。

完成证据：同原样本测量与完整事实校验、真实模型理解与引用/保存链路证据。若选择保持原格式，记录用户接受的量化差额；不能换样本或用字节推算 token/费用。

## 4. 雷达范围和原实现

当前只读证据仍显示旧 canary 已限流终止，无有效发布集合。历史分支 `3cf827e3` 含 09-30 简化范围及已实现的 TopDramasTaskSource、趋势表 API、日级 stable 接线和前端表格，尚未合入业务 main。

**等待范围选择：** 09-30 简化版规定热门榜优先取 100 部、全球近 30 天日级参考表，搁置 GSC 和智能体趋势排序；此前主线审计按旧完整计划列出 GSC 等缺口，两者范围不同。由用户确定本轮采用哪条路线，再锁定代码和上线门槛。

共同步骤：

1. 对照现行 main 逐项审查历史实现及消费者，列明应移植的最小提交集合，不盲合 125 个差异文件及托管副本。
2. 补读取时点、最新一期榜单、缺失不补零、逐剧身份、异常/限流、owner 隔离与旧数据兼容的测试。
3. 保持既有采集预算、暂停/终止与租约规则。429、验证码或同意页不靠换模式、出口或提高预算绕过；按选定计划处理失败及重新开始条件。
4. 仅在对应真实 canary/影子门槛完成后发布该阶段。连续 3 日、7 日或两周等门槛由真实时间窗记录证明，不能用一次测试替代。
5. 若选择完整 GSC 路线，先确认专用账号/属性权限、TR-07 实测、runner/快照/发布合同；凭据只通过既有安全配置路径使用。

完成证据：用户选定范围、独立代码审查、真实部署身份、对应采集批次/时效/覆盖门槛、登录后参考表或完整观测链路验证。仅恢复 cron 不算完成。

## 5. 下一阶段规格与验证补强

交付 `docs/plans/2026-10-06-pick-team-workflow-spec.md` 和 `docs/plans/2026-10-06-pick-external-write-spec.md`，明确对象、权限、状态转移、接口、幂等回执、故障恢复、逐项测试和发布门槛。这是本轮建议中的规格交付，不冒称团队功能或外部写入已实现。

补验证：Q13 无效条件和跨线程引用负向测试优先使用真实 API 或合成本地宿主，不增加无必要的模型 run；记录本轮实际覆盖。说明现有手工发布与自动发布链路的区别；按实际项目集成状态决定是否需要后续自动化任务，不能仅凭推 main 宣称自动发布完成。

## 6. 集成、发布和完成审计

1. 独立 review 关闭可达问题，源目录/官方 extension manager 生成的托管副本/installed 包对应一致。
2. 完成宿主 offline/blocking、业务双库、真实 PG reader、相关前端 check/build/测试及跨版本合同验证。未执行与跳过单列。
3. PR 绑定精确 HEAD 的最终 CI；合并后读取权威主分支，验证文件树，按既有 deploy guard 部署授权范围内的产品。
4. 记录 SHA → Gateway/Vercel 部署 → 正式域名 → UI 版本 → 真实 QA 证据。运行前检查活跃任务，保存测试仅使用专用普通 QA。
5. progress、acceptance 与运维索引追加当前事实，原始失败保留；停止本轮拥有的临时进程，保留数据。
6. 逐条审计本文件和用户最终选择。任何 required gate 缺失，目标保持 active；不可用 provider 数值仍明确 unknown，不能把缺计量写成 0。
