# Trends 夜间会话与 cron 入口（TR-14）

代码：`ggwork_pick/observe/trends/{__main__,run,executor,units,canary,top_dramas,admission,capacity,preflight,contract_check,settings,session_rows,session_summary}.py`，以及两个 cron 共用的 `ggwork_pick/observe/{cron,cron_status}.py`。设计 3.2、4.2–4.5、4.9–4.11、6.3；计划 TR-14、第 8 节、第 9 节、D10、D11、D23、D34；反例 1、2、10。

启动顺序（自检、租约、库内状态）与退出码的来历见 `lease-and-selfcheck.md`；客户端与执行器的接口见 `trends-client.md`。本页只讲会话本身。

**简化版（2026-09-30）**：范围以 `docs/plans/2026-09-30-trends-radar-simplified-scope.md` 为准。stable 模式每晚查我们最热的 100 部剧，结果由 gateway 的只读接口给资料页的趋势表，不发布集合。见本页「简化版的任务来源」「趋势表与排错」两节；金丝雀的部分不变。

## 命令

在 cron 容器里（打包与 Railway 配置是 TR-15 的事）：

| 命令 | 做什么 |
|---|---|
| `python -m ggwork_pick.observe.trends` 或 `… trends run` | 一次触发：到点就跑（或接着跑）当晚的会话，不到点或已过截止就什么都不做 |
| `python -m ggwork_pick.observe.trends status` | 只读查看：运行时行（不含 cookie 罐与 UA）、最近 7 个预算日、最近 7 个批次，以及这些批次的任务清单概览（`plan` 行：计划请求与门槛、各组对照的列出数与匹配数、缺的对照个数与前 5 个 identity、拒跑原因），每行一个 JSON；不取租约，不写任何东西 |
| `python -m ggwork_pick.observe.trends preflight` | 只读预演今晚：按下一次起跑会读的共享剧库批次、熔断状态与预算行展开任务清单，打印一行 JSON（见「金丝雀的负载闸门」）；不取租约、不写任何东西、不发 HTTP。今晚会被拒跑时退出 2，否则 0。在 Railway 上不单独跑：它是自检配置启动命令的第二步，自检通过才跑（`packaging.md` 第 3.2 节、第 6 节） |
| `python -m ggwork_pick.observe.trends --selfcheck-only` | 先校验会话配置（模式等变量、金丝雀对照清单与市场序列、状态密钥、出口测量地址，与 `run` 发请求前的校验相同），再做启动自检，打印自检行（S6 核对这一行）；不取租约、不发请求。配置不对以 2 退出，所以 S6 当场就能发现，不用等第一晚 |

参数写错只打印 usage 和一行固定提示，退出码 2，不回显输入。命令行、`status`、`--selfcheck-only` 与出错处理在 `observe/cron.py`，gsc 的入口（TR-21）用同一套；`preflight` 是 trends 自己的命令（`CronEntry.commands`）。

**环境变量名写错**：每条命令开始前，这个服务不读的 `PICK_OBS_*` 变量逐个在 stderr 上点名，并给出最接近的正确名字（只列名字，不回显值），命令照常继续。已知的一处：旧稿计划第 10 节 S5 写的是 `PICK_OBS_EGRESS_URL`，代码（TR-02）读的是 `PICK_OBS_EGRESS_ECHO_URL`。U13 批准后按代码的名字设，设错了出口测量会静默不开，这条警告就是为它加的。

**连接账**：`status` 与 `preflight` 运行的那几秒里以 `ggwp-obs-admin` 多占 1 条连接（设计 3.4 的「实际最多 2 条」只数两个采集进程，不含这条），读完就释放。采集进程在请求在外、两次写库之间不占连接，所以采集进行中跑 `status` 也不会挤掉它（`test_status_connections`）。

## 退出码

| 码 | 含义 | 处理 |
|---|---|---|
| 0 | 跑完了；或者不到起跑时刻、已过 01:45 截止、当天批次已结束或已发布，什么都不做 | 无 |
| 1 | 中途失败：租约在别的进程手里、启动时等锁或语句超时、跑到一半租约被接管（`LeaseLost`） | 下一次触发自动接着跑 |
| 2 | 拒跑，一个请求都没发：配置不对（包括模式在所设节奏下放不进窗口，见「容量」）、金丝雀对照清单缺失或不合格式、清单缺某个要查的 geo 的市场序列、没有已发布的共享剧库批次、金丝雀负载不够（`not_published_low_coverage`）、自检不过、stable 的粒度或去向不是 `D`、`a_only`、`disabled_7d`、`canary_terminated`；`preflight` 预演到今晚会被拒跑 | 看报错一行；负载不够见「金丝雀的负载闸门」，后两种见下文 |
| 3 | 库内状态或运行时行读不回来（D34）；当天批次的 `plan_json` 读不回来 | 按 `lease-and-selfcheck.md` 处理，不要删运行时行；`plan_json` 坏了先查那一行，程序不会另起任务清单（反例 1） |
| 130 | 被中断 | 重跑是安全的 |

`disabled_7d`、`canary_terminated` 与金丝雀负载不够持续期间，每次触发都以 2 退出，这是有意的：Railway 上一连串失败的 cron 本身就是告警。

## 环境变量

