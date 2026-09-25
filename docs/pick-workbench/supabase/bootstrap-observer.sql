-- 趋势雷达的观测角色 pick_observer（趋势雷达计划 TR-12、D3、D4；设计 3.4）。步骤与判定见 docs/pick-workbench/observe-runbook/observer-role.md。
-- 用于已经执行过 bootstrap.sql 上一版的项目，也就是生产项目（上线步骤 S2），只执行一次，而且必须早于带迁移 0007 的
-- gateway 部署（S3）：0007 遇到角色不存在只告警、跳过授权。新项目用 bootstrap.sql，里面已经有这个角色。
-- 以 Supabase 的 postgres 角色经 session pooler 连 postgres 库，非交互运行，输出留档：
--   psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -f bootstrap-observer.sql
-- 不含密码：执行完另开一个 psql 会话，\password pick_observer。
-- 整个脚本是一个事务：任何一句失败，psql 以状态 3 退出，库里什么都不变，改正后可以重跑。输出里不能有任何 WARNING 行。
-- 这里只建角色，授库的 CONNECT、deerflow 与 pick_mirror 的 USAGE，以及迁移头 deerflow.ggwp_alembic_version 的 SELECT：
-- S3 之前的部署守卫（D41）以本角色读生产迁移头，那时库还在 0006，regrant 也会因 0007 未跑而拒绝，所以这一条不能等 0007。
-- 其余表级授权由 0007 给，已发布镜像版本的 rs_ids 由 `python -m ggwork_pick.observe.admin regrant` 补（D15）；
-- 撤销用 bootstrap-observer-undo.sql。
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'postgres' THEN
    RAISE EXCEPTION '当前连接的是 % 库，本脚本只在 postgres 库上执行', current_database();
  END IF;
  IF to_regrole('deerflow_app') IS NULL OR to_regnamespace('deerflow') IS NULL OR to_regnamespace('pick_mirror') IS NULL THEN
    RAISE EXCEPTION '没有找到 deerflow_app 或 deerflow、pick_mirror 两个 schema：这个项目还没执行过 bootstrap.sql';
  END IF;
  -- 查目录而不用 to_regclass：postgres 不继承 deerflow_app，未必有 deerflow 的 USAGE，按名字解析会报 permission denied
  IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE n.nspname = 'deerflow' AND c.relname = 'ggwp_alembic_version' AND c.relkind = 'r') THEN
    RAISE EXCEPTION '没有找到 deerflow.ggwp_alembic_version：gateway 还没在这个库上跑过迁移。生产早已在 0006；新项目用完整的 bootstrap.sql';
  END IF;
END $$;
-- 上限等于 Supavisor 的 Pool Size 20（supabase 方案 922 的规则），实际最多 2 条：两个 cron 各 1 条（设计 3.4）
CREATE ROLE pick_observer LOGIN NOINHERIT CONNECTION LIMIT 20;
GRANT CONNECT ON DATABASE postgres TO pick_observer;
-- 两个 schema 属于 deerflow_app，postgres 不继承它的权限，以属主身份授权（bootstrap.sql 的同一做法）
SET ROLE deerflow_app;
GRANT USAGE ON SCHEMA deerflow, pick_mirror TO pick_observer;
-- 部署守卫读迁移头用（D41）。0007 以同一属主再授一次，ACL 里仍只有一条
GRANT SELECT ON deerflow.ggwp_alembic_version TO pick_observer;
RESET ROLE;
-- GRANT 没有权限时只报 WARNING、照样打印 GRANT；这里核对四项确实授给了这个角色本身，否则硬失败。
-- 只认 ACL 里写着 pick_observer 的项：has_*_privilege 会把 PUBLIC 的授权也算上
DO $$ BEGIN
  IF NOT EXISTS (
       SELECT 1 FROM pg_database d, aclexplode(d.datacl) a
        WHERE d.datname = current_database() AND a.grantee = 'pick_observer'::regrole AND a.privilege_type = 'CONNECT')
     OR (SELECT count(DISTINCT n.nspname) FROM pg_namespace n, aclexplode(n.nspacl) a
          WHERE n.nspname IN ('deerflow', 'pick_mirror') AND a.grantee = 'pick_observer'::regrole AND a.privilege_type = 'USAGE') <> 2
     OR NOT EXISTS (
       SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        WHERE n.nspname = 'deerflow' AND c.relname = 'ggwp_alembic_version' AND a.grantee = 'pick_observer'::regrole AND a.privilege_type = 'SELECT') THEN
    RAISE EXCEPTION 'pick_observer 的库级 CONNECT、两个 schema 的 USAGE 或迁移头的 SELECT 没有授上（GRANT 只报了 WARNING），停下来按手册处理';
  END IF;
END $$;
-- 两个观测 cron 的每一步都是短事务，HTTP 在事务之外（设计 3.3）；超时只兜底，个别步骤要更长时自己 SET LOCAL
ALTER ROLE pick_observer SET search_path = deerflow;
ALTER ROLE pick_observer SET timezone = 'UTC';
ALTER ROLE pick_observer SET idle_in_transaction_session_timeout = '1min';
ALTER ROLE pick_observer SET statement_timeout = '2min';
COMMIT;
