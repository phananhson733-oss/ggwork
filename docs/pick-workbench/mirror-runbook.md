# 镜像写入（P2）上线与值守手册

方案第 9 节第 6 步的操作细节，以及 P2 终审（2026-09-25）要求写进手册的几件事。命令都经 `railway ssh` 在 gateway 容器里执行，SQL 都是只读查询，在 Supabase SQL 编辑器里以 postgres 角色执行即可。这里不写任何变量的值。

## 1. 发布前门禁

- 托管副本必须与源码一致：`backend/extensions/sources/ggwork-pick` 是镜像实际安装的扩展（`docker/Dockerfile.pick-gateway` 只复制 `backend/`）。改完 `customizations/pick-workbench` 后按 [local-run.md](local-run.md) 刷新副本，并且**不带 `-k` 排除**跑一次：

  ```bash
  backend/.venv/bin/python -m pytest customizations/pick-workbench/tests/test_managed_copy.py -q
  ```

  这条不过就不能部署：照旧副本构建的镜像里没有 `ggwork_pick.mirror`，迁移也停在 0004。

## 2. 以开关关闭部署之后、配 token 之前的核验

部署本身不会因为扩展启动失败而失败：迁移在 `PickService.initialize` 里跑，失败时宿主只记一条日志、跳过扩展，`/health/ready` 照样通过。所以以下四项都要看，**全部通过后**才去配 `PICK_REALSHORT_EXPORT_TOKEN`、跑生产 dry-run、打开开关。

1. 日志里没有 `ggwork-pick` 的 `service start() failed`。
2. 容器里能导入镜像代码，并读到打包进 wheel 的 DDL：

   ```bash
   cd /app/backend && python -c 'import ggwork_pick.mirror.run, ggwork_pick.mirror.versions as v; v.ddl_template(); print("ok")'
   ```

3. 迁移到了 0006，`pick_mirror` 的四张表属主是 `deerflow_app`：

   ```sql
   SELECT version_num FROM deerflow.ggwp_alembic_version;          -- 0006
   SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'pick_mirror' ORDER BY 1;
   -- control、series、series_state、versions，属主都是 deerflow_app
   ```

4. reader 能读其中三张，读不到 control：

   ```sql
   SELECT has_table_privilege('pick_board_reader', 'pick_mirror.versions', 'SELECT'),
          has_table_privilege('pick_board_reader', 'pick_mirror.series', 'SELECT'),
          has_table_privilege('pick_board_reader', 'pick_mirror.series_state', 'SELECT'),
          has_table_privilege('pick_board_reader', 'pick_mirror.control', 'SELECT');  -- 前三个 true，最后一个 false
   ```

## 3. 回填与 cleanup 的时间窗

镜像锁被回填（`--backfill`）或 `admin cleanup` 占着时，定时同步直接记失败（`lock_busy`，不计数），v1 也不发布（U46）。定时只有 03:40 / 15:40 UTC 两档，补跑只在进程启动时发生；原生采集模式下，每次采集完成后还会自动同步一次（见第 4 节）。撞上一档，智能体的数据最长要晚 12 小时。

- 回填和 cleanup 在某一档**结束至少 30 分钟后**再开始（看 `/api/pick/sync` 的 runs[0] 已经结束），并在下一档之前确认进程已经退出。
- 回填 92 天约 92 个 rs_series_day 页，实测每页约 1.2 秒、2.9 MB、34,841 行；每合并一天都会整表重写一次曲线，92 次合并约写出 3.3 GB 行版本（series.py 的估算）。跑的时候看 Supabase 的磁盘和 IO。
- 回填中断了可以直接重跑：它从下一天续上。

## 4. 上线头一周的值守

降级（degraded）、容量超限（capacity）和退回 v1（fallback_v1）的运行都记为 success，现有资料页显示为绿色，原因只在 `details_json` 和 `/api/pick/sync` 的 `mirror` 里。P3 的横幅上线之前：

- 每档之后看一次 `/api/pick/sync` 的 `mirror`：`current.id` 前进了，`behind` 为 false，`consecutive_failures` 为 0。
- manifest 请求没有任何应答（60 秒没收完，或连接失败）时，镜像运行隔 90 秒换一个 as_of 再问一次，这次有应答就照常成对发布，`details_json.manifest_retries` 记 1；第二次仍无应答才退回 v1，`details_json.fallback.error` 会写明「重试 1 次后仍无应答」。manifest 平时就要 27–32 秒，2026-10-10 03:40 UTC 那一档超过 60 秒，当时没有重试，资料页因此落后智能体一档。
- manifest 失败退回 v1 时，gateway 会打一条 WARNING：`[pick-mirror] the manifest failed (…); this run falls back to v1 and publishes no version`；重试时是 `[pick-mirror] the manifest got no answer (…); asking once more in 90 seconds`。此前这条路径不写日志。
- 原生采集模式（`PICK_SOURCE_ENABLED=1`）下，gateway 每 5 分钟问一次采集服务的状态：有采集在最近一次同步开始之后完成、且没有采集在跑时，自动同步一次，记录的触发来源是 `collect`（资料页显示「采集后」）。每次采集只触发一次，这次同步失败或降级也不再为同一次采集重试，留给两档定时。按现在的采集节奏，每天约多 5 次同步（ReelShort 片库与账单 4 次、剧单 1 次）。「立即同步」的冷却不分触发来源，所以这次同步开始后的 5 分钟内（失败则 1 分钟内）点「立即同步」会得到 429。
- `mirror` 是 `{"error": "<类名>"}` 时，说明 gateway 读 `pick_mirror` 失败了（语句超时、权限等），其余字段照常，要查 gateway 日志。
- 连续失败到第 3 次，gateway 会打一条 ERROR：`[pick-mirror] 连续失败 N 次：<原因代码>`。可以在 Railway 日志上给这句配告警。
- `mirror.lock_stuck` 只在锁被别的进程占了 80 分钟以上时出现（F7：一次合法的运行最长约 71.5 分钟）。按其中的 pid 核实后，才考虑 `pg_terminate_backend`。

## 5. 回滚

1. 把 `PICK_MIRROR_ENABLED` 改成 `0`（Railway 会重新部署）。之后的同步走 v1。
2. **必做**：确认没有镜像同步或回填在跑（`/api/pick/sync` 的 runs[0] 已结束，`mirror.lock_stuck` 为 null），然后执行一次 cleanup：

   ```bash
   cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.admin cleanup
   ```

   退出码 0 才算完成；1 表示锁被占着，什么都没改，稍后再跑。
3. 确认没有遗留的暂存批次：

   ```sql
   SELECT count(*) FROM deerflow.ggwp_import_batches WHERE owner_id = 'system:shared' AND status = 'importing';  -- 0
   ```

4. 再等下一档或手动同步，确认 runs[0] 成功。

为什么必做：镜像运行在暂存之后被强杀（改开关本身就会触发重新部署），会留下两份 importing 批次，占着同内容的唯一约束。v1 同步在 PostgreSQL 上会先以 cleanup 身份不等待地拿镜像锁、清掉它们（`details_json.cleanup` 记录清了什么），拿不到锁时这次同步以「同内容的批次还处于暂存状态……」失败。cleanup 这一步保证回滚后第一次 v1 同步不会撞上这种情况，第 3 步的查询用来核对。