会话自己的变量（`settings.py`），在读库、发请求之前全部校验，值不合法以 2 拒跑且不回显值：

| 变量 | 取值 | 默认 | 含义 |
|---|---|---|---|
| `PICK_OBS_TRENDS_MODE` | `canary1`、`canary2`、`stable` | 无（必填） | 模式，见下一节 |
| `PICK_OBS_TRENDS_GRANULARITY` | `H`、`D`、`HD` | `H` | 阶段 0 选定的粒度（设计 4.9）；`HD` 每个剧目两个单元 |
| `PICK_OBS_TRENDS_ROUTE` | `both`、`a_only`、`b_only` | `both` | 第 8 节的去向；`a_only` 不混 relatedsearches；`neither` 拒跑（Trends 不上线） |
| `PICK_OBS_TRENDS_PACE` | `user`、`design` | `user` | 限速节奏（`pacing.PRESETS`，与阶段 0 的 `--pace` 同一份）：`user` 令牌桶 4、每分钟补 2；`design` 桶 8、每分钟补 4（设计 4.2 原值）。其余包络（每小时 200、40 分钟一段歇 10 分钟、单元内外间隔、半速）两者相同。模式在所设节奏下放不进窗口以 2 拒跑（「容量」） |
| `PICK_OBS_CONTRACT_CHECK` | `1` 开 | 关 | 每周线上合同检查，U12 批准后才开 |
| `PICK_OBS_PUBLISH` | `1` 为 live | shadow | D11；金丝雀无论如何都是 shadow，也不发布 |
| `PICK_OBS_CANARY_SINCE` | `YYYY-MM-DD` | 无 | 金丝雀终止规则从这天起数（TR-30 修复后重跑用） |

另外照 TR-13 与 TR-04：`PICK_DATABASE_URL`、`PICK_OBS_EXPECTED_COLLECTOR`、`PICK_OBS_EXPECTED_ROLE`、`PICK_OBS_STATE_KEY`（或 `PICK_OBS_STATE_KEY_FILE`）；出口测量 `PICK_OBS_EGRESS_ECHO_URL` 不设就不测（TR-02）。

## 模式与时间（第 9 节，D23）

target_date 是这晚要供给的那次 02:00 UTC 发布的日期：起跑到次日 01:45 同属一个 target_date，跨午夜不换日，预算行也不换日。

| 模式 | 起跑（UTC） | 计划请求 | 上限 | 单元可用 | 截止 |
|---|---|---|---|---|---|
| `canary1` | 21:00 | 220 | 220 | 205 | 01:45 |
| `canary2` | 18:30 | 约 300 | 450 | 285 | 01:45 |
| `stable` | 17:30 | ≤350（暂定） | 525 | 330 | 01:45 |

「单元可用」是计划减去预热、探针、重试的预留（15、15、20，设计 4.5；`budget.plan_budget`），任务清单按它截断。熔断当天的上限减半由 TR-03 的 `budget.day_limits` 决定。canary2 与 stable 的上限是计划的 1.5 倍。stable 的数字是暂定的：G4 定稳定期参数时重定（计划第 9 节）。简化版每晚 100 部剧、每部 2 个请求，共 200 个，放得下；单元可用的 330 个请求最多容 165 部（简化范围第 7 节第 2 条）。

cron 每 30 分钟触发一次（17:00 到 01:30，TR-15 配置）。早于起跑时刻的触发、01:45 及之后的触发，退出码 0，不取租约、不连库做任何事。01:45 是硬截止：会话里任何请求都不会在它之后发出，剩下的单元记 `deadline`。

**`window_end`**（设计 4.9）按粒度定。小时级（`H`、`HD`）是建批次那个整点减 3 小时。按时起跑时：canary1 21:00 建批次，`window_end` 18:00；canary2 18:30 建，15:00。起跑那次触发晚了几分钟不影响（21:10 建的也是 18:00）。日级（`D`，简化版的 stable）是建批次那天的 UTC 零点：stable 17:30 建批次，`window_end` 是当天 00:00，这一天及以后都算不完整的日子，最后一个完整日是前一天（`run.window_end_of`，预检用同一个函数）。无论哪种，会话崩溃后续跑都不重算（D23）。

**换起跑时刻的第一天**：改模式就换了起跑时刻，换后第一天的 `window_end` 与前一天的间隔不再是 24 小时：canary1 → canary2 是 21 小时（18:00 → 次日 15:00），canary2 → stable 是 23 小时（15:00 → 次日 14:00）。设计 4.9 第 6 条的 `confirmed` 要求相邻两天的 `window_end` 前移 20–28 小时，所以这两次切换当天仍可确认；以后 TR-18、G4 重定 stable 的起跑时，前后两个起跑相差超过 4 小时，换后第一天只能记 `first`，不会自动确认。金丝雀期间改节奏（`PICK_OBS_TRENDS_PACE`）、起跑时刻或负载，都从改后的第一天起重新数验收（计划第 9 节「金丝雀期间不换参数」）。

### 容量（`capacity.py`）

模式能不能在窗口里跑完，按节奏确定性地估：用真实的限速器、熔断器与预算把一晚逐个请求重放一遍，所有随机间隔都取上限，每个回答 2 秒，每个单元的完成时刻再加 10 分钟余量。三种夜晚：

