# 工作台数据库迁 Supabase：运行手册

对应方案 [2026-09-23-supabase-pick-board-plan.md](../plans/2026-09-23-supabase-pick-board-plan.md) 第 6 节（6.1–6.10）与第 9 节。本文只写怎么做、看什么结果，原因与取舍看方案；两者冲突时以方案第 13、14 节为准。

**状态（2026-09-23）：** 已切换。演练（P0-5）与正式切换（P0-6）都已完成，生产 gateway 从 2026-09-23 13:04 UTC 起跑在 Supabase 正式项目上，结果见第 12 节。48 小时内可以按第 11 节回滚到 SQLite。

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
- Database Settings → Connection pooling：Pool Size 设 **20**，与三个角色（`deerflow_app`、`pick_board_reader`、`pick_observer`）的 `CONNECTION LIMIT` 相等（第 4 节的表）。
- PITR（付费附加）要不要开、Spend cap 开还是关：由操作员决定并记下。Pro 默认每日备份、保留 7 天；Spend cap 开启时，超出配额（例如磁盘超过 8 GB）会被限制。

**禁止：** 以后任何时候都不要重新打开 Data API，也不要把 `deerflow`、`pick_mirror`、`pick_obs` 或 `pickm_v*` 加进 Exposed schemas。里面的推荐人、飞书记录 id、指标以及用户会话都会被公开（方案 10.1）。

## 2. bootstrap（6.2、3.1）

脚本：[supabase/bootstrap.sql](supabase/bootstrap.sql)。只执行一次，以 `postgres` 身份经 session pooler 连 `postgres` 库。它做的事：
- 建三个角色，都是 `LOGIN NOINHERIT CONNECTION LIMIT 20`：`deerflow_app`（Railway gateway 用，拥有 `deerflow`、`pick_mirror`、`pick_obs` 和以后的 `pickm_v*`，对库有 CREATE）；`pick_board_reader`（Vercel 资料页用，只对 `pick_mirror` 与 `pick_obs` 有 USAGE，默认只读事务、语句超时 8 秒）；`pick_observer`（趋势雷达的两个 cron 用，只对 `deerflow` 与 `pick_mirror` 有 USAGE，碰不到 `pick_obs`；它的表级授权由迁移 0007 给，见 [observe-runbook/observer-role.md](observe-runbook/observer-role.md)）。
- 给 `postgres` 补上对 `deerflow_app` 的 SET 权限（`INHERIT FALSE`），建三个 schema，`SET ROLE deerflow_app` 以属主身份做 REVOKE 和两个角色的 USAGE，再 `RESET ROLE` 回到 postgres 做 `ALTER ROLE`（审计 host-1：少了 SET ROLE 时要么报 permission denied，要么只报 WARNING、授权悄悄缺失）。
- 整个脚本是一个事务：任何一句失败都整体回滚，项目保持原样。连的不是 `postgres` 库、或者 `deerflow_app` 没拿到库的 CREATE 权限时，脚本里的检查会让它直接失败，而不是只报 WARNING 继续。

**生产项目不再执行本节。** 它 2026-09-23 执行的是上一版脚本（两个角色、两个 schema，原样留在 `customizations/pick-workbench/tests/fixtures/supabase/bootstrap-p0.sql`），观测角色由 [supabase/bootstrap-observer.sql](supabase/bootstrap-observer.sql) 补建（趋势雷达上线步骤 S2，步骤与判定见 observer-role.md），`pick_obs` 由迁移 0007 建。两条路走完迁移后，三个角色与所有授权完全相同。本节以下写的是新项目的完整 bootstrap。

`customizations/pick-workbench/tests/test_bootstrap_sql.py` 在本机 PG 17 上用 `NOSUPERUSER CREATEROLE`、持有库的替身角色执行同一份脚本（`PICK_TEST_PG_URL` 已设时运行），覆盖：无 WARNING、输出与 2.1 逐行一致、授权与角色设置、删掉 SET 授权或 SET ROLE 时在哪一句失败、替身另有 `pg_read_all_data`/`pg_write_all_data` 时仍然干净、库属主不对时硬失败，以及上一段说的两条路结果相同。真实 Supabase 上的完整执行由演练确认（第 10 节）。

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
CREATE ROLE
GRANT ROLE
GRANT
GRANT
GRANT
DO
CREATE SCHEMA
CREATE SCHEMA
CREATE SCHEMA
SET
REVOKE
REVOKE
GRANT
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

### 2.3 设置三个角色的密码

先按 2.4 检查通过，再另开一个交互式 psql 会话设置密码。`\password` 在本地算好 SCRAM 摘要再发给服务端，密码明文不会进服务端日志：

```bash
PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X
```

```
\password deerflow_app
\password pick_board_reader
\password pick_observer
```

三个密码各自用 `openssl rand -hex 32` 生成，互不相同，存进密码管理器。然后用新角色各登录一次确认（`pick_observer` 的登录与留档见 observer-role.md 的 S2）：

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
  FROM pg_roles WHERE rolname IN ('deerflow_app', 'pick_board_reader', 'pick_observer') ORDER BY 1;
\dn+ (deerflow|pick_mirror|pick_obs)
SELECT has_schema_privilege('pick_board_reader', 'pick_mirror', 'USAGE') AS reader_mirror,
       has_schema_privilege('pick_board_reader', 'pick_obs', 'USAGE') AS reader_obs,
       has_schema_privilege('pick_board_reader', 'deerflow', 'USAGE') AS reader_deerflow,
       has_schema_privilege('pick_observer', 'deerflow', 'USAGE') AS observer_deerflow,
       has_schema_privilege('pick_observer', 'pick_mirror', 'USAGE') AS observer_mirror,
       has_schema_privilege('pick_observer', 'pick_obs', 'USAGE') AS observer_obs,
       has_schema_privilege('anon', 'pick_mirror', 'USAGE') AS anon_mirror,
       has_schema_privilege('authenticated', 'deerflow', 'USAGE') AS authenticated_deerflow,
       has_database_privilege('deerflow_app', 'postgres', 'CREATE') AS app_create,
       has_database_privilege('pick_board_reader', 'postgres', 'CREATE') AS reader_create,
       has_database_privilege('pick_observer', 'postgres', 'CREATE') AS observer_create;
SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'postgres';
```

应当看到：
- 三个角色 `rolcanlogin=t`、`rolinherit=f`、`rolconnlimit=20`。
- `deerflow_app` 的 rolconfig 是 `{search_path=deerflow,TimeZone=UTC,idle_in_transaction_session_timeout=5min}`。
- `pick_board_reader` 的 rolconfig 是 `{search_path=pick_mirror,default_transaction_read_only=on,statement_timeout=8s,idle_in_transaction_session_timeout=15s,TimeZone=UTC}`。
- `pick_observer` 的 rolconfig 是 `{search_path=deerflow,TimeZone=UTC,idle_in_transaction_session_timeout=1min,statement_timeout=2min}`。
- `\dn+`：三个 schema 的属主都是 `deerflow_app`；`deerflow` 的权限是 `deerflow_app=UC/deerflow_app` 与 `pick_observer=U/deerflow_app`；`pick_mirror` 是 `deerflow_app=UC/deerflow_app`、`pick_board_reader=U/deerflow_app` 与 `pick_observer=U/deerflow_app`；`pick_obs` 是 `deerflow_app=UC/deerflow_app` 与 `pick_board_reader=U/deerflow_app`。没有 `=U/`（PUBLIC）、`anon=`、`authenticated=` 开头的项。
- `reader_mirror`、`reader_obs`、`observer_deerflow`、`observer_mirror`、`app_create` 为 t，其余为 f。
- 库属主记下来，供以后排查（实测是 `postgres`）。

`pick_board_reader` 与 `pick_observer` 经 PUBLIC 仍有 `public` schema 的 USAGE（PG 默认），所以工作台的任何表都不能建在 `public` 下；切换后第 8 节第 9 步会核对。

### 2.5 撤销

只用于 bootstrap 已提交、2.4 检查不通过、而且宿主还没有连过这个库的时候：

```bash
PGSSLMODE=require psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
  -X -f docs/pick-workbench/supabase/bootstrap-undo.sql
```

[supabase/bootstrap-undo.sql](supabase/bootstrap-undo.sql) 删掉三个 schema 和三个角色，也是一个事务。`DROP SCHEMA` 不带 CASCADE：宿主一旦建过表，它就在这一句失败并整体回滚，不会删掉任何数据。那时要推倒重来只能删除项目重建（方案第 9 节第 1 步的回滚）。它只配本节的新版 bootstrap；生产项目上撤销观测角色用 [supabase/bootstrap-observer-undo.sql](supabase/bootstrap-observer-undo.sql)（observer-role.md「撤销」）。

## 3. 连接（6.3）

**Railway gateway → session pooler（端口 5432），角色 `deerflow_app`：**
- `PICK_DATABASE_URL=postgresql://deerflow_app.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`，从控制台 Connect 面板复制 session pooler 一栏再换角色名。直连主机 `db.<ref>.supabase.co` 只有 IPv6，Railway 出站按 IPv4 处理，不用。
- 连接串里**不写任何 SSL 参数**：同一个串同时交给 asyncpg 和 libpq，`sslmode` 会让 asyncpg 报未知参数，`ssl` 会让 libpq 报错；入口遇到 `ssl` 开头的参数直接拒绝启动。TLS 由 `PGSSLMODE=require` 指定，两个驱动都读它。
- search_path 由 DeerFlow 为两个驱动分别设置（asyncpg 的 `server_settings`、psycopg 的 `options=-c search_path`），角色默认值 `deerflow` 兜底。演练时如果发现 Supavisor 拒绝 `options` 启动参数，改为 `postgres_schema: ""`，只靠角色默认值，表仍落在 `deerflow`。入口每次启动都重写运行时 yaml，所以这要改 `backend/app/gateway/pick_entrypoint.py` 的 `POSTGRES_DATABASE` 并重新部署，是一次代码改动，不能在卷上改文件。
- 备选：购买 IPv4 add-on 后直连。

**Vercel 资料页 → transaction pooler（端口 6543），角色 `pick_board_reader`（P3 才用）：**
- `PICK_MIRROR_READER_URL=postgresql://pick_board_reader.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:6543/postgres`，同样不带 sslmode；TLS 由代码指定 `rejectUnauthorized: true` 加 `PICK_MIRROR_CA_PEM`（从控制台下载的 CA）。
- 只配 Vercel Production，Preview 不配。

**Railway 两个观测 cron → session pooler（端口 5432），角色 `pick_observer`（趋势雷达）：**
- `pick-obs-trends`、`pick-obs-gsc` 的 `PICK_DATABASE_URL=postgresql://pick_observer.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`，同样不带任何 SSL 参数，`PGSSLMODE=require`。每个服务只开 1 条连接，每一步用完就关（趋势雷达计划 D1）。
- 这个连接串不配给 gateway 和 Vercel。gateway 上的 `observe.admin regrant` 用的是 gateway 自己的 `deerflow_app` 连接串。

**核对（演练与切换后各做一次）：**

```sql
-- 以 postgres 身份：业务连接都已加密
SELECT a.usename, s.ssl, s.version, count(*)
  FROM pg_stat_activity a JOIN pg_stat_ssl s USING (pid)
 WHERE a.usename IN ('deerflow_app', 'pick_board_reader', 'pick_observer') GROUP BY 1, 2, 3;
```

`postgres` 看不到别的角色的 `pg_stat_ssl` 时，改用对应角色自己的连接执行 `SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()`。两个驱动的 search_path 按第 10 节第 3 步检查。证书校验（verify-full）是后续加固项。

## 4. 连接预算（6.4）

| 角色 | 谁用 | 模式 | 角色上限 | 实际 |
|---|---|---|---|---|
| `deerflow_app` | Railway gateway（ORM、checkpointer、store、同步专用连接、`railway ssh` 跑的命令） | session | 20 | 常态约 9–10，理论峰值约 25，超出 20 的排队 |
| `pick_board_reader` | Vercel 资料页 | transaction | 20 | 并发高时用满 Pool Size 20，客户端排队（P3-6 压测） |
| `pick_observer` | 两个观测 cron；本机导入旧页快照与部署守卫 | session | 20 | 最多 2（每个 cron 1 条）；导入或守卫恰好同时在跑时短时 3–4 |
| Supabase 内部服务 | | | | 约 10–15 |

- Supavisor 的 Pool Size 按「用户 + 库」分别计。前两个业务用户各 20，加 Supabase 内部服务约 10–15，最坏约 50–55 个服务端连接；`pick_observer` 的上限虽然也是 20，实际最多 2 条，合计最坏约 52–57，在 `max_connections=60` 之内，余量很小（趋势雷达设计 3.4）。
- `deerflow_app` 常态约 9–10 个连接（ORM 3、checkpointer 4、store 1、同步专用 1）；建账号、启动迁移时短时多 2–3 个；各项同时到顶的理论峰值约 25，超出 20 的客户端在 session 模式里排队（最多约一分钟，方案 13.1 接受）。
- **角色的 `CONNECTION LIMIT` 不能小于 Pool Size。** 否则并发一高，Supavisor 去开超出角色上限的服务端连接会直接报错，而不是排队。要改 Pool Size 时，三个角色一起改：`ALTER ROLE deerflow_app CONNECTION LIMIT <n>; ALTER ROLE pick_board_reader CONNECTION LIMIT <n>; ALTER ROLE pick_observer CONNECTION LIMIT <n>;`，并重算上面的总数。
- `pick_observer` 的峰值在影子运行期间实测一次（趋势雷达 G5 的核对项，方法见 observer-role.md「连接账」），结果记进第 12 节。
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

