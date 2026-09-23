# 工作台数据库迁 Supabase：运行手册

对应方案 [2026-09-23-supabase-pick-board-plan.md](../plans/2026-09-23-supabase-pick-board-plan.md) 第 6 节（6.1–6.10）与第 9 节。本文只写怎么做、看什么结果，原因与取舍看方案；两者冲突时以方案第 13、14 节为准。

**状态（2026-09-23）：** 正式项目已建并实测（第 1 节）；bootstrap 脚本和测试已完成（P0-4），**还没有在正式项目上执行**；演练（P0-5）和正式切换（P0-6）都没做，结果按第 12 节留档。

**凭据规则：**
- 本文和仓库里只有占位符：`<ref>` 是项目 ref，`<pw>` 是对应角色的密码。真实值只在密码管理器里，不进 git，不贴进对话，不写在命令行参数里。
- 数据库密码一律用 `openssl rand -hex 32` 在本机生成，先写进权限 600 的临时文件，再存进密码管理器。不用 `-base64`：它会生成 `/`、`+`、`=`，放进连接串要百分号编码。
- 需要在 shell 里用连接串时，用 `read -rs PICK_DATABASE_URL && export PICK_DATABASE_URL` 粘贴进去，不回显、不进 shell 历史。

## 1. 项目设置（6.1）

**已建（2026-09-23 实测）：**

| 项 | 值 |
|---|---|
| 项目 | `ggwork-workbench`，Pro 组织，East US（North Virginia）即 `us-east-1`，compute Micro（方案 13.1） |
| 版本 | PostgreSQL 17.6 |
| 连接与内存 | `max_connections=60`，`shared_buffers` 256 MB，`work_mem` 3500 kB |
| 库 | `postgres`，collation `en_US.UTF-8`，时区保持 UTC |
| session pooler | `aws-0-us-east-1.pooler.supabase.com`（`aws-1` 对本项目不可用），本机经 IPv4 可连 |
| `postgres` 角色 | `rolsuper=f`、`rolcreaterole=t`；是 `postgres` 库的属主（`datdba=postgres`），有库的 CREATE 权限。bootstrap 用到的授权语句在一个回滚掉的事务里都成功过（方案 3.1、10.2 第 10 条） |

**控制台设置（执行 bootstrap 之前做完，结果记进第 12 节）：**
- Database → Settings：开启 Enforce SSL。
- Data API：关闭，或者把 Exposed schemas 清空。
- 不使用 anon key 和 service_role key，不配置 Auth、Storage、Realtime。
- Database Settings → Connection pooling：Pool Size 设 **20**，与两个业务角色的 `CONNECTION LIMIT` 相等（第 4 节）。
- PITR（付费附加）要不要开、Spend cap 开还是关：由操作员决定并记下。Pro 默认每日备份、保留 7 天；Spend cap 开启时，超出配额（例如磁盘超过 8 GB）会被限制。

**禁止：** 以后任何时候都不要重新打开 Data API，也不要把 `deerflow`、`pick_mirror` 或 `pickm_v*` 加进 Exposed schemas。里面的推荐人、飞书记录 id、指标以及用户会话都会被公开（方案 10.1）。

## 2. bootstrap（6.2、3.1）

脚本：[supabase/bootstrap.sql](supabase/bootstrap.sql)。只执行一次，以 `postgres` 身份经 session pooler 连 `postgres` 库。它做的事：
- 建 `deerflow_app`（Railway 用，拥有 `deerflow`、`pick_mirror` 和以后的 `pickm_v*`，对库有 CREATE）与 `pick_board_reader`（Vercel 资料页用，只对 `pick_mirror` 有 USAGE，默认只读事务、语句超时 8 秒），两个都是 `LOGIN NOINHERIT CONNECTION LIMIT 20`。
- 给 `postgres` 补上对 `deerflow_app` 的 SET 权限（`INHERIT FALSE`），建两个 schema，`SET ROLE deerflow_app` 以属主身份做 REVOKE 和 reader 的 USAGE，再 `RESET ROLE` 回到 postgres 做 `ALTER ROLE`（审计 host-1：少了 SET ROLE 时要么报 permission denied，要么只报 WARNING、授权悄悄缺失）。
- 整个脚本是一个事务：任何一句失败都整体回滚，项目保持原样。连的不是 `postgres` 库、或者 `deerflow_app` 没拿到库的 CREATE 权限时，脚本里的检查会让它直接失败，而不是只报 WARNING 继续。

