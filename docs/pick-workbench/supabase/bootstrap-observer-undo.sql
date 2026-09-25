-- 撤销 bootstrap-observer.sql（docs/pick-workbench/observe-runbook/observer-role.md「撤销」）。先停掉两个观测 cron。
-- 迁移 0007、镜像发布与 regrant 之后也能用：先以属主 deerflow_app 收回观测角色在表、列、序列、schema 上的全部授权，
-- 再收回库级授权，最后删掉角色。观测表与里面的数据都不动；reader 与 deerflow_app 的授权也不动。
--   psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -f bootstrap-observer-undo.sql
-- 一个事务：任何一句失败都整体回滚，角色与授权保持原样。输出里不能有任何 WARNING 行。
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'postgres' THEN
    RAISE EXCEPTION '当前连接的是 % 库，本脚本只在 postgres 库上执行', current_database();
  END IF;
END $$;
-- 授权都是属主 deerflow_app 给的；收回表的授权连带收回它各列上的授权
SET ROLE deerflow_app;
DO $$
DECLARE
  target record;
BEGIN
  FOR target IN
    SELECT c.oid::regclass AS name, c.relkind FROM pg_class c
     WHERE EXISTS (SELECT 1 FROM aclexplode(c.relacl) a WHERE a.grantee = 'pick_observer'::regrole)
        OR EXISTS (SELECT 1 FROM pg_attribute t, aclexplode(t.attacl) a WHERE t.attrelid = c.oid AND a.grantee = 'pick_observer'::regrole)
     ORDER BY c.oid
  LOOP
    EXECUTE format('REVOKE ALL ON %s %s FROM pick_observer', CASE WHEN target.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, target.name);
  END LOOP;
  FOR target IN
    SELECT n.nspname AS name FROM pg_namespace n
     WHERE EXISTS (SELECT 1 FROM aclexplode(n.nspacl) a WHERE a.grantee = 'pick_observer'::regrole)
     ORDER BY n.oid
  LOOP
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM pick_observer', target.name);
  END LOOP;
END $$;
RESET ROLE;
-- 角色在库上还有授权时 DROP ROLE 会失败
REVOKE ALL ON DATABASE postgres FROM pick_observer;
DROP ROLE pick_observer;
COMMIT;