**网盘片段核查（RealShort #67 上线前每周一次，结果写进第 12 节）。** v1 feed 的备注在 #67 合并前不经清洗，同步时原样入库（`pan_url`、`pan_pw` 这些字段本身从不同步），之后被复制到：同步留下的旧批次（最近 3 个，外加 30 天内有候选引用的）、候选快照、候选冻结的数据时点（`data_as_of_json`，含批次的范围说明）、保存选择时的快照、回答核对、宿主 checkpoint 里的工具输出与模型回答、宿主外置到线程目录下的大段工具输出（`.tool-results`），以及 `/data/pick` 下的原始 feed 文件。核查另外也看手工导入的知识文档的文件名、来源和正文，同步记录的详情（`details_json`），以及候选的排除集合（`excluded_json`，全是 identity）；迁移 0007 之后还看观察雷达存下的剧名、发现段的词、GSC 的查询词和它的各个 JSON 列，以及旧页快照的跳转链（`hops_json`）。脚本：[supabase/pan-check.sql](supabase/pan-check.sql)（只读）和 [supabase/pan-redact.sql](supabase/pan-redact.sql)。

模式沿用原来的宽模式，另补了 RealShort 清洗正则里的 `yun.baidu`、`115cdn`、`123684/123865/123912.com`、移动云盘的两个域名、#67 第二十二、二十三轮补的微云、坚果云、PikPak、联通云盘、城通、小飞机网盘、蓝奏云短域名（`lanz` 加一个字母，蓝奏云优享 `ilanzou` 已被 `lanzou` 覆盖）、文叔叔、奶牛快传、360 安全云盘，第二十四轮按审计清单补的各家分享短域名与别名（UC、115、123 云盘、城通镜像、小飞机、文叔叔、奶牛、360 旧域名）与曲奇云盘、MuseTransfer、钛盘、AirPortal、轻松传、联想 Filez、新浪微盘、威盘及几家已停运服务，以及后面（可隔空白）跟着 `=`、`:`、`：` 的「密码」「密碼」（不带分隔符的「密码」会误中「财富密码」这类剧名）。它还认任意层 JSON 转义的写法（反斜杠一个或多个：工具输出本身是 JSON，宿主的运行事件又把整条消息转一次，换行就成了两个反斜杠加 n）：中文关键字和全角冒号写成的 `\uXXXX`（ensure_ascii；手工导入的原始文件原样存盘，可能就是这种写法）；「密码」与分隔符之间的空白，可以是原样的空白（Unicode White_Space 里的 19 个非 ASCII 字符，U+0085、U+00A0、U+1680、U+2000 到 U+200A、U+2028、U+2029、U+202F、U+205F、U+3000，逐个写成一条分支，原样写在 `PAN=` 里、看不见；不靠 `[[:space:]]`，它认不认这些字符随 locale 变，checkpoint 的二进制列按字节比时也碰不到它们），也可以是 `\n`、`\t` 这类转义或任意 `\uXXXX`。两个脚本和第 1 步的 `PAN=` 用同一个模式。`customizations/pick-workbench/tests/test_pan_runbook_sql.py` 钉住三处逐字相同；它在 PG 17 上用真实的同步、选剧和保存代码造出污染数据，以非超级用户的表属主替身按下面的顺序跑这两个脚本，并在临时目录里跑下面的 shell 命令。

