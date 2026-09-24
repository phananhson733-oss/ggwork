-- 选剧工作台网盘片段核查（只读；步骤与判定见 docs/pick-workbench/supabase.md 第 6 节）。
-- 以 deerflow_app 经 session pooler 连 postgres 库执行，密码在提示时粘贴。disk_threads 是运行手册第 1 步在容器里
-- find 出的线程（逗号隔开，没有就传空串），不传直接报错：
--   psql "postgresql://deerflow_app.<ref>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" -X -v disk_threads='<线程>' -f pan-check.sql
-- 输出两张表，只有条数、线程 id 和属主，不输出命中的文本：
--   1. 工作台 11 个位置各有几行命中。前 9 个 pan-redact.sql 会清除；最后两个（知识正文、知识来源）脚本不改，命中时停下来另议。
--   2. 含命中的线程、属主邮箱和命中所在：宿主的表，或者 .tool-results。宿主把超过阈值的工具输出整份写进线程目录下的
--      user-data/outputs/.tool-results/，库里只留预览和文件引用，这些命中只能在容器里找到，由 disk_threads 带进来。
--      checkpoint 是二进制，只能经线程的 DELETE 接口整段删掉。
-- 库里为 0 不代表磁盘上没有：导入先写原始文件、后写库。运行手册第 1 步先在容器里查 /data/pick 和各线程的 .tool-results。
-- 模式比 RealShort #67 的清洗正则宽，命中不一定是网盘信息，要人看。除了原文，它认任意层 JSON 转义的写法（反斜杠一个或多个，
-- 比如工具输出本身是 JSON、又被宿主的运行事件整个再转一次，换行就成了两个反斜杠加 n）：
--   - 中文关键字与全角冒号写成 \uXXXX（ensure_ascii；十六进制不分大小写）；
--   - 「密码」与分隔符之间的空白：原样的 ASCII 空白；Unicode White_Space 里全部 19 个非 ASCII 字符（U+0085、U+00A0、U+1680、
--     U+2000 到 U+200A、U+2028、U+2029、U+202F、U+205F、U+3000），每个单独一条分支、原样写在模式里（看不见）；
--     或者 \n、\t 这类转义与任意 \uXXXX。非 ASCII 空白不靠 [[:space:]]：它认不认这些字符随 locale 变（C locale 只认 ASCII），
--     下面 blob 的匹配和容器里的 grep 也都可能是按字节比。
-- 它与 pan-redact.sql、运行手册里的 PAN= 逐字相同（customizations/pick-workbench/tests/test_pan_runbook_sql.py 钉住），三处一起改。
\set ON_ERROR_STOP on
\if :{?disk_threads}
\else
DO $$ BEGIN RAISE EXCEPTION '缺 -v disk_threads=...：先按运行手册第 1 步在容器里 find，把列出的线程传进来，没有就传空串'; END $$;
\endif
SELECT 'pan\.baidu|yun\.baidu|pan\.quark|aliyundrive|alipan|115\.com|115cdn|123pan|123684\.com|123865\.com|123912\.com|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|caiyun\.139|yun\.139|weiyun\.com|jianguoyun\.com|mypikpak|pan\.wo\.cn|ctfile|feijipan|lanz[a-z]\.com|wenshushu|cowtransfer|yunpan\.360|fast\.uc\.cn|anxia\.com|123952\.com|400gb\.com|pipipan\.com|545c\.com|90pan\.com|089u\.com|474b\.com|t00y\.com|306t\.com|47ks\.com|4765\.com|77tj\.com|feijix\.com|fjpan\.com|wss\.cc|c-t\.work|yunpan\.cn|yunpan\.com|pan\.360\.cn|quqi\.com|musetransfer\.com|tmp\.link|airportal\.cn|airportal\.link|easychuan\.cn|filez\.com|box\.lenovo\.com|vdisk\.weibo\.com|v\.disk\.weibo\.com|vdisk\.cn|kuaipan\.cn|dbank\.com|dbank\.vmall\.com|pan\.sohu\.net|fhrl\.wostore\.cn|提取码|提取碼|访问码|訪問碼|pwd=|(密码|密碼)([[:space:]]|| | | | | | | | | | | | | | | | | |　|\\+[bfnrtv]|\\+u[0-9a-fA-F]{4})*(=|:|：|\\+uff1a)|\\+u63d0\\+u53d6\\+u78(01|bc)|\\+u8bbf\\+u95ee\\+u7801|\\+u8a2a\\+u554f\\+u78bc|\\+u5bc6\\+u78(01|bc)([[:space:]]|| | | | | | | | | | | | | | | | | |　|\\+[bfnrtv]|\\+u[0-9a-fA-F]{4})*(=|:|：|\\+uff1a)' AS pan \gset
-- checkpoint 的 blob 是 msgpack，字符串是原样的 UTF-8 字节。encode(blob, 'escape') 只把 0x00 和 0x80 以上的字节写成 \ooo、
-- 把反斜杠写成 \\，其余字节（含真实的换行、制表符）原样。模式里的反斜杠都写成「一个或多个」，编码后翻倍的反斜杠照样匹配；
-- 所以匹配 blob 前只需把每个非 ASCII 字符换成它的 \ooo 字节序列。ASCII 部分照旧不分大小写。改写是逐字符的，
-- 非 ASCII 字符在模式里只能是单独的分支、不能放进 [...]；[[:space:]] 碰不到 \ooo，非 ASCII 空白只靠那 19 条分支。
SELECT string_agg(CASE WHEN ascii(c) > 127
                       THEN '(?:' || replace(encode(convert_to(c, 'UTF8'), 'escape'), '\', '\\') || ')'
                       ELSE c END, '' ORDER BY i) AS pan_bytes
  FROM regexp_split_to_table(:'pan', '') WITH ORDINALITY AS s(c, i) \gset
