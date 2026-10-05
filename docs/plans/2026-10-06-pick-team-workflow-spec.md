# 个人选剧进入团队制作流程：下一阶段实施规格

状态：本轮交付的是可实施规格；团队功能尚未实现。当前个人工作台、候选快照与确认保存合同继续作为来源。本文默认首版服务一个小团队；这些业务默认值应在实施 PR 中明确展示，不能当作既有线上行为。

## 1. 目标和首版范围

把成员已经选中的剧，经明确分享加入团队池，指定制作负责人，进入制作、待发布、排期和发布回填；每一步有权限、状态和审计记录。个人清单和团队池是两个对象，分享不会移动或删除个人记录。

首版支持一个团队的 owner、editor、viewer 三种角色；允许多团队数据隔离，但不做跨团队共享、复杂审批、自动发布、收益结算或自动写飞书。外部写入遵循另一份规格，不能把修改内部状态当作外部发布成功。

## 2. 来源与隐私合同

- 复用宿主身份和认证 principal。team_id、owner_id、assignee_id 等请求字段不能授予权限。
- 只有个人记录的真实 owner 可以发起分享。模型只准备分享草案，用户看到具体团队、剧目及共享字段后明确确认。
- 团队池保存独立、不可变的分享来源快照和来源版本；不得让另一成员访问原 owner 的私人 result/notes API。
- 默认共享 title、theater、language、入选依据及来源时点；个人 note 不自动共享。用户明确选中共享备注时才复制该字段。
- 团队去重身份使用既有完整 identity（平台、series_id、语种）；同名不等于同剧，同剧不同语种保持独立。
- 历史来源更新不自动改写已有团队快照。刷新资料是一条单独、可审查的操作，保留旧版本。

## 3. 对象和存储

业务表继续使用 `ggwp_` 前缀、业务扩展独立 Alembic 链；先读取真实链头再创建新 revision，不占用旧计划的预留编号，不修改宿主迁移。

| 对象 | 最少字段及约束 |
|---|---|
| team | id、name、created_by、created_at、version；创建者成为首个 owner |
| membership | team_id、user_id、role、active、version；团队内用户唯一；禁止移除最后一名 active owner |
| team_item | id、team_id、identity、source_snapshot、source_owner_id、shared_by、shared_at、state、assignee_id、version；团队内 active identity 唯一 |
| schedule | team_item_id、channel、account_ref、publish_at_utc、timezone、version；账号是经授权的内部引用；保留用户输入时区 |
| publication | team_item_id、external_url、published_at、recorded_by、recorded_at、evidence；只有明确回填才建立 |
| transition | item_id、actor_id、from_state、to_state、expected_version、command_id、occurred_at；追加记录，不能覆盖历史 |
| command receipt | team_id、actor_id、request_id、payload_hash、outcome；相同 request_id/相同 body 返回相同回执，不同 body 拒绝 |

所有可存储文本沿用 StrictInput、storable、VARCHAR 长度与 UTC stamp 规则。snapshot 只复制允许共享的字段，不保存认证令牌。

## 4. 权限与状态

| 操作 | owner | editor | viewer |
|---|---|---|---|
| 读取团队池及来源快照 | 可以 | 可以 | 可以 |
| 分享本人个人记录 | 可以 | 可以 | 不可以 |
| 管理成员、分配负责人、取消任务 | 可以 | 不可以 | 不可以 |
| 推进制作状态 | 可以 | 仅本人负责的任务 | 不可以 |
| 排期与发布回填 | 可以 | 仅本人负责的任务 | 不可以 |

team_item 状态为 `selected → in_progress → ready → scheduled → published`；非 published 项可经 owner 操作进入 `cancelled`。负责人分配独立记录，不以“分配成功”冒称已经开始制作。

| 转移 | 条件 |
|---|---|
| selected → in_progress | 已有 active editor/owner 负责人 |
| in_progress → ready | 制作完成说明可存储；actor 有状态修改权 |
| ready → scheduled | 已填写经授权的账号、渠道及有效带时区发布时间 |
| scheduled → ready | 撤销内部排期，保留原排期审计 |
| scheduled → published | 明确回填实际发布 URL/时间；不能由计划时间到达自动生成 |
| 任意非 published → cancelled | owner 确认取消；保留全部历史 |

已发布内容更正仅新增更正记录，不能回退到未发布并抹去证据。状态修改必须提供 expected_version；并发过期返回 409，不能覆盖另一成员修改。

## 5. 接口与模型边界

路由归业务扩展，建议文件 `ggwork_pick/team/{contracts,repository,service,routes}.py`，前端归 workspace 的团队页面。遵循现有路由注册和 owner 检查，不创建另一套用户系统。

- `POST /api/pick/teams`：创建团队及首个 owner，事务完成。
- `GET /api/pick/teams` 与 `GET /api/pick/teams/{team_id}/items`：仅返回 active membership 可见对象；过滤和分页在权限范围内进行。
- `POST /api/pick/teams/{team_id}/share/prepare`：绑定本人个人 selection、允许共享字段与团队，返回具体草案和确认 token，尚无 team_item。
- `POST /api/pick/teams/{team_id}/share/confirm`：principal、草案 hash、状态、成员资格再次核验；一次事务建立 team_item 和回执。
- `PATCH /api/pick/teams/{team_id}/items/{item_id}`：只接受负责人等允许字段及 expected_version；不能直接更新 state/source_snapshot。
- `POST /api/pick/teams/{team_id}/items/{item_id}/transitions`：按有限状态表执行，绑定 request_id 与 expected_version。
- 排期、发布回填和成员变更使用独立请求模型，禁止任意 JSON patch。

模型工具只允许读取、准备草案。confirm/transition 属于用户明确操作；模型调用不能绕过确认或通过传入角色提升权限。

## 6. UI 验收

团队页显示负责人、状态、版本、来源日期和下一步允许操作。无权限按钮不显示；后端仍独立拒绝请求。确认分享页列出团队名称、剧目、会共享的备注；成功反馈来自真实回执。

409 后显示最新状态和冲突原因，由用户重新确认；不要在后台自动重发旧状态操作。发布状态区分“计划发布”和“已回填实际发布”。

## 7. 实施顺序与测试

1. 先写成员/owner 隔离、最后 owner、共享字段和身份去重的失败测试；实现 repository/service 后跑 SQLite/PostgreSQL。
2. 写状态机、expected_version、事务幂等和响应丢失测试；并发用显式事件同步，不使用 sleep 猜测竞争窗口。
3. 接路由并验证客户端伪造 owner/role、跨团队、跨私人候选、移除成员后的旧确认 token、过期草案全部拒绝。
4. 完成团队页面、错误反馈和既有个人清单回归，再使用两个合成普通身份执行分享、分配、制作、排期、回填与刷新 E2E。
5. 官方 extension manager 同步托管副本，独立安全/代码审查、最终 CI；按实际授权部署，真实业务写验收使用隔离 QA 团队。

完成必须证明：未授权分享不泄露私人候选；未确认不写团队池；两种数据库拒绝相同非法值；并发不丢修改；重复确认只生成一个团队对象；每个发布状态有实际回填证据。用户可看到对应来源和审计记录。

## 8. 交付说明

实施 Agent 在报告中区分已完成接口/UI、合成测试、真实 QA、生产发布和仍待业务决定的规则。本文默认角色与状态是首版建议，实施前用户若改变业务要求，应更新规格而不是写入隐藏例外。