1. **核查：库和磁盘都查，每次都做。** 先在 gateway 容器里查磁盘，再带着它找到的线程在本机查库。

   `railway ssh -i ~/.ssh/railway_ggwork` 进 gateway 容器。镜像里没有 `docs/`，`PAN=` 这一行只能粘贴：从仓库里的文件原样复制，不要从渲染后的网页复制，里面 19 个看不见的空白字符可能被换成普通空格、换行或者别的空白。设好后 `printf %s "$PAN" | sha256sum` 应输出 `1f48b15bf43983854d225b85640e3ed560fb4a90462ef85f8bb4c08cbacd7d21`（后面跟着 `  -`），不一样就重新复制。`PAN` 和 `pan_scan` 后面几步还要用，在同一个 ssh 会话里做；新开会话先把下面整段重新贴一次。

   ```bash
   PAN='pan\.baidu|yun\.baidu|pan\.quark|aliyundrive|alipan|115\.com|115cdn|123pan|123684\.com|123865\.com|123912\.com|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|caiyun\.139|yun\.139|weiyun\.com|jianguoyun\.com|mypikpak|pan\.wo\.cn|ctfile|feijipan|lanz[a-z]\.com|wenshushu|cowtransfer|yunpan\.360|fast\.uc\.cn|anxia\.com|123952\.com|400gb\.com|pipipan\.com|545c\.com|90pan\.com|089u\.com|474b\.com|t00y\.com|306t\.com|47ks\.com|4765\.com|77tj\.com|feijix\.com|fjpan\.com|wss\.cc|c-t\.work|yunpan\.cn|yunpan\.com|pan\.360\.cn|quqi\.com|musetransfer\.com|tmp\.link|airportal\.cn|airportal\.link|easychuan\.cn|filez\.com|box\.lenovo\.com|vdisk\.weibo\.com|v\.disk\.weibo\.com|vdisk\.cn|kuaipan\.cn|dbank\.com|dbank\.vmall\.com|pan\.sohu\.net|fhrl\.wostore\.cn|提取码|提取碼|访问码|訪問碼|pwd=|(密码|密碼)([[:space:]]|| | | | | | | | | | | | | | | | | |　|\\+[bfnrtv]|\\+u[0-9a-fA-F]{4})*(=|:|：|\\+uff1a)|\\+u63d0\\+u53d6\\+u78(01|bc)|\\+u8bbf\\+u95ee\\+u7801|\\+u8a2a\\+u554f\\+u78bc|\\+u5bc6\\+u78(01|bc)([[:space:]]|| | | | | | | | | | | | | | | | | |　|\\+[bfnrtv]|\\+u[0-9a-fA-F]{4})*(=|:|：|\\+uff1a)'
   printf %s "$PAN" | sha256sum
   pan_scan() {
     roots='/data/pick /data/users'; if [ -e /data/threads ] || [ -L /data/threads ]; then roots="$roots /data/threads"; fi
     err=$(mktemp) || return 2
     find $roots -type l >"$err" 2>&1
     hits=$(find $roots -type f \( -path '/data/pick/*' -o -path '*/threads/*/user-data/outputs/.tool-results/*' \) -exec grep -lziE -- "$PAN" {} + 2>>"$err")
     if [ -s "$err" ]; then cat "$err" >&2; rm -f "$err"; echo '扫描不完整：上面是符号链接或者读不了的路径，这次的名单不能用' >&2; return 2; fi
     rm -f "$err"
     echo '[原始 feed 文件]'; printf '%s\n' "$hits" | grep '^/data/pick/'
     echo '[线程]'; printf '%s\n' "$hits" | sed -nE 's#^.*/threads/([^/]+)/user-data/outputs/\.tool-results/.*$#\1#p' | sort -u | paste -sd, -
   }
   pan_scan; echo "pan_scan 退出码 $?"
   ```

   `pan_scan` 查两处磁盘，`grep -z` 把整个文件当成一行（手工导入的 CSV 原样存盘，带引号的字段可以跨行，逐行找会漏掉「密码」换行「：ab12」）：
   - `[原始 feed 文件]`：`/data/pick` 下含命中的原始 feed 文件。
   - `[线程]`：外置的工具输出里含命中的线程，逗号隔开，没有命中时是空行。工具输出超过阈值（默认 12000 字；pick 的配置只把五个选剧工具列进 `tool_output.exempt_tools`，web_search、web_fetch 和 MCP 工具的输出照旧外置）时，宿主把全文写进 `/data/users/<属主>/threads/<线程>/user-data/outputs/.tool-results/`，checkpoint 里只留预览和文件引用，命中可能只在文件里。分支对话会把整个 `user-data` 复制到新线程，新线程单独列出；旧布局 `/data/threads/<线程>/` 下的也算。上传目录不查（见下面的范围）。
   - 最后一行必须是 `pan_scan 退出码 0`，名单才完整。它不跟随符号链接，所以先列出 `/data/pick`、`/data/users`、`/data/threads` 下的所有符号链接（这三个目录本身是链接也算，`/data/threads` 是断开或者绕圈的链接也算，不会被当成不存在跳过）；有符号链接、读不了的文件或目录、遍历出错时，它把这些路径打出来、退出码 2，不给名单。这时空名单也不能当成干净：符号链接停下来另议，读不了的查清原因再扫。

   再在本机以表的属主 `deerflow_app` 查库，连法与 2.3 相同，密码在提示时粘贴。`disk_threads` 填 `[线程]` 下面那一行，是空行就填 `''`（只有 `pan_scan` 退出码 0 时才能这样填）；不填，脚本直接报错：

   ```bash
   PGSSLMODE=require psql "postgresql://deerflow_app.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
     -X -v disk_threads='<[线程] 下面那一行>' -f docs/pick-workbench/supabase/pan-check.sql
   ```

   输出两张表，只有条数、线程和属主，没有命中的文本：工作台 47 个位置各有几行命中（前 39 个清除脚本会处理，最后 8 个还包括反馈版本清单、原始记录、冻结结果证据和身份映射依据，不能原地改写哈希或历史证明；库还在迁移 0005 之前时，`data_as_of_json`、`details_json`、`excluded_json` 三列还不存在，还在迁移 0007 之前时，观察雷达的表和 `obs_as_of_json` 还不存在，这些位置照样列出、记 0；迁移 0008 前的反馈表也记 0，脚本不必换版本）；有命中的线程、属主邮箱、命中在哪（宿主的表名，`.tool-results` 表示在线程目录的文件里）。

   磁盘每次都要查：导入先写原始文件、后写库，导入中途失败，或者上次处置在删文件前中断，库里就是 0 而磁盘上仍有原文；外置的工具输出在库里本来就只有预览。47 个 0、线程表为空、`pan_scan` 退出码 0 且两段都是空的，才算没有命中，到此结束，结果记进第 12 节。

   反馈新增的四个位置只检测、不原地改写：命中时先停止反馈同步并按数据属主核验，再制定替代版本或历史证据隔离方案；不能把通用清洗脚本执行成功当成这些命中已经清除。
2. **逐条人看。** 模式比 #67 的清洗正则宽，误报是预期的。库里的命中：在交互式 psql 里先设好 `disk_threads` 再 `\i` 核查脚本，`:pan` 就设好了，再按位置看命中的片段，例如剧目（其他位置换表名和列名）：

   ```sql
   \set disk_threads '<[线程] 下面那一行>'
   \i docs/pick-workbench/supabase/pan-check.sql
   SELECT batch_id, identity, (regexp_match(payload_json::jsonb::text, '(.{0,40}(?:' || :'pan' || ').{0,40})', 'i'))[1]
     FROM deerflow.ggwp_drama_versions WHERE payload_json::jsonb::text ~* :'pan';
   ```

   磁盘上的文件在容器里看：`grep -oziE ".{0,40}($PAN).{0,40}" <文件> | tr '\0' '\n'`；某个线程外置的文件用 `find /data -type f -path '*/threads/<线程>/user-data/outputs/.tool-results/*'` 找。看到的内容不贴进第 12 节，也不贴进任何对话。线程在库里的原文没法看，它的命中通常就是同一 `thread_id` 的候选快照里那条备注。全是误报时到此为止，第 12 节记下误报的位置（含文件路径）和原因。有真命中，不管在库里还是只在磁盘上，都从第 3 步做起。库里 47 个位置都是 0 时可以跳过第 4、5 步：`.tool-results` 的线程做第 6 步，`/data/pick` 的文件做第 7 步，再做第 8、9 步。
3. **源头改掉，然后暂停同步。** 任何真命中都先做这一步，只在磁盘上的也一样：导入先写原始文件、后发布，发布失败会留下没入库的原文；源头不改，下一次同步又会把原文写回磁盘和库。
   - 在 RealShort 改掉那条备注，再手动同步一次。成功后在第 2 步那个 psql 里确认，最近一次成功同步用的批次里没有命中，下面这句应为 0，不是 0 就是源头还没改干净：

     ```sql
     SELECT count(*) FROM deerflow.ggwp_drama_versions
      WHERE batch_id = (SELECT catalog_batch_id FROM deerflow.ggwp_sync_runs WHERE status = 'success' ORDER BY started_at DESC LIMIT 1)
        AND payload_json::jsonb::text ~* :'pan';
     ```

     内容变了会发布新批次并成为当前批次；旧批次和它的行不会因此消失（见上），所以仍要做后面几步。
   - 然后暂停同步，到第 9 步再恢复。定时同步（北京时间 11:40、23:40；进程启动时离上次成功超过 12 小时，还会在一分钟后补拉一次）和管理页的手动同步，只在 `PICK_REALSHORT_FEED_URL` 和 `PICK_REALSHORT_FEED_TOKEN` 都有值时才开，没有别的开关。在 Railway 的 gateway 变量里，先把 `PICK_REALSHORT_FEED_URL` 的值存进密码管理器，再删掉这个变量并部署。新进程不排定时，手动同步返回 503「尚未配置 RealShort 数据接口」。
   - 确认没有在途的同步：Railway 上新部署已经在跑、旧部署已经停掉（停掉时在途的同步最多再等 20 秒，之后中止并记为失败），再看下面这句，最近几行里没有 `running`：

     ```sql
     SELECT id, trigger, status, started_at, finished_at FROM deerflow.ggwp_sync_runs ORDER BY started_at DESC LIMIT 3;
     ```

     旧部署已经停掉却仍有 `running`，是进程被直接杀掉时留下的：在 Railway 控制台再重启一次 gateway，新进程启动时会把它记为失败，再看一次。