`customizations/pick-workbench/tests/test_bootstrap_sql.py` 在本机 PG 17 上用 `NOSUPERUSER CREATEROLE`、持有库的替身角色执行同一份脚本（`PICK_TEST_PG_URL` 已设时运行），覆盖：无 WARNING、授权与角色设置、删掉 SET 授权或 SET ROLE 时在哪一句失败、替身另有 `pg_read_all_data`/`pg_write_all_data` 时仍然干净、库属主不对时硬失败。真实 Supabase 上的完整执行由演练确认（第 10 节）。

### 2.1 执行

在仓库根目录执行。密码在提示时从密码管理器粘贴，不写进 URL：

```bash
PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
  -X -f docs/pick-workbench/supabase/bootstrap.sql > <scratch>/bootstrap.out 2>&1
echo "exit=$?"
grep -c WARNING <scratch>/bootstrap.out   # 必须输出 0
```

**判定：** 退出状态 0，`grep -c WARNING` 输出 0，`bootstrap.out` 的内容逐行是：

```
BEGIN
DO
CREATE ROLE
CREATE ROLE
GRANT ROLE
GRANT
GRANT
DO
CREATE SCHEMA
CREATE SCHEMA
SET
REVOKE
REVOKE
GRANT
RESET
ALTER ROLE
ALTER ROLE
ALTER ROLE
ALTER ROLE
ALTER ROLE
ALTER ROLE
ALTER ROLE
ALTER ROLE
COMMIT
```

psql 在服务端只警告、什么都没授予时照样打印 `GRANT`，所以只看到 `GRANT` 不算通过，必须同时确认没有 WARNING。

### 2.2 失败时

退出状态为 3 时，脚本在第一个 `ERROR` 处停下，之前的语句已经全部回滚，库里什么都没变。按报错处理，改正后重跑：

| 报错 | 原因与处理 |
|---|---|
| `当前连接的是 … 库` | 连错了库。连接串最后的库名必须是 `postgres` |
| `role "deerflow_app" already exists` | 已经执行过。不要重跑，先按 2.4 检查现状 |
| `… 没有拿到 postgres 库的 CREATE 权限` | `postgres` 不是库的属主，或者没有 grant option（前面两句 `GRANT … ON DATABASE` 会各有一行 WARNING）。停下来，按方案 10.2 第 10 条的备选处理 |
| `must be able to SET ROLE "deerflow_app"` | `GRANT deerflow_app TO CURRENT_USER … SET TRUE` 没有生效。核对脚本没被改过、连接用的是 `postgres.<ref>` |
| `permission denied for schema deerflow` | REVOKE 没有在 `SET ROLE deerflow_app` 之下执行。核对脚本没被改过 |

退出状态为 0 但有 WARNING：说明环境与测试里的替身不同，授权可能缺失。不要设置密码，也不要部署，先按 2.4 逐项检查；要推倒重来时用 2.5 撤销。

### 2.3 设置两个角色的密码

先按 2.4 检查通过，再另开一个交互式 psql 会话设置密码。`\password` 在本地算好 SCRAM 摘要再发给服务端，密码明文不会进服务端日志：

```bash
PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X
```

```
\password deerflow_app
\password pick_board_reader
```

两个密码各自用 `openssl rand -hex 32` 生成，互不相同，存进密码管理器。然后用新角色各登录一次确认：

```bash
PGSSLMODE=require psql "postgresql://deerflow_app.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
  -X -Atc "SHOW search_path"                      # deerflow
PGSSLMODE=require psql "postgresql://pick_board_reader.<ref>@aws-0-us-east-1.pooler.supabase.com:6543/postgres" \
  -X -Atc "SHOW default_transaction_read_only"    # on
```

### 2.4 留档检查

以 `postgres` 身份执行，结果贴进第 12 节：

```sql
SELECT rolname, rolcanlogin, rolinherit, rolconnlimit, rolconfig
  FROM pg_roles WHERE rolname IN ('deerflow_app', 'pick_board_reader') ORDER BY 1;
\dn+ (deerflow|pick_mirror)
SELECT has_schema_privilege('pick_board_reader', 'pick_mirror', 'USAGE') AS reader_mirror,
       has_schema_privilege('pick_board_reader', 'deerflow', 'USAGE') AS reader_deerflow,
       has_schema_privilege('anon', 'pick_mirror', 'USAGE') AS anon_mirror,
       has_schema_privilege('authenticated', 'deerflow', 'USAGE') AS authenticated_deerflow,
       has_database_privilege('deerflow_app', 'postgres', 'CREATE') AS app_create,
       has_database_privilege('pick_board_reader', 'postgres', 'CREATE') AS reader_create;
SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'postgres';
```