BEGIN READ ONLY;
-- JSON 列先转 jsonb 再转文本，\uXXXX 转义的中文还原成字符再匹配；与 pan-redact.sql 选行的条件相同
SELECT location, count AS rows FROM (
            SELECT 1 AS n, 'ggwp_drama_versions.payload_json' AS location, count(*) FROM deerflow.ggwp_drama_versions WHERE payload_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 2, 'ggwp_candidate_sets.ordered_items_json', count(*) FROM deerflow.ggwp_candidate_sets WHERE ordered_items_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 3, 'ggwp_candidate_sets.conditions_json', count(*) FROM deerflow.ggwp_candidate_sets WHERE conditions_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 4, 'ggwp_selections.snapshot_json', count(*) FROM deerflow.ggwp_selections WHERE snapshot_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 5, 'ggwp_selection_commands.receipt_json', count(*) FROM deerflow.ggwp_selection_commands WHERE receipt_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 6, 'ggwp_answer_checks.notes_json', count(*) FROM deerflow.ggwp_answer_checks WHERE notes_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 7, 'ggwp_import_batches.validation_json', count(*) FROM deerflow.ggwp_import_batches WHERE validation_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 8, 'ggwp_knowledge_versions.metadata_json', count(*) FROM deerflow.ggwp_knowledge_versions WHERE metadata_json::jsonb::text ~* :'pan'
  UNION ALL SELECT 9, 'ggwp_knowledge_versions.title', count(*) FROM deerflow.ggwp_knowledge_versions WHERE title ~* :'pan'
  UNION ALL SELECT 10, 'ggwp_knowledge_versions.text', count(*) FROM deerflow.ggwp_knowledge_versions WHERE text ~* :'pan'
  UNION ALL SELECT 11, 'ggwp_knowledge_versions.source_ref', count(*) FROM deerflow.ggwp_knowledge_versions WHERE source_ref ~* :'pan'
) AS located ORDER BY n;
-- 线程的 DELETE 接口会删掉下面每张表里这个线程的行（runs 只删 operation_kind = 'run' 的）和属主目录下的线程目录，
-- 但只认 threads_meta 里记的属主：没有 threads_meta 行、或者属主账号已经不存在时，接口删不掉；这一行没有属主时，
-- 任何登录用户都能删
WITH hits (thread_id, found_in) AS (
            SELECT d.thread_id, '.tool-results' FROM regexp_split_to_table(:'disk_threads', '[,[:space:]]+') AS d(thread_id) WHERE d.thread_id <> ''
  UNION ALL SELECT thread_id, 'checkpoints' FROM deerflow.checkpoints WHERE checkpoint::text ~* :'pan' OR metadata::text ~* :'pan'
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