- 无熔断；
- 第 56 个请求（阶段 0 第一天吃到第一个 429 的位置）是 429：那个单元作废，暂停 30 分钟，探针正常，当天余下半速；
- 同上，而且进程在午夜后发出第一个请求时崩溃，租约失效后的下一次半小时触发续跑，那个单元从头重做。

配置校验时（每次触发、`--selfcheck-only`），模式的最大计划在所设节奏下要满足：无熔断覆盖 100%，一次 429 覆盖至少 95%；不满足以 2 拒跑，报错写明两种覆盖率。第三种夜晚只报告，不设门槛。`test_trends_capacity.py` 用真实的入口与执行器（ManualClock、MockTransport）跑同样的三种夜晚，每种夜晚都钉住真跑覆盖了能覆盖的全部单元、估算覆盖的单元不比真跑的多、完成不比真跑的早。

估算偏保守只靠那 10 分钟余量，其余几项是照实估的：`user` 节奏下快慢由补桶速度决定，回答耗时与间隔取上限几乎不起作用（两者都换到另一头，估算变化不到 1 分钟）；一晚自己的任务清单不加余量重放，与真跑相差不到 1 分钟；模式校验用的标准计划不是真实的任务清单，不加余量时它的限速夜晚比真跑早 1–2 分钟完成（canary1 一次 429：219.8 对 220.5 分钟，加崩溃：240.2 对 242.1 分钟）。重放把 429 打中的单元算作丢失、不模拟执行器之后怎么处理它；执行器保留它在 429 之前拿到的序列（相关查询被 429 拒掉而序列已经回来，这个单元仍算拿到），所以估算的覆盖率偏低。

生产节奏 `user` 下的估算（分钟为起跑到最后一个单元完成，含余量）：

| 模式 | 单元 | 无熔断 | 一次 429 | 一次 429 + 午夜后崩溃 |
|---|---|---|---|---|
| `canary1`（21:00，220） | 91 | 127 分钟，100% | 230 分钟，98.9% | 250 分钟，98.9% |
| `canary2`（18:30，300） | 126 | 175 分钟，100% | 321 分钟，99.2% | 同左（午夜前已跑完） |
| `stable`（17:30，350） | 146 | 206 分钟，100% | 372 分钟，99.3% | 同左 |
| `stable` 简化版（17:30，100 部 × 2） | 100 | 125 分钟，100% | 225 分钟，99% | 同左（午夜前已跑完） |

G3 之前的参数在这个节奏下放不进窗口：canary2 22:00 起 430 个请求，无熔断只覆盖 89%、一次 429 只有 47%；stable 20:30 起 650 个，无熔断 81%；canary1 22:00 起一次 429 覆盖 96%，刚过线，午夜后再崩溃一次只剩 86%。

## 一次触发做什么

0. 校验配置：上表的变量（包括模式在所设节奏下的容量）、金丝雀的对照清单、状态密钥、出口测量地址。不合格以 2 拒跑，不在窗口里也一样（配置错了要当晚就看见）。
1. 按触发时刻算 target_date，不在窗口里就退出 0。
2. 自检、取租约、在租约之下读状态（`lease.collector_session`）。
3. 拒跑检查：熔断停用（`disabled_7d`），或金丝雀已终止（`canary_terminated`），就把这个码写到当天的批次行上再以 2 退出（见「拒跑行」）。
4. 取当天批次：
   - 没有就新建。`window_end` 在建批次时写定（小时级是建批次那个整点减 3 小时，设计 4.9，21:10 建的是 18:00；日级是那天的 UTC 零点），任务清单在这时展开、排序、截断，一并写进 `plan_json`（被截断的单元按截断顺序另列在 `truncated`，概览在 `notes.admission`）。金丝雀的任务清单要先过负载闸门（「金丝雀的负载闸门」），不过就写拒跑行、以 2 退出，不建批次。更早的、还在 running 的批次一并记成 failed。
   - 已有而没结束的，原样接着跑：`window_end` 与任务清单都不重算（反例 1），已完成的单元跳过。
   - 已结束（有 finished_at）或已发布的：退出 0。拒跑行不算，见「拒跑行」。
5. 执行器逐个单元跑（下一节）。
6. 收尾：写汇总与状态码，金丝雀记 withheld，不发布集合。

## 执行器（反例 2、10）

每一个 HTTP 请求（预热、探针、重试都算）都按同一个顺序走：

1. 等限速器（设计 4.2 的包络）与熔断器（暂停、重试等待）都放行；等待按块睡，每次醒来续租；等不到截止之前就停；
2. 距上次续租满 60 秒就续租；
3. 在租约之下扣预算并写状态，提交之后请求才发出；扣了就不退（设计 3.3）；
4. 请求回来，在同一个租约步骤里写请求行、批次的请求计数和状态。请求在外时租约被接管，这一步就报 `LeaseLost`：旧进程不提交请求行、不改预算、不写原始行，退出码 1。

一个单元要么整个跑，要么不开始：开始前按单元的请求数（explore、multiline，另加 relatedsearches 时是 3）判断当天还放得下；首个 5xx 或超时整单元隔 30–60 秒重跑一次；429 等限流信号暂停（首次 30 分钟），暂停后第一个请求是探针，之后当天半速；验证码或同意页当场熄火。一个单元开始不了（`truncated`、`skipped_breaker`、`deadline`），它之后的单元都记同一个原因。单元结束时，原始行（每条序列一行：`market`、`bare`，另有 `related`）与进度在同一个租约步骤里写入。