4. **清除工作台里的副本。** 先通知大家暂停使用工作台，或者挑没人用的时候做，并紧接着做第 5 步：清除提交后、重启前，已经读到旧数据的请求会把原文写回去，换一批写进新的候选快照，保存选择写进选择快照。

   ```bash
   PGSSLMODE=require psql "postgresql://deerflow_app.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
     -X -f docs/pick-workbench/supabase/pan-redact.sql
   ```

   - 一个事务。30 个 JSON 列里，含命中的 JSON 字符串整串换成 `[网盘信息已移除]`，其余字节不动，JSON 仍然有效；知识文档的文件名（`title`，只用于显示和检索），以及观察雷达的剧名、规范化剧名、别名的新旧剧名、发现段的相关查询与规范化词，命中时整个换掉；GSC 的查询词是 `ggwp_gsc_query_daily` 主键的一部分，命中的行整行删掉（D18）。当前批次、旧批次、候选卡、换一批和已存选择照常能读能用。
   - 输出逐行是 `BEGIN`、`CREATE FUNCTION`、38 行 `UPDATE n`、1 行 `DELETE n`（依次是 30 个 JSON 列、知识文件名、观察雷达的 7 个剧名与词、GSC 的查询词，顺序与核查结果的前 39 行相同；库还在迁移 0007 之前时没有观察雷达的 27 行 UPDATE 和那行 DELETE，只有 11 行；还在迁移 0005 之前时又少 `data_as_of_json`、`details_json` 两行，只有 9 行）、`DROP FUNCTION`、`COMMIT`，各行的 n 记进第 12 节。可以重复执行，第二次全是 `UPDATE 0` 和 `DELETE 0`。
   - 用的是与核查相同的模式，同一次会把误报一起换掉。
   - 不改这几处，它们有命中时不算清除完成，停下来另议：主键和 identity 列；JSON 里 `identity`、`source_id`、`item_id`、`citation_id`、`request_id`，以及观察雷达的 `old_identity`、`new_identity`、`root_identity`、`matched_identity` 的值（改了剧目、快照、选择、回执、别名与提示就对不上）；知识来源 `source_ref`（`document_id` 是它的 sha256，属于文档身份）；知识正文（规则全文）；候选的排除集合 `excluded_json`（整列是 identity，改了换一批的回放就对不上）；旧页快照的跳转链 `ggwp_obs_legacy.hops_json`（从 RealShort 某个提交解析出的不可改快照）；宿主的表。
5. **立刻重启 gateway。** 在 Railway 控制台重启 gateway 服务，之后再恢复使用。进程内的批次缓存（最多两个批次）还留着清除前读进来的行，不重启的话，在旧候选卡上换一批会把原文再写进新的候选快照。
6. **删会话。** 核查列出的每个线程，请属主在界面里删掉这段对话。前端删对话走的就是 `DELETE /api/threads/{thread_id}`，接口只认 `threads_meta` 里记的属主，管理员也删不了别人的。
   - 这会删掉整段对话：checkpoint、运行记录、线程元数据，以及属主目录下的线程目录 `/data/users/<属主>/threads/<线程>/`（外置的工具输出和上传都在它的 `user-data` 下；接口先删目录，再删库里的行），属主再也看不到它。只能整段删，是因为 checkpoint 是二进制（msgpack），没法像 JSON 列那样就地替换。
   - 删线程不删工作台的行：`ggwp_candidate_sets` 没有外键，按 `thread_id` 和属主读，线程删了照样读得到，所以第 4 步不能省。
   - 属主一栏是 `(没有 threads_meta 行)`、命中只在 `.tool-results` 时：接口删不到，这些文件也不属于任何还能打开的会话（旧布局 `/data/threads/<线程>/`，或者会话已经删了、文件在别的用户目录下）。在第 1 步设好 `PAN` 的 ssh 会话里，人看过再直接删文件：

     ```bash
     find /data -type f -path '*/threads/<线程>/user-data/outputs/.tool-results/*' -exec grep -lziE --null "$PAN" -- {} + | xargs -0r rm -v --
     ```

   - 其余 `(没有 threads_meta 行)` 或 `(属主账号已不存在)`，接口删不掉，停下来另议；是 `(无属主)` 时，任何登录用户都能删。
7. **原始 feed 文件。** 每个批次的原文存成 `/data/pick/<属主哈希>/<内容哈希>`，只在导入时写。运行时没有代码读它，只有再次导入同样的内容时会读它核对哈希，文件不在就重写一份；清理旧批次时文件不在也不报错。所以直接删，不要改写：改写过的文件哈希对不上，同样的内容再导入会失败。在第 1 步设好 `PAN` 的 ssh 会话里，人看过列表再删：

   ```bash
   grep -rlziE --null "$PAN" /data/pick | xargs -0r rm -v --
   ```

8. **复查。** 同步仍然暂停着，再做一遍第 1 步，库和磁盘都查：47 个位置全为 0，线程表为空，`pan_scan` 退出码 0 且两段都是空的。
   - 知识来源、知识正文、候选的排除集合、旧页快照的跳转链、反馈版本清单、反馈原始记录、冻结结果证据、身份映射依据，或者 identity 等键的值里仍有命中：第 4 步不改这几处，不算清除完成，停止通用清洗并按来源属主制定处理方案。
   - 其余任何位置不为 0（剧目、候选快照、选择快照、回执……），都重做第 4、5 步再复查：多半是清除后、重启前有请求读到了旧数据。
   - 线程表不为空：还有会话没删，或者还留着外置的文件（第 6 步）。`[原始 feed 文件]` 不为空：重做第 7 步。`pan_scan` 退出码不是 0：按第 1 步处理后重扫。
   - 全部通过后做第 9 步。
