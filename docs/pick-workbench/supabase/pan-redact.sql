-- 选剧工作台网盘片段清除（步骤见 docs/pick-workbench/supabase.md 第 6 节）。先跑 pan-check.sql，人看过、确认有真命中才跑。
-- 以 deerflow_app（ggwp_* 表与 deerflow schema 的属主）经 session pooler 连 postgres 库执行，密码在提示时粘贴：
--   psql "postgresql://deerflow_app.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -f pan-redact.sql
-- 做的事：ggwp_* 的 8 个 JSON 列里，凡是含命中的 JSON 字符串（整个字符串字面量，不是其中一段）都换成 "[网盘信息已移除]"，
-- 其余字节原样不动，JSON 仍然有效；没有命中的行不写。与 pan-check.sql 用同一个模式、同一个选行条件，所以误报也会一起换掉。
-- 不改：
--   - 任何主键与 identity 列；JSON 里 identity、source_id、item_id、citation_id、request_id 这几个键的值。改了会让剧目、快照、
--     选择与回执彼此对不上；这里有命中时复查仍然报出来，停下来另议。
--   - ggwp_knowledge_versions.text：规则全文，整篇换掉会丢规则。
--   - 宿主的任何表：checkpoint 是二进制，没法就地改，命中的线程只能经线程的 DELETE 接口删整段会话。
-- 整个脚本是一个事务，任何一句失败都整体回滚。辅助函数在事务里建、提交前删掉，库里不留东西。
-- 可以重复执行：第二次每个 UPDATE 都是 0。
-- 模式与 pan-check.sql、运行手册里 grep 的模式逐字相同（customizations/pick-workbench/tests/test_pan_runbook_sql.py 钉住）。
\set ON_ERROR_STOP on
SELECT 'pan\.baidu|yun\.baidu|pan\.quark|aliyundrive|alipan|115\.com|115cdn|123pan|123684\.com|123865\.com|123912\.com|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|caiyun\.139|yun\.139|提取码|提取碼|访问码|訪問碼|pwd=|(密码|密碼)[[:space:]]*(=|:|：)' AS pan \gset
BEGIN;
-- 把 JSON 文本从头切成两种片段：字符串字面量（引号到引号，\" 与 \\ 这类转义算在串里），和两个串之间的其余字符。
-- 合法 JSON 里每个引号都是某个串的开头或结尾，所以切出来的片段首尾相接、拼回去与原文逐字相同，一个片段不会跨两个串。
-- 只对字符串片段按解码后的值判断命中，命中就把整个片段换掉。拼回去与原文不同（不该发生）时原样返回，不改这一行。
CREATE FUNCTION deerflow.ggwp_pan_redact(doc json, pattern text) RETURNS json
LANGUAGE sql IMMUTABLE STRICT AS $fn$
  WITH pieces AS (
    SELECT m.ord, m.piece[1] AS piece
      FROM regexp_matches(doc::text, '"(?:[^"\\]|\\.)*"|[^"]+', 'g') WITH ORDINALITY AS m(piece, ord)
  ), judged AS (
    SELECT ord, piece,
           CASE WHEN left(piece, 1) <> '"' THEN false
                WHEN lag(piece, 1) OVER w ~ '^\s*:\s*$'
                 AND lag(piece, 2) OVER w IN ('"identity"', '"source_id"', '"item_id"', '"citation_id"', '"request_id"') THEN false
                ELSE (piece::json #>> '{}') ~* pattern END AS hit
      FROM pieces
    WINDOW w AS (ORDER BY ord)
  )
  SELECT CASE WHEN string_agg(piece, '' ORDER BY ord) = doc::text
              THEN string_agg(CASE WHEN hit THEN '"[网盘信息已移除]"' ELSE piece END, '' ORDER BY ord)::json
              ELSE doc END
    FROM judged
$fn$;
UPDATE deerflow.ggwp_drama_versions SET payload_json = deerflow.ggwp_pan_redact(payload_json, :'pan')
 WHERE payload_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(payload_json, :'pan')::text <> payload_json::text;
UPDATE deerflow.ggwp_candidate_sets SET ordered_items_json = deerflow.ggwp_pan_redact(ordered_items_json, :'pan')
 WHERE ordered_items_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(ordered_items_json, :'pan')::text <> ordered_items_json::text;
UPDATE deerflow.ggwp_candidate_sets SET conditions_json = deerflow.ggwp_pan_redact(conditions_json, :'pan')
 WHERE conditions_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(conditions_json, :'pan')::text <> conditions_json::text;
UPDATE deerflow.ggwp_selections SET snapshot_json = deerflow.ggwp_pan_redact(snapshot_json, :'pan')
 WHERE snapshot_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(snapshot_json, :'pan')::text <> snapshot_json::text;
UPDATE deerflow.ggwp_selection_commands SET receipt_json = deerflow.ggwp_pan_redact(receipt_json, :'pan')
 WHERE receipt_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(receipt_json, :'pan')::text <> receipt_json::text;
UPDATE deerflow.ggwp_answer_checks SET notes_json = deerflow.ggwp_pan_redact(notes_json, :'pan')
 WHERE notes_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(notes_json, :'pan')::text <> notes_json::text;
UPDATE deerflow.ggwp_import_batches SET validation_json = deerflow.ggwp_pan_redact(validation_json, :'pan')
 WHERE validation_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(validation_json, :'pan')::text <> validation_json::text;
UPDATE deerflow.ggwp_knowledge_versions SET metadata_json = deerflow.ggwp_pan_redact(metadata_json, :'pan')
 WHERE metadata_json::jsonb::text ~* :'pan' AND deerflow.ggwp_pan_redact(metadata_json, :'pan')::text <> metadata_json::text;
DROP FUNCTION deerflow.ggwp_pan_redact(json, text);
COMMIT;