**重跑与「连续两次按限流处理」**（设计 4.2）。widget 请求要用 explore 新给的 token，所以重跑从 explore 开始，失败的那个请求不是紧接着重发的。重跑里排在失败请求之前的请求（explore；相关查询失败时还有 multiline）只是为了回到它：它们成功时不喂给熔断器，连续计数不清零。于是同一个 multiline 或 relatedsearches 重跑后再 5xx 或超时，就是连续第二次，按限流暂停 30 分钟、记一次熔断。这些引导请求如果失败（429、5xx、验证码、解析不了），照常交给熔断器。每条 widget 请求都 5xx 的极端情况下，每个单元第二次失败就熔断一次，第三次熔断当天熄火，一晚只发十几个请求（`test_every_widget_5xx_trips_the_breaker`）。代价：每次重跑多发一个 explore（相关查询失败时再多一个 multiline），都记在预算项 `retry` 下，从设计 4.5 的「预热、探针、重试」预留里出。

测试：`test_trends_wiring.py` 在 MockTransport 的时刻日志上断言包络，请求数 = 预算扣减数 = 请求行数；把限速器换成空操作，同一断言失败。`test_widget_5xx_twice_pauses` 断言重跑间隔 30–60 秒、第二次 5xx 之后 30 分钟才发探针。

## 任务清单与截断（设计 4.5）

排序键依次是：规则优先级（合同检查 0、市场序列 1、对照剧 2、首轮 A 档 geo 3、B 档 geo 4）、`listed_at` 新的在前（没有的排最后）、最新依据日期新的在前、按 target_date 轮换的 identity 哈希。同一个单元只保留排在前面的一个。放不下的单元按这个顺序截断，截断是严格前缀：后面一个小单元不会插队。

收尾时 `summary_json.uncovered_units` 列出所有没覆盖的单元，先列计划里截断的（原因 `truncated`），再列当晚停下的（`deadline`、`skipped_breaker`、`truncated`），每个带 key、item、identity、geo、粒度。

## 金丝雀的任务来源

`CanaryTaskSource` 读 TR-05 的 `trends/canary_controls.json`（格式 `trends-canary-controls-v1`：对照剧的 identity、geo、组别，每个 geo 一条市场短语，不写剧名）。文件缺失或不合格式，金丝雀以 2 拒跑，报错写明文件名，不回显内容。

**每个要查的 geo 都要有市场序列**（设计 4.7「每个 geo 单独请求一条对照序列」，4.5 按每个 geo 一个大盘单元计量）。要查的 geo 是对照剧的 geo，加上六个语种在市场映射 v1 首轮的 geo：WW、US、ES、MX、DE、FR、IT、BR。清单少了任何一个，金丝雀以 2 拒跑，报错列出缺的 geo，在连库之前就判定。这样选而不是「丢掉那些 geo 的剧目单元」：丢掉会让 WW 这类主力 geo 整片消失，负载结构偏离设计，而且等 TR-05 补上短语时参数会在金丝雀中途变化（第 9 节要求期间不换参数）。TR-05 当前的文件（3a78e2f）只有 US、BG、DE、FR、IT 五条，缺 BR、ES、MX、PL、RO、TW、WW，需要 TR-05 补上本地化短语，否则金丝雀不会跑，`test_packaged_controls_file_when_present` 也会在集成分支上变红。

- 市场序列：清单里每个 geo 一条；
- 对照剧：在各自的 geo 上取；
- 当前共享批次（最新一个已发布的 `system:shared` catalog 批次，只读）里 `listed_at` 落在 target_date 前 14 天到 target_date 之间（含两端）的欧美六语剧目（en、es、de、fr、it、pt），在市场映射 v1 给该语言的首轮 geo 上取。「14 天内」按上架天数 0–14 算，与设计 4.6 规则 2 的「7 天内进 A、8–14 天进 B」同一口径（14 天的算在内），所以是 15 个日历日；没有已发布的共享批次时金丝雀以 2 拒跑（只剩市场序列，量不出金丝雀要量的东西）；
- 每个单元一个词，只用裸剧名；
- 按截断顺序每第 4 个剧目单元加 relatedsearches，去向为 `a_only` 时不加；
- 对照清单里的 identity 在当前批次里找不到的，记在 `plan_json.notes.missing_controls`，不中断；各组对照的列出数与匹配数记在 `notes.controls`，近 14 天剧目数记在 `notes.recent_dramas`。

stable 模式的任务来源见下一节。

## 简化版的任务来源（stable，`top_dramas.py`）

2026-09-30 简化范围第 3 节、第 6 节第 1 项。`PICK_OBS_TRENDS_MODE=stable` 用 `TopDramasTaskSource`，只在 `PICK_OBS_TRENDS_GRANULARITY=D` 且 `PICK_OBS_TRENDS_ROUTE=a_only` 时跑，其余组合以 2 拒跑，报错写明这两个变量（`__main__.source_for`）。金丝雀的任务来源、负载闸门与 `canary_controls.json` 都不受影响。