9. **恢复同步。** 在 Railway 把 `PICK_REALSHORT_FEED_URL` 的原值加回去并部署，再手动同步一次；新进程启动一分钟后如果离上次成功已超过 12 小时，它会先自己补拉，这时手动同步会提示正在同步或刚同步过，等它跑完即可。同步成功后再做一遍第 1 步：仍然 47 个 0、线程表为空、`pan_scan` 退出码 0 且两段都是空的，才算处置完成，结果记进第 12 节。又有命中，说明源头没改干净，回到第 3 步。

- 不在范围内：
  - 用户自己写或上传的内容：选择的备注 `ggwp_selections.note`、反馈 `feedback.comment`、线程上传目录 `/data/users/<user>/threads/<thread>/user-data/uploads/`。硬规则针对的是从 RealShort 同步来的网盘片段；这几处是用户自己放进来的。
  - `/data/data/deerflow.db` 和 `/data/backup/` 下的 SQLite 文件（切换前的旧数据，第 11 节要求保留）。
- 2026-09-23 的基线：7,669 行剧目、12 份候选、2 条选择，全部为 0（当时用的是原来的模式，只查了剧目与候选快照两处）。

## 7. PG 兼容的现场检查（6.7）

扩展侧的 NUL、孤立代理项、VARCHAR 长度、时间戳排序、批次缓存都已在 P0-2 按两种方言测过。测试引擎与宿主用同一个 JSON 序列化器（`ensure_ascii=False`）；不经 `StrictInput` 的写入（feed 元数据、模型供应商给的 id、引用模型回答的核对提示）在写入边界换成 U+FFFD（`contracts.storable`）。

宿主自己的表（消息、run events、checkpoint metadata）已在本机 PG 17 上用宿主的序列化器实测：原样的 NUL（字符串消息）和孤立代理项（字符串与字典消息）都写不进去，换成 U+FFFD 后都能写。所以 pick 入口服务的是 `app.gateway.pick_asgi:app`：网关外面包一层 `JsonBodySanitizer`，只对 JSON 请求体（`application/json` 与 `*+json`，8 MiB 以内）把 NUL 和孤立代理项替换为 U+FFFD。`\ud800` 这类转义和直接写成 UTF-8 字节的代理项都算；响应（含 SSE）和上传原样透传。模型输出不经过这一层，出现这类字符时那一轮的写入会失败，是已知限制。

演练和切换后仍各发一条含 NUL、一条含孤立代理项（`\ud800` 转义）的消息，各走完一次选剧对话：预期都成功，消息里对应位置显示为 U+FFFD。失败就停下排查，不切换。

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

      不设 feed token，不会触发同步。`DEER_FLOW_HOME` 必须显式设：缺省的 `/data` 在 macOS 上建不了。三个 `AZURE_OPENAI_*` 只是占位：运行时 yaml 从 `config.pick.example.yaml` 抄来模型段的 `$AZURE_OPENAI_*`，缺一个，gateway 启动时就报 `Environment variable AZURE_OPENAI_DEPLOYMENT not found`。模型段的 `$PICK_LLM_*` 由入口补默认值，这里不用导出。开发机上 `app_config.py` 的 `load_dotenv()` 会往上找到仓库根被 git 忽略的 `.env`，把这个问题盖住；新 worktree 上面没有 `.env`。建管理员不调模型，真实密钥不必上本机。启动会完成宿主建表、checkpointer 与 store 建表，以及 ggwp 迁移到最新版本（当前是 0006，PostgreSQL 上会建 `pick_mirror` 的四张表）。入口监听 `0.0.0.0:8001`，建好管理员之前同一网络里的人也能调 `/api/v1/auth/initialize`，所以只在可信网络上做，并尽快走完下一步。
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
| Railway gateway | `PICK_EMIT_MIRROR_VERSION` | P4-1 | 结果与工具的 `data_as_of` 带不带 `mirror_version`，值严格等于 `1` 才输出；前端上线、提醒大家刷新页面之后再设，见 [realshort-sync.md](realshort-sync.md) 的「候选卡的镜像版本号（P4-1）」 |
| Railway `pick-obs-trends`、`pick-obs-gsc` | `PICK_DATABASE_URL`、`PGSSLMODE` | 趋势雷达 | session pooler 连接串，角色 `pick_observer`（第 3 节）；`require`。其余变量见趋势雷达计划第 10 节 S5、S9 |
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
- 两种后端共用 `/data/pick` 下的原始文件目录（按 owner 与内容哈希命名）。PG 上的清理只看 PG 里的批次，可能删掉旧 SQLite 批次也指向的原始文件。原始文件只在导入时写入，运行时不读，接口也不返回路径，所以回滚后 SQLite 照常工作；少掉的只是那几份原始 feed 的留档副本。要保留它们，把 `/data/pick` 复制进 `/data/backup`（2026-09-23 的切换没有复制）。

## 12. 留档

操作员执行后填写；只写结果，不写任何凭据。

**控制台设置（第 1 节）：**

| 项 | 结果 | 日期 |
|---|---|---|
| Enforce SSL | 开（CLI：`supabase ssl-enforcement update --experimental … --enable-db-ssl-enforcement`） | 2026-09-23 |
| Data API 关闭 / Exposed schemas 为空 | 已关闭（Integrations → Data API → Overview） | 2026-09-23 |
| Pool Size = 20 | 20（Database → Settings → Connection pooling） | 2026-09-23 |
| PITR | 不开（Add-ons 显示 Disabled），靠 Pro 自带的每日备份（保留 7 天） | 2026-09-23 |
| Spend cap | 开（组织 Billing 显示 enabled） | 2026-09-23 |

**bootstrap（第 2 节）：2026-09-23 完成。** 执行的是上一版脚本（两个角色，见第 2 节开头）。退出状态 0，`grep -c WARNING` 为 0，输出与当时 2.1 的 24 行逐行一致；2.4 各查询结果与演练逐字相同（两个角色 `rolconnlimit=20`，schema 权限只有 `deerflow_app=UC` 与 `pick_board_reader=U`，库属主 postgres）。两个角色的密码用本机算好的 SCRAM 摘要设置，`deerflow_app` 经 5432 登录，search_path 为 `deerflow`；reader 经 6543 登录，默认只读事务为 on。

**演练（第 10 节）：2026-09-23 完成，全部通过。** 临时项目 `ggwork-rehearsal`（us-east-1，Micro，PostgreSQL 17.6）与 Railway 临时环境 `rehearsal`（从 production 复制，镜像 81a518d），演练后都已删除。

