-- 选剧工作台网盘片段核查（只读；步骤与判定见 docs/pick-workbench/supabase.md 第 6 节）。
-- 以 deerflow_app 经 session pooler 连 postgres 库执行，密码在提示时粘贴：
--   psql "postgresql://deerflow_app.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -f pan-check.sql
-- 输出两张表，只有条数、线程 id 和属主，不输出命中的文本：
--   1. 工作台各列含命中的行数。前 8 行是 pan-redact.sql 会清除的 JSON 列；最后一行是知识正文，脚本不改。
--   2. 宿主表里含命中的线程、属主邮箱和命中所在的表。checkpoint 是二进制，只能经线程的 DELETE 接口整段删掉。
-- 模式比 RealShort #67 的清洗正则宽，命中不一定是网盘信息，要人看。它与 pan-redact.sql、运行手册里 grep 的模式逐字相同
-- （customizations/pick-workbench/tests/test_pan_runbook_sql.py 钉住），改的时候三处一起改。
\set ON_ERROR_STOP on
SELECT 'pan\.baidu|yun\.baidu|pan\.quark|aliyundrive|alipan|115\.com|115cdn|123pan|123684\.com|123865\.com|123912\.com|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|caiyun\.139|yun\.139|提取码|提取碼|访问码|訪問碼|pwd=|(密码|密碼)[[:space:]]*(=|:|：)' AS pan \gset
-- checkpoint 的 blob 是 msgpack：字符串是原样的 UTF-8 字节，工具输出按 ensure_ascii 写的 JSON 里中文是 \uXXXX。
-- encode(blob, 'escape') 把 0x00 与 0x80 以上的字节写成 \ooo、反斜杠写成 \\，其余 ASCII 原样；
-- 所以把模式里每个非 ASCII 字符换成「它的 \ooo 字节序列，或者 \\uXXXX」，ASCII 部分照旧不分大小写匹配。
SELECT string_agg(CASE WHEN ascii(c) > 127
                       THEN '(?:' || replace(encode(convert_to(c, 'UTF8'), 'escape'), '\', '\\')
                            || '|\\\\u' || lpad(to_hex(ascii(c)), 4, '0') || ')'
                       ELSE c END, '' ORDER BY i) AS pan_bytes
  FROM regexp_split_to_table(:'pan', '') WITH ORDINALITY AS s(c, i) \gset
BEGIN READ ONLY;
-- JSON 列先转 jsonb 再转文本：\uXXXX 转义的中文也还原成字符再匹配，与 pan-redact.sql 选行的条件相同
SELECT location, count AS rows FROM (
            SELECT 1 AS n, 'ggwp_drama_versions.payload_json' AS location, count(*) FROM deerflow.ggwp_drama_versions WHERE payload_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 2, 'ggwp_candidate_sets.ordered_items_json', count(*) FROM deerflow.ggwp_candidate_sets WHERE ordered_items_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 3, 'ggwp_candidate_sets.conditions_json', count(*) FROM deerflow.ggwp_candidate_sets WHERE conditions_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 4, 'ggwp_selections.snapshot_json', count(*) FROM deerflow.ggwp_selections WHERE snapshot_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 5, 'ggwp_selection_commands.receipt_json', count(*) FROM deerflow.ggwp_selection_commands WHERE receipt_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 6, 'ggwp_answer_checks.notes_json', count(*) FROM deerflow.ggwp_answer_checks WHERE notes_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 7, 'ggwp_import_batches.validation_json', count(*) FROM deerflow.ggwp_import_batches WHERE validation_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 8, 'ggwp_knowledge_versions.metadata_json', count(*) FROM deerflow.ggwp_knowledge_versions WHERE metadata_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 9, 'ggwp_knowledge_versions.text', count(*) FROM deerflow.ggwp_knowledge_versions WHERE text ~* :'pan'
) AS located ORDER BY n;
-- 线程的 DELETE 接口会删掉下面每张表里这个线程的行（runs 只删 operation_kind = 'run' 的），但只认 threads_meta 里记的属主：
-- 没有 threads_meta 行、或者属主账号已经不存在时，接口删不掉；这一行没有属主时，任何登录用户都能删
WITH hits (thread_id, found_in) AS (
            SELECT thread_id, 'checkpoints' FROM deerflow.checkpoints WHERE checkpoint::text ~* :'pan' OR metadata::text ~* :'pan'
  UNION ALL SELECT thread_id, 'checkpoint_blobs' FROM deerflow.checkpoint_blobs WHERE encode(blob, 'escape') ~* :'pan_bytes'
  UNION ALL SELECT thread_id, 'checkpoint_writes' FROM deerflow.checkpoint_writes WHERE encode(blob, 'escape') ~* :'pan_bytes'
  UNION ALL SELECT thread_id, 'runs' FROM deerflow.runs
             WHERE first_human_message ~* :'pan' OR last_ai_message ~* :'pan' OR kwargs_json::text ~* :'pan' OR metadata_json::text ~* :'pan'
  UNION ALL SELECT thread_id, 'run_events' FROM deerflow.run_events WHERE content ~* :'pan' OR event_metadata::text ~* :'pan'
  UNION ALL SELECT thread_id, 'threads_meta' FROM deerflow.threads_meta WHERE display_name ~* :'pan' OR metadata_json::text ~* :'pan'
)
SELECT h.thread_id,
       CASE WHEN t.thread_id IS NULL THEN '(没有 threads_meta 行)'
            WHEN t.user_id IS NULL THEN '(无属主)'
            ELSE coalesce(u.email, '(属主账号已不存在)') END AS owner,
       h.found_in
  FROM (SELECT thread_id, string_agg(DISTINCT found_in, ',' ORDER BY found_in) AS found_in FROM hits GROUP BY thread_id) AS h
  LEFT JOIN deerflow.threads_meta AS t ON t.thread_id = h.thread_id
  LEFT JOIN deerflow.users AS u ON u.id = t.user_id
 ORDER BY h.thread_id;
COMMIT;