应当看到：
- 两个角色 `rolcanlogin=t`、`rolinherit=f`、`rolconnlimit=20`。
- `deerflow_app` 的 rolconfig 是 `{search_path=deerflow,TimeZone=UTC,idle_in_transaction_session_timeout=5min}`。
- `pick_board_reader` 的 rolconfig 是 `{search_path=pick_mirror,default_transaction_read_only=on,statement_timeout=8s,idle_in_transaction_session_timeout=15s,TimeZone=UTC}`。
- `\dn+`：两个 schema 的属主都是 `deerflow_app`；`deerflow` 的权限只有 `deerflow_app=UC/deerflow_app`；`pick_mirror` 另有一行 `pick_board_reader=U/deerflow_app`。没有 `=U/`（PUBLIC）、`anon=`、`authenticated=` 开头的项。
- `reader_mirror` 为 t，其余几个 reader、anon、authenticated 的列为 f，`app_create` 为 t。
- 库属主记下来，供以后排查（实测是 `postgres`）。

`pick_board_reader` 经 PUBLIC 仍有 `public` schema 的 USAGE（PG 默认），所以工作台的任何表都不能建在 `public` 下；切换后第 8 节第 9 步会核对。

### 2.5 撤销

只用于 bootstrap 已提交、2.4 检查不通过、而且宿主还没有连过这个库的时候：

```bash
PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
  -X -f docs/pick-workbench/supabase/bootstrap-undo.sql
```

[supabase/bootstrap-undo.sql](supabase/bootstrap-undo.sql) 删掉两个 schema 和两个角色，也是一个事务。`DROP SCHEMA` 不带 CASCADE：宿主一旦建过表，它就在这一句失败并整体回滚，不会删掉任何数据。那时要推倒重来只能删除项目重建（方案第 9 节第 1 步的回滚）。

## 3. 连接（6.3）

**Railway gateway → session pooler（端口 5432），角色 `deerflow_app`：**
- `PICK_DATABASE_URL=postgresql://deerflow_app.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`，从控制台 Connect 面板复制 session pooler 一栏再换角色名。直连主机 `db.<ref>.supabase.co` 只有 IPv6，Railway 出站按 IPv4 处理，不用。
- 连接串里**不写任何 SSL 参数**：同一个串同时交给 asyncpg 和 libpq，`sslmode` 会让 asyncpg 报未知参数，`ssl` 会让 libpq 报错；入口遇到 `ssl` 开头的参数直接拒绝启动。TLS 由 `PGSSLMODE=require` 指定，两个驱动都读它。
- search_path 由 DeerFlow 为两个驱动分别设置（asyncpg 的 `server_settings`、psycopg 的 `options=-c search_path`），角色默认值 `deerflow` 兜底。演练时如果发现 Supavisor 拒绝 `options` 启动参数，改为 `postgres_schema: ""`，只靠角色默认值，表仍落在 `deerflow`。入口每次启动都重写运行时 yaml，所以这要改 `backend/app/gateway/pick_entrypoint.py` 的 `POSTGRES_DATABASE` 并重新部署，是一次代码改动，不能在卷上改文件。
- 备选：购买 IPv4 add-on 后直连。

**Vercel 资料页 → transaction pooler（端口 6543），角色 `pick_board_reader`（P3 才用）：**
- `PICK_MIRROR_READER_URL=postgresql://pick_board_reader.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:6543/postgres`，同样不带 sslmode；TLS 由代码指定 `rejectUnauthorized: true` 加 `PICK_MIRROR_CA_PEM`（从控制台下载的 CA）。
- 只配 Vercel Production，Preview 不配。

**核对（演练与切换后各做一次）：**

```sql
-- 以 postgres 身份：业务连接都已加密
SELECT a.usename, s.ssl, s.version, count(*)
  FROM pg_stat_activity a JOIN pg_stat_ssl s USING (pid)
 WHERE a.usename IN ('deerflow_app', 'pick_board_reader') GROUP BY 1, 2, 3;
```

