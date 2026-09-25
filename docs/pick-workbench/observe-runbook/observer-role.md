# 观测角色 pick_observer（TR-12）

脚本：[bootstrap-observer.sql](../supabase/bootstrap-observer.sql)、[bootstrap-observer-undo.sql](../supabase/bootstrap-observer-undo.sql)；新项目用的完整 [bootstrap.sql](../supabase/bootstrap.sql) 里也有这个角色（[supabase.md](../supabase.md) 第 2 节）。代码：`ggwork_pick/observe/grants.py`、`ggwork_pick/observe/admin/cmd_regrant.py`、`ggwork_pick/mirror/publish.py` 的 `grant_observer`。设计 3.4；计划 TR-12、D3、D4、D14、D15、D16，上线步骤 S2、S4。

凭据规则同 [supabase.md](../supabase.md) 开头：仓库里只有占位符 `<ref>`、`<pw>`，口令用 `openssl rand -hex 32` 生成，只进密码管理器和权限 600 的文件，不进 git、不贴进对话、不写在命令行参数里。

## 它能碰什么

两个观测 cron（`pick-obs-trends`、`pick-obs-gsc`）以这个角色经 session pooler 连库；本机导入旧页快照（S8）与部署守卫读迁移头（TR-34）也用它。

| 对象 | 权限 | 谁给 |
|---|---|---|
| 库 `postgres` | CONNECT | bootstrap-observer.sql |
| schema `deerflow`、`pick_mirror` | USAGE | bootstrap-observer.sql |
| `ggwp_alembic_version`（迁移头） | SELECT | bootstrap-observer.sql（新项目由迁移 0007 给；生产上 0007 以同一属主再授一次，ACL 里仍只有一条） |
| `ggwp_import_batches`、`ggwp_drama_versions`、`ggwp_obs_decisions` | SELECT | 迁移 0007 |
| `ggwp_candidate_sets` 的 `id`、`trends_set_id`、`gsc_set_id`、`created_at` 四列 | SELECT（列级，D16） | 迁移 0007 |
| 其余 20 张 `ggwp_obs_*`、`ggwp_gsc_*`，含旧页快照 `ggwp_obs_legacy`（D14） | SELECT、INSERT、UPDATE、DELETE | 迁移 0007 |
| 这些表的 12 个自增序列 | USAGE、SELECT | 迁移 0007 |
| `pick_mirror.versions`、`pick_mirror.series` | SELECT（D15） | 迁移 0007 |
| 每个已发布镜像版本 `pickm_v*` | schema 的 USAGE、`rs_ids` 的 SELECT（D15） | 镜像每次发布；0007 之前发布的由 `regrant` 补 |

**碰不到：** 会话与 checkpoint、用户表、`ggwp_selections` 等其余工作台表、候选集的其他列、`pick_mirror.control` 与 `series_state`、镜像版本里 `rs_ids` 以外的表、`pick_obs`（资料页的只读视图，只给 `pick_board_reader`）。它对任何 schema 都没有 CREATE，对库也没有 CREATE，建不了表。

**角色属性：** `LOGIN NOINHERIT CONNECTION LIMIT 20`；默认 `search_path=deerflow`、`TimeZone=UTC`、`idle_in_transaction_session_timeout=1min`、`statement_timeout=2min`。两个 cron 的每一步都是短事务，HTTP 在事务之外（设计 3.3），这两个超时只兜底：事务里停住超过 1 分钟就被服务端断开，单条语句超过 2 分钟被取消。个别步骤确实要更长时，在自己的事务里 `SET LOCAL`，不改角色默认值。

## 上线顺序

- **S2 在 S3 之前。** 迁移 0007 给这个角色表级授权，但角色不存在时只在 gateway 日志里记一行 `[pick-obs] the role PICK_OBS_OBSERVER_ROLE names does not exist; observer table grants skipped` 就跳过，迁移照样成功。所以先建角色（S2），再部署带 0007 的 gateway（S3）。
- **S3 的部署守卫靠 S2 的一条授权。** 部署带 0007 的 gateway 之前，守卫（计划 D41、TR-34，手册 `deploy-guard.md`）以这个角色登录读生产迁移头，这时库还在 0006。`regrant` 在 0007 之前会拒绝（「迁移 0007 还没执行」），所以 `ggwp_alembic_version` 的 SELECT 由 bootstrap-observer.sql 自己授，S2 做完守卫就能读到头。
- **S4 补现存版本。** 0007 之前发布的镜像版本不会自动给这个角色授权（D15），S4 用 `regrant` 补齐。以后每次镜像发布都在发布事务里授予该版本的 `rs_ids`。
- 顺序做反了（S3 先于 S2）也不要紧：建好角色后跑 `regrant`，它补的就是 0007 本来会给的那一份，外加现存版本。

