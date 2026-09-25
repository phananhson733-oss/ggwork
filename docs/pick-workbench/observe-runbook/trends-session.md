# Trends 夜间会话与 cron 入口（TR-14）

代码：`ggwork_pick/observe/trends/{__main__,run,executor,units,canary,contract_check,settings,session_rows,session_summary}.py`，以及两个 cron 共用的 `ggwork_pick/observe/{cron,cron_status}.py`。设计 3.2、4.2–4.5、4.9–4.11、6.3；计划 TR-14、第 8 节、第 9 节、D10、D11、D23、D34；反例 1、2、10。

启动顺序（自检、租约、库内状态）与退出码的来历见 `lease-and-selfcheck.md`；客户端与执行器的接口见 `trends-client.md`。本页只讲会话本身。

## 命令

在 cron 容器里（打包与 Railway 配置是 TR-15 的事）：

| 命令 | 做什么 |
|---|---|
| `python -m ggwork_pick.observe.trends` 或 `… trends run` | 一次触发：到点就跑（或接着跑）当晚的会话，不到点或已过截止就什么都不做 |
| `python -m ggwork_pick.observe.trends status` | 只读查看：运行时行（不含 cookie 罐与 UA）、最近 7 个预算日、最近 7 个批次，每行一个 JSON；不取租约，不写任何东西 |
| `python -m ggwork_pick.observe.trends --selfcheck-only` | 先校验会话配置（模式等变量、金丝雀对照清单与市场序列、状态密钥、出口测量地址，与 `run` 发请求前的校验相同），再做启动自检，打印自检行（S6 核对这一行）；不取租约、不发请求。配置不对以 2 退出，所以 S6 当场就能发现，不用等第一晚 |

参数写错只打印 usage 和一行固定提示，退出码 2，不回显输入。命令行、`status`、`--selfcheck-only` 与出错处理在 `observe/cron.py`，gsc 的入口（TR-21）用同一套。

**环境变量名写错**：每条命令开始前，这个服务不读的 `PICK_OBS_*` 变量逐个在 stderr 上点名，并给出最接近的正确名字（只列名字，不回显值），命令照常继续。已知的一处：计划第 10 节 S5 写的是 `PICK_OBS_EGRESS_URL`，代码（TR-02）读的是 `PICK_OBS_EGRESS_ECHO_URL`。U13 批准后按代码的名字设，设错了出口测量会静默不开，这条警告就是为它加的。

**连接账**：`status` 运行的那几秒里以 `ggwp-obs-admin` 多占 1 条连接（设计 3.4 的「实际最多 2 条」只数两个采集进程，不含这条），读完就释放。采集进程在请求在外、两次写库之间不占连接，所以采集进行中跑 `status` 也不会挤掉它（`test_status_connections`）。

## 退出码

| 码 | 含义 | 处理 |
|---|---|---|
| 0 | 跑完了；或者不到起跑时刻、已过 01:45 截止、当天批次已结束或已发布，什么都不做 | 无 |
| 1 | 中途失败：租约在别的进程手里、启动时等锁或语句超时、跑到一半租约被接管（`LeaseLost`） | 下一次触发自动接着跑 |
| 2 | 拒跑，一个请求都没发：配置不对、金丝雀对照清单缺失或不合格式、清单缺某个要查的 geo 的市场序列、没有已发布的共享剧库批次、自检不过、stable 还没有任务来源、`disabled_7d`、`canary_terminated` | 看报错一行；后两种见下文 |
| 3 | 库内状态或运行时行读不回来（D34）；当天批次的 `plan_json` 读不回来 | 按 `lease-and-selfcheck.md` 处理，不要删运行时行；`plan_json` 坏了先查那一行，程序不会另起任务清单（反例 1） |
| 130 | 被中断 | 重跑是安全的 |

`disabled_7d` 与 `canary_terminated` 持续期间，每次触发都以 2 退出，这是有意的：Railway 上一连串失败的 cron 本身就是告警。

## 环境变量

会话自己的变量（`settings.py`），在读库、发请求之前全部校验，值不合法以 2 拒跑且不回显值：

