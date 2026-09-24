# RealShort 只读接口与定时同步

2026-09-23 起取代 [realshort-snapshot.md](realshort-snapshot.md) 里的人工快照流程。同日评审后，定时从 Vercel Cron 改为 Railway gateway 进程内触发。

## 链路

```
Railway gateway 进程内定时（每天 03:40 / 15:40 UTC，北京 11:40 / 23:40；ggwork_pick/schedule.py）
  → RealShort GET https://dramashortstv.com/api/pick-feed?cursor=&limit=1000（Bearer PICK_FEED_TOKEN，只 SELECT）
  → 共享批次（owner system:shared），所有登录用户可读
```

- 没有任何入站的定时接口，宿主的内部 token 不离开 Railway。gateway 启动时，如果最近一次成功同步已超过 12 小时（例如部署或崩溃跨过了一个时间点），60 秒后补跑一次。
- 按单个 gateway 进程设计。若扩成多副本，每个副本都会拉取；内容相同的批次会去重，但会多占 RealShort 的查询。
- RealShort 侧：`src/lib/pick/feed-map.ts`（纯映射）、`src/lib/pick/feed.ts`（查询）、`src/app/api/pick-feed/route.ts`。它与选剧 tab 用同一个 `unionRows()`，范围是「有来源信号且未标下架」的候选池，不是全部剧库。每行带两类数据：
  - 信号：种类、中文名、日期、名次、评级、备注。
  - 发布记录汇总：`matched`、帖子数、待公开数、最近发布日、发过的账号。

  网盘链接与提取码不出。PR：phananhson733-oss/realshort#64。
- 工作台侧：`customizations/pick-workbench/ggwork_pick/sync.py`；同步记录在 `ggwp_sync_runs`（迁移 0003）。
- 资料页「RealShort 数据同步」区显示当前批次、最近 5 次同步和「立即同步」按钮。所有登录用户都能点：上一次成功或正在进行时要隔 5 分钟，上一次失败则隔 1 分钟。

## 完整性与失败行为

- 按 row_key 游标分页，游标链证明没有漏页；同一批次内来源 ID 重复则整批拒绝。
- 首页返回 `total`。ReelShort 那一支按实时点击 / 订单信号进出候选池，分页期间条数会变：
  - 漂移不超过 max(20, 1%)：照常发布，并在批次上记 `paging_drift` 与 `source_total`。
  - 超出：本次失败，继续用上一版。
- 整个下载有 10 分钟总时限。进程停止时同步会被取消，记录为失败（「同步被中止」）；进程重启时，把上一个进程留下的 `running` 记录改为 `failed`（原因记为 `interrupted`）。gateway 关停时，定时任务和手动同步合计最多等 20 秒。
- 失败原因写进 `ggwp_sync_runs.error`，所有登录用户可见：
  - 只含 HTTP 状态、异常类名，或数据校验不通过的字段位置与类型。
  - 不含 token、响应正文和字段值。
- 规则 Markdown 先校验再发布剧库，避免剧库已更新、规则却失败的半截状态。
- 内容与之前某一版完全相同时复用那一版：更新它的采集时间，如果它已不是当前版本，就重新设为当前版本（A→B→A 会回到 A）。规则 Markdown 去掉采集时间行后再算哈希，以便去重。
- 生产方新增或改动 feed 行字段必须升级 `FEED_VERSION`。工作台对行做严格校验，未知字段会让整次同步失败并继续用上一版。

## 存储上限

选剧表与宿主共用 /data 卷上的 deerflow.db，卷满会让整个 gateway 停写，所以占用必须有上限：

- 同步批次只存规整后的行，不再额外保存原样副本（原样内容在原始 blob 文件里）。
- 共享批次只保留两类：最新 3 份，以及 30 天内被候选快照用到的。其余批次删除行数据、删除原始 blob 文件，批次记录标为 `pruned`。
- 旧候选卡照常可看（快照里带着当时的依据），只是超过保留期后不能在上面「换一批」，会提示重新查询。
- 发布前先清理已过保留期的批次，再检查剩余空间，低于 500 MB 就拒绝发布。先清理，是为了让磁盘满了之后下一次同步还能腾出空间自行恢复。
- 按目前约 6.8k 行估算：保留期内最多约 60 批，行数据加 blob 合计约 1 GB。

## 凭据与配置（只列名字，值不进仓库）

