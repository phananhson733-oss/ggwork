# 飞书反馈验收台账（2026-10-07）

实现分支 `codex/feishu-feedback`，基线 `ggwork/main@fef2b891`。状态：软件实现及本地验证完成到下列证据范围，**独立复审确认的10项缺陷及后续边界已修复，本地复验通过（含最后补修的候选/反馈原子发布）**。代码已随 PR #44 部署；没有权限扩展或反馈生产调度启用，详见[发布记录](releases/2026-10-07-feishu-feedback.md)。

## 验收映射

| 编号 | 证据 | 状态及限制 |
|---|---|---|
| A01 | feedback/test_source.py、test_snapshot_contracts.py、test_feishu_source.py；浏览器15/15表 | 软件验证通过；A14历史验收已验证当时用户视角15表全部记录与完整终止页 |
| A02 | test_repository.py：失败保留current、过期writer拒绝；PG真实锁等待回归 | SQLite/PG测试使用独立数据库；生产迁移见发布记录 |
| A03 | schema baseline改名/删除/类型变化、hash不含scan时钟、同内容复用版本、新增允许字段拒绝静默遗漏 | 合成schema/真实DB通过 |
| A04 | test_sync.py的慢claim等待、pending coalesce、receipt恢复、废弃轮询取消；PG takeover | 调用方预算与worker隔离通过；前台pending续查通过 |
| A05 | routes/runtime与浏览器auth/partial/pending/失败状态；私密错误正文测试 | 通过；未将旧版本误标最新 |
| A06 | test_result_evidence.py、test_tool_pinning.py实际历史/详情/分析工具两种版本调用顺序；浏览器150→900且旧证据不变 | 通过；独立sidecar保持原candidate/items协议 |
| A07 | test_identity.py/test_normalize.py同名异语、未解析竞争关联、第25候选冲突、RSBoost命名空间 | 合成身份用例通过；未确认收入关联保持未知 |
| A08 | per-metric latest/null/0、缺失字段/失败采集、pending发布抑制、时点与30天规则 | 通过；不生成不存在的D7历史 |
| A09 | lane/currency/grain/basis隔离；账号/渠道筛选不带不适用整剧收益；无币种已知12/0订单及缺失值 | 通过；未归因收入保留原始来源，不分摊到剧/题材实绩 |
| A10 | 25帖子分页不改变总体与分母，多标签非可加、场景构成/年龄分布 | 通过；统计是描述性关联，不是因果结论 |
| A11 | country提前返回unsupported；无付费地区/转化分母不推断 | 通过 |
| A12 | owner仓库/HTTP/工具输入、source scope、隔离CLI、artifact安全读取；DB/前端审查 | 通过；生产owner配置未启用 |
| A13 | 工具注册/最终白名单/显式pick-drama技能、默认关闭、前端check/build与真实HTTP浏览器 | 通过到脚本化工具调用层；没有声称真实模型自主调用或生产验证 |
| A14 | 授权来源验证结果保存在受控内部报告 | **本地真实来源验收通过**；代码已部署；云端反馈授权尚未验收 |
| A15 | 无新综合评分/重排/飞书写入；浏览器先不保存再显式保存及幂等重试 | 通过 |

## 最终软件验证

