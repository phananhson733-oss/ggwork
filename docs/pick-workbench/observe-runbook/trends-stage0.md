# Trends 阶段 0：本机两天运行与报告（TR-05）

代码：`ggwork_pick/observe/trends/stage0*.py`；对照清单格式 `trends-stage0-controls-v1`，任务清单格式 `trends-stage0-plan-v1`。设计 4.9、4.11；计划 TR-05、第 8 节、D20、D23、D39。状态文件与密钥见 `trends-state.md`，客户端见 `trends-client.md`。

阶段 0 不是测限流。它回答三件事：Trends 看不看得见 GSC 看得见的剧（闸门 A）；本机直连会话拿不拿得到相关查询与 userType（闸门 B）；真实回答长什么样（接口核实）。结论决定颗粒度（H、D 或 H+D）、N、滞后、trend-rules-v1 全文，以及计划第 8 节的四种去向选哪一种，由 G2 审定。

## 目录与文件

产物根目录 `~/.gstack/projects/ggwork-deerflow/artifacts/trends-stage0/`（目录 700、文件 600，全部不进仓库）：

| 路径 | 内容 |
|---|---|
| `controls.json` | 对照清单：正对照 15–20 部、反对照、BG/DE/FR/IT 地区对照、市场序列、种子；带剧名，所以只放这里 |
| `plan/day1.json`、`plan/day2.json` | 两天的任务清单，每个单元写明词、geo、请求数 |
| `state/trends-state.json` | 加密状态（限速、熔断、预算、cookie 罐），TR-04 |
| `runs/day<N>/results.jsonl` | 每个单元一行（重跑追加，按最后一行算）；没发出去的单元记 `reason` |
| `runs/day<N>/requests.jsonl` | 每个 HTTP 请求一行，不含 cookie、不含序列值 |
| `runs/day<N>/meta.json` | 每次会话一条：目标日、起止时间、请求数、熔断事件、代理变量是否设置（只记名字） |
| `raw/day<N>/` | 每个 API 回答的原文与 `index.jsonl`；预热页只记哈希 |
| `trends-stage0-interim-report-<日期>.md`、`trends-stage0-report-<日期>.md` | 中期报告、定稿报告 |
| `fixtures/day<N>/` | 脱敏夹具，人工看过再拷进 `tests/fixtures/trends` |

仓库里只有 `ggwork_pick/observe/trends/canary_controls.json`：对照的身份键、geo 与分组，外加市场序列的词，不带剧名。它是 TR-14 金丝雀的输入，剧名在运行时从当前共享批次取。

## 预算与时间

- 两天合计至多 180 次请求，每天至多 90 次，预热、explore、multiline、相关查询与重复获取都算。现在的清单是第一天 88 次、第二天 84 次；每天给重试留的余量很少，所以市场序列排在最后，额度用完时先砍它们。
- 每部剧各取小时级 `now 7-d` 与日级 `today 1-m`，一个词一个请求。标了 `related` 的对照在日级单元上顺带取相关查询；种子只取相关查询。每天把当天第一部正对照的小时级序列隔 6 个单元再取一次：第一天用 GET，第二天用 POST，顺带核实 explore 两种方法都答。
- 目标日按 D23：UTC 02:00（北京时间 10:00）起到次日 01:45 属于下一个目标日，01:45 之后到 02:00 不发请求。第二天须在比第一天晚的目标日运行，也就是第一天之后、至少过了一次北京时间 10:00。
- 限速与熔断照 TR-03：每个请求发出前先等限速器和熔断器，再预留额度并保存状态；429 暂停至少 30 分钟再试探，验证码或同意页当天熄火，其余单元记未覆盖。按模拟时钟估算，一天约 25 分钟（实际等待以限速器为准，遇到暂停会更久）。

## 步骤

以下在仓库的 `customizations/pick-workbench` 目录执行，`PY` 指后端虚拟环境里的 python。状态密钥按 `trends-state.md` 准备，`export PICK_OBS_STATE_KEY_FILE=…`。