| 项 | 结果 |
|---|---|
| 控制台设置 | SSL 强制用 CLI 打开：`supabase ssl-enforcement update --experimental --project-ref <ref> --enable-db-ssl-enforcement`。Data API 在 Integrations → Data API → Overview 里关闭。Pool Size 在 Database → Settings → Connection pooling 里改为 20 |
| bootstrap | 上一版脚本（两个角色，见第 2 节开头）。退出状态 0，`grep -c WARNING` 为 0，输出与当时的 2.1 逐行一致。2.4 全部符合：两个角色 `rolconnlimit=20`，rolconfig 与预期一致；`deerflow` 只有 `deerflow_app=UC`，`pick_mirror` 另有 `pick_board_reader=U`；reader_mirror、app_create 为 t，其余为 f；库属主是 postgres |
| 角色密码 | 本机算好 SCRAM 摘要后执行 `ALTER ROLE … PASSWORD`，与 `\password` 发出的语句相同。`deerflow_app` 经 5432 登录，search_path 为 `deerflow`；reader 经 6543 登录，`default_transaction_read_only=on` |
| 本机建管理员（8.3） | 在新 worktree 里启动，没有 `.env`，只用 8.3.3 列出的变量。宿主建表、ggwp 0001–0004 都完成。`POST /api/v1/auth/initialize` 返回 201，setup-status 为 `needs_setup=false`。第一次 readiness 探测超过 3 秒、返回 503（本机到 us-east-1 延迟高），稍等后为 200 |
| 新镜像 | 启动时没有 alembic 版本错误（宿主迁移已在 head），`/health/ready` 返回 200，本机建的管理员能登录 |
| 表的位置 | 38 张表全部在 `deerflow`，属主都是 `deerflow_app`，`public` 下为 0。checkpoint 三张表（psycopg 建）和 users、ggwp 表（asyncpg 建）都在 `deerflow` |
| search_path | 本机和容器内的探针都打印 `deerflow, public`（asyncpg）和 `deerflow,public`（psycopg）。Supavisor 转发了两个驱动的启动参数，`postgres_schema` 不用改 |
| SSL | `deerflow_app` 的连接全部 `ssl=t`，TLSv1.3 |
| 同步 | 启动后的补跑成功（7,590 行，约 12 秒）；手动同步成功（约 12 秒） |
| 10 题验收 | 经 API 跑，追问按前端的做法带上 `pick_reference`。用 `scripts/pick-acceptance-verify.py` 对 12:38Z 拉取的 feed 独立核对：8 张卡的顺序、条件和符合总数全部一致。Q5 总数 2,351 与 8 个剧场的分项都一致；Q7 沿用 Q1 的批次，5 部全是新剧；Q8 的依据与卡片一致 |
| 保存与导出 | 保存 2 部成功，同一个 request_id 重试拿到相同的回执；CSV 有 2 行 |
| NUL 与孤立代理项（第 7 节） | 两条消息都返回 200 并走完工具调用，存下来的消息里对应位置是 U+FFFD |
| create_user（`railway ssh`） | ssh 会话里 `PICK_DATABASE_URL`、`PGSSLMODE`、`PICK_DB_BACKEND` 都可见。账号建在演练库的 `deerflow.users` 里（`needs_setup=t`）。`/data/credentials` 权限 700，文件 600 |
| Supavisor 会话重置 | 客户端被 kill 之后，下一个客户端拿到同一个服务端连接（pid 相同），但 advisory lock 已释放，`lock_timeout` 回到 0。session 模式下客户端断开后会话状态会被重置 |
| 连接峰值 | 空闲时 `deerflow_app` 8 个；对话和同步同时进行时 `deerflow_app` 7 个，所有角色合计 22 个（`max_connections=60`） |
| 延迟 | 每个对话的第一轮约 14 秒（含把批次载入 LRU 缓存），之后 5–9 秒，与 SQLite 时期（5–13 秒）持平。有一轮 59 秒，慢在 Azure 的第一次调用（54 秒），不是数据库 |

**演练中确认的操作细节（切换时照做）：**
- 复制 Railway 环境后，本地 CLI 会自动 link 到新环境。演练期间所有命令都显式带 `--environment`，结束后用 `railway environment production` 切回。
- `railway ssh` 要求账号上登记过公钥。本机的专用钥匙是 `~/.ssh/railway_ggwork`，2026-09-23 已登记为 `railway-ggwork`，用法是 `railway ssh -i ~/.ssh/railway_ggwork …`。远端命令作为一个字符串传：它会被拼成 `bash -c`，外面再包一层 `sh -c` 会被拆坏。
- 建管理员可以不起前端，直接 `POST /api/v1/auth/initialize`（JSON，含 email 和 password），这个路径免 CSRF。请求体从权限 600 的文件读。
- pick 配置没有设置 `run_events`，运行事件走内存后端，所以 `deerflow.run_events` 为空是正常的；消息存在 checkpointer 的表里。

**正式切换（第 8 节）：2026-09-23 完成。** 镜像 add76ec，生产 gateway 13:04 UTC 以 `backend=postgres` 启动。

| 项 | 结果 |
|---|---|
| SQLite 备份 | `/data/backup/deerflow-sqlite-20260923.db`，`integrity_check` 为 `('ok',)`，sha256 `5f00b41f6ae0edfe95a634e3dc7489932077d9326a154536b371e81f35ccc79a`。原文件 `/data/data/deerflow.db` 未动。旧库里有 2 个账号、22 个对话、2 条选择，按用户决定不迁移 |
| 本机建管理员（第 3 步） | 建表前 `deerflow` 为 0 张表；本机 worktree（add76ec，无 `.env`）建表与 ggwp 0001–0004 完成，用原管理员邮箱 `POST /api/v1/auth/initialize` 返回 201，`needs_setup=false`；之后删掉 worktree 与 home |
| Railway 变量（第 4 步） | `PICK_DB_BACKEND=postgres`、`PGSSLMODE=require`、`PICK_DATABASE_URL`（从 stdin 设置），都带 `--skip-deploys`，没有触发旧镜像重启 |
| 部署与登录（第 5、6 步） | `/health/ready` 200；经 Vercel 入口用管理员登录 200，`/auth/me` 为 admin |
| 补跑同步（第 7 步） | 13:05:03 启动补跑成功，7,669 行，约 12 秒；13:12 手动同步成功，约 12 秒 |
| 重建账号（第 8 步） | `railway ssh` 会话里三个变量都可见；用原邮箱 `create_user` 重建 1 个普通账号（`needs_setup=t`），初始密码已取回本机交给用户，容器里的文件已删 |
| 表与 SSL | 38 张表全在 `deerflow`，属主 `deerflow_app`，`public` 为 0；`deerflow_app` 的连接全部 TLSv1.3 |
| 对话与 checkpoint | 选剧对话端到端跑通（含工具调用），`deerflow.checkpoints` 541 行 |
| 保存与导出 | 保存 2 部成功，同一 request_id 重试回执相同，CSV 2 行；验收用的两条随后移出，清单为空 |
| NUL 与孤立代理项 | 两条消息都 200，存下的消息里对应位置是 U+FFFD |
| 10 题验收 | 经 Vercel 入口跑，对 13:06Z 拉取的 feed 用 `scripts/pick-acceptance-verify.py` 独立核对：8 张卡的顺序、条件与符合总数全部一致；Q5 英语剧 2,360 部与 8 个剧场分项一致；带 `pick_reference` 的 Q7 为 5 部新剧、同一批次，Q8 依据与卡片一致 |
| 连接峰值 | 对话、保存与同步期间 `deerflow_app` 10 个，所有角色合计 25 个（`max_connections=60`） |
| 整库大小 | 26 MB |

