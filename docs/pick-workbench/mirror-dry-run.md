# feed v2 dry-run 实测（P1-6）

`python -m ggwork_pick.mirror.client --dry-run` 把 RealShort feed v2（和 v1）完整拉一遍，只量每页耗时、字节数和行数，然后丢掉：不落盘，不连数据库，也不读 app 配置。它回答两个问题：

- 新接口的每一页能不能在门槛内拉完。
- RealShort 的网盘清洗有没有误伤剧名一类的字段。

这两个结论决定 realshort#67 能不能合并、P2 镜像能不能上线（终版简报 P1 第 4 到 7 步）。

本文单独成篇，没有写进 [realshort-sync.md](realshort-sync.md)。那篇是常驻同步的运行手册；这里是一次性流程：P1-6 在 Preview 上跑一次，`--scan` 必要时在 Production 上再跑一次，都要临时开凭据、测完全部撤掉。临时凭据的步骤写进常驻手册，就会一直留在那里。实测数字和最终的 `rs_rows` 页大小记回 realshort-sync.md（见文末「测完之后」）。

## 什么时候跑

- **P1-6**：P2-2a 客户端合并之后、realshort#67 合并之前，在 RealShort `feat/pick-export-v2` 分支的 Preview 上跑。Preview 读的是生产库，测到的数字就是生产的查询增量。
- 避开三个时段：RealShort 的 cron、Mac mini 的剧单导入、工作台 03:40 / 15:40 UTC 的定时同步。
- **`--scan`（U52）**：能在 P1-6 这次一起跑就一起跑。没跑成的话，等 Production 配好正式 `PICK_EXPORT_TOKEN`、打开镜像之前，在 Production 上单独跑一次，同样避开 cron。Production 不需要 bypass。

## 前提（RealShort 的 Vercel 项目）

在控制台操作，或在已 `vercel link` 到该项目的 RealShort 检出目录里用 `vercel` 命令：

1. 确认 Rolling Releases 已关闭。看 Preview 环境已有哪些变量：`vercel env ls preview` 只列名字，不显示值。
2. 配临时 `PICK_EXPORT_TOKEN`：**只配 Preview，并限定 git 分支 `feat/pick-export-v2`**（生成与写入见下一节）。不配 Production，也不配 Development：配到 Development 会被 `vercel env pull` 拉到本机文件里。
3. 要量 v1、而 Preview 上没有 `PICK_FEED_TOKEN` 时，另配一个临时值，作用域同上。**不要用生产的值。** 不量 v1 就不配，dry-run 会跳过 v1 并在汇总里注明。**加 `--scan` 就必须量 v1**：没有 v1 token 时 `pan_scan` 门槛直接判不通过（v1 行与 `v1.rules` 没扫，P1 第 7 步要求含 `v1.rules` 全为 0）。
4. 打开 Deployment Protection 里的 Protection Bypass for Automation，拿到 bypass 值。**bypass 是项目级的，对这个项目的所有 Preview 部署都生效**，所以测完当天就撤。项目上原本就有 bypass 时，先问清用途再动。
5. **Redeploy** 这个分支的 Preview：环境变量只对新部署生效，旧部署看不到新配的 token。

## 准备凭据文件

值只进 0600 的文件，不进命令行参数（会留在 shell 历史和 `ps` 里），不进仓库目录。`printf` 是 shell 内建命令，值不会出现在 `ps` 里。

```bash
dir=$(mktemp -d)          # 目录 0700，只有自己能进
umask 077                 # 这个终端之后新建的文件都是 0600

# 临时 v2 token：生成后直接进文件，再从文件配到 Vercel（用重定向，不用管道：慢管道会被当成缺值）
printf '%s' "$(openssl rand -hex 32)" > "$dir/export-token"
vercel env add PICK_EXPORT_TOKEN preview feat/pick-export-v2 < "$dir/export-token"

# 需要时：临时 v1 token，做法同上
printf '%s' "$(openssl rand -hex 32)" > "$dir/feed-token"
vercel env add PICK_FEED_TOKEN preview feat/pick-export-v2 < "$dir/feed-token"

# bypass：从控制台复制，粘贴后回车；不回显，也不进 shell 历史
read -rs v && printf '%s' "$v" > "$dir/bypass"; unset v

ls -l "$dir"              # 三个文件都应是 -rw-------；不要 cat
```

