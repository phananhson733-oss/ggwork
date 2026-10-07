# 回滚矩阵与前端合同（TR-16）

代码：`frontend/src/core/pick/types.ts`（观测条件、`sort=obs`、结果级 `observations`）、`obs-format.ts`（措辞与显示）、`obs-contract.ts`（证据拍平规则的严格校验，只给测试与 TR-36 的校验脚本用）、`format.ts`、`components/workspace/pick-board/views/replay-rules.ts`。合同：`docs/pick-workbench/observe-contract.md`（TR-33）。计划第 10 节（回滚规则与回滚矩阵）、TR-16、D8、D10、D19、D31；设计 3.8、7.5 第 1 步；G3 处置（计划第 14.3 节，分层 P2-4 与「回滚矩阵应明确数据库维度」）。

本页写回滚矩阵的逐格表（含数据库维度）、来源检查与合同能力检查的分工、每次发布与回滚都要跑的四格验证，以及前端四格的保证方式。两个开关的关法与新迁移上线流程由 TR-29 在索引与跨主题部分补写；后端两格归 TR-27，守卫归 TR-34（`deploy-guard.md`）。

## 记号

- 前端：F0 = TR-16 之前的版本；F1 = TR-16 及以后。
- gateway：G0 = 不带 0007；M0 = 带 0007、还不认识观测字段；M1 = 认识观测字段（TR-26 至 TR-28）。
- 库：DB0006 = 生产迁移头是 0006；DB0007 = 生产迁移头是 0007，或之后本镜像认识的修订。
- 卡片：旧卡 = 不带观测条件的结果；新卡 = 带观测条件、`sort=obs`、obs 证据与 `observations` 的结果；混合会话 = 同一会话里两种卡都有；存量快照 = 「我的选剧」里保存的条目（`/selections` 的 `snapshot_json`，按 `pickItemSchema` 解析）。

## 逐格表（计划第 10 节）

| 时段 | 组合（前端 × gateway × 库 × 卡片） | 允许 | 由谁保证 |
|---|---|---|---|
| S1–S2 | F1 × G0 × DB0006 × 旧卡 | 是，S1 到 S3 之间的实际过渡状态 | TR-16：`api.test.ts` 的「F1 x old card」「F1 x stored snapshots」；G0 是当时生产上未改的 gateway |
| S3 部署中 | M0 启动时面对 DB0006 | 升级过程：M0 启动时把库迁到 DB0007，不是计划长期运行的组合 | TR-11 的 `test_0007.py`（升级、重跑、补齐部分执行）；S4 核对迁移头 |
| S3–S12 | F1 × M0 × DB0007 × 旧卡 | 是，S3 之后本阶段的正常状态 | 下文四格验证的旧卡格与存量快照格；S4 的认证业务路径 |
| S11 起 | F1 × M1（开关关）× DB0007 × 旧卡 | 是 | TR-27 测试（开关关着时工具 schema 与卡片逐字不变） |
| S13 起 | F1 × M1 × DB0007 × 旧卡、新卡、混合会话 | 是 | TR-16 前端三格：「F1 x old card」「F1 x new card」「F1 x mixed session」；TR-27 后端两格；TR-36 |
| S13 起开关又关 | F1 × M1（开关关）× DB0007 × 新卡 | 是，换一批返回「趋势条件尚未开放」 | TR-27 测试；前端照常读这张新卡（「F1 x new card」） |
| S3 起 | 任意前端 × G0 × DB0007 | **否**：旧扩展不认识迁移头，扩展加载失败；宿主 `/health/ready` 仍可能通过，不能靠健康检查判断 | 回滚规则；TR-34 守卫拒绝迁移头认不出的 gateway 部署；S4 的业务路径核对 |
| 已有新卡后（S13 起） | 回滚到 M0（任意前端 × M0 × DB0007 × 新卡） | **否**，不能当安全回滚方案：M0 解析不了新条件 | 回滚规则；合同能力检查（守卫放行 main 上的 revert，拦不住它） |
| S1 起，已有新卡后尤甚 | 恢复 F0（F0 × 任意） | **否**：新卡的 `observations`、`sort=obs` 等未知字段让 strict 解析失败；Git revert 本身不保证解析能力 | 回滚规则；四格验证的前端格；守卫的前端模式只管来源 |
| 任何时候 | 生产执行 0007 downgrade | **否**，不是无损回滚：它删掉观测表、视图与候选集的新增列，观测历史随之丢失 | 回滚规则：0007 的降级只在测试库上用 |
| 任何时候 | 停 cron、关开关、保留 DB0007 与最低兼容网关 | **是，正确的止损方向**：S3 之后最低 M0，产生新卡后最低 M1 | 回滚规则（计划第 10 节）：按顺序关 `PICK_OBS_AGENT`、关 `PICK_OBS_PUBLISH`、停 cron，不回退镜像、不降级库 |