1. **生成任务清单**（不联网）：`$PY -m ggwork_pick.observe.trends.stage0 plan`。某一天开跑后再生成会被拒（退出码 2）；要重来，先把 `runs/`、`raw/` 移走。
2. **核对**（不联网）：`$PY -m ggwork_pick.observe.trends.stage0 run --day 1 --dry-run`，列出待跑单元、请求数、geo 与方法。
3. **第一天**：`$PY -m ggwork_pick.observe.trends.stage0 run --day 1 --init-state`。只有第一次运行带 `--init-state`。中途被打断就不带它重跑，已完成的单元不再请求。
4. **中期报告**：`$PY -m ggwork_pick.observe.trends.stage0 report`。只有第一天的数据时出中期报告，标「待第二天」的结论都是暂定：闸门 B 要两次会话都稳定；闸门 A 按一半的正对照暂判（至少 4 部才判定）。
5. **第二天**（晚一个目标日）：`$PY -m ggwork_pick.observe.trends.stage0 run --day 2`。
6. **定稿报告**：`$PY -m ggwork_pick.observe.trends.stage0 report`，拿到 U5 后加 `--manual <文件>`（可重复），给出手工导出与抓取序列的秩相关 ρ（≥0.6 为形状一致）。浏览器时区默认 UTC+8（`--utc-offset-minutes 480`）；中文界面导出的「天 / 时间」与全角标点也能读。`--n` 改日级可见的非零日下限，报告里本来就有 N=6、9、12、15、20 的对比表。
7. **脱敏夹具**：`$PY -m ggwork_pick.observe.trends.stage0 fixtures --day 1`（第二天同理）。剧名换成 `stage0 <组> <序号>`，相关查询换成稳定的假名，widget token 换成 `REDACTED_TOKEN`，像 IP 地址的串换成 `192.0.2.1`。人工看过再挑几份替换 `tests/fixtures/trends` 里的构造夹具（`constructed: false`），并把对应的 `pending_stage0` 条目核掉。

`--root` 可以换产物目录（测试就是这样做的）。

## 报告里有什么

结论一览；输入与来源（含 GSC 导出的时效说明：小时级 now 7-d 看不到那时的需求，日级 today 1-m 能覆盖）；每次会话的请求数、熔断事件与代理变量；闸门 A（H、D 各一行）与 N 的敏感性表；逐个对照的非零小时/非零日与相关查询；闸门 B；第 8 节的去向；滞后（按 isPartial 的位置回标，小时级取看到的最大值）；接口核实（点数、isPartial 位置、time 类型、userType、空响应、GET/POST、重复获取的差异、相关查询与 Breakout）；U5；选定颗粒度的 trend-rules-v1 全文与占位参数；选 D 或 H+D 时的数据合同变更单（D39）；还欠什么。

判定口径：

- 请求失败或没发出去的单元一律写「未观测到」加原因，不当作零，也不进可见率的分母（前提 1）。
- 可见：小时级最近 144 个完整小时里非零 ≥12；日级最近 30 个完整日里非零 ≥N，默认 N=12（与小时级下限对应：四个 7 天块每块至少 3 个非零日）。
- 闸门 A：已观测正对照可见 ≥50%，且估算每天非泛词判定 ≥5 条（A 档约 60 部 × 地区对照可见率；H+D 时减半重算）。正对照观测不到 8 部（中期报告 4 部）不判定。
- 闸门 B：每次会话要了相关查询的单元里可用 ≥80%，至少一个种子拿到列表，有回答的 explore 都带 userType；两次会话都要过。
- 颗粒度：H 过选 H；只有 D 过时，小时级仍看得见 ≥25% 的正对照选 H+D，否则选 D；都不过则规则全文不定稿。

## 出口与代理

阶段 0 在本机跑，D20 的出口开关不开，不发任何探测请求。会话开始时只检查代理环境变量（`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及其小写）有没有设，把名字记进 `meta.json` 和报告，不记值（值里可能有账号）。设了代理时，报告会注明出口不一定是本机直连。

## 退出码

| 退出码 | 情形 |
|---|---|
| 0 | 完成；`--dry-run`、`plan`、`report`、`fixtures` 正常结束 |
| 1 | 当天跑了，但有单元没覆盖（额度、熔断或截止时刻）；看输出里的原因，同一目标日重跑只会补还能补的 |
| 2 | 拒跑，一个请求都没发：没有对照清单或任务清单、清单在生成任务清单后改过、没设密钥、第二天早于第一天或在同一目标日 |
| 3 | 状态文件不存在（首次运行忘了 `--init-state`）、被别的进程占用，或读不出来；见 `trends-state.md` |

出错信息只写类名与我们自己的说明，不回显命令行参数，也不打印 HTTP 原文。