Redeploy 之后先验一次：带 bypass、不带 token 请求 manifest，应得 `401`（正文是 `{"error":"unauthorized"}`）。

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H @<(printf 'x-vercel-protection-bypass: %s\n' "$(cat "$dir/bypass")") \
  'https://<preview-host>/api/pick-feed/v2/manifest'
```

- `404`：token 没生效，可能是没 Redeploy，或作用域不是这个分支。
- `3xx` 或登录页：bypass 不对。
- 带 token 的那一次，就是下面的 dry-run 本身。

## 命令

在 ggwork-deerflow 仓库根目录执行。本机 `backend/.venv` 里装的是托管副本，可能还没刷新；`PYTHONPATH` 让它直接用仓库里的源码。

```bash
PYTHONPATH=customizations/pick-workbench backend/.venv/bin/python -m ggwork_pick.mirror.client --dry-run \
  --base-url 'https://<preview-host>' \
  --bypass-header-file "$dir/bypass" \
  --token-file "$dir/export-token" \
  --v1-token-file "$dir/feed-token" \
  --scan \
  > /tmp/pick-dry-run.jsonl; echo "exit=$?"
```

通用形式：`--dry-run --base-url <源站> --bypass-header-file <文件> --token-file <文件> [--v1-token-file <文件>] [--scan] [--limit rs_rows=N]`。

| 参数 | 说明 |
|---|---|
| `--base-url` | 只写源站，如 `https://<preview-host>`，不带路径；路径由客户端拼 |
| `--bypass-header-file` | 只放 bypass 值的文件；不收命令行明文。Production 上不给 |
| `--token-file` | v2 token 文件；不给就读环境变量 `PICK_REALSHORT_EXPORT_TOKEN` |
| `--v1-token-file` | v1 token 文件；不给就读 `PICK_REALSHORT_FEED_TOKEN`；都没有就跳过 v1 |
| `--scan` | 在内存里用 Python 网盘清洗扫描每一页，只报路径与次数。要连 v1 一起扫，所以同时要有 v1 token，否则 `pan_scan` 不通过 |
| `--limit rs_rows=N` | 覆盖页大小，可以写多个：`--limit rs_rows=1000 rs_ids=5000`；取值 1 到该资源的服务端上限 |
| `--series-days N` | 拉 `snapshotDays` 最后几天的 rs_series_day，0 到 93，缺省 1（只拉 latestSnapshot 那天） |

- stdout 只有 JSON 行，不含 token、bypass 值和行内容，可以存在仓库外。参数错误只写 stderr。
- 边跑边看：另开一个终端 `tail -f /tmp/pick-dry-run.jsonl`。看汇总：`tail -n 1 /tmp/pick-dry-run.jsonl | backend/.venv/bin/python -m json.tool`。

流程：选 as_of → manifest → v1 → v2 八个资源 → rs_series_day。

- manifest 遇到 `source_busy`，按 Retry-After 等待后换新的 as_of 再请求，累计最多等 1200 秒。
- 遇到以下情况，记一次，换新的 as_of 从 manifest 重来（漂移时先等 90 秒），最多重来 2 次：
  - `409 source_changed`；
  - manifest 之后的 `source_busy`；
  - 页里回显的 fingerprint 对不上；
  - as_of 过期（RealShort 只收 30 分钟内的 as_of，客户端到 25 分钟就不再发请求）。
- `read_failed` 在同一页重试 1 次。
- 每个请求最多等 60 秒。

## 输出

### 每页一行