## S2：建角色

以 `postgres` 身份经 session pooler 执行，在仓库根目录：

```bash
PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
  -X -f docs/pick-workbench/supabase/bootstrap-observer.sql > <scratch>/bootstrap-observer.out 2>&1
echo "exit=$?"
grep -c WARNING <scratch>/bootstrap-observer.out   # 必须输出 0
```

**判定：** 退出状态 0，`grep -c WARNING` 输出 0，`bootstrap-observer.out` 的内容逐行是：

```
BEGIN
DO
CREATE ROLE
GRANT
SET
GRANT
GRANT
RESET
DO
ALTER ROLE
ALTER ROLE
ALTER ROLE
ALTER ROLE
COMMIT
```

脚本是一个事务。退出状态为 3 时，它在第一个 `ERROR` 处停下，库里什么都没变，按报错处理后可以重跑：

| 报错 | 原因与处理 |
|---|---|
| `当前连接的是 … 库` | 连错了库，连接串最后的库名必须是 `postgres` |
| `没有找到 deerflow_app 或 deerflow、pick_mirror 两个 schema` | 连错了项目，或这个项目还没执行过 bootstrap。新项目直接用完整的 `bootstrap.sql`，里面已有这个角色 |
| `没有找到 deerflow.ggwp_alembic_version` | gateway 还没在这个库上跑过迁移，不是生产项目（生产早已在 0006）。连错了项目就换连接串；新项目用完整的 `bootstrap.sql` |
| `role "pick_observer" already exists` | 已经执行过。不要重跑，按下面的留档检查看现状；要推倒重来先撤销 |
| `must be able to SET ROLE "deerflow_app"` 或 `permission denied for schema deerflow` | 连接用的不是 `postgres.<ref>`，或脚本被改过 |
| `pick_observer 的库级 CONNECT、两个 schema 的 USAGE 或迁移头的 SELECT 没有授上` | 某句 GRANT 只报了 WARNING、什么都没授（上面会有 WARNING 行）：连接角色不是库的属主，`ggwp_alembic_version` 的属主不是 `deerflow_app`，或脚本被改过。核对只认授给这个角色本身的项，PUBLIC 有的不算。停下来排查，不要设口令 |

然后另开一个交互式 psql（同一个 `postgres` 连接串）设口令，口令用 `openssl rand -hex 32` 生成：

```
\password pick_observer
```

观测角色的连接串 `postgresql://pick_observer.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`（session pooler，不带任何 ssl 参数，TLS 由 `PGSSLMODE=require` 指定）存进密码管理器，本机再存一份权限 600 的文件给部署守卫与 S8 的导入用；两个 cron 服务的 `PICK_DATABASE_URL` 由用户在 Railway 上填（S5、S9）。登录一次确认：

```bash
PGSSLMODE=require psql "postgresql://pick_observer.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -Atc "SHOW search_path"   # deerflow
```

**留档检查**，以 `postgres` 身份执行，结果记进 [supabase.md](../supabase.md) 第 12 节：

```sql
SELECT rolname, rolcanlogin, rolinherit, rolconnlimit, rolconfig FROM pg_roles WHERE rolname = 'pick_observer';
\dn+ (deerflow|pick_mirror|pick_obs)
SELECT has_schema_privilege('pick_observer', 'deerflow', 'USAGE') AS observer_deerflow,
       has_schema_privilege('pick_observer', 'pick_mirror', 'USAGE') AS observer_mirror,
       has_table_privilege('pick_observer', (SELECT oid FROM pg_class WHERE relnamespace = 'deerflow'::regnamespace AND relname = 'ggwp_alembic_version'), 'SELECT') AS observer_head,
       has_table_privilege('pick_observer', (SELECT oid FROM pg_class WHERE relnamespace = 'deerflow'::regnamespace AND relname = 'ggwp_import_batches'), 'SELECT') AS observer_batches,
       has_database_privilege('pick_observer', 'postgres', 'CREATE') AS observer_create;
```

两张表按 oid 查：`postgres` 不继承 `deerflow_app`，未必有 `deerflow` 的 USAGE，写成 `'deerflow.ggwp_alembic_version'` 按名字解析会报 permission denied。

