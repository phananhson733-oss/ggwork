-- 选剧工作台 Supabase bootstrap（方案 3.1、6.2；步骤与留档见 docs/pick-workbench/supabase.md 第 2 节）。
-- 只执行一次：以 Supabase 的 postgres 角色经 session pooler 连 postgres 库，非交互运行，输出留档：
--   psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -f bootstrap.sql
-- 不含密码：执行完另开一个 psql 会话，\password deerflow_app 与 \password pick_board_reader。
-- 整个脚本是一个事务：任何一句失败，psql 以状态 3 退出，之前的语句全部回滚，项目保持原样，改正后可以重跑。
-- 输出里不能有任何 WARNING 行：GRANT/REVOKE 没有权限时只警告、照样打印 GRANT（方案 3.1，审计 host-1）。
\set ON_ERROR_STOP on
BEGIN;
-- 连错库时 schema 会建在那个库里，库级授权却给的是 postgres 库
DO $$ BEGIN
  IF current_database() <> 'postgres' THEN
    RAISE EXCEPTION '当前连接的是 % 库，bootstrap 只在 postgres 库上执行', current_database();
  END IF;
END $$;
CREATE ROLE deerflow_app LOGIN NOINHERIT CONNECTION LIMIT 20;
CREATE ROLE pick_board_reader LOGIN NOINHERIT CONNECTION LIMIT 20;
-- 两个上限都等于 Supavisor 的 Pool Size 20（方案 13.1、6.4）：角色上限低于池大小时，
-- Supavisor 去开超出角色上限的服务端连接会直接报错，而不是排队
-- PG16+：CREATEROLE 建出的角色只给创建者 ADMIN，没有 SET；CREATE SCHEMA ... AUTHORIZATION 需要 SET
-- 在 Supabase 上 CURRENT_USER 就是 postgres；写成 CURRENT_USER 是为了测试能用替身角色跑同一份脚本
GRANT deerflow_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CONNECT, CREATE ON DATABASE postgres TO deerflow_app;
GRANT CONNECT ON DATABASE postgres TO pick_board_reader;
-- 库属主不是当前角色且没有 grant option 时，上一句 GRANT 只报 WARNING、不授权；这里改成硬失败
DO $$ BEGIN
  IF NOT has_database_privilege('deerflow_app', 'postgres', 'CREATE') THEN
    RAISE EXCEPTION 'deerflow_app 没有拿到 postgres 库的 CREATE 权限，停下来按方案 10.2 第 10 条处理';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS deerflow AUTHORIZATION deerflow_app;
CREATE SCHEMA IF NOT EXISTS pick_mirror AUTHORIZATION deerflow_app;
-- INHERIT FALSE：当前角色不继承 deerflow_app 的权限，对它名下的 schema 做 GRANT/REVOKE 必须先 SET ROLE；
-- 否则报 permission denied，或者（当前角色另有 pg_read_all_data 等时）只报 WARNING、授权悄悄缺失
SET ROLE deerflow_app;
REVOKE ALL ON SCHEMA deerflow, pick_mirror FROM PUBLIC;
REVOKE ALL ON SCHEMA deerflow, pick_mirror FROM anon, authenticated;
GRANT USAGE ON SCHEMA pick_mirror TO pick_board_reader;
RESET ROLE;
-- 以下回到 postgres：它对两个角色有 ADMIN OPTION，可以 ALTER ROLE
ALTER ROLE deerflow_app SET search_path = deerflow;
ALTER ROLE deerflow_app SET timezone = 'UTC';
ALTER ROLE deerflow_app SET idle_in_transaction_session_timeout = '5min';
ALTER ROLE pick_board_reader SET search_path = pick_mirror;
ALTER ROLE pick_board_reader SET default_transaction_read_only = on;
ALTER ROLE pick_board_reader SET statement_timeout = '8s';
ALTER ROLE pick_board_reader SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE pick_board_reader SET timezone = 'UTC';
COMMIT;
