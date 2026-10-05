# 选剧结果写入飞书：下一阶段实施规格

状态：规格交付，外部写入尚未实现。首版只支持“把明确选中的条目写入一个预配置飞书多维表格”，不自动排期、不发布视频、不批量改写任意文档或联系人。

## 1. 完成目标

用户先看到精确写入目标和字段，再明确确认；系统生成可重放的命令，验证外部结果并提供回执。响应丢失、进程重启、权限撤销、表结构变化都不能导致静默重复写或假成功。

复用宿主每用户集成和认证，不能把管理员 token 放入业务数据、前端、模型参数或普通用户的命令。内部业务写仍由 `ggwork_pick` 扩展承担，不搭建另一套 Agent runtime。

## 2. 预配置和准备阶段

一个目标配置包含 app/table 的实际标识、可写字段 ID 映射、scope、配置版本、schema hash、集成 owner 和允许使用的团队或个人作用域。字段按服务端配置选取，客户端不得提交任意目标 URL、字段 ID 或 credential。

最小写入字段为剧名、平台、语种、来源版本/日期、来源引用、选剧备注和 `ggwork_command_id`。后者是专门的命令标识字段；不能假定飞书为它提供唯一约束。

实施前使用已有集成工具/CLI 或官方 API 核验：实际身份、表读写权限、字段类型、记录创建/读取/过滤能力和限流响应。以现场能力确定 adapter 调用，不能把此规格里的能力假设当作 API 已支持。

- prepare 验证 principal 对来源 selection/team_item 的权限和目标授权。
- 读取当前目标 schema，验证字段映射和可存储文本，生成完整显示草案。
- 返回 command_id、来源 snapshot、目标名称、逐字段拟写值、schema/config 版本、payload hash 和服务端确认 token。
- prepare 不建立外部记录；模型只有 prepare 权限。

## 3. 确认与内部事务

`POST /api/pick/external-writes/confirm` 接收 command_id、确认 token 和 request_id。确认时再次验证 principal、来源、目标权限、配置/schema 版本及草案 hash。

一次业务事务建立命令、唯一 request_id 回执和业务 outbox 行；在事务提交前不调用外部服务。宿主现有 `McpTaskService.submit()` 会先执行 driver.submit，再独立持久化任务，不能直接当作本业务事务的 outbox；普通 MCP driver 也不等于飞书 CLI adapter。

首版由业务扩展注册一个处理既有 outbox 的 Gateway 生命周期服务，不运行新的 Agent 图。worker 用状态/version 的原子条件更新领取 queued 行，写入执行者和租约；任务参数只含命令引用，不含凭据。完成写入必须核验执行者、版本和 lease；过期 executing 行进入 uncertain，接管者先核对，不能当作新 queued 命令再 create。外部 API 不提供 fence 时，内部租约不保证远端 exactly-once。多进程领取、接管与暂停必须有 SQLite/PostgreSQL 竞争测试。

建议文件：`ggwork_pick/external_writes/{contracts,repository,service,worker,lark_adapter,routes}.py`。新业务表走独立 `ggwp_` 迁移链，revision 在实施时从实际头生成，不复用预留编号。未来若改接宿主持久任务驱动，必须先验证事务接缝、启动注册和业务 fence，不沿用尚未证明的原子性承诺。

| 命令字段 | 要求 |
|---|---|
| owner/team scope | 服务端身份与授权快照；重试时再次核验 |
| source snapshot | 不可变的确认时来源；不在重试中偷换最新剧库 |
| target/config/schema | 固定的目标标识、版本及 hash |
| request_id/payload_hash | 同 id 同 body 重放原回执；不同 body 返回冲突 |
| status/version | 有限状态、乐观并发版本及任务所有权 fence |
| external_record_id | 只由实际响应或核对结果填写 |
| attempts/events | 追加记录；token、原始 headers 和 credential 永不持久化 |

## 4. 外部执行状态机

`prepared → queued → executing → succeeded`；queued 可进入 `cancelled`。执行失败按证据进入 `failed` 或 `uncertain`，不能都变成 failed 并自动重发。

- 请求尚未发送且权限/输入验证失败：failed，可在新确认后建立新命令。
- 外部返回明确拒绝且能证明未创建：failed，保存非机密原因。
- 外部返回创建 ID：读取该记录，核验命令标识和拟写字段；通过才 succeeded。
- 超时、连接中断、进程丢失或请求已发出但无权威结果：uncertain，先核对，不能直接重发 create。