## 来源检查与合同能力检查

两类检查回答的是不同的问题，每次发布、每次回滚都要两类都过，缺一不可。

| | 来源检查 | 合同能力检查 |
|---|---|---|
| 谁做 | TR-34 守卫（`deploy-guard.md`） | 下文的四格验证：现有测试加部署后的手工核对 |
| 保证什么 | 提交来自共享仓库的 `ggwork/main`（守卫默认远端 `ggwork`；本检出的 `origin` 是上游 DeerFlow）；工作区干净、没有 `.env*`；是生产上一次守卫记录的提交的后代（不从旧检出回退）；迁移链认识生产迁移头（cron 还要求等于链头） | 要部署的代码仍读得了旧卡、新卡、混合会话与存量快照：前端不低于 F1，gateway 不低于当时的下限（S3 之后 M0，产生新卡后 M1） |
| 不保证什么 | 合同能力。在 main 上 revert 掉 TR-16 的前端解析改动（或 S13 之后 revert 掉 TR-26 至 TR-28），新提交仍是上次生产提交的后代、仍在 main 上，守卫照样放行，解析器却可能已退回 F0（gateway 退回 M0）。守卫也证明不了 Railway 控制台里的配置正确 | 来源。四格全绿的一份旧检出照样不许部署 |

所以「回滚一律用 main 上的 revert 提交」只满足来源检查；revert 提交同样要过四格验证，不能因为走的是 main 上的 revert 就视为安全。

## 四格验证（每次发布与回滚）

在**要部署的那个提交**上跑（前端在守卫所用的检出里跑：守卫导出的目录只有已跟踪的文件，没有依赖），revert 提交也不例外。测试部分在跑守卫之前做完，守卫通过后立即部署（`deploy-guard.md`「通过之后立即部署」）；手工核对在部署之后。结果（命令与通过数）先记在工作区之外（终端输出或本机的临时文件）：守卫要求工作区干净、HEAD 等于 `ggwork/main`，跑守卫之前改 progress.md 会被拒。部署并核对之后，把结果写成普通的一行，与守卫记录行一起追加进 progress.md、一起提交（只有带 `pick-deploy-guard target=` 的行算守卫记录，别的行不影响守卫）。

| 格 | 前端（每次前端发布或回滚） | gateway（每次 gateway 发布或回滚） | 部署后在生产上手工核对（登录用户） |
|---|---|---|---|
| 旧卡 | 「F1 x old card」 | `test_frontend_contract.py`：gateway 的结果形状等于前端旧卡夹具 `backend-result.json`（镜像版本开关两种取值）；`test_mirror_frozen.py`：旧结果保持它冻结的 `data_as_of`；S11 起加 TR-27「开关关着时工具 schema 与卡片逐字不变」 | 打开一个已有旧卡的会话，卡片正常展开，面板没有解析错误 |
| 新卡 | 「F1 x new card」；`contract-fixtures.test.ts`（TR-33 合同的正例全过、反例全拒） | S13 起：TR-27 的后端新卡格；TR-36 的 `admin shadow-e2e` 在要部署的镜像上通过 | S13 之前生产上没有新卡，这一格只靠测试；S13 起打开一张带趋势条件的卡 |
| 混合会话 | 「F1 x mixed session」 | S13 起：TR-27 的后端两格 | S13 起打开一个新旧卡都有的会话，整批能展开 |
| 存量快照 | 「F1 x stored snapshots」 | `test_routes.py`（保存与回执）、`test_restart_persistence.py`（重启后候选、来源、备注仍在）；快照的解析在前端 | 打开「我的选剧」，改动前保存的条目都能展开 |

**前端的命令**（`frontend/` 下）：

```sh
pnpm test tests/unit/core/pick/api.test.ts -t "rollback matrix" --reporter verbose
pnpm test tests/unit/core/pick/contract-fixtures.test.ts
```