`postgres` 看不到别的角色的 `pg_stat_ssl` 时，改用对应角色自己的连接执行 `SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()`。两个驱动的 search_path 按第 10 节第 3 步检查。证书校验（verify-full）是后续加固项。

## 4. 连接预算（6.4）

- Supavisor 的 Pool Size 按「用户 + 库」分别计。两个业务用户各 20，加 Supabase 内部服务约 10–15，最坏约 50–55 个服务端连接，在 `max_connections=60` 之内。
- `deerflow_app` 常态约 9–10 个连接（ORM 3、checkpointer 4、store 1、同步专用 1）；建账号、启动迁移时短时多 2–3 个；各项同时到顶的理论峰值约 25，超出 20 的客户端在 session 模式里排队（最多约一分钟，方案 13.1 接受）。
- **角色的 `CONNECTION LIMIT` 不能小于 Pool Size。** 否则并发一高，Supavisor 去开超出角色上限的服务端连接会直接报错，而不是排队。要改 Pool Size 时，两个角色一起改：`ALTER ROLE deerflow_app CONNECTION LIMIT <n>; ALTER ROLE pick_board_reader CONNECTION LIMIT <n>;`，并重算上面的总数。
- 查看峰值：

  ```sql
  SELECT usename, count(*) FROM pg_stat_activity GROUP BY 1 ORDER BY 2 DESC;
  ```

  演练时同时跑一次对话、一次同步、一次资料页渲染再看；超出预算时再议升级 compute 到 Small。

## 5. Railway 配置（6.5）

- 变量见第 9 节。`PICK_DB_BACKEND` 必须显式设置，没设或取值不对时入口直接退出；**先**设好 `PICK_DB_BACKEND=sqlite`，**再**部署含 P0-3 的镜像（方案第 9 节第 2 步）。
- `postgres` 模式下，入口生成的 `/data/pick-runtime.yaml` 只含字面的 `$PICK_DATABASE_URL`，由 AppConfig 加载时解析，卷上的文件不含密钥。database 段：`postgres_schema: deerflow`、`pool_size: 3`、`pool_recycle: 300`、`command_timeout: 30`、`checkpoint_channel_mode: full`；不写 `checkpointer:` 段，checkpointer 与 store 从 database 推导，第一次启动时建在 `deerflow` 下。
- 镜像构建带 `--extra postgres`（asyncpg、psycopg、psycopg-pool、langgraph-checkpoint-postgres）。
- 其他成员的账号经 `railway ssh` 用 `create_user` 建，写法见第 8 节第 8 步；入口与 gateway 共用补缺省值的函数，不依赖 cwd。详见 [azure-cloud-deployment.md](azure-cloud-deployment.md)。

## 6. checkpoint 与容量（6.6）

每周以 `postgres` 身份记录一次，写进第 12 节：

```sql
SELECT pg_size_pretty(pg_total_relation_size('deerflow.checkpoints')) AS checkpoints,
       pg_size_pretty(pg_total_relation_size('deerflow.checkpoint_blobs')) AS blobs,
       pg_size_pretty(pg_total_relation_size('deerflow.checkpoint_writes')) AS writes,
       pg_size_pretty(pg_database_size('postgres')) AS database;
```

- 告警阈值：整库超过 5 GB（Pro 含 8 GB 磁盘；P2 的同步容量闸门在 6 GiB）。
- 超过时：经线程的 DELETE 接口删掉不再需要的旧会话，和/或在控制台扩磁盘。
- 不要指望 `checkpoint_retention.py`：它没有生产触发点，也不剪长对话的主链；要接上它是另一项需要评审的任务。

## 7. PG 兼容的现场检查（6.7）

扩展侧的 NUL、孤立代理项、VARCHAR 长度、时间戳排序、批次缓存都已在 P0-2 按两种方言测过。只有宿主自己的 jsonb（消息、run events、checkpoint metadata）要在真实库上试：演练和切换后各发一条含 NUL、一条含孤立代理项（`\ud800` 转义）的消息，各走完一次选剧对话。任一失败，就在 pick 入口给 ASGI 应用包一层中间件，只对 JSON 请求体把 NUL 和孤立代理项替换为 U+FFFD，并补测试，然后再切换。

## 8. 正式切换（6.8，P0-6）

约 30 分钟的窗口。开始前 P0-1 到 P0-5 必须全部完成，并向用户再确认一次（方案 13.4）。