**观测角色（observer-role.md，趋势雷达 S2、S4）：**
- S2 于 2026-09-26 03:46 UTC 执行。执行前只读核对：`pick_observer` 不存在，两个旧角色与迁移头表都在，还没有 `pick_obs`，迁移头 0006，已发布镜像版本 4 个。
- `bootstrap-observer.sql` 以 postgres 经 session pooler 执行：退出码 0，WARNING 0 行，输出与 observer-role.md 的 14 行逐行一致。
- 口令用 `openssl rand -hex 32` 生成，只进本机 600 文件。设置时和 `\password` 一样，只把在本机算出的 SCRAM-SHA-256 校验值（libpq 的 PQencryptPasswordConn）发给库，明文不进 SQL。
- 以观测角色登录：`search_path` 为 `deerflow`，连接走 SSL，读得到迁移头 0006。
- 留档检查：`rolcanlogin=t`、`rolinherit=f`、`rolconnlimit=20`，rolconfig 为 `{search_path=deerflow,TimeZone=UTC,idle_in_transaction_session_timeout=1min,statement_timeout=2min}`；`\dn+` 里 `deerflow` 与 `pick_mirror` 各多一行 `pick_observer=U/deerflow_app`；observer_deerflow、observer_mirror、observer_head 为 t，observer_batches、observer_create 为 f。
- S4 于 2026-09-28 14:25 UTC 在 gateway 容器里以 deerflow_app 执行：`regrant --check` 列出缺少授权 8 项（pickm_v000001、v000009、v000010、v000011 的 schema USAGE 与 rs_ids SELECT），退出码 1；`regrant` 打印「观测角色的授权已补齐：schema 6 个、表 30 张、列 4 个、序列 12 个，已发布镜像版本 4 个」，退出码 0；再次 `--check` 为「授权齐全」，退出码 0。没有出现「已发布但没有 rs_ids 的镜像版本」。
- 影子运行期间 `pick_observer` 的连接峰值，待记。

**每周容量（第 6 节）：** 切换后开始记录。

**网盘片段核查（第 6 节）：** 2026-09-23 只查了剧目与候选快照两处，都是 0（原来的模式）。2026-09-24 03:53 UTC 按第 1 步做了第一次完整核查，模式是 24c74fe 的 `PAN=`（容器里 SHA-256 自检一致）：gateway 容器里 `pan_scan` 退出码 0，`[原始 feed 文件]` 与 `[线程]` 都是空的（railway ssh 进去是 bash，`/bin/sh` 是 dash，GNU grep 3.8，`/data/threads` 不存在）；本机以 `deerflow_app` 跑 `pan-check.sql`（`disk_threads=''`），11 个位置全是 0，线程表为空。覆盖 3 个批次 23,976 行剧目（含 03:40 UTC 那次成功同步的批次）、12 份候选、2 条选择、2 条回答核对、1 份知识文档。没有命中，第 2 步以后不用做。

**资料页上线（P3-6）：2026-09-24 完成。** 前端 301b0ea 于 20:40 UTC 上线（`dpl_8oZPJ…`），别名 ggwork-deerflow.vercel.app 指向它。

| 项 | 结果 |
|---|---|
| 部署来源 | 从 `git archive 301b0ea` 导出的干净目录部署，只带 `.vercel/project.json`。Vercel CLI 会上传工作区里 gitignore 的文件，所以不在工作区直接部署：09-22 至 09-23 的四个旧部署源码里带了 `.env`、`frontend/.env`、`config.yaml`、`extensions_config.json`、`backend/.deer-flow/.jwt_secret`（项目开了 SSO 保护，源码只有团队成员能看）。新部署的源码里没有这些文件 |
| 变量 | Production 有 `PICK_MIRROR_READER_URL` 与 `PICK_MIRROR_CA_PEM`；Preview、Development 没有 |
| TLS | 按 CA 校验通过，没有退回不校验：`db.ts` 固定 `rejectUnauthorized: true` 加 `PICK_MIRROR_CA_PEM`，缺 CA 时报 `MirrorMisconfigured`、不会回落到系统根证书；生产 6 个数据 tab 都读到镜像 |
| 上线后验证 | 未登录请求被 307 到 `/login`；带 `RSC: 1` 的未登录请求 8 KB，40 个已知剧名 0 个出现；登录后 pick、all、rank、posted、rules 与单条证据页都是 200、页头显示「镜像 v1」、没有错误页；imports tab 不读镜像，版本号由浏览器端取 |
| 运行时日志 | 上线后 30 分钟与压测期间没有 error / warning，没有 `[pick-board] query failed` 或 `outside a version scope` |
| 压测 | 21:06:48 UTC 起 20 并发、120 秒，混合 5 个数据 tab、两个翻页与 5 个证据页：1,634 个请求全部 200，0 个连接错误、0 个错误页，13.6 请求/秒。本机（亚洲）测得 p50 1.31 s、p95 2.80 s、p99 3.69 s |
| p95 的口径 | 目标 p95 < 2.5 s 是服务端口径；团队套餐没有 Observability，拿不到函数耗时。本机不加压逐个请求：TLS 握手 0.36 s，静态 favicon 首字节 0.95 s，各 tab 首字节 0.85–1.2 s，正文 160–520 KB 传输 0.35–1.0 s。页面首字节只比静态文件多 0.25 s 以内，所以 2.80 s 里大部分是跨洋往返和传输；服务端 p95 达标是估计，没有直接测到 |
| reader 峰值 | 每 5 秒采样一次，压测期间 `pick_board_reader` 17–20 个，单次采样最高 22（连接开关的瞬间）；压测后仍是 20 个 Supavisor 空闲连接。20 并发时把 Pool Size 20 用满，客户端在 transaction 模式里排队，没有报错 |
| 连接计数的读法 | Supavisor 在客户端断开后把服务端连接留在池里给同一角色复用，所以 `pg_stat_activity` 看到的是池子大小，不是同时在用的客户端。压测后 `deerflow_app` 18 个 idle、1 个 active（采样本身留下的），不挡 gateway |