每个 HTTP 响应一行，按发生顺序输出，409、503 和重试也各占一行。下面的数字只是示意：

```json
{"attempt":1,"resource":"rs_rows","page":3,"status":200,"elapsed_ms":812.4,"bytes":1843221,"wire_bytes":402113,"rows":2000,"retry_after":null,"retried":false,"run":1}
```

| 字段 | 含义 |
|---|---|
| `attempt` | 第几次拉取，从 1 起；从 manifest 重来一次就加 1。与 `run` 相同（简报用的字段名） |
| `run` | 同 `attempt` |
| `resource` | `manifest`、`v1`、八个计数资源之一，或 `rs_series_day` |
| `page` | 这个资源的第几页，从 1 起 |
| `status` | HTTP 状态码 |
| `elapsed_ms` | 从发出请求到收完正文的毫秒数，不含解析和 `--scan` |
| `bytes` | 解压后的正文字节数，页大小门槛按它算 |
| `wire_bytes` | 线上实际收到的字节数（压缩后） |
| `rows` | 这一页的行数；非 200 时为 null |
| `retry_after` | 响应头 Retry-After 的秒数；没有时为 null |
| `retried` | true 表示这个请求是 `read_failed` 之后的那次重试 |
| `day` | 只在 rs_series_day 的行里出现，表示拉的是哪一天 |
| `error` | 只在非 200、且 RealShort 给了已知错误词时出现，如 `source_changed`、`source_busy`、`read_failed` |

### 汇总（最后一行，`"summary":true`）

| 字段 | 含义 |
|---|---|
| `ok` | 全部门槛通过时为 true |
| `failed_gates` | 没通过的门槛名 |
| `as_of` | 最后一次拉取用的 as_of |
| `runs` | 一共拉了几次；1 表示没有重来 |
| `run_ms` | 最后一次拉取的总耗时（毫秒）：从 manifest 请求发出，到最后一页收完。不含 manifest 之前的 busy 等待，也不含 `--scan` 的扫描时间 |
| `manifest` | manifest 这一请求的 `elapsed_ms`、`bytes`、`wire_bytes` |
| `resources` | 八个计数资源，每个一项，字段见下表 |
| `series_days` | 拉到的每一天 rs_series_day 一项，字段同 `resources`；期望行数取 manifest `snapshotDays` 里这一天的 rows |
| `v1` | v1 的统计，字段同 `resources`，另有 `total`（v1 首页报的总数）和 `rows_match_total`（实收行数是否等于它）；没有 v1 token 时只有 `skipped` 与 `reason` |
| `scrub` | manifest 的 `meta.scrub`，即 RealShort 自己清洗的命中，形如「资源.列 → 次数」 |
| `warnings` | manifest `meta.warnings` 的 code 列表 |
| `source_revision_null` | manifest 的 `sourceRevision` 是否为 null |
| `retries` | 重试与漂移的计数，字段见下表 |
| `scan` | 只在加了 `--scan` 时出现：`hits`（路径 → 次数，不含值；路径里不是字母、数字、下划线的键名写成 `<非常规键名>`，键名本身也可能带着网盘片段）、`total`（次数合计）、`elapsed_ms`（扫描线程耗时合计） |
| `gates` | 每个门槛的判定，字段见下表 |

`resources` 每一项（`series_days`、`v1` 同）：

| 字段 | 含义 |
|---|---|
| `pages` | 页数 |
| `rows` | 实收行数 |
| `bytes` / `wire_bytes` | 各页之和 |
| `max_bytes` | 最大的一页 |
| `max_elapsed_ms` | 最慢的一页 |
| `sum_elapsed_ms` | 各页耗时之和 |
| `expected_rows` | manifest 承诺的行数（`counts`，rs_series_day 取当天的 rows） |
| `rows_match` | `rows` 是否等于 `expected_rows` |

`retries`：