核对使用固定目标内的 `ggwork_command_id` 精确查询，完成分页、字段及作用域验证。零结果只有在 API 一致性/权限条件足以证明未创建时才允许安全重试；无法证明时继续 uncertain。一个匹配且字段一致可恢复 succeeded；多个匹配或内容不一致进入人工核对。

不假定外部查询马上可见。不能为了回滚而盲删可能已被人工编辑的外部记录；补偿是新的、明确确认的命令。

## 5. 授权、幂等和故障边界

- queued/executing 任务每次取凭据时验证集成 owner、当前权限及目标配置；成员撤销、凭据失效或配置改变不能沿用旧授权继续写。
- 服务端只向已核验的官方目标发送请求；不以模型给的 URL 作为写入地址。
- 请求默认一次 create。任何 create 重试必须有“未产生外部记录”的证据；仅对只读核对执行有限重试和既有退避。
- 任务接管和完成使用业务 outbox 的执行者/lease/version fence；旧 worker 恢复后不能更新命令。若它已发出外部请求，接管者不能重复 create，只能先核对。若外部 API 不能提供幂等或 fencing 能力，持有租约不等于远端 exactly-once，应在规格/回执中保留 uncertain 状态。
- 内部命令和外部记录之间不宣称分布式原子事务。取消 queued 的操作检查当前 principal、expected_version 和 status=queued，并与 worker 领取使用相同原子条件；取消获胜则终态 cancelled、保留幂等回执且不得发出外部请求。worker 已领取则返回 409 和当前状态；首版不提供 executing 取消承诺，调用方通过结果核对处理已发出的请求。

## 6. 接口和 UI

- `POST /api/pick/external-writes/prepare`：绑定允许的来源与预配置 target_ref，返回具体草案。
- `POST /api/pick/external-writes/confirm`：创建一次持久命令，返回 queued/已有回执。
- `GET /api/pick/external-writes/{command_id}`：检查 owner/team 权限后返回状态、外部记录链接及核对依据。
- `POST /api/pick/external-writes/{command_id}/reconcile`：仅执行核对，不隐含新的 create。
- `POST /api/pick/external-writes/{command_id}/cancel`：接收 request_id、expected_version，仅取消仍 queued 的本人/授权团队命令；重放原回执，不改变已经领取或终态命令。

确认页展示目标表、每个字段及来源日期，注明备注是否写入。用户看到 queued 是“等待写入”，executing 是“写入中”，uncertain 是“结果待核对”，cancelled 是“已取消，尚未发送”；只有 verified succeeded 才显示“已写入”。queued 显示取消操作，领取竞争返回 409 后展示当前状态并引导核对。模型不能仅因调用 prepare 或获得 queued 回执就说已写入。

## 7. 必须先写的测试

1. prepare/未确认不产生外部请求；客户端伪造目标、owner、credential 或 scope 全部拒绝。
2. 同 request_id/body 重放一个命令；不同 body 冲突；内部事务失败不留半条 outbox。
3. 真实提交后丢响应，mock remote 已有一条记录：核对恢复相同 external_record_id，不第二次 create。
4. 不一致/多个/权限不足/分页不完整查询结果继续 uncertain，不转换成“未创建”。
5. 旧 worker 接管、延迟响应、重启、成员撤销和 schema 改变均有明确同步点测试，不能用睡眠猜测窗口。
6. 明确 429/403、服务端错误、超时等路径的有限请求数、状态与日志脱敏。
7. SQLite/PostgreSQL 的约束及回执一致；普通身份不能读另一 owner 的命令和来源。
8. 浏览器验证实际确认、状态轮询、uncertain 恢复、刷新和外部已验证链接；成功反馈来自回执。
9. 取消与领取的两个竞争顺序、重复取消、过期 expected_version 和跨 owner 取消均有确定性测试；取消获胜时外部 create 次数为 0，领取获胜时取消请求拒绝。

测试先使用合成 target/remote。真实验收必须使用用户授权的隔离 QA 表及专用身份，预先锁定字段、允许记录数和核对方式；实际 API 能力、scope 和配置需在该阶段重新读取。

## 8. 发布和交付门槛

独立安全/代码审查、源码/托管副本一致、双库与宿主持久任务测试、前端 check/build、精确 HEAD CI、部署守卫和真实隔离验收全部完成后，才可称外部写入可用。仅能创建一条记录或某个工具已安装，均不足以证明整个闭环。

交付报告必须记录应用 SHA、target 配置版本、权限验证、成功与 uncertain 样本、真实外部记录核对和剩余限制；不提交凭据、私人业务内容或原始授权响应。
