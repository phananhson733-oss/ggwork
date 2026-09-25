# 数据库连接、租约、自检与库内状态（TR-13）

代码：`ggwork_pick/observe/{db,lease,selfcheck}.py`、`ggwork_pick/observe/admin/cmd_reset_disable.py`。设计 3.1、3.3、3.4、4.3；计划 TR-13、D1、D2、D5、D17、D18、D23、D34。

## 启动顺序

两个 cron（trends、gsc）每次启动都按同一个顺序走，任何一步不过就停下，这时还没发过任何 HTTP 请求：

1. **自检**（下一节）。不过：退出码 2；读不了库：退出码 3。
2. **取租约**。运行时行不存在或读不了：退出码 3；另一个进程的租约还没到期：退出码 1，本次什么都不做，下一次触发再试。
3. **读状态**（只有 trends 有，见「库里的状态」），在租约之下读，读到的就是本进程接着要写的状态。读不回来：退出码 3。
4. 之后才开始发请求。

这三步里任何一步等锁或语句超时（另一个进程卡在一步里占着运行时行，或者迁移正占着版本表），都是退出码 1，下一次触发再试；不算「状态读不了」。

代码入口是 `lease.collector_session(channel, clock=…)`，TR-14、TR-21 的 `__main__` 都从它开始；`--selfcheck-only` 用 `selfcheck.selfcheck_only(channel)`，只做自检、不取租约；`status` 这类只读命令用 `lease.status_reader(channel)`，一个只读事务，不取租约，不打扰正在跑的采集进程。

上面的顺序与退出码由 TR-13 用一个替身入口（`tests/observe/obs_db_helpers.collector_day`）测过；TR-14、TR-21 接上真实的 `__main__` 后，要用同一组错误矩阵（自检各项不符、状态读不回来的各种情形、运行时行缺失、启动时等锁超时）对真实入口再测一遍，断言零 HTTP。

## 连接

- 变量 `PICK_DATABASE_URL`：生产填 pick_observer 的 Supavisor session DSN，URL 里不带 ssl 参数，TLS 由 `PGSSLMODE` 决定（asyncpg 读这个变量）。`postgres://`、`postgresql://` 都会换成 asyncpg 驱动；本机测试可以填 `sqlite:///…`。URL 不会出现在日志或报错里，写错时只报变量名。
- 每个服务同一时刻最多一条连接，只在一步（一个事务）里持有，这一步结束就关掉；熔断暂停的几个小时里不占连接。连接账（手册 supabase.md 第 6 节）按每个服务 1 条算，两个 cron 合计最多 2 条。
- `application_name`：trends 的采集进程是 `ggwp-obs-trends`，gsc 的是 `ggwp-obs-gsc`；运维命令（reset-disable 等）和 `status` 这类只读查看（`lease.status_reader`）都是 `ggwp-obs-admin`，所以按采集进程的名字数到的只有采集进程本身，每个通道任何时刻 ≤1 条。实测时用这句查（以有权限看全部会话的身份）：

  ```sql
  SELECT application_name, count(*) FROM pg_stat_activity
  WHERE application_name LIKE 'ggwp-obs-%' GROUP BY 1;
  ```

- 每一步开头用 `set_config(…, true)` 设三个只在本事务有效的限制（`db.StepLimits` 的默认值）：等锁最多 15 秒、单条语句最多 30 秒、事务里空闲最多 60 秒。一步本来只有几毫秒的 SQL，超过这些限制说明另一个进程卡住了，这一步报 `StepTimedOut` 退出（退出码 1，启动时也一样），不会一直占着运行时行。不用会话级 `SET`（计划 6.7）。
- 驱动自己的上限跟着这一步走：asyncpg 的 `command_timeout` 取 30 秒与语句上限中较大的一个；SQLite 的 busy timeout 就是等锁上限。
- 确实要更久的一步（TR-20、TR-23b 的大批清理、联动物化）写成 `writer.step(limits=StepLimits(statement_ms=…))`，只放宽这一步，其余步骤仍用默认值；单项上限 10 分钟（GSC 一轮的硬截止）。
- JSON 用宿主（gateway）同一个序列化器写入。

## 自检（D5，反例 15）

| 比对 | 来源 | 不符时 |
|---|---|---|
| 采集合同版本 | 环境变量 `PICK_OBS_EXPECTED_COLLECTOR` 对 `versions.COLLECTOR_VERSION` | 退出码 2 |
| 迁移头 | 库里 `ggwp_alembic_version` 对本镜像自带的迁移链 | 退出码 2 |
| 角色（只查 PG） | `current_user` 对环境变量 `PICK_OBS_EXPECTED_ROLE`（生产是 `pick_observer`） | 退出码 2 |
| 包摘要 | 镜像里装的 `ggwork_pick` 全部文件的 sha256 | 只打印，由人核对 |