应当看到：
- `rolcanlogin=t`、`rolinherit=f`、`rolconnlimit=20`。
- `pick_observer` 的 rolconfig 是 `{search_path=deerflow,TimeZone=UTC,idle_in_transaction_session_timeout=1min,statement_timeout=2min}`。
- `\dn+`：`deerflow` 与 `pick_mirror` 各多一行 `pick_observer=U/deerflow_app`，其余不变。S3 之前还没有 `pick_obs`；S3 之后它的权限只有 `deerflow_app=UC/deerflow_app` 与 `pick_board_reader=U/deerflow_app`，没有 `pick_observer`。
- `observer_deerflow`、`observer_mirror`、`observer_head` 为 t，其余为 f。迁移头以外的表级授权要等 0007，所以 S3 之前 observer_batches 是 f。

## S4：核对与补授权

S3 部署带 0007 的 gateway 之后，在 gateway 容器里（`railway ssh -i ~/.ssh/railway_ggwork` 进去）以 gateway 自己的 `PICK_DATABASE_URL`，即 `deerflow_app` 执行：

```bash
cd /app/backend && python -m ggwork_pick.observe.admin regrant --check
cd /app/backend && python -m ggwork_pick.observe.admin regrant
cd /app/backend && python -m ggwork_pick.observe.admin regrant --check
```

- 第一次 `--check` 在生产上预期列出「缺少授权」：0007 之前已发布的镜像版本的 schema USAGE 与 `rs_ids` SELECT。0007 自己那一份在 S2 先于 S3 时本来就在；列表里出现 `deerflow.ggwp_obs_*` 这类表，说明 0007 跑时角色还不存在。
- `regrant` 打印 `regrant：观测角色的授权已补齐：…`，退出码 0；最后一次 `--check` 打印 `授权齐全`，退出码 0。
- 输出里出现 `已发布但没有 rs_ids 的镜像版本` 这一行就不算通过，这时退出码也是 1：观测侧解析不了那个版本的非正典 id（D15、D36）。按下面 regrant 一节处理后重跑。
- 然后在本机以观测角色连库（登录命令同上，去掉 `-Atc …`），在 psql 里以 observer 连接、只读事务读当前版本的 rs_ids 后回滚，应打印当前版本名和一个大于 0 的行数，最后是 `ROLLBACK`：

```sql
BEGIN READ ONLY;
SELECT schema_name AS current_version FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1 \gset
SELECT :'current_version' AS current_version, count(*) AS rs_ids FROM :"current_version".rs_ids;
ROLLBACK;
```

## regrant

`python -m ggwork_pick.observe.admin regrant [--check]`，只在 gateway 容器里用 `deerflow_app` 的连接执行：授权要由表的属主给。它读 `PICK_DATABASE_URL`、`PGSSLMODE`（必须存在）和 `PICK_OBS_OBSERVER_ROLE`（没设时是 `pick_observer`）。

- **做什么：** 在一个事务里把上表的授权全部 GRANT 一遍（重复执行不改变任何东西），然后从系统目录读回核对，还有缺项就整个事务回滚、以 1 退出。`--check` 只读不授权。两种模式最后都列出「越权」：观测角色持有、但不在上表里的授权（别的表或列、`pick_obs`、未发布版本的 schema 等）。
- **什么时候用：** S4；cron 日志里出现 SQLSTATE `42501`（缺授权，见目录页的退出码说明）；撤销后重建角色之后；有人手工改过授权之后。
- **退出码：** 0 授权齐全、没有越权、每个已发布版本都有 `rs_ids`；1 缺授权（`--check`）、有越权、有已发布却没有 `rs_ids` 的版本，或授权后读回仍缺（已回滚）；2 什么都没做就拒绝了：缺 `PICK_DATABASE_URL` 或 `PGSSLMODE`、`PICK_OBS_OBSERVER_ROLE` 不合规则、角色不存在（先做 S2）、连接的 search_path 用不了（`deerflow` 不存在或当前角色对它没有 USAGE，例如拿 reader 的连接串跑）、连接的不是表的属主（例如拿观测角色的连接串跑）、观测表不存在（0007 还没执行）。输出与报错都不带连接串或口令。
- **已发布但没有 `rs_ids` 的版本**单独列出，以 1 退出（`regrant` 模式下其余授权照样提交）：那个版本没有可授的表，观测侧解析不了它的非正典 id。每个版本的 DDL 都建 `rs_ids`，只有镜像的写入端改了表名时才会出现，要与维护镜像的会话核对。
- **锁等待：** 每条语句最多等锁 10 秒、执行 60 秒。镜像正在建版本或清理旧版本时可能等不到锁、以 1 退出（SQLSTATE `55P03`），什么都没改，稍后重跑。
- **与镜像清理的关系：** 清理旧版本用 `DROP SCHEMA … CASCADE`，版本上给观测角色的授权随之消失，不用另外收回；观测 cron 正在读某个旧版本的 `rs_ids` 时，那次 DROP 会等锁，等不到就留给下一次清理，与资料页 reader 的情形相同。
- **越权的处理：** `regrant` 从不收回。按列出的对象，以 `deerflow_app` 身份逐个收回，例如 `REVOKE ALL ON deerflow.ggwp_selections FROM pick_observer;`、`REVOKE SELECT (owner_id) ON deerflow.ggwp_candidate_sets FROM pick_observer;`、`REVOKE ALL ON SCHEMA pick_obs FROM pick_observer;`，再跑一次 `--check`。