**取剧**（至多 100 部，`TARGET_DRAMAS`）：

1. 当前共享剧库批次（最新一个已发布的 `system:shared` catalog 批次，只读）里 `payload_json.signals[]` 带名次的三个榜，按鹊娱转化榜 `qc`、鹊娱收入榜 `qr`、Kalos 日榜 `kd` 的顺序各取各的最新一期。一期是这个 kind 在全部行里 `observed_at` 最大的那一天，只取那一天上榜的行；同一行在一期里有几个名次时取最好的一个。不同榜的名次不混排。每行的信号只保留最后一次上榜，资料页「依据」徽标上的数是历来上过榜、现在还在剧库里的行数，不是最新一期的行数。
2. 不够 100 部时，用 ReelShort 的收入补足：最新一个已发布的镜像版本，在它的快照日（`latest_snapshot`，不晚于当天 UTC 日期）读 `pick_mirror.series.revenue_cents` 那一天的值，按从高到低取公开的规范剧（`is_public_canonical`），收入为 0 的不取，剧名与语种来自该版本的 `rs_ids`，最多读 200 个名次。这个数是 RealShort 的滚动 30 天收入快照，只读一天，不相加也不相减。只有 PostgreSQL 有镜像；没有已发布版本、表读不了或没有授权时不补，原因记在 notes 里，只用榜单照跑。
3. 去重只按身份（identity，`[source, source_id, language]`，一行就是一部剧的一个语种版本）。同一行上了几个榜，只占一个单元，几个依据都记下。不同身份一律各占一行，清洗后查询词相同也不合并（简化范围第 3、5 节：每个语种剧名各占一行，不自动归并），各自向 Google 查一次；不跨语种合并。ReelShort 行在剧库里的身份是 `["realshort-pick", base64url("reelshort-<id>"), 语种]`，所以榜单上的 ReelShort 剧与收入补足的是同一个身份。

一部剧也取不到时以 2 拒跑（这一晚什么都量不到）。

**单元**：每部剧一个日级单元，地区为全球（`WW`），时间范围 `today 1-m`，只要序列，不查相关搜索，每部 2 个请求（explore、multiline）。查询词是完整剧名经 NFKC 规范化，去掉配音标记（括号里的，或剧名末尾的 dub、dubbed、doblado、dublado、doublé、synchronisiert、doppiato、配音、译制等，整词匹配，所以 Dubai 不受影响），标点与符号换成空格（词中间的撇号保留并统一成 `'`），空白合并。清洗后为空、超过 200 个字符或含不可打印字符的剧名不查，计入 `unusable_titles`。单元的优先级就是入选顺序，截断按这个顺序。

**记在批次上的东西**：`plan_json.source` 为 `top_dramas`，`plan_json.notes.top_dramas` 记目标部数、实际取到的部数、各榜（kind、那一期的日期、列出的部数）、收入补足（是否可用、原因、镜像版本、快照日、补了几部）、不能用的剧名个数，以及按单元 key 记的每部剧（入选顺序、身份、剧名、平台、语种、依据列表）。gateway 的趋势表只读这一份，不另查剧库，所以表里的入选依据就是那一晚取剧时的样子。

**授权**：采集角色本来就能读这些（`observe/grants.py`：共享剧库批次、`pick_mirror.versions` 与 `series`、各镜像版本的 `rs_ids`），不加新授权。读镜像前先查 schema 与表的权限，缺授权时只是不补收入，不会让整个事务因报错中止。

**2026-09-30 的生产数据**（另一会话以 `pick_observer` 只读核实，批次 `5b590062`，12,519 行）：`kd` 最新一期 09-28，10 行；`qc`、`qr` 最新一期 09-30，各 25 行；合计 60 行，去重后 52 行，所以 100 部里约 48 部靠收入补足。Kalos 日榜 09-28 之后没有更新（上游榜单采集还没恢复），表头与入选依据都写出每一期的日期。

## 金丝雀的负载闸门（`admission.py`）

共享剧库批次发布了还不够：批次里的剧目可能都早于 14 天，对照剧也可能不在批次里。那样一晚只剩十来条市场序列，覆盖率 100%，却量不出金丝雀要量的负载与对照。所以金丝雀（`canary1`、`canary2`）的任务清单截断到计划之后，要满足两条：

- 计划请求（各单元自己的请求，不含预热、探针、重试）不少于当天计划量的 80%（canary1 是 176 次，canary2 是 240 次）；
- 匹配到的正对照（对照清单里组别为 `positive`、identity 在批次里找得到的）不少于清单里正对照的一半（向上取整）。

不满足任何一条，`run` 在任何请求之前以 2 拒跑：当天的拒跑行（没有就新建，有就改写）写 `not_published_low_coverage`（复用合同里已有的码，金丝雀从不发布，所以金丝雀行上的这个码只可能来自这里），`summary_json.admission` 记概览与原因，outcome failed。这样的一天**不算金丝雀日**，不计入三天或七天的验收；数据页的红色横幅照常亮。门槛不因「今晚先跑着」而放宽。

