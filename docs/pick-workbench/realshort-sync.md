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
- 迁移 0003、0004 都可以重复执行。0003 降级时，已清理的批次会标成 `failed`，被删掉的行数据无法恢复。
- 把代码回滚到 0004 之前的镜像前，要先把 `ggwp_alembic_version` 手工改回对应版本，否则旧代码找不到 0004 会停用选剧扩展；再次前进前，把它改回 0004。

## 手动操作

- 立即同步：资料页按钮。
- 看同步结果：资料页；或者 `railway logs`（定时失败记为 `[pick-sync] scheduled pull failed`）。

## 已知限制

- 候选池以外的剧（没有信号的、已下架的）不在工作台里。
- 发布记录来自运营飞书选剧池的归一结果。对不上的剧只能说「记录里没有」，不能说「从未发布」；工具与界面都按这个口径输出。
- 有名次的信号目前只有 kd、qc、qr；其余种类只能用来筛选「有这类依据」，不能按名次排序。
- ReelShort 行的信号没有日期，默认按依据日期排序时排在后面；ReelShort 标签在 feed 里被拼成一条。这两处都要在 RealShort 侧修。