| 位置 | 变量 | 用途 |
|---|---|---|
| RealShort Vercel production | `PICK_FEED_TOKEN` | feed 的 Bearer；未配置时 feed 路由 404。刻意不复用能触发写库的 `CRON_SECRET` / `SYNC_SECRET` |
| Railway gateway | `PICK_REALSHORT_FEED_URL`、`PICK_REALSHORT_FEED_TOKEN` | 拉 feed（后者与上一行是同一个值）；两者都有才启用定时 |

ggwork-deerflow 的 Vercel 项目不再需要 `CRON_SECRET`、`PICK_SYNC_TOKEN`、`DEER_FLOW_INTERNAL_AUTH_TOKEN`；Railway 上的 `PICK_SYNC_TOKEN` 也不再使用。

**轮换**：先改 RealShort 的 `PICK_FEED_TOKEN`（`vercel env update`，需要重新部署），再改 Railway 的 `PICK_REALSHORT_FEED_TOKEN`，最后点一次「立即同步」确认。

## 部署与回滚

- 结果接口新增字段时先部署前端：前端用严格 schema 解析候选结果，旧前端会拒绝新字段。
- 迁移 0003 到 0006 都可以重复执行。0003 降级时，已清理的批次会标成 `failed`，被删掉的行数据无法恢复。
- 0005 给 `ggwp_candidate_sets`、`ggwp_sync_runs` 加的是可空列，0006 只在 PostgreSQL 上建 `pick_mirror` 的四张表；旧代码不读这些列和表，所以回滚到 0005 之前的镜像只需改版本表，不用降级。版本表改回 0004 而这些对象都还在时，再次前进照常成功（测试钉住）。
- 把代码回滚到 0004 之前的镜像前，要先把 `ggwp_alembic_version` 手工改回对应版本，否则旧代码找不到 0004 会停用选剧扩展；再次前进前，把它改回 0004。

## 手动操作

- 立即同步：资料页按钮。
- 看同步结果：资料页；或者 `railway logs`（定时失败记为 `[pick-sync] scheduled pull failed`）。
- feed v2 的实测（P1-6 dry-run）和镜像上线前的 `--scan`：见 [mirror-dry-run.md](mirror-dry-run.md)。那是一次性流程，临时凭据测完即删，所以单独成篇；实测数字与最终的 `rs_rows` 页大小记在下面「feed v2（镜像用）」一节。

## feed v2（镜像用）

P2 镜像读 RealShort 的 feed v2（realshort#67：manifest 加八个行资源和 rs_series_day）。上面的 v1 定时同步不变。

**凭据**（只列名字，值不进仓库）：

| 位置 | 变量 | 用途 |
|---|---|---|
| RealShort Vercel，只配 Production | `PICK_EXPORT_TOKEN` | v2 的 Bearer；没配时 v2 路由返回 404 |
| Railway gateway | `PICK_REALSHORT_EXPORT_TOKEN` | 拉 v2；与上一行是同一个值 |

- 2026-09-24 16:40 UTC 起，RealShort Production 配了 `PICK_EXPORT_TOKEN`（只配 Production，用同一个提交 c45c520 重新部署），v2 不带 token 返回 401；Railway 的 `PICK_REALSHORT_EXPORT_TOKEN` 是同一个值。
- 实测和镜像上线前 `--scan` 的一次性流程（临时凭据、bypass、收尾）见 [mirror-dry-run.md](mirror-dry-run.md)。
- **轮换**：先改 RealShort 的 `PICK_EXPORT_TOKEN`（需要重新部署），再改 Railway 的 `PICK_REALSHORT_EXPORT_TOKEN`，最后点一次「立即同步」确认。

**P1-6 实测（2026-09-24 10:37–10:44 UTC）**：在 RealShort `feat/pick-export-v2`@816ca2e 重新部署的 Preview 上（读生产库）跑了两次 dry-run。

- 第一次，默认页大小：
  - rs_rows 按 3 MB 截页，约 1,250 行一页，共 25 页；单页 2.6–25.4 s，整次 228.8 s；manifest 25.5 s。
  - `--scan`（含 v1 行与 `v1.rules`）0 命中。
  - 行数 8 个资源与当天 rs_series_day 全对：catalog_rows 42,025、catalog_signals 2,783、catalog_posted 193、catalog_accounts 19、rs_rows 31,848、rs_ids 34,913、rs_clicks14 11,277、rs_bill_orders 14；rs_series_day 34,841；v1 8,141。
  - title 类字段的 `meta.scrub` 为 0；`sourceRevision` 非空；漂移与 busy 都是 0。