**被拒之后同一晚接管**：补齐剧库或对照之后，下一次触发重新判，过了就接管这一行开跑。这一晚的起跑已经变了：`window_end` 按接管那次触发的整点算（例如 canary1 22:30 接管是 19:00，按时是 18:00），窗口也相应变短，可能跑不完。所以接管开跑的批次在 `plan_json.notes.late_admission` 记 `true`（按时建的批次记 `false`），日志打一行 `admitted late, after a refusal`，`status` 的 plan 行显示；TR-30 同样不把这一天计入三天或七天。停用解除、终止重置之后同一晚接管的，也照此记。

`python -m ggwork_pick.observe.trends preflight` 在开跑之前给出同一份概览（S6a，S6 与 S7 之间）。**在 Railway 上怎么跑**：它是自检配置启动命令的第二步，与 S6 的自检同一次部署、同一个镜像、同一套服务变量，自检以 0 退出才跑，部署的退出码就是它的；读这次部署日志里 `selfcheck ok` 之后的那一行（`packaging.md` 第 3.2 节、第 6 节第 3 步）。不在别处跑：cron 服务平时没有在跑的容器，`railway ssh` 进不去；在本机 `railway run` 要把 `PICK_OBS_STATE_KEY` 带到本机。金丝雀期间想在某一晚之前再看一次，就在部署时段里重做那一套自检部署（`packaging.md` 第 7 节）。退出 2 时不切回 cron 配置，按 `reasons` 补齐后对那次部署点 Redeploy 再读；`refused_by` 非空按「状态码与拒跑行」「金丝雀终止」处理。输出一行 `{"preflight": …}`：

| 字段 | 含义 |
|---|---|
| `target_date`、`mode`、`start`、`deadline` | 下一次起跑供给的 target_date、模式与窗口 |
| `pace` | 节奏：预设名、桶容量、每分钟补充数（与批次 `plan_json.notes.pace` 同一形状） |
| `window_end_if_started_on_time` | 按时起跑时批次会得到的 `window_end` |
| `plan`、`min_requests`、`planned_requests` | 当天计划量、门槛、截断后的计划请求 |
| `planned_units`、`truncated_units` | 计划内与被截断的单元数 |
| `catalog_batch_id`、`catalog_dramas`、`recent_dramas` | 用的共享剧库批次、其中的剧目数、近 14 天的欧美六语剧目数 |
| `controls`、`min_positive` | 各组对照的列出数与匹配数；正对照门槛 |
| `missing_controls`、`missing_first` | 缺的对照个数，前 5 个 identity（不写剧名） |
| `reasons` | 负载闸门的拒跑原因（只对金丝雀判；空表示过） |
| `refused_by` | 任务清单之前就会拒跑的码（`disabled_7d`、`canary_terminated`） |
| `estimates` | 这份任务清单在所设节奏下三种夜晚的容量估算（「容量」） |

`reasons` 或 `refused_by` 非空时退出 2。`status` 的 `plan` 行对已建的批次与拒跑行给出同样的概览；已建的批次另带 `pace` 与 `late_admission`，TR-30 按这两项逐晚核对节奏没变、起跑没挪（计划第 9 节：节奏、起跑时刻与负载中途改了，就从改后的第一天重新数验收）。

## 状态码与拒跑行

收尾写进批次行 `status_codes_json` 的码（合同 `STATUS_CODES` 的顺序）：

- `not_published_low_coverage`：金丝雀行上是负载闸门拒跑（「金丝雀的负载闸门」）；发布期由 TR-20 的覆盖门槛写。横幅文字是合同里发布期的那一句（「A 档覆盖率低于 80%，本批没有发布」），金丝雀行上要读 `status` 的 `plan` 行看原因；
- `extinguished_today`、`disabled_7d`：来自熔断器；
- `canary_terminated`：金丝雀期撞到一次验证码或同意页，或因其他原因熄火累计 2 天（见「金丝雀终止」）；
- `all_zero_jump`：裸剧名序列里全零的比例比上一个 target_date 升了 0.20 以上（当天可判的序列至少 10 条；阈值是占位，影子期校准）；
- `usertype_changed`：当晚见到不止一种 userType，或与上一个 target_date 的不同；
- `parse_error`：每周合同检查解析失败，或从上一个批次带过来、还没有通过的检查清掉它。

只有最新一次运行的码会成为横幅（`status_rules.py`），所以持续的状况每一行都要重写：

- 拒跑时，当天已有批次行就把码并进去；没有就写一行拒跑行（outcome failed，没有 `plan_json`、没有 `window_end`，finished_at 为拒跑时刻），数据页的红色横幅因此不会被清掉。当天或更早还停在 running 的批次（会话中途崩溃留下的），拒跑时一并记成 failed：被拒的日子不会再跑，不收尾的话 run_status 视图会一直显示在跑。
- 停用解除、终止规则重置，或金丝雀的负载补齐之后再触发，会接管这一行：写上 `window_end` 与任务清单、改回 running，拒跑时的码不再带着，`plan_json.notes.late_admission` 记 `true`（「金丝雀的负载闸门」）。负载闸门的拒跑行另在 `summary_json.admission` 记概览，接管时一并换掉。
- `parse_error` 由下一个批次从上一个批次接过来，直到某次合同检查通过。

## 金丝雀终止（设计 4.11）