1. **公告。** 旧会话、已保存的选择、候选卡和回答核对都不会带到新库，所有人需要重新登录。线上有必须保留的对话时，先导出截图或文本。
2. **备份 SQLite。** `railway ssh` 进 gateway 容器（镜像里没有 sqlite3 命令行，用 Python；`/data/backup` 需要先建）：

   ```bash
   mkdir -p /data/backup && chmod 700 /data/backup
   python - <<'PY'
   import sqlite3
   src = sqlite3.connect("/data/data/deerflow.db")
   dst = sqlite3.connect("/data/backup/deerflow-sqlite-<日期>.db")
   src.backup(dst)
   print(dst.execute("PRAGMA integrity_check").fetchone())
   PY
   sha256sum /data/backup/deerflow-sqlite-<日期>.db
   ```

   `integrity_check` 要是 `('ok',)`；sha256 记进第 12 节。原文件和 blob 目录都不动。
3. **在本机先建管理员**（堵住空库时任何人都能调 `/api/v1/auth/initialize` 抢注管理员的窗口）：
   1. 确认正式项目的 `deerflow` 是空的：`SELECT count(*) FROM pg_tables WHERE schemaname = 'deerflow'` 为 0，不是就停下排查。
   2. 本机代码必须与要部署的镜像是同一个 commit：`git worktree add <scratch>/gw-cutover <镜像的 commit>`，在那里的 `backend/` 执行 `uv sync --locked --extra postgres`。本机代码更新时，库里的 alembic head 会领先于镜像，镜像启动就会失败。
   3. 在 `<scratch>/gw-cutover/backend` 下：

      ```bash
      export PICK_DB_BACKEND=postgres PGSSLMODE=require DEER_FLOW_HOME=<scratch>/gw-cutover-home
      export AZURE_OPENAI_DEPLOYMENT=unused AZURE_OPENAI_BASE_URL=http://127.0.0.1:9 AZURE_OPENAI_API_KEY=unused
      read -rs PICK_DATABASE_URL && export PICK_DATABASE_URL   # 粘贴 deerflow_app 的 session pooler 连接串
      .venv/bin/python -m app.gateway.pick_entrypoint
      ```

      不设 feed token，不会触发同步。`DEER_FLOW_HOME` 必须显式设：缺省的 `/data` 在 macOS 上建不了。三个 `AZURE_OPENAI_*` 只是占位：运行时 yaml 从 `config.pick.example.yaml` 抄来模型段的 `$AZURE_OPENAI_*`，缺一个，gateway 启动时就报 `Environment variable AZURE_OPENAI_DEPLOYMENT not found`。开发机上 `app_config.py` 的 `load_dotenv()` 会往上找到仓库根被 git 忽略的 `.env`，把这个问题盖住；新 worktree 上面没有 `.env`。建管理员不调模型，真实密钥不必上本机。启动会完成宿主建表、checkpointer 与 store 建表、ggwp 迁移 0001–0004。入口监听 `0.0.0.0:8001`，建好管理员之前同一网络里的人也能调 `/api/v1/auth/initialize`，所以只在可信网络上做，并尽快走完下一步。
   4. 本机起前端（`pnpm dev`，`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8001`），打开 `/setup` 建管理员。
   5. `GET /api/v1/auth/setup-status` 返回 `needs_setup=false` 后，停掉本机网关，删掉 worktree 和 `<scratch>/gw-cutover-home`（里面有 JWT 密钥文件）。
4. **设置 Railway 变量。** 在控制台 Variables 的 Raw Editor 里粘贴，不走命令行参数：`PICK_DB_BACKEND=postgres`、`PICK_DATABASE_URL`、`PGSSLMODE=require`。
5. **部署新镜像。** `/health/ready` 必须返回 200。
6. **立即用管理员登录。** 登录不上，或者 setup-status 显示需要初始化，立刻停服务排查。
7. **确认补跑同步。** 新库里没有成功的同步记录，gateway 启动 60 秒后会补跑一次。在资料页或 `/api/pick/sync` 确认同步成功、行数合理。
8. **重建其他账号。** `railway ssh` 进 gateway 容器，先确认变量在这个会话里可见（只看有没有，不打印值）：

   ```bash
   for v in PICK_DATABASE_URL PGSSLMODE PICK_DB_BACKEND; do printenv "$v" >/dev/null && echo "$v set" || echo "$v MISSING"; done
   cd /app/backend && DEER_FLOW_HOME=/data python -m app.gateway.auth.create_user --email <邮箱>
   ```

   有 MISSING 就停下排查，不在命令行里手打连接串。初始密码从 `/data/credentials/<邮箱>.txt` 取出，经密码管理器发给本人后删掉文件。自助注册保持关闭。`PICK_E2E_EMAIL` 那个 QA 账号不在生产库重建。