**迁移头的规则**：库的迁移头必须是本镜像迁移链里认识的修订，而且是 0007 或它之后的修订。

- 头比镜像新、镜像认不出（比如另一个会话的 0008 上了生产，cron 还是旧镜像）：退出码 2。处理：用带这个迁移的提交重新部署两个 cron。**以后任何新迁移上生产，都要同步重部署 gateway 与两个 cron**（计划第 10 节回滚规则）。
- 头早于 0007，或者库里没有迁移头：退出码 2，说明库还没升到 0007。
- 头是镜像认识的更新修订：通过，不要求恰好等于 0007。
- 迁移链从镜像里的 `migrations/versions/*.py` 静态读出，不导入 alembic。

**读不了库**（连不上、口令错、缺授权）：退出码 3，报错带 SQLSTATE。`42501` 是缺授权，在 gateway 容器里跑 `observe.admin regrant`（TR-12）；没有 SQLSTATE 多半是连不上（查 DSN、`PGSSLMODE`、网络）。

**等锁或语句超时**（SQLSTATE `55P03`、`57014`，比如迁移正占着 `ggwp_alembic_version`）：退出码 1，下一次触发再试。

**search_path**：采集侧的 SQL 都不写 schema，靠连接角色的 `search_path` 找到表。生产的 pick_observer 由 TR-12 的 `bootstrap-observer.sql` 设成 `deerflow`（`ALTER ROLE … SET search_path = deerflow`），`db.py` 不在连接参数里另设。`search_path` 不对时，看起来就像库里没有迁移头，所以这条拒绝会带上当前 schema 的名字（或者说明 search_path 里没有一个存在的 schema）：先查角色的 search_path，不要急着去跑迁移。

**自检通过**时日志里有一行（S6 就核对这一行）：

```
[pick-obs] selfcheck ok: collector=obs-collector-v1 head=0007 role=pick_observer package=sha256:… at=/…/ggwork_pick
```

包摘要的核对：在部署所用提交的干净检出里，于仓库根目录执行

```sh
backend/.venv/bin/python -c "import sys; sys.path.insert(0, 'customizations/pick-workbench'); from ggwork_pick.observe.selfcheck import package_digest; print(package_digest())"
```

两边摘要一致，说明镜像里装的正是这一版源码（托管副本与源码相同时）；不一致就是镜像里还是旧快照，按打包手册刷新托管副本后重新构建。摘要不含 `__pycache__`、`.pyc` 与点开头的文件。

## 租约（设计 3.3，D2）

- 租约在 `ggwp_obs_runtime` 每个通道一行：`lease_owner`、`lease_until`、`lease_generation`。0007 预置了 trends、gsc 两行（D34）。
- 取租约时先锁住这一行（PG 用 `FOR UPDATE`，SQLite 用 `BEGIN IMMEDIATE`），再判断租约是否到期：正在续租的持有者提交之前，接管方一直等着，等到了读的是续过的 `lease_until`。别人的租约没到期就不取；否则取 5 分钟，代次加一。接管与正常开始都是新的一代。
- 持有者每 60 秒续一次：每个请求前调 `ensure_fresh()`，熔断暂停期间每醒来一次（最多 60 秒一醒）也续。
- **每一次写库都在一步里**：先锁运行时行，核对 owner、代次、`lease_until` 都还有效，才写业务行，最后一起提交。代次必须核对：就算 owner 名字每次相同（比如固定的副本 ID），旧的一代也写不了、续不了、放不掉新一代的租约。预留请求预算、写入响应、发布集合、切换切片指针、写提示与里程碑都这样。接管也要锁同一行，所以一步正在写的时候接管会等它提交；旧进程不可能在接管之后提交。
- 核对不过就是租约丢了（`LeaseLost`，退出码 1）：这一步什么都没写，本进程之后的每一步都直接拒绝。下一次触发由新进程从库里的状态续跑。
- 正常结束时释放：`lease_owner` 置空、`lease_until` 记为当时，下一次运行不用等 5 分钟。进程被杀时不释放，最多 5 分钟后自然过期。
- 采集代码（`observe/trends/*`、`observe/gsc/*`）只能经 `LeasedWriter` 碰数据库，由导入图测试 `test_write_paths.py` 钉住：从 lease、selfcheck、store 三个入口只能拿 `DOORS` 里登记过的名字（`lease.py` 自己导入的 `open_database`、`ObsDatabase` 拿不到），不能碰写入口或一步的 `._db`、`._engine`、`._conn`，也不能按运行时拼出的名字导入。TR-20 的 `store.py` 每个公开函数的第一个参数都是 `LeasedStep`（只读的是 `ReadStep`），导出给采集代码的名字要在 `DOORS` 里登记。