| 变量 | 取值 | 默认 | 含义 |
|---|---|---|---|
| `PICK_OBS_TRENDS_MODE` | `canary1`、`canary2`、`stable` | 无（必填） | 模式，见下一节 |
| `PICK_OBS_TRENDS_GRANULARITY` | `H`、`D`、`HD` | `H` | 阶段 0 选定的粒度（设计 4.9）；`HD` 每个剧目两个单元 |
| `PICK_OBS_TRENDS_ROUTE` | `both`、`a_only`、`b_only` | `both` | 第 8 节的去向；`a_only` 不混 relatedsearches；`neither` 拒跑（Trends 不上线） |
| `PICK_OBS_CONTRACT_CHECK` | `1` 开 | 关 | 每周线上合同检查，U12 批准后才开 |
| `PICK_OBS_PUBLISH` | `1` 为 live | shadow | D11；金丝雀无论如何都是 shadow，也不发布 |
| `PICK_OBS_CANARY_SINCE` | `YYYY-MM-DD` | 无 | 金丝雀终止规则从这天起数（TR-30 修复后重跑用） |

另外照 TR-13 与 TR-04：`PICK_DATABASE_URL`、`PICK_OBS_EXPECTED_COLLECTOR`、`PICK_OBS_EXPECTED_ROLE`、`PICK_OBS_STATE_KEY`（或 `PICK_OBS_STATE_KEY_FILE`）；出口测量 `PICK_OBS_EGRESS_ECHO_URL` 不设就不测（TR-02）。

## 模式与时间（第 9 节，D23）

target_date 是这晚要供给的那次 02:00 UTC 发布的日期：20:30 到次日 01:45 同属一个 target_date，跨午夜不换日，预算行也不换日。

| 模式 | 起跑（UTC） | 计划请求 | 上限 | 单元可用 | 截止 |
|---|---|---|---|---|---|
| `canary1` | 22:00 | 220 | 220 | 205 | 01:45 |
| `canary2` | 22:00 | 约 430 | 600 | 415 | 01:45 |
| `stable` | 20:30 | ≤650 | 800 | 630 | 01:45 |

「单元可用」是计划减去预热、探针、重试的预留（15、15、20，设计 4.5），任务清单按它截断。熔断当天的上限减半由 TR-03 的 `budget.day_limits` 决定。

cron 每 30 分钟触发一次（20:00 到 01:30，TR-15 配置）。早于起跑时刻的触发、01:45 及之后的触发，退出码 0，不取租约、不连库做任何事。01:45 是硬截止：会话里任何请求都不会在它之后发出，剩下的单元记 `deadline`。

## 一次触发做什么

0. 校验配置：上表的变量、金丝雀的对照清单、状态密钥、出口测量地址。不合格以 2 拒跑，不在窗口里也一样（配置错了要当晚就看见）。
1. 按触发时刻算 target_date，不在窗口里就退出 0。
2. 自检、取租约、在租约之下读状态（`lease.collector_session`）。
3. 拒跑检查：熔断停用（`disabled_7d`），或金丝雀已终止（`canary_terminated`），就把这个码写到当天的批次行上再以 2 退出（见「拒跑行」）。
4. 取当天批次：
   - 没有就新建。`window_end` 在建批次时写定（建批次那个整点减 3 小时，设计 4.9；22:10 建的是 19:00），任务清单在这时展开、排序、截断，一并写进 `plan_json`（被截断的单元按截断顺序另列在 `truncated`）。更早的、还在 running 的批次一并记成 failed。
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
- 对照清单里的 identity 在当前批次里找不到的，记在 `plan_json.notes.missing_controls`，不中断。

stable 模式的任务来源是 TR-18 的 `WatchTaskSource`，本任务里还没接上：`PICK_OBS_TRENDS_MODE=stable` 以 2 拒跑，报错说明要等 TR-18。

## 状态码与拒跑行

收尾写进批次行 `status_codes_json` 的码（合同 `STATUS_CODES` 的顺序）：

- `extinguished_today`、`disabled_7d`：来自熔断器；
- `canary_terminated`：金丝雀期的 target_date 被熄火（验证码必然熄火）累计 2 天；
- `all_zero_jump`：裸剧名序列里全零的比例比上一个 target_date 升了 0.20 以上（当天可判的序列至少 10 条；阈值是占位，影子期校准）；
- `usertype_changed`：当晚见到不止一种 userType，或与上一个 target_date 的不同；
- `parse_error`：每周合同检查解析失败，或从上一个批次带过来、还没有通过的检查清掉它。

