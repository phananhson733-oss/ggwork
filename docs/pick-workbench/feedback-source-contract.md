# 飞书反馈来源契约与实施证据

2026-10-07，实施分支 `codex/feishu-feedback`，基线 `ggwork/main@fef2b891`。本文件持续记录已验证事实与尚未完成的验证，不将计划当作运行结果。

## 执行目标与现有契约

- 业务远端为 `ggwork`（GGWork业务仓库），`origin`是DeerFlow上游，不能从origin/main实现本功能。
- 独立worktree：`<feedback-worktree>`。
- 新主干的模块AGENTS要求候选附加信息经 `GET /results/{id}/notes` 返回。反馈使用独立result-evidence关联记录，不往原候选/items快照增加字段；模型工具投影可以附带同一份证据。
- 原spec中“候选快照扩展”为逻辑需求，落地调整为私有feedback result evidence表；历史固定要求不变。不得改变现有strict result协议或候选编号。

## 来源路径

来源Base及15表见配套spec。现有 `ggwork_pick.lark_runner` 已有按用户凭据隔离、最小环境、降权、锁和有界输出，优先复用；不能用网关环境中的全局token替代用户身份。

本机 `lark-cli` 帮助已验证：
- `base +field-list` 支持 `--offset/--limit`，只读；最多200字段/页。
- `base +record-list` 支持显式 `--as user`，不传view读取表范围；JSON limit最多200，NDJSON最多2000；NDJSON通过相对output文件及manifest返回。
- 现有runner每次在临时目录运行且最终销毁；stdout会截断。因此反馈适配器必须在临时目录存活期间安全读取固定名称artifact，并验证未截断和manifest分页；不能把被截断stdout算作全量成功。
- 生产固定CLI版本必须由运行时probe核验；本机帮助不证明生产输出协议完全相同。

## 授权与live验收

用户已完成本机最小只读授权，AI已执行device-flow完成步骤（退出码0）；实际以 `--as user` 成功读取schema及记录。凭据没有复制进云端，没有切换bot。此前user missing已解除。

真实读取发现并修复：field-list返回 `fields + total`；小字段页存在重复ID，因此改用最大200字段/页并保留总数稳定性及重复检测。NDJSON成功stdout是bare manifest，不含ok；适配器验证退出码、v1/ndjson标识、stdout与文件共同元数据一致，再执行严格字段/条数检查。错误JSON可能只在stderr，非零退出时安全解析。视频链接是Markdown形式，已补正确的平台识别。

授权来源验证记录保存在本地受控目录，不公开业务数量或明细。云端用户身份与授权需独立验证。

## 测试证据

隔离worktree已建立独立Python虚拟环境，测试显式将本worktree的harness/extension-api/backend加入PYTHONPATH；不修改原共享虚拟环境。

未改业务实现前的完整基线：3711 passed、923 skipped（159.85秒）。跳过项不算验证完成。

已建立仅监听127.0.0.1的独立PostgreSQL17临时集群用于新代码测试，不使用生产数据库。反馈、CLI隔离执行器、PG迁移及旧镜像迁移的定向回归曾达到169 passed；后续归因修复仍持续追加用例，最终全量结果见feedback-progress。

## 实施中的契约适配

- 新增独立 `ggwp_feedback_runs`，不复用面向全部登录者的共享sync运行列表，防止财务来源状态跨用户泄漏。
- 来源schema绑定从上一成功版本manifest加载，保留field_id/type/semantic_name；初始绑定使用15表的必需字段/类型检查。此检查是保守bootstrap，并不替代真实schema验收。
- 超过20秒的同一请求可以凭已授权的run receipt继续等待，完成后使用该receipt的版本；不会每次重试重新扫描。receipt仅在服务端查owner、终态与短有效期后使用；新选剧仍需新刷新。
- 请求等待时间包含数据库claim和轮询；admission与worker属于服务，单个调用方的超时不取消它们。
- 本机真实CLI返回兼容性已完成live核验；生产CLI版本及用户连接仍需部署验收：当前适配器严格检查固定artifact、manifest、记录数和类型，未知shape直接拒绝，不回退截图或不完整stdout。