| 字段 | 含义 |
|---|---|
| `drift_409` | `409 source_changed` 的响应数 |
| `busy_503` | `source_busy` 的响应数，包括 manifest 阶段的 |
| `manifest_busy_503` | 其中 manifest 阶段的；那是等待，不算漂移 |
| `busy_wait_seconds` | manifest 阶段按 Retry-After 等待的总秒数 |
| `read_failed_503` | `read_failed` 的响应数 |
| `read_failed_retries` | `read_failed` 之后重试的请求数，每页最多 1 次 |
| `as_of_expired` | 因 as_of 过期而重来的次数 |
| `reruns` | 从 manifest 重来的次数，等于 `runs` 减 1 |
| `causes` | 每次被打断各一项：`run`；`cause`（`drift_409`、`drift_busy_503`、`drift_echo`、`as_of_expired_400`、`as_of_expired_local`）；`side`（v1 或 v2）；`resource` |

`gates`：

| 门槛 | 字段 |
|---|---|
| `page_time` | `ok`、`limit_ms`、`worst_ms`、`worst`（最慢的是哪个：`manifest`、资源名，或 `rs_series_day@<日期>`） |
| `page_bytes` | `ok`、`limit_bytes`、`worst_bytes`、`worst` |
| `run_time` | `ok`、`limit_ms`、`run_ms` |
| `row_counts` | `ok`、`mismatched`（行数对不上的资源） |
| `title_scrub` | `ok`、`hits`（六个标题字段上的 `meta.scrub` 命中）；有命中时另有 `blocks` |
| `pan_scan` | 只在加了 `--scan` 时出现：`ok`、`paths`（有命中的路径数）、`hits`（命中次数合计）；没有 v1 token 时另有 `unscanned`（没扫到的 `v1.rows`、`v1.rules`）和 `reason`，`ok` 为 false |

`page_time` 和 `page_bytes` 覆盖 manifest 和所有 v2 页（含 rs_series_day），不含 v1。

### 失败时的汇总

- 退出码 3：`ok` 为 false，另有 `error_type`（异常类名）、`error`（中文说明，只含状态码、类名和字段位置，不含值）、`runs`、`retries`。
- 退出码 4：`error_type`、`error`（固定的一句话）和 `where`（出错的文件名与行号）。

## 门槛

| 要求 | 看哪里 | 不达标时 |
|---|---|---|
| 每页少于 15 秒 | `gates.page_time` | 退出码 1 |
| 每页少于 3,000,000 字节（十进制，按解压后的 `bytes`） | `gates.page_bytes` | 退出码 1 |
| 整次少于 3 分钟 | `gates.run_time` | 退出码 1 |
| 实收行数等于 `counts` | `gates.row_counts`；逐项看 `resources`、`series_days` 的 `rows_match` | 退出码 1 |
| 六个标题字段的 `meta.scrub` 全为 0：`*.title`、`*.title_cn`、`*.description`、`rs_ids.title`、`catalog_posted.title`、`rs_bill_orders.book_title` | `gates.title_scrub` | 退出码 1，`blocks` 写「阻断 #67 合并」 |
| `--scan` 所有路径为 0（含 `v1.rules`），且 v1 确实扫过 | `gates.pan_scan`、`scan.hits`；`gates.pan_scan.unscanned` 不应出现 | 退出码 1 |
| `sourceRevision` 不为 null | `source_revision_null` 必须是 false | **不影响退出码，要人工看** |
| 数据库时间少于 90 秒 | Neon Monitoring；各资源 `sum_elapsed_ms` 与 manifest 耗时之和可以当上限 | 人工看 |

- 标题字段有命中：不合并 #67，也不进入 P2 上线。v1 的 `title` 不在 RealShort 的豁免清单里，一合并，智能体看到的剧名就会被整串替换。按 RealShort 清洗正则的 bug 处理，在 PR 分支上修好后重测。
- `--scan` 有命中：不打开镜像。先分清是 Python 多认了，还是 RealShort 漏清了。`scan.hits` 只给路径，要看值得去 RealShort 那边按路径查。
- 另外记下 `retries.drift_409`、`retries.busy_503`。Preview 的构建 SHA 是固定的，测不出生产频繁部署带来的漂移频率，那要上线后再统计。
- v1 不设门槛（它不是新接口），`v1` 里的数字只作记录。