**不要手工改租约列**。确实要让新进程立刻接手（旧进程已确认退出），把 `lease_until` 改成过去的时刻即可，不要改 `lease_generation`，也不要删行。

## 库里的状态（trends，D17、D18、D23）

金丝雀与生产不用本机的状态文件（`trends-state.md`），状态存在库里：

| 位置 | 内容 |
|---|---|
| `ggwp_obs_runtime.state_json` | 限速器与熔断两个状态机的数据，带格式版本 |
| `paused_until`、`breaker_level` | 熔断暂停截止时刻与当日已暂停次数的明文副本，状态查询用；以 `state_json` 里的熔断状态为准 |
| `cookie_jar` | cookie 罐的 Fernet 密文（与状态文件同一个 `PICK_OBS_STATE_KEY`），不进任何视图 |
| `user_agent`、`cookie_warmed_at` | 与罐绑定的 UA；罐的这次预热第一次被存下的时刻（与其他 `_at` 列一样是 UTC stamp，同一次预热之后再存不变；预热所属的目标日在罐里） |
| `disabled_at`、`reset_by`、`reset_at` | 停用（disabled_7d）开始时刻；最近一次解除的操作人与时刻 |
| `ggwp_obs_budget`（channel, budget_day） | 每个目标日一行（D23：起跑到次日 01:45 同属一天）：已预留请求数、首次限流前的请求数与时刻、当天熔断次数、429 次数、当时连续失败的探针数、熄火原因与时刻、模式与上限 |

- 预算在发请求之前扣，超时、没收到响应的都不退回；库里的请求数只增不减，要写的数比库里少会被拒（退出码 3），这说明进程手里的状态已经过时。
- 读不回来一律退出码 3，当天不跑，从不换一份空状态重来：罐用别的密钥封的、UA 列与罐里的 UA 不一致、罐与 UA 只存了一半、`state_json` 版本不认识、某个状态机的数据格式不对、预算行自相矛盾、`lease_until` 不是时刻、表读不了。处理同 `trends-state.md` 的「不要用删文件来修好」：先查清原因，不要删行、不要把列清空了事。
- 运行时行从来没写过（刚跑完 0007）就是空状态，首次运行不需要初始化步骤。**运行时行被删掉不会被当成新开始**（退出码 3）；要恢复，照 0007 的写法补回这一行：`INSERT INTO ggwp_obs_runtime (channel) VALUES ('trends') ON CONFLICT DO NOTHING`，并在 progress.md 记下原因（熔断暂停、当日计数与 cookie 罐都随之清零，至少等到下一个目标日再跑）。

## 解除停用：reset-disable

7 个目标日内熄火 3 次，直连进入停用（状态码 `disabled_7d`），之后每天都不跑，直到人工解除。只有这条命令能解除；采集进程保存的状态里如果停用没了，会被拒绝。

```sh
cd /app/backend && python -m ggwork_pick.observe.admin reset-disable --operator <你的名字>
```

- 在 `pick-obs-trends` 容器里执行（用它的 `PICK_DATABASE_URL`）。
- 有采集进程正持有租约时拒绝（退出码 1）：等它退出，或等租约过期（停止续租 5 分钟后）再执行。
- 清除熔断状态里的停用标记，但保留熄火历史，所以窗口内再熄火一天会再次停用。`disabled_at` 清空，记下 `reset_by` 与 `reset_at`。
- 当前没有停用时什么都不改，退出码 0。
- 解除之前先看熄火原因（`ggwp_obs_budget` 近 7 天的 `extinguish_reason`），决定是否要先降档或换出口，并把决定记进 progress.md。

## 退出码速查

| 退出码 | 情形 |
|---|---|
| 1 | 另一个进程持有租约（启动时）；等锁或语句超时（启动时或运行中）；本进程的租约丢了（运行中）；某一步的语句出错。下一次触发会续跑 |
| 2 | 缺 `PICK_DATABASE_URL`、`PICK_OBS_EXPECTED_COLLECTOR`、`PICK_OBS_EXPECTED_ROLE`（PG）；URL 不是 PG 或 SQLite；自检不过（合同版本、迁移头、角色） |
| 3 | 库连不上或读不了（带 SQLSTATE）；运行时行不存在；库里的状态读不回来 |