- 第二次，`--limit rs_rows=1000`：rs_rows 32 页，单页 1.9–2.6 s，整次 120.8 s；manifest 22.7 s；最大页 2,995,895 字节。
- 结论：
  - rs_rows 页大小定为 1000（U47），写在 `customizations/pick-workbench/ggwork_pick/mirror/client.py` 的 `PAGE_LIMITS["rs_rows"]`。
  - manifest 22–25 s，超过方案每页 15 s 的门槛。用户决定先接受：manifest 单独门槛 45 s（dry-run 的 `manifest_time`），上线后看运行记录里的 manifest 耗时；RealShort 另开后续任务，优化 manifest 的 13 个并行查询。
  - 数据库时间没有在 Neon 控制台上看；请求耗时合计约 120 s，是它的上限。
- 合并：#67 于 2026-09-24 10:48 UTC 以 merge commit c45c520 合并。生产部署后 v2 不带 token 返回 404，v1 返回 401；10:50 UTC 工作台手动同步成功，8,141 部。

## 镜像写入（P2）

开关 `PICK_MIRROR_ENABLED=1` 打开后，同一个定时改为镜像同步（v1 批次与 `pick_mirror` 版本一起发布）。上线核验、回填时间窗、值守与回滚见 [mirror-runbook.md](mirror-runbook.md)。回滚到 v1 之后必须跑一次 `mirror.admin cleanup`。

**上线记录（2026-09-24，UTC）**：

- 16:28 生产 dry-run `--scan`（入口 `python -m ggwork_pick.mirror.client --dry-run`，Production 不给 bypass）：整次 132.9 s；manifest 22.5 s；行页最慢 3.75 s；最大页 2,995,895 字节（catalog_rows，离 3 MB 只差约 4 KB，是 RealShort 按字节截页的结果）；v1 8,143 行等于 total；八个资源与 rs_series_day 34,841 行全对；扫描 0 命中；严格行模型 52 页 0 失败；title 类 scrub 0；sourceRevision 非空；漂移、busy、read_failed 都是 0。
- 18:23 以开关关闭部署 1db08b6，迁移 0004→0005→0006；[mirror-runbook.md](mirror-runbook.md) 第 2 节四项全部通过。
- 18:27 回填 `--backfill 92`：RealShort 的 snapshotDays 从 2026-09-10 开始，合并 15 天，每天 1.8–2.7 s；through 2026-09-24，trimmed_before 2026-09-10，缺天 0；库 61 MB 到 100 MB，series 表 28 MB。
- 18:29 设 `PICK_MIRROR_ENABLED=1`（Railway 自动重新部署）。
- 18:32 手动同步：success、paired，版本 1（`pickm_v000001`，as_of 18:30）；整次 114 s（manifest 24.2 s、v1 10.3 s、v2 73.3 s、收尾 5.1 s）；八道闸门全过；网盘命中 v1、规则、镜像都是 0；`mirror.behind` 为 false，共享批次的 source_as_of 等于版本 as_of；`meta.scrub` 为空；reader 对新版本有 USAGE 和九张表的 SELECT；advisory 锁计数 0；库 188 MB。
- 智能体 10 题验收：10 题都是 200，调用的工具与返回条数和 P0-6 切换那次逐题一致。

## 候选卡的镜像版本号（P4-1）

Railway 变量 `PICK_EMIT_MIRROR_VERSION` 决定结果接口（`/api/pick/results`）、查询与单条详情工具、统计工具的 `data_as_of` 里带不带 `mirror_version`：候选当时配对的镜像版本号，降级发布（没有配对版本）时为 null。值严格等于 `1` 才输出；不设、`0`、` 1`、`true` 都不输出这个键。候选卡上的「在选剧资料核对」链接靠它显示。

上线顺序：先部署接受这个字段的前端（资料页与 P4-1 前端）；后端可以不设变量先部署。至少隔一天或在非工作时间，提前提醒大家刷新页面，再在 Railway 设 `PICK_EMIT_MIRROR_VERSION=1`。改变量会触发重新部署、打断进行中的运行，要避开 03:40 / 15:40 UTC 的同步。原因：前端按 strict 解析 `data_as_of`，前端上线前打开、之后没刷新的标签页见到新键会解析失败（候选卡报错，刷新即恢复，库里数据不受影响）。回滚：删掉变量或设为 `0`。

## 已知限制

- 候选池以外的剧（没有信号的、已下架的）不在工作台里。
- 发布记录来自运营飞书选剧池的归一结果。对不上的剧只能说「记录里没有」，不能说「从未发布」；工具与界面都按这个口径输出。
- 有名次的信号目前只有 kd、qc、qr；其余种类只能用来筛选「有这类依据」，不能按名次排序。