- 全业务扩展回归含SQLite及独立PostgreSQL17：**4871 passed、25 skipped，374.21秒**。日志：`backend/.deer-flow/review/atomic-final-backend-pytest.log`。跳过项不算通过；测试使用本任务的临时数据库。
- 所有352个业务Python文件Ruff检查及格式检查通过，git diff --check通过。
- 官方 `deerflow extensions upgrade` 已生成托管快照并安装。最终全回归包含 `test_managed_copy`，先前托管副本滞后的一项失败已通过更新副本消除，未降低断言。
- 前端 **678项定向测试**、`pnpm check`及生产`pnpm build`通过。日志：`backend/.deer-flow/review/final-frontend-tests.log`、`final-frontend-check.log`、`final-frontend-build.log`。
- 原普通10/20条模型投影基准 `--require-target` 通过，分别为25.73%/27.59%字节减少；不等于新反馈的token/成本节省。
- F1–F10、后续订单计数/CPU异步边界及F11候选原子发布均已修复；每组独立需求复核及随后质量复核通过。版本冲突、权限门禁、废弃轮询、页面迟到响应、身份图冲突、币种缺失计数与取消/租约均补有反例回归。
- F11反馈构建在候选插入前完成，候选与冻结证据同事务发布；失败/取消时两者回滚，列表/详情/保存均不能取得半成品。历史候选不补写，重复调用返回已有证据。反馈套件SQLite/PostgreSQL255通过，独立需求、代码、Python及数据库复核通过。
- 大快照发布准备、读取重建和双扫描哈希在线程中执行，SQL事务仍在事件循环；确定性测试验证可调度、取消及租约边界，不作为硬实时保证。
- Python、数据库、前端与测试夹具均经独立审查。原生schema、complete/已回填状态、favorites缺失别名、Markdown链接及缺Post ID通过唯一采集关联补全的新增逻辑均有回归与审查证据。
- 新增4个反馈JSON位置进入既有只读敏感片段检查，并作为不可随意原地改写的历史证据保留；SQL/文本/旧版本兼容检查通过，没有运行生产清洗。

## A14真实来源验证的公开边界

完整实采报告、原始业务数据和回查结果仅保留在本地受控目录。本次公开交付包含软件测试与工程结论，不发布真实来源数量、账号、金额或覆盖统计。本机只读验证不等于云端授权、生产启用或定时运行验收。

## 浏览器证据

隔离实例使用真实Gateway认证、SQLite、HTTP接口、查询工具、候选notes和前端。飞书provider为明确合成来源，模型由脚本化工具调用代替；没有mock反馈HTTP路由。

- 最终修复版稳定复跑：**3/3通过，29.5秒，零重试/跳过**。日志：`backend/.deer-flow/review/atomic-final-e2e-tests.log`。
- 覆盖15表刷新、缺失/零值、历史候选证据、源变化不改旧结果、partial/auth/失败/pending和显式保存。
- 专用Playwright配置禁用trace/video，避免记录登录会话；测试凭据仅在私有忽略目录。
- QA服务已按进程身份核验后关闭，截图及报告保留。

截图目录：`frontend/test-results/feedback/artifacts/pick-feedback-synthetic-so-83e6d-ence-candidate-notes-and-UI/`，包含 `feedback-15-tables.png`、`feedback-frozen-candidate.png`、`feedback-auth-required.png`、`feedback-pending.png`。报告：`frontend/test-results/feedback/report/index.html`。复现见[feedback-e2e.md](feedback-e2e.md)。

## 交付边界

本地主要链路与历史真实来源验收有证据；独立复审发现的10项缺陷已有逐项修复、需求复核与质量复核，本地复验通过。N1日增量查询范围及表格映射设计延期讨论，15表入库不等于15表全部语义均有查询入口。没有声称真实LLM自主选用工具、云端用户连接、生产浏览器或生产定时运行已验证。功能和调度默认关闭；代码部署和0008迁移已完成；owner授权和启用另行验收，启用调度后还需观察下一次真实触发。

外部模型交叉审查未运行（Codex宿主不兼容）；采用当前宿主独立审查。设计机械检测器不可用，代码与保存截图已核验。修复清单见[复审修复计划](../plans/2026-10-07-feishu-feedback-review-fixes.md)。

详细真实数据缺口及字段建议见[feedback-live-findings.md](feedback-live-findings.md)，启停与回滚见[feedback-runbook.md](feedback-runbook.md)。

发布后状态以[PR #44 发布记录](releases/2026-10-07-feishu-feedback.md)为准：代码、迁移及正式域名已验证，owner 授权、真实生产反馈读取及调度启用仍未验收。