9. **验证：**
   - `deerflow` 下的表齐全，`public` 下没有工作台的表：`SELECT schemaname, count(*) FROM pg_tables WHERE schemaname IN ('deerflow', 'public') GROUP BY 1`。checkpoint 的三张表由 psycopg 建，`users`、`ggwp_*` 由 asyncpg 建，都在 `deerflow` 下就说明两个驱动的 search_path 都生效（Supavisor 转不转发启动参数已在演练时按第 10 节第 3 步确认）。
   - 第 3 节的 `pg_stat_ssl` 查询显示已加密。
   - 一次选剧对话端到端跑通（含工具调用），`deerflow.checkpoints` 有行。
   - 保存并导出一次选择。
   - 第 7 节的 NUL 与孤立代理项消息测试通过。
   - 按 [acceptance.md](acceptance.md) 重跑 10 题验收。
   - 第 4 节的查询看连接峰值。
   - 结果记进第 12 节，并更新 [progress.md](progress.md)。

## 9. 环境变量（只列名字，值不进仓库）

| 位置 | 变量 | 阶段 | 用途 |
|---|---|---|---|
| Railway gateway | `PICK_DB_BACKEND` | P0 | `sqlite` 或 `postgres`，必须显式设置 |
| Railway gateway | `PICK_DATABASE_URL` | P0 | session pooler 连接串，角色 `deerflow_app`，不带 SSL 参数；`sqlite` 模式下不读 |
| Railway gateway | `PGSSLMODE` | P0 | `require` |
| Railway gateway | `PICK_REALSHORT_FEED_URL`、`PICK_REALSHORT_FEED_TOKEN` | 已有 | v1 feed |
| Railway gateway | `PICK_REALSHORT_EXPORT_TOKEN` | P2 | v2 导出，与 RealShort 的 `PICK_EXPORT_TOKEN` 是同一个值 |
| Railway gateway | `PICK_MIRROR_ENABLED`、`PICK_DB_SIZE_CAP_BYTES` | P2 | 镜像开关与容量阈值 |
| RealShort Vercel Production | `PICK_EXPORT_TOKEN` | P1 | v2 的 Bearer；未配置时 v2 路由返回 404 |
| ggwork Vercel Production | `PICK_MIRROR_READER_URL` | P3 | transaction pooler 连接串，角色 `pick_board_reader` |
| ggwork Vercel Production | `PICK_MIRROR_CA_PEM` | P3 | Supabase 的 CA 证书（公开信息，作为配置放在环境变量里） |
| 本机，只在切换第 3 步 | `PICK_DB_BACKEND`、`PICK_DATABASE_URL`、`PGSSLMODE`、`DEER_FLOW_HOME`、`AZURE_OPENAI_DEPLOYMENT`、`AZURE_OPENAI_BASE_URL`、`AZURE_OPENAI_API_KEY` | P0-5、P0-6 | 本机建表与建管理员，用完即关掉 shell；三个 `AZURE_OPENAI_*` 填占位值，不用真实密钥 |

- 所有 token 与数据库密码都用 `openssl rand -hex 32` 生成，存进密码管理器。写给 `vercel env add` 的临时文件用 `printf '%s'`（不带末尾换行），再重定向输入，不用管道。
- 轮换 v2 token 的顺序：先改 RealShort 的值并重新部署，再改 Railway 的值，最后手动同步一次确认。
- Supabase 的 `postgres` 角色只在 bootstrap、`\password` 和本文的检查查询里用，连接串不放在任何部署平台上。

## 10. 演练（P0-5）

只在独立的临时项目 `ggwork-rehearsal` 上做（按小时计费，演练完删除），不在正式项目里演练；临时项目开不出来时演练顺延，不降级。有一项不通过，就不进入正式切换。

