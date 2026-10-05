# 运行用量观测与取消核对

新增 `RunResponse.metadata.deerflow_usage_observation` 用于说明实际观测到的调用生命周期和供应商用量。它与原整数 token 字段并存；没有数据库迁移。实际部署状态见 [progress](progress.md)，不能仅凭本文或代码存在判断已上线。

| 字段 | 含义 |
|---|---|
| version | 当前合同版本 1 |
| finalized | 终态观察已组装；不表示供应商账单完整 |
| call_scope | `local_callback_lifecycle`：计数针对本 journal 的实际 LangChain 回调 UUID，不能解释为 provider HTTP 重试数 |
| calls_started/completed/errored/cancelled | 对应已观测回调事实，同 UUID 去重；完成回调缺失不补造 |
| calls_missing_usage/calls_partial_usage | 没有用量字段、或者只有部分/异常路径用量的调用数 |
| external_usage_reports | 按来源去重的 external/subagent 用量报告数；不能据此推算子调用生命周期 |
| coverage | complete、partial、unknown、no_calls；供应商字段的完整性与回调边界决定，不由 run success 单独决定 |
| known_input_tokens/known_output_tokens/known_total_tokens | 实际收到的各字段小计，包含去重 external 报告；没有观测到该字段时为 null，明确返回的 0 保持 0 |
| reasons | 缺失、部分、不一致、关闭跟踪、缺开始回调、调用尚未终止或 external 生命周期未知等有限原因码 |

`known_*` 为已观测小计，partial/unknown 时不等于最终用量。不能拿旧 API 默认 0、已发出请求或工具调用 0 证明模型没有消耗。显式输入/输出/total 不一致时保留供应商字段并标 `inconsistent_provider_usage`；不把派生值冒充实际返回值。

`no_calls` 仅在终态观察已组装、journal 无缺失原因且本地开始/完成回调均未观测到时出现，描述本 journal 的回调范围。它不能证明供应商 HTTP 请求数或账单为 0；没有收到 token 字段时仍保持 null。历史无键、未建立 journal 或关闭跟踪的运行不会据此变成 no_calls。

## 取消路径

LangChain streaming 异常回调若提供部分响应，其已知 usage 会记录下来，但不会生成虚假的完成 AI 消息或工具结果。供应商没有返回最终 usage 时，取消记录仍显示 unknown/partial。即使运行已 interrupted，费用也不能从这些字段直接计算。

普通运行创建和原子 thread operation admission 均覆盖客户端伪造的保留键，保留其余 trace/replay 元数据。progress、finalizing、completion 在既有受状态约束的 store UPDATE 中持久化；旧 worker 丢失 ownership 后不再覆盖。非模型 checkpoint reservation 不添加模型计量记录。

旧运行没有该键时，按历史计量未核实处理，不自动填 complete 或 0。原 2026-10-06 Q19 的 token 无可恢复证据，继续为 unknown。

## 验证

`backend/tests/test_run_usage_observation.py` 覆盖真实本地异步流在部分 usage 后取消、缺 usage、显式零、不一致数字、重复回调、external 去重、closed journal、即时 admission 持久化、新 manager hydration、worker 取消和 memory/SQLite/临时 PostgreSQL。

真实验收使用专用普通 QA 身份和统一模型运行账本。核对 owner/thread/run、最终状态、metadata 与原始事件；实际 provider 未返回的字段保持 unknown。测试输入 7/3/10 是合成用量，不代表生产账单。