**每次镜像发布：** 从 0007 起，`publish_mirror_pair` 在同一个发布事务里给观测角色授该版本 schema 的 USAGE 与 `rs_ids` 的 SELECT，守卫与 reader 相同：角色不存在就跳过。gateway 上**不设** `PICK_OBS_OBSERVER_ROLE`（用默认名）；设了而不合规则时，迁移与每次发布都会在执行任何 SQL 之前报错，镜像发布因此降级。

## 撤销

用于 S2 已提交、但留档检查不通过；或者要整个撤掉观测角色。0007、镜像发布与 `regrant` 之后也能用：它先以 `deerflow_app` 身份收回这个角色在所有表、列、序列、schema 上的授权，再收回库级授权，最后删掉角色。观测表和里面的数据不动，reader 与 `deerflow_app` 的授权不动。

1. 先停掉两个观测 cron（去掉 cronSchedule 或暂停服务），确认没有在跑的一轮。Supavisor 可能还留着这个角色的空闲服务端连接，不影响撤销，角色删掉后它们在下次使用时失败。
2. 以 `postgres` 身份执行：

   ```bash
   PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
     -X -f docs/pick-workbench/supabase/bootstrap-observer-undo.sql > <scratch>/bootstrap-observer-undo.out 2>&1
   echo "exit=$?"
   grep -c WARNING <scratch>/bootstrap-observer-undo.out   # 必须输出 0
   ```

   **判定：** 退出状态 0，`grep -c WARNING` 输出 0，`bootstrap-observer-undo.out` 的内容逐行是：

   ```
   BEGIN
   DO
   SET
   DO
   RESET
   REVOKE
   DROP ROLE
   COMMIT
   ```

   退出状态为 3 时整个事务已回滚，角色与授权保持原样。`role "pick_observer" does not exist` 说明已经撤销过。其他报错多半是某个对象的授权不是 `deerflow_app` 给的（例如有人以别的身份手工授过），先按报错里的对象收回再重跑。
3. 要重新启用：再执行一次 S2（新角色只有库的 CONNECT、两个 schema 的 USAGE 与迁移头的 SELECT），设新口令并更新各处的连接串，然后按 S4 跑 `regrant`：旧角色的表级授权随它一起删掉了，0007 不会再跑一次。

## 连接账

- `CONNECTION LIMIT 20`，等于 Supavisor 的 Pool Size（supabase 方案 plan:922 的规则：角色上限低于池大小时，Supavisor 开超出上限的服务端连接会直接报错，而不是排队；D4）。
- 实际最多 2 条：两个 cron 各限定 1 条连接（D1），Railway 不让同一服务的两轮重叠。本机导入（S8）与部署守卫（TR-34）也用这个角色，各 1 条、用完即断，与 cron 同时进行时短时多 1–2 条。
- 合计见 [supabase.md](../supabase.md) 第 4 节：原有最坏约 50–55，加上后约 52–57，库上限 60，余量很小。
- **实测**（G5 的核对项）：影子运行期间按 plan:923 的方法，同时跑一次对话、一次同步、一次资料页渲染，并赶上两个 cron 的运行窗口，以 `postgres` 身份看 `SELECT usename, count(*) FROM pg_stat_activity GROUP BY 1 ORDER BY 2 DESC`。Supavisor 会把客户端断开后的服务端连接留在池里复用（supabase.md 第 12 节「连接计数的读法」），所以看到的是池子大小：`pick_observer` 不超过 2（导入或守卫恰好同时在跑时不超过 4）算正常。合计超过预算时升 compute，或者让 cron 暂用 `deerflow_app` 的池（后者权限更宽，要用户定）。结果记进 supabase.md 第 12 节。