1. 在临时项目上按第 1 节设置，按第 2 节执行 bootstrap：以真实 `postgres` 完整执行，退出状态 0、输出无 WARNING、2.4 全部符合。
2. 先对临时项目走一遍第 8 节第 3 步：在新 worktree 里照抄那一步的命令（不靠仓库的 `.env`），建好管理员，`needs_setup=false`，再删掉 worktree 和 home。然后新镜像连 session pooler：启动不报 alembic 版本错误，能用这个管理员登录；再执行第 8 节第 9 步的全部检查。
3. 另外核对：
   - 宿主 bootstrap（create_all 加 alembic stamp）与 ggwp 迁移；`/health/ready` 的 postgres checkpointer 探针；
   - 连接峰值（第 4 节）；SSL（第 3 节）；
   - 两个驱动的 search_path 与 Supavisor 是否转发启动参数。在 gateway 容器里（`railway ssh`）用一个不同于角色默认值的探针值连一次：

     ```bash
     cd /app/backend && python - <<'PY'
     import asyncio, os
     import asyncpg, psycopg
     url = os.environ["PICK_DATABASE_URL"]
     async def probe():
         conn = await asyncpg.connect(url, server_settings={"search_path": "deerflow, public"})
         print("asyncpg", await conn.fetchval("SHOW search_path"))
         await conn.close()
     asyncio.run(probe())
     with psycopg.connect(url, options="-c search_path=deerflow,public") as conn:
         print("psycopg", conn.execute("SHOW search_path").fetchone()[0])
     PY
     ```

     打印 `deerflow, public` 说明启动参数被转发；打印 `deerflow` 说明被丢掉、靠角色默认值兜底，两种都能用，记下是哪种。连接直接报错说明 Supavisor 拒绝该参数，按第 3 节改成 `postgres_schema: ""`（代码改动）后重新演练。最后以 gateway 实际建表的位置为准（第 8 节第 9 步第 1 条）；
   - NUL 和孤立代理项（第 7 节）；一次手动同步；
   - 经 `railway ssh` 按第 8 节第 8 步跑一次 `create_user`：ssh 会话里看得到 `PICK_DATABASE_URL`、`PGSSLMODE`，账号建在演练库里，credentials 落在 `/data/credentials`；
   - Supavisor session 模式下客户端断开后会话状态是否被重置：一条连接取 advisory lock 并 `SET lock_timeout`，然后直接断开；新连接查 `pg_locks` 里没有这把锁、`SHOW lock_timeout` 是默认值。不满足时，同步的 `finally` 解锁是唯一保障，在本文写明 `lock_stuck` 的处理。
4. 测一次工具调用延迟，与 SQLite 对比，确认批次 LRU 缓存生效。
5. 删除临时项目。

## 11. 回滚（6.9、6.10）

| 时点 | 做法 |
|---|---|
| bootstrap 已提交、检查不通过、宿主还没连过 | 2.5 的撤销脚本；或删除项目重建 |
| 部署含 P0-3 的镜像之后、切换之前 | `PICK_DB_BACKEND=sqlite` 时行为不变；有问题回到上一个镜像 |
| 切换后 48 小时内 | 把 `PICK_DB_BACKEND` 改成 `sqlite` 并重新部署。新镜像在 sqlite 模式下行为与旧版相同，会重新打开原来的 `/data/data/deerflow.db`。这段时间写进 PG 的数据不会合并回来（本来就是重新开始，可以接受）。`PICK_DATABASE_URL`、`PGSSLMODE` 可以留着，sqlite 模式不读 |
| 之后 | 只保留 SQLite 备份 |

- 原文件 `/data/data/deerflow.db` 和备份 `/data/backup/deerflow-sqlite-<日期>.db` 都至少保留 30 天，备份的 sha256 记在第 12 节。
- 备份只用于回滚，不能在应用里浏览。

## 12. 留档

操作员执行后填写；只写结果，不写任何凭据。

**控制台设置（第 1 节）：**

| 项 | 结果 | 日期 |
|---|---|---|
| Enforce SSL | 待填 | |
| Data API 关闭 / Exposed schemas 为空 | 待填 | |
| Pool Size = 20 | 待填 | |
| PITR | 待填（开或不开） | |
| Spend cap | 待填（开或关） | |

**bootstrap（第 2 节）：** 待执行。记：退出状态、`grep -c WARNING` 的输出、2.4 各查询的结果、两个角色的密码已设置并已登录验证。

**演练（第 10 节）：** 待执行。

**正式切换（第 8 节）：** 待执行。记：SQLite 备份文件名与 sha256、`integrity_check` 结果、切换时间、第 9 步各项结果、连接峰值。

**每周容量（第 6 节）：** 切换后开始记录。
