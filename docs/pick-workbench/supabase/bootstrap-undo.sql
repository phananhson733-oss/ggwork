-- 撤销 bootstrap.sql（docs/pick-workbench/supabase.md 第 2 节）。只用于 bootstrap 已提交、但留档检查不通过，
-- 且宿主还没有连过这个库的时候。DROP SCHEMA 不带 CASCADE：三个 schema 里任何一个已经有表就失败，整个事务回滚。
-- 生产项目执行的是上一版 bootstrap（没有 pick_obs 与 pick_observer），本脚本不用于它；撤销观测角色用 bootstrap-observer-undo.sql。
--   psql "postgresql://postgres.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -f bootstrap-undo.sql
\set ON_ERROR_STOP on
BEGIN;
-- schema 属于 deerflow_app，postgres 不继承它的权限，删之前先 SET ROLE
SET ROLE deerflow_app;
DROP SCHEMA deerflow, pick_mirror, pick_obs;
RESET ROLE;
-- 角色在库上还有授权时 DROP ROLE 会失败
REVOKE ALL ON DATABASE postgres FROM deerflow_app, pick_board_reader, pick_observer;
DROP ROLE deerflow_app, pick_board_reader, pick_observer;
COMMIT;