金丝雀期（`canary1`、`canary2`）的预算行里，只要有一天的熄火原因是验证码或同意页（`wall`），或者因其他原因（限流、熔断次数、探针失败）熄火的 target_date 累计到 2 天，就写 `canary_terminated`，之后每次触发都以 2 拒跑、零 HTTP，批次行一直带着这个码。只看金丝雀模式的预算行；stable 不适用这条。

判定只读库里已提交的预算行（熄火原因与模式跟请求行在同一个租约步骤里写），起跑前的拒跑检查与收尾用同一条规则（`session_summary.canary_terminated`）。所以撞墙那一晚即使在收尾之前崩溃，下一次触发也会在起跑前判出终止、写码、以 2 拒跑，不会再发请求（`test_canary_wall_terminates_after_a_crash`）。其他原因熄火一天不终止，第二天熄火才终止（`test_canary_other_extinguished_days_terminate_on_the_second`）。

TR-30 修复原因后要重跑金丝雀：设 `PICK_OBS_CANARY_SINCE=YYYY-MM-DD`（修复后的第一个 target_date），从这天起重新数，不删任何行。

`disabled_7d` 与之不同，只由 `python -m ggwork_pick.observe.admin reset-disable --operator <名字>` 解除（TR-13）。

## 每周线上合同检查（设计第 10 节，U12）

默认关。`PICK_OBS_CONTRACT_CHECK=1` 之后，每个周一的 target_date 最先跑两个固定单元（US 的 `short drama`，H 与 D 两种时间范围），计入当天预算。判定：

- 任一个解析失败（`parse_error`）：批次写 `parse_error` 告警码，失败的请求不写值；
- 两个都拿到解析得了的答复（`ok`、`ok_zero`、`no_data`）：通过，清掉上一批次带来的 `parse_error`；
- 其余情况（限流、5xx、超时、HTML、验证码，或有一个没跑到）：没有结论，`parse_error` 照旧沿用，不会因为周一被限流就把改版告警清掉一周。

## 趋势表与排错（gateway 只读接口）

资料页的「Google 趋势」tab 读 gateway 的 `GET /api/pick/obs/trends-table`（`ggwork_pick/observe/trends_table.py`，要登录，每个登录用户看到同一份）。不加迁移，不给前端的库角色加授权；gateway 本来就拥有这些表。

- **读哪一晚**：最新的 stable 批次，要求 `window_end` 不为空（拒跑行没有），且 `plan_json.source` 为 `top_dramas`。金丝雀批次与拒跑行都不算。
- **每行**：任务清单上的每个单元（包括被截断的），按入选顺序，最多 200 行，每行最多 40 个点。采集结果只看这一晚这个单元的裸序列原始行：`ok`、`ok_zero` 且至少有一天有值为有数据（全是 0 的曲线是 Google 的相对指数，照样画线、照样算均值，通常落在「数据太少」，不当成「未返回」，也不当成搜索量为 0）；`no_data`，或者一天有值的都没有，为 Google 未返回数据；其余失败状态、被截断（`truncated`）、熔断或截止跳过、没轮到（`not_reached`）为这晚未查到。单元还没有原始行时：汇总里已记下停止原因的（`skipped_breaker`、`deadline`、`truncated`）直接按这晚未查到显示，即使批次还在跑；没有原因、批次还在跑且没过 01:45 截止的为还在查。表头的 `collecting` 只在批次还在跑且没过截止时为真；过了截止还停在 running（进程死了、还没有触发把它收尾），页面写「没有采完：采集中途停了」。原始行只在单元结束时写，所以没查到的剧没有曲线，**不会拿前几晚的曲线顶替**。原始行读不懂的记一行警告（只写单元 key），按未查到显示。
- **曲线**：Google 返回的序列，`hasData` 为 false 的那天是 null，不补 0；`isPartial` 为 true 或日期不早于 `window_end` 那天的点标为不完整。
- **横幅**：与 `/sync` 的 Trends 横幅同一套规则（`status.banners_of`），去掉 `shadow_mode`（简化版不发布集合，发布开关与表无关）。`stale_26h` 对表是按批次判的：最新一个采完（`finished_at` 不为空）的表批次的 target_date 早于 02:30 UTC 起应到的日期；或者一个都没采完过，而第一个表批次的 target_date 已经应到（首晚就崩的情况）；两种都要 `run_missed` 没有成立（`status_rules.trends_table_stale`）。一个表批次都没有时不亮。
- **读失败**：返回 503「趋势表暂时读不了，稍后再试」，日志只写异常类名。页面显示同一句话，页签外壳照留；401 转去登录页。
- **两段均值与标签**在前端算（`frontend/src/core/pick/trends-table.ts`），门槛（±25%、少于 3 个不为 0 的天）只在 `TREND_RULES` 一处，改门槛只需要发前端。

**排错**：

