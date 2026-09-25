# 回滚矩阵与前端合同（TR-16）

代码：`frontend/src/core/pick/types.ts`（观测条件、`sort=obs`、结果级 `observations`）、`obs-format.ts`（措辞与显示）、`obs-contract.ts`（证据拍平规则的严格校验，只给测试与 TR-36 的校验脚本用）、`format.ts`、`components/workspace/pick-board/views/replay-rules.ts`。合同：`docs/pick-workbench/observe-contract.md`（TR-33）。计划第 10 节、TR-16、D8、D10、D19、D31；设计 3.8、7.5 第 1 步。

本页只写回滚矩阵的逐格表与前端四格的保证方式。回滚规则的全文、最低可回滚版本与两个开关的关法由 TR-29 在索引与跨主题部分补写；后端两格归 TR-27，守卫归 TR-34。

## 记号

- 前端：F0 = TR-16 之前的版本；F1 = TR-16 及以后。
- gateway：G0 = 不带 0007；M0 = 带 0007、还不认识观测字段；M1 = 认识观测字段（TR-26 至 TR-28）。
- 卡片：旧卡 = 不带观测条件的结果；新卡 = 带观测条件、`sort=obs`、obs 证据与 `observations` 的结果；混合会话 = 同一会话里两种卡都有；存量快照 = 「我的选剧」里保存的条目（`/selections` 的 `snapshot_json`，按 `pickItemSchema` 解析）。

## 逐格表（计划第 10 节）

| 时段 | 组合 | 允许 | 由谁保证 |
|---|---|---|---|
| S1–S12 | F1 × M0 × 旧卡 | 是 | TR-16：`api.test.ts` 的「F1 x old card」与「F1 x stored snapshots」 |
| S11 起 | F1 × M1（开关关）× 旧卡 | 是 | TR-27 测试（开关关着时工具 schema 与卡片逐字不变） |
| S13 起 | F1 × M1 × 旧卡、新卡、混合会话 | 是 | TR-16 前端三格：「F1 x old card」「F1 x new card」「F1 x mixed session」；TR-27 后端两格 |
| S13 起开关又关 | F1 × M1（开关关）× 新卡 | 是，换一批返回「趋势条件尚未开放」 | TR-27 测试；前端照常读这张新卡（「F1 x new card」） |
| S3 起 | 任何 × G0 | 否 | 回滚规则；TR-34 守卫拒绝迁移头认不出的部署 |
| S13 起 | 任何 × M0 × 新卡 | 否（M0 解析不了新条件） | 回滚规则；只用 main 上的 revert 提交回滚 |
| S1 起 | F0 × 任何 | 否 | 回滚规则；TR-34 守卫的前端模式只从 `origin/main` 导出的干净目录部署 |

## 前端四格（TR-16，反例 14）

四格都在 `frontend/tests/unit/core/pick/api.test.ts` 的 `rollback matrix: the new frontend reads every card it can meet` 里，走真实的 `api.ts` 解析路径（只替换 fetch）：

| 格 | 用例 | 断言 |
|---|---|---|
| 新前端 × 旧卡 | `F1 x old card` | `getPickResult` 读 `backend-result.json`，没有 `observations` 键 |
| 新前端 × 新卡 | `F1 x new card` | `getPickResult` 读 `backend-result-obs.json`：`sort=obs`、`observations`、三种 obs 证据都在 |
| 新前端 × 混合会话 | `F1 x mixed session` | `listPickResults` 一次返回旧、新、旧三张卡，整批解析成功（它对整个会话做 strict 解析，一张解析不了整个面板就报错） |
| 新前端 × 存量快照 | `F1 x stored snapshots` | `listSavedPicks` 一次返回改动前保存的条目与带 obs 证据的条目，都能解析 |

「换成错误实现后变红」的记录（2026-09-25，任务分支 `feat/trends-radar-tr-16`）：

| 错误实现 | 变红的格 |
|---|---|
| 退回 F0 的 schema：`sort` 不认 `obs`，结果不认 `observations` | 新卡、混合会话 |
| 以为所有卡都是新卡：`observations` 必填 | 旧卡、混合会话 |
| 在解析时按 obs 拍平规则拒收证据（违反 D8） | 新卡、混合会话、存量快照 |

同一保证的旁证：`types.test.ts` 的「observation fields (TR-16)」（两份夹具都能解析，各层未知键仍被拒绝）；`contract.test.ts`；`pick-tool-card.dom.test.tsx` 的「a card with observations」（新卡能确认保存）；`server/pick-board/replay.test.ts`（回放在 200、409、410 下都拿得到带观测条件的 conditions）；`replay-rules.test.ts`（近似筛选把七个观测字段列为资料页没有对应筛选的条件）。

## 前端对新字段的做法

- **只放行、不收紧证据**（D8）：`pickEvidenceSchema` 不改，obs 证据沿用九个键；老前端也能解析新条目，一条格式不对的证据不会让整个面板解析失败。拍平规则由 `obs-contract.ts` 的 `obsEvidenceSchema` 严格校验，`contract-fixtures.test.ts` 用它跑 TR-33 的全部正例与反例，TR-36 的 `scripts/pick-validate-result.ts` 用它校验生产 shadow 结果。聊天卡片不引入它（`client-bundle.test.ts`）。
- **条件与 observations 严格**：七个观测条件键与 `observations` 的形状照合同写死，未知键、错误的 geo 与国家码、不在清单里的排除原因都被拒绝；`observations` 只能缺省，不能为 null。
- **「未观测到」（前提 1）**：obs 证据的值为空（null 或空串）时，卡片显示该通道的「未观测到」措辞，note 里已经写了就不重复；GSC 的值不是正整数、Trends 的值不是状态码时，只显示「取值不符合约定，未展示」，从不把 0 或指数显示出来。措辞常量与禁用词在 `obs-format.ts`，`obs-format.test.ts` 从 `contract.py`、`wording.py` 的源码读出同名常量逐字比对。
- **近似筛选**：七个观测字段接在现有七项之后，按合同的 `UNMAPPABLE_OBS_ORDER` 与 `OBS_CONDITION_LABELS` 列出（`unmappable_cases.json`）。

## 夹具

- `frontend/tests/unit/core/pick/fixtures/backend-result-obs.json`：TR-33 合同夹具 `result_obs.json` 的正例，用 `json.dumps(indent=2, ensure_ascii=False)` 原样写出；`contract-fixtures.test.ts` 钉住两者相等。TR-27 改由 `test_frontend_contract.py` 从真实同步重新生成，那时把这条相等断言换成对真实结果的合同校验。文件已列入 `frontend/.prettierignore`，排版以生成器为准。

## 上线（S1）

TR-16 先于任何会输出观测字段的 gateway 上生产（设计 7.5 第 1 步，计划第 10 节 S1），经 TR-34 守卫的前端模式部署，需用户批准。S1 之后前端不回退到 F0。