只有最新一次运行的码会成为横幅（`status_rules.py`），所以持续的状况每一行都要重写：

- 拒跑时，当天已有批次行就把码并进去；没有就写一行拒跑行（outcome failed，没有 `plan_json`、没有 `window_end`，finished_at 为拒跑时刻），数据页的红色横幅因此不会被清掉。当天或更早还停在 running 的批次（会话中途崩溃留下的），拒跑时一并记成 failed：被拒的日子不会再跑，不收尾的话 run_status 视图会一直显示在跑。
- 停用解除或终止规则重置后再触发，会接管这一行：写上 `window_end` 与任务清单、改回 running，拒跑时的码不再带着。
- `parse_error` 由下一个批次从上一个批次接过来，直到某次合同检查通过。

## 金丝雀终止（设计 4.11）

金丝雀期（`canary1`、`canary2`）的预算行里，熄火过的 target_date 累计到 2 天，那一晚收尾就写 `canary_terminated`，之后每次触发都以 2 拒跑、零 HTTP，批次行一直带着这个码。只看金丝雀模式的预算行；stable 不适用这条。

TR-30 修复原因后要重跑金丝雀：设 `PICK_OBS_CANARY_SINCE=YYYY-MM-DD`（修复后的第一个 target_date），从这天起重新数，不删任何行。

`disabled_7d` 与之不同，只由 `python -m ggwork_pick.observe.admin reset-disable --operator <名字>` 解除（TR-13）。

## 每周线上合同检查（设计第 10 节，U12）

默认关。`PICK_OBS_CONTRACT_CHECK=1` 之后，每个周一的 target_date 最先跑两个固定单元（US 的 `short drama`，H 与 D 两种时间范围），计入当天预算。判定：

- 任一个解析失败（`parse_error`）：批次写 `parse_error` 告警码，失败的请求不写值；
- 两个都拿到解析得了的答复（`ok`、`ok_zero`、`no_data`）：通过，清掉上一批次带来的 `parse_error`；
- 其余情况（限流、5xx、超时、HTML、验证码，或有一个没跑到）：没有结论，`parse_error` 照旧沿用，不会因为周一被限流就把改版告警清掉一周。

## 发布接缝（TR-20）

两个接缝，本任务里都不接（金丝雀从不发布；stable 还没有任务来源），收尾一律记 withheld：

- `refetch`（`executor.Refetch`）：任务清单跑完之后、客户端关闭之前调用，拿到一个「再跑一个单元」的函数。设计 4.9 第 6 条对 first 命中的一致性复取要发 HTTP，而收尾步骤里不能发请求，所以放在这里；复取的每个请求照样等限速器、扣预算、写请求行。
- `publish`：在收尾那个租约步骤里调用，拿到 `Finishing`（批次、任务清单与进度、状态机、汇总文档、会话得出的状态码，以及 `uncovered_dramas`：按合同 `UncoveredUnit` 的形状列出没覆盖的剧目单元，市场序列与合同检查单元没有 identity，已滤掉），返回 `Published`（集合 id 或 None，外加它要加的状态码，比如 80% 覆盖门槛没过时的 `not_published_low_coverage`；只收合同 `STATUS_CODES` 里的码）。有 id 记 published，否则 withheld，码并进批次行。

测试：`test_publish_seam_for_tr20`。

## 验收记录

`test_trends_wiring.py::test_simulated_canary2_night`：canary2 从 22:00 起跑，260 部剧目、6 个对照、8 个 geo 的市场序列，注入一次 503 与一次超时（都重跑成功），在本机 SQLite 与 PostgreSQL 上各跑一遍：420 个请求，模拟时间约 2 小时 06 分（22:00 到 00:06），覆盖率 1.0，withheld，包络、预算、请求行、传输日志四者一致；墙钟时间 SQLite 约 3 秒、PostgreSQL 约 15 秒。

`test_night_as_the_observer`：以按 TR-12 授权的观测角色（生产里是 `pick_observer`）连库跑一整晚（含一次重跑与一次暂停）再跑 `status`，授权够用。