第一条的输出里，除了文件那一行，必须有 5 行 ✓，describe 名之后的用例名依次以 `F1 x old card`、`F1 x new card`、`F1 x mixed session`、`F1 x hot card`、`F1 x stored snapshots` 开头，汇总行以 `Tests 5 passed` 开头。第四行不属于四格，是 2026-09-28 随 `hot_only` 加进同一个 describe 的用例（见下文「前端四格」）；`-t` 选中整个 describe，它同样必须在。汇总行后半的 skipped 是同一文件里没被 `-t` 选中的 `pick API` 用例，个数随那个 describe 变，不作要求（2026-09-29 在 rstest 0.10.6 上核实，整行是 `Tests 5 passed | 5 skipped (10)`）。这 5 行里少一行、改了名、被跳过，或者报找不到测试文件，都按没过处理：revert 掉 TR-16 的提交通常连这些用例一起删掉，只看「全绿」会漏掉它。退出码 0 也不够：`-t` 一个也没匹配到时，rstest 把整个文件记为 skipped，照样以 0 退出（2026-09-25 在 rstest 0.10.6 上核实）。然后照常跑一遍 `pnpm test` 与 `pnpm typecheck` 全量。

**gateway 的命令**（仓库根目录下）：在要部署的提交上先跑扩展完整套件（两种库，不带 `-k`，计划第 10 节 S3；按 D21 在集成分支与 main 上跑，含 `test_managed_copy`），再把上表 gateway 一列点名的文件单独跑一遍、逐个看结果。`PICK_TEST_PG_URL` 指向本机一次性的 PostgreSQL 17（起法见 `local-run.md`「扩展单元测试」），绝不是生产库：用例会在上面建删数据库和角色。

```sh
PICK_TEST_PG_URL=<一次性测试库> backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q -rs -p no:cacheprovider
PICK_TEST_PG_URL=<一次性测试库> backend/.venv/bin/python -m pytest -v -rs -p no:cacheprovider customizations/pick-workbench/tests/test_frontend_contract.py customizations/pick-workbench/tests/test_mirror_frozen.py customizations/pick-workbench/tests/test_routes.py customizations/pick-workbench/tests/test_restart_persistence.py
```

第一条以 0 退出，`-rs` 列出的跳过原因里没有 `PICK_TEST_PG_URL is not set`。第二条的输出里，四个文件每个都有 `PASSED` 行，而且 `[sqlite…]` 与 `[postgres…]` 两种参数都在；汇总只有 passed，skipped 为 0。少一个文件、报找不到文件、有 skipped，都按没过处理：没设 `PICK_TEST_PG_URL` 时 PG 那一半整体跳过，skip 不算失败，pytest 照样以 0 退出（2026-09-25 核实：设了时这四个文件 35 passed、0 skipped；不设时 16 passed、19 skipped，退出码 0）。S13 起还要确认 TR-27 的后端两格在输出里、TR-36 通过。

**cron 的部署**不产出卡片，不跑四格；它的合同是采集合同版本与迁移头，由启动自检与 S6 的包摘要核对（`packaging.md` 第 6 节）。

## 前端四格（TR-16，反例 14）

四格都在 `frontend/tests/unit/core/pick/api.test.ts` 的 `rollback matrix: the new frontend reads every card it can meet` 里，走真实的 `api.ts` 解析路径（只替换 fetch）：

| 格 | 用例 | 断言 |
|---|---|---|
| 新前端 × 旧卡 | `F1 x old card` | `getPickResult` 读 `backend-result.json`，没有 `observations` 键 |
| 新前端 × 新卡 | `F1 x new card` | `getPickResult` 读 `backend-result-obs.json`：`sort=obs`、`observations`、三种 obs 证据都在 |
| 新前端 × 混合会话 | `F1 x mixed session` | `listPickResults` 一次返回旧、新、旧三张卡，整批解析成功（它对整个会话做 strict 解析，一张解析不了整个面板就报错） |
| 新前端 × 存量快照 | `F1 x stored snapshots` | `listSavedPicks` 一次返回改动前保存的条目与带 obs 证据的条目，都能解析 |

同一个 describe 里还有第五个用例 `F1 x hot card`（2026-09-28，提交 95be88d2），按文件里的顺序排在 `F1 x mixed session` 之后、`F1 x stored snapshots` 之前：`listPickResults` 一次返回一张旧卡和一张 `conditions.hot_only` 为 true 的卡（`ranking_version` 为 `hot-evidence-date-v1`），整批解析成功；`hot_only` 只在为 true 时才存，旧卡的条件里没有这个键。它不在计划第 10 节的四格之内，逐格表也没有它的行；但上文前端命令的 `-t` 选中整个 describe，所以输出是 5 行。

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

TR-16 先于任何会输出观测字段的 gateway 上生产（设计 7.5 第 1 步，计划第 10 节 S1），经 TR-34 守卫的前端模式从 `ggwork/main` 导出的干净目录部署，需用户批准；部署前在同一提交上跑上文前端的四格命令。S1 之后前端不回退到 F0。