## 退出码

| 退出码 | 含义 | 看什么 |
|---|---|---|
| 0 | 全部门槛通过 | `source_revision_null` 仍要人工看 |
| 1 | 拉取成功，但有门槛没过 | `failed_gates`、`gates` |
| 2 | 用法错误：参数不对；token 或 bypass 文件读不了、是空的；没有 v2 token；`--base-url` 不是源站，或 token、bypass 里有空白、控制字符或非 ASCII 字符 | 只在 stderr 写一行原因，没有汇总行 |
| 3 | 拉取本身失败 | 汇总的 `error_type` 与 `error`，见下 |
| 4 | 意外错误：代码 bug，或者有一种形状没有任何检查拦下 | `error_type` 与 `where`，按类名和位置排查 |

退出码 3 常见的 `error_type`：

- `ConfigError`：401 是 token 不对；404 是 token 没配、没 Redeploy 或作用域不对；3xx 是被部署保护拦下，检查 bypass（客户端不跟随跳转）。
- `DriftError`、`AsOfExpiredError`：重来 2 次之后第三次仍然漂移或过期；每次的原因在 `retries.causes`。
- `BusyTimeout`：manifest 阶段的 `source_busy` 累计等满 1200 秒。
- `SourceReadError`：`read_failed` 重试 1 次后仍失败。
- `ContractError`：响应不合契约，字段位置写在 `error` 里。
- `RowTooLargeError`：有一行超过 RealShort 的单行上限。
- `FeedConnectionError`：连接失败，或 60 秒内没收完。

## 超门槛时的两级退路

1. 先用 `--limit rs_rows=1000` 复测（服务端上限 2000），RealShort 不用改。
   - 通过的话，把 1000 写进 `customizations/pick-workbench/ggwork_pick/mirror/client.py` 的 `PAGE_LIMITS["rs_rows"]`（U47）。
   - 同时改 `customizations/pick-workbench/tests/mirror/test_feed_client.py` 的 `test_page_limits_default_to_each_resources_server_maximum`：它现在断言缺省值等于服务端上限。
2. 还是不过：把 rs_rows 改成每天只拉一次（plan:1496）。这要改 P2 的编排（5c），RealShort 仍然不用改。

超门槛的若是别的资源，不在这两级预案里，记下数字交评审。

## 收尾

测完当天做完，不论结果：

1. 删掉 Preview 上的临时变量：`vercel env rm PICK_EXPORT_TOKEN preview feat/pick-export-v2`，或在控制台删。另配过 `PICK_FEED_TOKEN` 的，同样删掉。
2. 撤销这次用的 Protection Bypass for Automation。
3. 删除那个 Preview 部署，或者在删变量之后重新部署它：旧部署仍带着 token。
4. 验证：
   - 再请求那个 Preview，应该是登录页或 404。
   - 用旧的 bypass 值（上面那条 curl）请求任意一个 Preview，应该被重定向到登录页。
5. 最后删本机文件：`rm -rf "$dir"`。`/tmp/pick-dry-run.jsonl` 里没有凭据，数字记好后也删掉。

## 测完之后

- 通过门槛的 rs_rows 页大小写成 `PAGE_LIMITS` 常量，见「超门槛时的两级退路」第 1 级。
- 实测数字记进 [realshort-sync.md](realshort-sync.md) 的 v2 一节（P1 第 11 步补这一节），同时写进 [progress.md](progress.md)。要记的是：
  - 最慢页与最大页；
  - 整次 `run_ms`；
  - `drift_409`、`busy_503`；
  - 数据库时间；
  - 用的页大小。

  只记数字和路径，不记值。
