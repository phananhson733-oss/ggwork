# RealShort 只读接口与定时同步

2026-09-23 起取代 [realshort-snapshot.md](realshort-snapshot.md) 里的人工快照流程。

## 链路

```
Vercel Cron（每天 03:40 / 15:40 UTC，北京 11:40 / 23:40）
  → ggwork-deerflow 前端 GET /api/cron/pick-sync（校验 CRON_SECRET）
  → Railway Gateway POST /api/pick/cron/sync（宿主内部鉴权 + X-Pick-Sync-Token，Gateway 后台执行后立即回 202）
  → RealShort GET https://dramashortstv.com/api/pick-feed?cursor=&limit=1000（Bearer PICK_FEED_TOKEN，只 SELECT）
  → 共享批次（owner system:shared），所有登录用户可读
```

- RealShort 侧：`src/lib/pick/feed-map.ts`（纯映射）、`src/lib/pick/feed.ts`（查询）、`src/app/api/pick-feed/route.ts`。与选剧 tab 同一个 `unionRows()`，范围是「有来源信号且未标下架」的候选池，不是全部剧库。每行带信号（种类、中文名、日期、名次、评级、备注）和发布记录汇总（`matched`、帖子数、待公开数、最近发布日、发过的账号）。网盘链接与提取码不出。PR：phananhson733-oss/realshort#64。
- 工作台侧：`customizations/pick-workbench/ggwork_pick/sync.py`；同步记录在 `ggwp_sync_runs`（迁移 0003）。
- 资料页「RealShort 数据同步」区显示当前批次、最近 5 次同步和「立即同步」（登录用户可点，5 分钟冷却）。

## 完整性与失败行为

- 按 row_key 游标分页，游标链证明没有漏页；同一批次内来源 ID 重复则整批拒绝。
- 首页返回 `total`。ReelShort 那一支按实时点击 / 订单信号进出候选池，分页期间条数会变：漂移不超过 max(20, 1%) 就发布，并在批次上记 `paging_drift` 与 `source_total`；超出则本次失败，继续用上一版。
- 失败原因写进 `ggwp_sync_runs.error`，只含 HTTP 状态或异常类名，不含 token 或响应正文。
- 内容与上一版完全相同时复用旧批次，不新建。规则 Markdown 去掉采集时间行后再算哈希，以便去重。
- 保留最新 3 个共享批次；更早且没有被任何候选快照引用的批次，删除行数据，批次记录标为 `pruned`。被引用的旧批次永远保留，历史候选依据不会丢。
- 同一 Gateway 进程内，定时任务与手动按钮共用一把锁；进程重启后遗留的 `running` 记录超过 15 分钟就标记为 `interrupted`。

## 凭据与配置（只列名字，值不进仓库）

| 位置 | 变量 | 用途 |
|---|---|---|
| RealShort Vercel production | `PICK_FEED_TOKEN` | feed 的 Bearer；未配置时 feed 路由 404。刻意不复用能触发写库的 `CRON_SECRET` / `SYNC_SECRET` |
| Railway gateway | `PICK_REALSHORT_FEED_URL`、`PICK_REALSHORT_FEED_TOKEN` | 拉 feed（后者与上一行是同一个值） |
| Railway gateway | `PICK_SYNC_TOKEN` | cron 调用的第二把钥匙 |
| ggwork-deerflow Vercel production | `CRON_SECRET` | Vercel Cron 自动带上 |
| ggwork-deerflow Vercel production | `DEER_FLOW_INTERNAL_AUTH_TOKEN`、`PICK_SYNC_TOKEN` | 转调 Gateway；前者必须与 Railway 上的值一致 |

扩展不能把路由挂在宿主的免鉴权前缀（`/api/webhooks/` 等）下，这是宿主有意做的限制。所以 cron 端点走宿主内部鉴权：Gateway 要求 `principal.is_internal`，并额外校验 `X-Pick-Sync-Token`。这条路径上没有浏览器，CSRF 双提交由 Next 路由用服务端随机值自己配对。

**轮换**：先改 RealShort 的 `PICK_FEED_TOKEN`（`vercel env update`，需要重新部署），再改 Railway 的 `PICK_REALSHORT_FEED_TOKEN`，最后点一次「立即同步」确认。`PICK_SYNC_TOKEN` 两处要同时改。

## 手动操作

- 立即同步：资料页按钮；或者用 CRON_SECRET 直接调 `GET https://ggwork-deerflow.vercel.app/api/cron/pick-sync`。
- 看同步结果：资料页；或者 `railway logs`。

## 已知限制

- 候选池以外的剧（没有信号的、已下架的）不在工作台里。
- 发布记录来自运营飞书选剧池的归一结果。对不上的剧只能说「记录里没有」，不能说「从未发布」；工具与界面都按这个口径输出。
- 有名次的信号目前只有 kd、qc、qr；其余种类只能用来筛选「有这类依据」，不能按名次排序。