| 现象 | 查什么 |
|---|---|
| 页面写「还没有趋势表」 | 还没有一晚 stable 跑过，或最新的 stable 批次不是 `top_dramas`。跑 `python -m ggwork_pick.observe.trends status` 看最近的批次与 `plan` 行；服务变量要是 `MODE=stable`、`GRANULARITY=D`、`ROUTE=a_only`，否则每次触发都以 2 拒跑 |
| 红色「Trends 数据过期」或「采集没有按时运行」 | 看 Railway 上 `pick-obs-trends` 最近几次触发的退出码与日志；`status` 的最近批次是否停在 running（续跑失败）或是拒跑行（`disabled_7d` 等，见「状态码与拒跑行」） |
| 大量「这晚未查到」 | 看行上的原因：`rate_limited` 等是被限流，`deadline` 是 01:45 前没跑完，`truncated` 是超出当晚预算。`status` 的预算行与熔断状态说明当晚发生了什么 |
| 大量「Google 未返回数据」 | Google 没有返回可用的曲线（`no_data`，或曲线一天有值的都没有）。可能与剧名在全球范围的搜索量不足有关，但不能据此判断没有需求，也不能单凭它排除采集或解析的问题：先看同一晚其他行是否正常有数据，再用行尾链接在 Google Trends 上手动查几部对照。第一周查到数据的比例很低时，按简化范围第 10 节第 4 步考虑改地区 |
| 收入没有补足 | 表头的来源一行写了原因：没有已发布的镜像版本、镜像读不了（授权缺失时跑 `admin regrant --check`）、或榜单已凑满 |
| 页面写「趋势表暂时读不了」 | gateway 日志里 `[pick-obs] reading the trends table failed:` 那一行的异常类名；gateway 自己连不上库时 `/sync` 也会同时报错 |

## 发布接缝（TR-20）

两个接缝，本任务里都不接（金丝雀从不发布；简化版的 stable 也不发布集合，结果由 gateway 的趋势表接口直接读），收尾一律记 withheld：

- `refetch`（`executor.Refetch`）：任务清单跑完之后、客户端关闭之前调用，拿到一个「再跑一个单元」的函数。设计 4.9 第 6 条对 first 命中的一致性复取要发 HTTP，而收尾步骤里不能发请求，所以放在这里；复取的每个请求照样等限速器、扣预算、写请求行。
- `publish`：在收尾那个租约步骤里调用，拿到 `Finishing`（批次、任务清单与进度、状态机、汇总文档、会话得出的状态码，以及 `uncovered_dramas`：按合同 `UncoveredUnit` 的形状列出没覆盖的剧目单元，市场序列与合同检查单元没有 identity，已滤掉），返回 `Published`（集合 id 或 None，外加它要加的状态码，比如 80% 覆盖门槛没过时的 `not_published_low_coverage`；只收合同 `STATUS_CODES` 里的码）。有 id 记 published，否则 withheld，码并进批次行。

测试：`test_publish_seam_for_tr20`。

## 验收记录

`test_trends_wiring.py::test_simulated_canary2_night`：canary2 从 18:30 起跑（`window_end` 15:00），生产节奏 `user`，生产的负载闸门，260 部剧目、6 个对照、8 个 geo 的市场序列，注入一次 503 与一次超时（都重跑成功），在本机 SQLite 与 PostgreSQL 上各跑一遍：289 个请求，模拟时间约 2 小时 47 分（18:30 到 21:17），覆盖率 1.0，withheld，包络、预算、请求行、传输日志四者一致，`plan_json.notes.admission` 带概览。G3 之前是 22:00 起跑、设计节奏、420 个请求、约 2 小时 06 分。

`test_trends_capacity.py`：canary1、canary2 各三种夜晚（无熔断、一次 429、一次 429 加午夜后崩溃续跑），用真实的入口与执行器跑，估算覆盖不多于、完成不早于真跑（「容量」）。生产节奏、FakeGoogle 0.4 秒延迟下的真跑：canary1 无熔断 117 分钟、一次 429 约 221 分钟、加崩溃约 242 分钟，都覆盖全部 92 个单元；canary2 无熔断 165 分钟、一次 429 约 312 分钟，覆盖全部 127 个单元。

`test_trends_run.py::test_batch_keeps_the_pace_it_ran_at`、`test_a_night_admitted_after_a_refusal_is_marked_late`：批次的 `plan_json.notes` 记下节奏（`user` 与 `design` 各一遍）与 `late_admission`（按时建的 `false`；21:00 被负载闸门拒、补齐后 22:30 接管的 `true`，`window_end` 19:00），`status` 的 plan 行显示两项。

`test_night_as_the_observer`：以按 TR-12 授权的观测角色（生产里是 `pick_observer`）连库跑一整晚（含一次重跑与一次暂停）再跑 `status`，授权够用。

`test_trends_stable_night.py`：stable 简化版一晚，走真实的入口与执行器（ManualClock、FakeGoogle），生产节奏 `user`：100 部 × 2 个请求，无熔断 19:24 全部查完（估算 125 分钟），第 56 个请求 429 时 21:04 查完 99 部（估算 225 分钟，99%），远早于 01:45；熔断当天上限减半后也放得下。`window_end` 为建批次那天的 UTC 零点，预检给出同一个值。

`test_trends_table.py`：gateway 的趋势表在本机 SQLite 与 PostgreSQL 上各跑一遍：任务清单驱动、有数据与未返回数据与未查到分开计数、没轮到的剧不拿前一晚的曲线顶替、按批次判过期、金丝雀与拒跑行不算表批次、读失败 503 不带原文；并写出前端契约夹具 `frontend/tests/unit/core/pick/fixtures/backend-trends-table.json`（`PICK_WRITE_CONTRACT=1` 时重写）。
