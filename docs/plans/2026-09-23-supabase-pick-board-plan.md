# 选剧资料全量镜像与工作台迁 Supabase 实施方案

> 执行方式：按阶段、按任务实施。每个影响行为的任务先写失败的测试，再实现，跑通该任务测试和整套回归后再进入下一项。本文件是 2026-09-23 的实施计划，两个仓库的代码都还没有改。它综合了三份候选方案和两份评审，取舍理由见第 0 节；之后又按一轮完整性评审修订，改动与驳回理由见第 12 节。开工前的用户决定（第 13 节）已并入正文；gpt-6-astra 审计经逐条核实后的处置见第 14 节。

**Goal:** 两件事，分阶段上线：
1. 把整个工作台迁到一个新的专用 Supabase Postgres 项目。范围包括 DeerFlow 宿主的用户、会话、运行、checkpoint/store，以及选剧扩展。
2. 让「选剧资料」页（`/workspace/pick-data`）完整复刻 RealShort 旧选剧台的全部 tab。页面还要能切到智能体当时用的那一版数据，用来核对它的回答。

**Architecture:** RealShort 新增一组只读导出接口（feed v2）。Railway gateway 在现有的进程内定时任务里拉取，写进 Supabase 上的镜像：
- 每个版本一个不可变 schema，表名、列名与 RealShort 一致，敏感列根本不建。
- 镜像版本与智能体用的 v1 批次在同一个事务里发布。镜像这一侧失败时自动降级为只发布智能体批次，资料页显示「落后」横幅（2.5 第 1 条、5.5）。

资料页是 Vercel 上的服务端组件，用只读数据库角色读指定版本。SQL 和组件基本照搬 RealShort。

**Tech Stack:**
- 数据库：Supabase Postgres 17（实测 17.6），Pro 套餐，compute 选 Micro（用户决定，13.1；`max_connections=60`），Supavisor Pool Size 与两个业务角色的 `CONNECTION LIMIT` 都是 20（6.4），区域 East US（North Virginia），即 `us-east-1`。
- 连接池：Supavisor。Railway 用 session 模式（端口 5432），Vercel 用 transaction 模式（端口 6543）。
- 后端：DeerFlow 2.1 宿主加 `ggwork_pick` 扩展（Python 3.12、SQLAlchemy/asyncpg、psycopg、Alembic）。
- 前端：Next.js 16 服务端组件，数据库访问用 `pg` 加 drizzle-orm 的 `sql` 模板。
- 数据源：RealShort（Next.js、drizzle、Neon）。

**Status:** 已批准开工 P0 与 P1（2026-09-23）。第 13 节记录开工前的用户决定，正文已按它统一修改（仍以第 13 节为准）；第 14 节记录 gpt-6-astra 审计（2026-09-23）的核实结论与改动位置，采纳的条目已并入正文与各任务的测试和验收。

**路径约定：**
- 以 `rs:` 开头的路径相对 RealShort 本地检出 `/Users/wzb/Code/realshort-pick-feed-fixes`。这份检出停在 `fix/pick-feed-reelshort-dates` 分支（7dd9908），内容与 origin/main `9fc159c` 逐字相同，已含 PR #66。P1 的分支必须从 `origin/main` 新开，不在这个分支上继续。
- 其余路径相对 `/Users/wzb/Code/ggwork-deerflow`。
- 本文件里所有行号都以 `9fc159c` 为准。

## 0. 方案取舍

**两份评审的结论不同：**
- 评审 1 从鉴权、凭据、连接池角度出发，推荐方案 2 的拓扑：查询全放在 gateway，SQL 改写成 Python，Vercel 不持有数据库凭据。它同时写明，如果用户更看重复用 RealShort 的 TS 代码，方案 1 在修掉三处问题后是合理的备选。
- 评审 2 从还原度、复用角度出发，推荐方案 1。按方案 1，RealShort 的 SQL 文本、drizzle 片段、服务端组件、GET 表单和 `view()` 分发几乎不改就能跑，移植的代码也能直接和 RealShort 做 diff。

**本计划以方案 1 为底。** 用户已定「尽量复用 RealShort 代码」（决策 3）。如果把约 1.7k 行 TS 查询改写成 Python，就多了一份要手工同步的实现，RealShort 以后每改一次口径都要人工跟进。

**接受的代价：** Vercel 上多一个只读数据库凭据。约束见 2.4：只能读镜像 schema，默认只读事务，有语句超时和连接数上限，只配 Production。

**评审 1 要求修掉的三处，本计划都已处理：**
1. Railway 连接串不带 `sslmode`，改设环境变量 `PGSSLMODE=require`（6.3）。
2. 两类 pooler 用户的连接数统一预算（6.4）。
3. Railway 切到 Supabase 之前，先从本机建好管理员（6.8）。

**从各方案和评审采纳的做法：**

| 来源 | 采纳的做法 | 位置 |
|---|---|---|
| 方案 1 | 每版一个不可变 schema `pickm_vNNNNNN`，清理用 `DROP SCHEMA` | 3 |
| 方案 1 | 智能体继续用 v1，钉在同一 as_of 与 fingerprint 上，与镜像同事务配对发布；镜像失败时自动降级（评审修订） | 2.5、5 |
| 方案 1 | 显式的 `PICK_DB_BACKEND` | 6.5 |
| 方案 1 | 90 天曲线按剧存数组 | 3.2、5.6 |
| 方案 1 | `asOf` 参数，默认 `now()` | 4.2 |
| 方案 1 | 资料页所有链接带 `v` | 2.5、7 |
| 方案 2 | `GRANT CREATE ON DATABASE` | 3.1 |
| 方案 2 | 共享连接串不带 SSL 参数 | 6.3 |
| 方案 2 | 审计 VARCHAR 长度、排序规则、孤立代理项 | 6.7 |
| 方案 2 | 智能体批次的 LRU 缓存；同步用 `pg_try_advisory_lock` | 6.7、5.2 |
| 方案 2 | Postgres CI | P0-1 |
| 方案 2 | 按智能体语义精确回放结果（`/api/pick/replay` 加 `excluded_json`） | 2.5、P2-8、P4-2 |
| 方案 3 | 本机先建管理员，再切 Railway | 6.8 |
| 方案 3 | 自由文本里的网盘信息清洗，加正则闸门 | 4.6、5.3 |
| 方案 3 | 剧单导入忙标记 `pick_catalog` | 4.3 |
| 方案 3 | 控制总数由 RealShort 自己的函数算 | 4.5、5.3 |
| 方案 3 | SQLite 用 `.backup` 备份并记 sha256 | 6.9 |
| 方案 3 | 数据库体积闸门 | 5.2 |
| 评审 2 | 页面时间冻结到版本的 `as_of` | 2.5 |
| 评审 2 | 三处 `FROM dramas` 改读含非正典 id 的 `rs_ids` | 3.3、7.3 |
| 评审 2 | 核对两边的 `lc_collate` | 10 |
| 评审 2 | 在页面上写明与旧页的有意差异 | 7.4 |

## 1. 目标与已定决策

以下是用户已定的决策，不再讨论：
1. 整个工作台迁到一个新的专用 Supabase 项目，与 RealShort 的 Neon 没有任何重叠。区域选 East US（North Virginia），与 Railway 的 us-east4 同在弗吉尼亚。
2. 从零开始：
   - 不迁移 SQLite 数据，旧 SQLite 文件留在 Railway 卷上作备份。
   - 重建管理员账号，共享剧库重新同步。
3. 一次建完整镜像。选剧资料页要完整复刻旧选剧台的所有 tab：
   - 选剧；
   - 全部剧库：约 7.36 万行，含已下架；
   - 榜单：含名次历史小图和 ReelShort 数值榜；
   - 发布记录：约 180 条，含单条记录页；
   - 剧场规则：含术语表；
   - 证据页。

   页面现有的内容（RealShort 同步状态、导入记录、手动导入）变成其中一个子 tab。入口仍在左侧栏，路由 `/workspace/pick-data`。尽量复用 RealShort 的本地代码。
4. 同步范围：
   - 可以同步：发布记录的推荐人、推荐理由和飞书记录 id；ReelShort 数值指标（30 天指标、7 天出站点击、推广人数、搜索展示、增量）；已下架行和全量剧库。
   - 开工前另定也同步（13.3）：订单笔数与账号台账（rs_bill 订单数、对账表的订单笔数与同日出站、`catalog_accounts` 的地址与粉丝数）；`has_pan` 布尔（只说有没有网盘）；由分成 USD 推出的名次 `bill_rank`（只有序号，没有金额）。
   - 永不同步：网盘链接 `pan_url`、提取码 `pan_pw`、分成金额（`cps_bill_daily.promotion_value`、分成 USD、分成对账里的金额）。这是按字段的禁令：自由文本只按网盘模式清洗（4.6），不按金额语义清洗。
5. 数据只从 RealShort 流向工作台，走经过鉴权的只读 HTTP 导出接口（在 feed 上扩展）。工作台从不连接 RealShort 的数据库。

**同时遵守：**
- 问答抽屉不移植，工作台的对话替代它。
- 密钥不进 git。Vercel 环境变量先写进临时文件再重定向输入，不用管道。
- 「一次建完」指资料页一次上线全部 tab。工程上仍按阶段做，前面各阶段都能单独上线（第 8 节）。

**不在本期：**
- 多副本 gateway；
- 团队权限区分：所有登录用户都可读，与现在的共享批次相同；
- Supabase 的 Auth、Storage、Realtime；
- 把智能体的选剧引擎改成 SQL。

## 2. 架构

### 2.1 数据在哪

```text
RealShort（Vercel + Neon；生产库不共享）
  /api/pick-feed              v1：智能体候选池（新增可选 as_of 参数与 fingerprint 字段）
  /api/pick-feed/v2/<资源>    v2：镜像导出（新 token PICK_EXPORT_TOKEN，只 SELECT）
          | HTTPS + Bearer，只读，由工作台主动拉取
          v
Railway gateway（us-east4，1 个实例，/data 卷）
  ggwork_pick 进程内定时（03:40 / 15:40 UTC）：读 manifest，先拉同一 as_of 的 v1 并暂存，再拉 v2
          | Supavisor session 模式 :5432，角色 deerflow_app
          v
Supabase 项目 ggwork-workbench（Pro，us-east-1，Postgres 17）
  schema deerflow         宿主表 + LangGraph checkpoint/store + ggwp_* 扩展表
  schema pick_mirror      versions（版本控制行）、series（90 天曲线）、series_state
  schema pickm_v000001 …  每版一个；表名、列名同 RealShort；发布后只读
          ^ Supavisor transaction 模式 :6543，角色 pick_board_reader（只读）
          |
Vercel frontend（iad1）
  /workspace/pick-data 的服务端组件；其余 /api/* 照旧 rewrite 到 gateway
```

- Railway 的 `/data` 卷继续保存以下内容：
  - JWT 密钥文件（未设 `AUTH_JWT_SECRET` 时）；
  - `extensions_config.json` 和运行时 yaml；
  - 文件形式的 agent 与 memory；
  - 选剧原始导入的 blob；
  - 旧 SQLite 备份。
- 不用 `public` schema，并关闭 Data API（PostgREST）。这样 anon 和 authenticated 两个角色碰不到任何表。

### 2.2 查询在哪跑

| 读者 | 在哪跑 | 读什么 | 变化 |
|---|---|---|---|
| 智能体工具 | Railway gateway，`selection.py` | `ggwp_drama_versions` 里的 JSON 批次 | 查询逻辑不变。批次改为与镜像版本配对发布，另加 LRU 缓存 |
| 资料页的 6 个数据 tab | Vercel 服务端组件，代码在 `frontend/src/server/pick-board/` | 指定版本的 `pickm_vN` 和 `pick_mirror` | 新增 |
| 资料页的「同步与导入」子 tab | 浏览器 → `/api/pick/*` → gateway | 现有接口 | 现有组件去掉外框后搬进来 |
| 回放智能体结果 | 名单由 gateway 的 `GET /api/pick/replay` 算（Python，智能体语义）；行由 Vercel 从镜像读 | 智能体批次和镜像 | 新增 |

**资料页查镜像的方式：**
- 每个请求在服务端解析一次版本：默认是当前版本，URL 带 `?v=` 时用钉住的版本。
- 解析出的版本存进一个按请求隔离的容器：React `cache()` 返回的对象，页面在返回任何 JSX 之前写入一次。不用 AsyncLocalStorage：`withVersion(scope, () => view())` 只包住 `view()` 的同步调用，ListView 这类异步子组件要等 React 自己调度时才执行，那时已经在 `run()` 之外，读不到 store（见 7.5）。
- 每条 SQL 在自己的只读事务里执行：`BEGIN READ ONLY; SET LOCAL search_path TO pickm_v000123, pick_mirror; <查询>; COMMIT`。
- schema 名取自 `pick_mirror.versions`，并用 `^pickm_v\d{6}$` 校验，绝不从 URL 拼出来。
- 版本不可变，所以落在不同连接上的并行查询看到的也是同一份数据，不需要共享快照。

**RealShort 查询代码怎么复用：**
- 这些代码的写法都是 `getDb().execute(sql`...`)`。已核对：没有 `db.batch`，也没有事务。
- 移植后的 `getDb()` 返回一个只有 `execute` 方法的薄封装：先用 drizzle 的 `PgDialect` 把 `sql` 编译成 `{text, values}`，再按上面的方式在连接池里执行。
- 这样 SQL 文本不用改（见 7.5 的 `db.ts` 草图）。

### 2.3 UI 怎么搬

- RealShort 有 20 个非问答组件，都是「props 进、渲染出」，其中只有 `glossary.tsx` 和 `queyu-button.tsx` 是 client 组件。
  - 移植后它们仍是服务端组件。
  - GET 表单照旧可用。
- `page.tsx` 保留 `view()` 分发，以及 ListView、RankView、PostedView、PostedRecordView、DetailView、GrowthEmpty、EmptyState 这几个视图。在此基础上：
  - 外面套上 `WorkspaceContainer`；
  - 加鉴权、版本横幅和 `tab=imports`。
- 必须改的地方：
  1. 链接前缀：`/admin/pick` 改为 `/workspace/pick-data`。
  2. 所有链接和 GET 表单都带上 `v`。
  3. `<Link>` 一律加 `prefetch={false}`。否则一屏几百个链接，每个都会触发一次服务端渲染和一次 `/auth/me`。
  4. 网盘单元格只按 `has_pan` 显示「有网盘 / 无网盘」，不显示链接和提取码；删除所有 USD 列和 USD 区块；分成排序按导出的名次 `bill_rank`（13.3）。
  5. 剧场规则和术语表改从版本数据渲染。
- 颜色 token 用一份 `pick-board.css` 映射到工作台的主题变量，组件里的 class 不改。

### 2.4 鉴权边界与凭据

**浏览器 → 资料页**

- 页面自己在任何查询之前调用 `getServerSideUser()`（用 React 的 `cache()` 包过），不只依赖 workspace layout。原因有两个：
  - 客户端导航和手工构造的 RSC 请求不会重跑 layout；
  - `gateway_unavailable` 时 layout 仍会渲染子页面。
- 按返回的 `AuthResult.tag` 穷举处理（`frontend/src/core/auth/server.ts`）：
  - `authenticated`：再看用户 id，是 `default`（`AUTH_DISABLED_USER`）或 `static-website-user`（`STATIC_WEBSITE_USER`）就拒绝，不看任何环境变量；其余才查库。原因是 `isAuthDisabledMode()` 只认 `DEER_FLOW_ENV`/`ENVIRONMENT`（`auth-disabled-user.ts:11-24`），Vercel 生产不一定设这两个变量；
  - `unauthenticated`：跳转到 `/login`；
  - `needs_setup`：跳转到 `/setup`，不查库；
  - `system_setup_required`：跳转到 `/setup`，不查库；
  - `gateway_unavailable`、`config_error`：只显示提示，不查库。
- 所有登录用户都能读，与共享批次相同，没有角色区分。

**Vercel → Supabase**

- 用角色 `pick_board_reader`：
  - 只对 `pick_mirror` 和已发布的 `pickm_v*` 有 USAGE 和 SELECT，对 `deerflow` 和 `public` 没有任何权限；
  - `default_transaction_read_only=on`、`statement_timeout=8s`、`idle_in_transaction_session_timeout=15s`、`CONNECTION LIMIT 20`（等于 Supavisor 的 Pool Size 20，见 6.4 与 13.1：角色上限低于池大小时，并发一高 Supavisor 去开超出角色上限的服务端连接会直接报错，而不是排队）。
- 连接串 `PICK_MIRROR_READER_URL` 只配在 Vercel Production。Preview 部署不配，页面显示「此部署未连接镜像库」。
- 即使泄露，影响面也只是只读的镜像数据：里面没有用户数据，也没有禁止字段。

**Railway → Supabase**

- 用角色 `deerflow_app`。它拥有 `deerflow`、`pick_mirror` 和所有 `pickm_v*` 三类 schema，并对数据库有 CREATE 权限。
- 连接串只放在 Railway。

**Railway → RealShort**

- v1 继续用现有的 `PICK_FEED_TOKEN`。
- v2 用新的 `PICK_EXPORT_TOKEN`，可以单独吊销，不影响智能体。

**回放接口**

- `GET /api/pick/replay` 走现有的 `repository(request)`：
  - 没有用户返回 401；
  - `default` 和 `system:shared` 两个身份被拒绝；
  - 结果按 owner 过滤。
- 资料页在服务端带上 `access_token` cookie 调用它。

**其他**

- JWT 密钥不放上 Vercel。
- Supabase 的 `postgres` 角色（不是超级用户，见 3.1）只在一次性 bootstrap 时使用。

### 2.5 与智能体用同一版数据

1. **同一次采集。**
   - 一次同步只有一个 `as_of`（当前时刻取整到分钟再减 2 分钟）和一个 fingerprint。v2 的各资源和 v1 候选池都在这个时点计算。每次等待 `source_busy` 或因漂移重来之后，as_of 都重新选取（5.2 第 3 步）。
   - v1 传了 `as_of` 时，响应的 `capturedAt` 就等于 as_of（4.7），所以新建批次的 `source_as_of` 等于版本的 `as_of`。去重让几个版本共用一个批次时，发布事务把批次的 `source_as_of` 改写成本次的（3.5），所以准确的说法是：批次的 `source_as_of` 等于最近一次配对版本的 `as_of`。已经生成的结果不跟着变，用查询当时冻结的 `data_as_of_json`（第 5、9 条）。
   - v1 批次先以 `importing` 状态写入，两边都通过时与镜像版本在同一个事务里发布。
   - **镜像失败时自动降级（用户已定，13.2）：** v2 拉取、镜像写入、镜像闸门、一致性闸门任何一处失败，或镜像阶段用完自己的时限（5.1），镜像版本判失败并删掉；v1 批次只要自己的闸门（5.3 表里「作用于」为 v1 或两边的几行，加上现有 v1 流程的行数核对）通过，就单独发布。版本行不动，同时记连续失败次数（5.5）。降级发布的是新批次时，`/api/pick/sync` 的 `mirror.behind` 为真，资料页横幅显示「资料页落后于智能体：镜像 vN 采集于 …，智能体数据采集于 …」；v1 去重复用的恰好是当前版本配对的那一对批次时，智能体的数据没有变，`behind` 仍为假。v1 自己的闸门失败时两边都不发布。
   - **配对关系只记在 `pick_mirror.versions`**（`agent_catalog_batch_id`、`agent_knowledge_batch_id`）。批次侧不再另记 `validation_json.mirror_version`：推迟的 `_reuse` 写入会用新 meta 覆盖 `validation_json`，两处记录会互相矛盾。以下三个口径都从版本表推出：
     - 当前版本：状态为 published、`published_at` 最新的一行（3.2）；
     - `behind`：当前版本的 (`agent_catalog_batch_id`, `agent_knowledge_batch_id`) 不等于当前共享的 (剧库批次, 知识批次)；
     - 钉住的镜像版本：当前版本，且它的两个批次 id 分别等于钉住的剧库批次和知识批次；不相等时为空（第 5 条）。
   - 内容去重可能让几个版本共用一个批次。这是允许的，因为它们的候选池相同。
2. **发布前的一致性闸门。** 以下任何一条不满足，镜像版本不发布（智能体批次按上一条降级处理）：
   - row_key 集合相等：镜像里 `(catalog_rows ∪ rs_rows) WHERE has_signal AND off_on IS NULL` 的 row_key 集合，必须与 v1 行的 `source_id`（base64url 解码后）集合完全相等。
   - 每行的信号种类集合一致。
   - 每行对上的发布记录 sd 集合一致。

   这个过滤条件就是资料页选剧 tab 的默认条件：`w`、`inuse`、`off` 默认都关，已在 `rs:src/lib/pick/queries.ts` 的 `filtersFor` 核对。所以智能体的候选范围等于运营默认看到的选剧 tab。

   页面另外显示「智能体候选池 N」，并注明它不等于 tab 徽标：徽标沿用 RealShort 的 withSignal + rsCandidates，含已下架的行。
3. **规则用同一份。**
   - 剧场规则、术语表、依据标签、日期含义随 manifest 的 `rules` 进入版本。
   - 同一次采集的 v1 规则 Markdown 成为智能体的知识批次。
   - 剧场规则 tab 和术语表都按版本数据渲染。
4. **结果能精确回放。** 智能体的筛选语义和资料页不同，条件无法一一翻译：
   - 智能体的 `query` 匹配剧名加标签；资料页的 `q` 匹配 title/title_cn，或精确匹配 row_key/drama_id。
   - `tags`、`posted_account`、`channel` 加 `confirmed_eligible_only` 在资料页没有对应的筛选。
   - 两边的排序也不同。

   所以回放不翻译条件，做法是：
   - gateway 的 `GET /api/pick/replay?result_id=` 在同一个批次上用 `selection.matching_rows` 重跑。排除集合取结果里保存的 `excluded_json`。
     - 返回内容：完整的有序 identity 列表；前 `limit` 个（也就是卡片上展示的）；配对的镜像版本；不能映射成资料页筛选的条件清单。
     - 旧结果没有 `excluded_json` 时，注明「排除已选无法复现」。
   - 资料页 `tab=pick&result=<id>&v=<N>` 按这个顺序从 `pickm_vN` 取行，用同一个 RowsTable 渲染，并高亮前 `limit` 个。
   - **vN 已被清理时的回退：** 名单与顺序仍按回放接口（它读批次，批次保留 30 天内被引用的）；行改从当前版本 vM 按 row_key 取，横幅写「镜像 vN 已清理：名单与顺序按智能体当时的批次，行数据取自当前版本 vM，可能与当时不同」。当前版本里已经没有的 row_key 逐条列出「当前版本已无此行」。结果本身没有配对版本（降级发布时的结果）也走这条回退。
   - 页面另给一个「近似筛选」链接，映射关系是：
     - 剧场显示名 → platform 键：按版本 `meta.rules` 里的 PLATFORM_RULES 反查 `name`；
     - 语种代码 → lang：按 `LANG_LOC` 反查中文名（`rows.lang` 存的是「英语」这类中文名，feed 输出时才转成代码）；
     - signal_kind → basis，query → q，exclude_posted → posted=no。
     - 反查不到的值和无法表达的条件逐条列出。
5. **深链。**
   - 候选卡和工具卡加「在选剧资料核对」链接：`/workspace/pick-data?tab=row&row=<row_key>&v=<N>`。row_key 由 identity 里的 source_id 解码得到。
   - 版本 N 在查询当时就存下来，不事后按时间反推：运行开始钉住批次时（`context.py:68-71` 与 `tools.py::_pin_latest`），在同一次读取里取当前镜像版本，按第 1 条的口径决定钉住的版本号（配不上就为空）；同时算出这一刻的 `data_as_of`：有钉住版本时 `source_as_of` 取 `pick_mirror.versions.as_of`、新鲜度取该版本的 meta，没有时取批次。
   - 镜像版本与剧库、知识批次一起由 `SelectionService._scope` 选定。实现上把 `pinned_versions` 以及 `_parent_versions`/`_current_versions` 的返回值扩成 (catalog, knowledge, mirror) 三元组：
     - 实际沿用父结果的数据版本时（`selection.py:214` 的 `derived and not use_latest`），取父结果的 `mirror_version` 和 `data_as_of_json`；
     - 否则取本轮钉住的镜像版本和 data_as_of。「换一批」同时带 `use_latest`，或本轮更早的调用已经 `_pin_latest` 过（`versions_refreshed` 为真，`tools.py:88` 把它当作 `use_latest` 传下去），走的都是这一支：批次是本轮的新批次，镜像版本也必须是本轮的，不能沿用父结果的。
   - `SelectionService.query` 把两者写进 `ggwp_candidate_sets.mirror_version` 和 `data_as_of_json`（都由 0005 新增）。
   - `count` 不保存结果行（`selection.py:264-278`），它的版本同样由 `_scope` 决定：派生统计（exclude_previous；count 固定 `use_latest=False`，`selection.py:266`）取父结果的，独立统计取本轮钉住的。`detail` 取所属结果行上的。
   - 结果级的链接指向回放视图。
6. **版本横幅。**
   - `v` 不是当前版本时显示「正在看智能体当时用的版本 vN（采集于 …），当前最新 vM」，并给出切换链接。
   - vN 已被清理时显示「该版本已清理，已显示当前版本 vM」。
   - 页内所有链接和 GET 表单都带上解析后的 `v`。这样翻页、筛选途中即使发布了新版本，也不会串到另一个版本。
7. **时间冻结。**
   - 页面把版本的 `as_of` 当作 `now` 传给组件：Sources、ReelshortTable 和详情页。
   - 改写后的 rs 查询里，上线分桶等计算用 `as_of`，不用 SQL 的 `now()`。
   - 页头写「截至 as_of」。
   - 7 天出站、1/7/15 天增量、账单窗口这些都由 RealShort 在导出时按 `as_of` 算好。
8. **个人导入。**
   - 用户自己导入的剧库仍会遮住共享批次，这是现有行为。
   - 资料页通过 gateway 的 `/api/pick/sync` 判断当前批次是不是共享批次。如果不是，页头警告「智能体当前用的是你手动导入的剧库，不在本页」，并链接到 `tab=imports`。
9. **新鲜度一行。** 格式为：剧单导入于 catalogImportedAt · ReelShort 指标采集 rsSyncedAt · 镜像 vN 采集于 as_of · 曲线截至 min(latest_snapshot, series_state.through)。前三项来自版本 meta，所以资料页和智能体的 `data_as_of` 指向同一次采集。曲线折叠在发布之后才跑，可能失败或落后，所以曲线日期要取两者较早的一个，而不是直接写 latest_snapshot。
   - 智能体一侧，结果的 `data_as_of` 以查询当时冻结的 `data_as_of_json` 为准（第 5 条）。`status_view`（`routes.py:78`，覆盖列表、单结果和保存前的检查）、query 与 detail 工具（`tools.py:95`、`:141`）和回放接口都先读冻结值，为空时（P2 之前的结果）回退到批次；count（`tools.py:116`）的 data_as_of 跟它的版本走：派生统计取父结果冻结的，独立统计取本轮钉住的快照。不这样做的话，批次被后来的版本去重复用、`source_as_of` 被改写之后（`repository.py:178-181`），旧卡会显示新的时点和新鲜度，链接打开的却是旧版本。
   - 输出的键仍是 `DATA_AS_OF_KEYS`，P4-1 起加可选的 `mirror_version`。不停止改写批次的 `source_as_of`：那样新卡会显示旧时点。

## 3. 数据模型

### 3.1 Supabase 角色与 schema

bootstrap 只执行一次，以 postgres 身份运行。脚本放在 `docs/pick-workbench/supabase/bootstrap.sql`，不含密码。

Supabase 的 `postgres` 不是超级用户，只有 CREATEROLE 等属性（真正的超级用户是 `supabase_admin`）。PG16 起，CREATEROLE 用户建出的角色默认只给创建者 ADMIN OPTION，不给 SET 权限，而 `CREATE SCHEMA … AUTHORIZATION` 要求能 SET ROLE 到属主。所以脚本在建 schema 之前先给 postgres 补上 SET 权限。

**2026-09-23 在正式项目上的实测**（PostgreSQL 17.6，结果记在 scratchpad 的 `supabase-facts.md`，P0-4 时抄进运行手册）：`postgres` 的 rolsuper=f、rolcreaterole=t；它是 `postgres` 库的属主（datdba=postgres），`has_database_privilege(CREATE)` 为真；在一个回滚掉的事务里，CREATE SCHEMA、`CREATE ROLE … LOGIN NOINHERIT CONNECTION LIMIT 20`、`GRANT CREATE ON DATABASE`、`ALTER ROLE … SET`、`GRANT <role> TO current_user WITH INHERIT FALSE, SET TRUE` 都成功。所以 10.2 第 10 条已关闭。

**授权必须在 SET ROLE 之下做（审计 host-1，已在本机 PG 17.10 上复现）：** 以 `INHERIT FALSE` 授予之后，postgres 不继承 deerflow_app 的任何权限，也就不能对 deerflow_app 名下的 schema 做 GRANT/REVOKE。按原脚本，替身环境里 `REVOKE ALL ON SCHEMA deerflow, pick_mirror FROM PUBLIC` 直接报 `permission denied for schema deerflow`，后面的 `ALTER ROLE` 一句都没执行，reader 既没有 read_only 也没有 statement_timeout；如果 postgres 另有 `pg_read_all_data`/`pg_write_all_data`（据称 Supabase 会给，本项目未核实），脚本反而 exit 0，只打印 `WARNING: no privileges were granted for "pick_mirror"`，reader 的 USAGE 悄悄缺失（psql 这时照样打印 `GRANT`）。实测里「GRANT USAGE/SELECT 成功」只说明语句没报错，不说明授权生效。修法是建完 schema 后 `SET ROLE deerflow_app`，以属主身份做两句 REVOKE 和 reader 的 USAGE，再 `RESET ROLE` 回到 postgres 做 `ALTER ROLE`（postgres 对两个角色有 ADMIN OPTION）。本机复现里改后脚本干净通过，`\dn+` 显示 `pick_board_reader=U/deerflow_app`。

```sql
\set ON_ERROR_STOP on
CREATE ROLE deerflow_app LOGIN NOINHERIT CONNECTION LIMIT 20;
CREATE ROLE pick_board_reader LOGIN NOINHERIT CONNECTION LIMIT 20;
-- 两个上限都等于 Supavisor 的 Pool Size 20（13.1、6.4）
-- 密码不写进文件：在 psql 里执行 \password deerflow_app 与 \password pick_board_reader
-- PG16+：CREATEROLE 建出的角色只给创建者 ADMIN，没有 SET；AUTHORIZATION 需要 SET
-- 在 Supabase 上 CURRENT_USER 就是 postgres；写成 CURRENT_USER 是为了 P0-4 能用替身角色跑同一份脚本
GRANT deerflow_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
GRANT CONNECT, CREATE ON DATABASE postgres TO deerflow_app;
GRANT CONNECT ON DATABASE postgres TO pick_board_reader;
-- 库属主不是 postgres 且没有 grant option 时，上一句 GRANT 只报 WARNING、不授权；这里改成硬失败
DO $$ BEGIN
  IF NOT has_database_privilege('deerflow_app', 'postgres', 'CREATE') THEN
    RAISE EXCEPTION 'deerflow_app 没有拿到 postgres 库的 CREATE 权限，停下来按 10.2 第 10 条处理';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS deerflow AUTHORIZATION deerflow_app;
CREATE SCHEMA IF NOT EXISTS pick_mirror AUTHORIZATION deerflow_app;
-- INHERIT FALSE：postgres 不继承 deerflow_app 的权限，对它名下的 schema 做 GRANT/REVOKE 必须先 SET ROLE；
-- 否则报 permission denied，或者（postgres 另有 pg_read_all_data 等时）只报 WARNING、授权悄悄缺失
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
```

- `CREATE ON DATABASE` 是必需的，原因有两个：
  - DeerFlow 启动时，engine（`backend/packages/harness/deerflow/persistence/engine.py`）和 checkpointer 都会执行 `CREATE SCHEMA IF NOT EXISTS`。PG 先检查数据库的 CREATE 权限，再判断 schema 是否已存在，所以即使 schema 已经建好也需要这个权限。
  - writer 要建 `pickm_v*`。
- deerflow_app 不设角色级的 `statement_timeout`。宿主启动迁移时要等 `pg_advisory_lock`；ORM 自己已经有 `command_timeout=30`，它作用于 ORM 池里的每条语句。镜像写入（COPY、建索引、ANALYZE、闸门扫描、曲线 upsert）因此不走 ORM 池，改用一条独立的 asyncpg 连接，超时逐条显式指定（5.2 第 1 步）。
- 角色默认的 `search_path` 是兜底：万一 Supavisor 丢掉 asyncpg 的 `server_settings` 或 libpq 的 `options=-c search_path`，表仍然落在 deerflow。
- `pick_mirror` 里的表由扩展迁移 0006 建（见 3.2）。对 reader 的授权也在 0006 里做，只在角色存在时执行。

### 3.2 `pick_mirror`（不分版本）

`pick_mirror.versions` 每个镜像版本一行：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | bigserial PK | 即 URL 里的 `v` |
| `schema_name` | text UNIQUE NOT NULL | `pickm_v%06d`，只由 id 生成 |
| `status` | text CHECK IN (`building`,`published`,`failed`,`dropped`) | |
| `as_of` | timestamptz NOT NULL | 采集时点 |
| `fingerprint` | jsonb | 见 4.3 |
| `counts` | jsonb | 各表行数 |
| `latest_snapshot` | date | 这一版对应的曲线最新日 |
| `agent_catalog_batch_id`, `agent_knowledge_batch_id` | text | 配对的 v1 批次 |
| `sync_run_id` | text | 对应的 `ggwp_sync_runs.id` |
| `created_at`, `published_at`, `superseded_at`, `dropped_at` | timestamptz | |
| `error` | text | 安全错误文本，规则同 `sync.py::_safe_error` |

- 索引：`(status, published_at DESC)` 和 `(agent_catalog_batch_id, published_at)`。
- 当前版本 = 状态为 published、`published_at` 最新的那一行。这和 ggwp 的 `current_batch` 是同一条规则。

另外三张表：
- `pick_mirror.series`：`drama_id text PK`、`days date[]`、`revenue_cents float8[]`、`promoters int[]`、`updated_at`。
  - 每个有 metrics_valid 快照的 drama_id 一行，**不按导出当天是否正典过滤**。RealShort 的 `loadDramaDetail` 是在渲染时先把 id 解析成当前正典，再按 `drama_observations.drama_id = 正典 id` 取 90 天（`rs:src/lib/observe/queries.ts:849-874`）。如果导出时只收当天的正典行，某部剧后来才成为正典，它更早的点在镜像里就缺了。资料页同样先用版本的 `rs_ids` 解析正典，再按 id 读这张表，与 RealShort 逐点一致。
  - 行数和体积以首次回填实测为准，估 40–100 MB。
  - 只收 metrics_valid 的点，与 `loadDramaDetail` 的曲线查询口径相同。
  - 保留窗口按仍在保留的版本算，不再固定 93 天。折叠（5.2 第 11 步，排在第 10 步保留清理之后）的截断点 cutoff = min(第 10 步之后仍为 published 的各版本 `as_of` 的 UTC 日期) − 90 天，且不晚于 `through` − 92 天（至少留 93 天）。版本数上限 10 个（3.6），数组长度因此有界。截断点记进 `series_state.trimmed_before`。原先固定的 93 天比 3.6 的 7 天引用保留短：一个被「换一批」续上引用的旧版本 schema 还在，它窗口开头的几天却已被截掉，曲线悄悄变短，也不会出「已清理」横幅。
  - 读取时用 `unnest(days, revenue_cents, promoters)` 展开成逐日的行，`day` 转成 `YYYY-MM-DD` 文本，行形状与 RealShort 的曲线查询相同，不依赖数组类型解析（7.5）。
- `pick_mirror.series_state`：`id smallint PK CHECK (id = 1)`、`through date`、`trimmed_before date`（最近一次折叠的截断点，此前的点已不在 series 里）、`updated_at`。
- `pick_mirror.control`：`id smallint PK CHECK (id = 1)`、`accept_empty_once boolean NOT NULL DEFAULT false`、`accept_empty_set_at timestamptz`、`consecutive_failures int NOT NULL DEFAULT 0`、`last_failure_at timestamptz`；实现时另加了 `last_failure text`、`lock_holder_since timestamptz`、`lock_holder text`（只收 sync、backfill、cleanup），以迁移 0006 为准（P2 实现说明终版 P2-0、P2-1）。「放行一次空表」的标记放在库里，由发布事务消费后清掉（5.3），不再用环境变量。

**曲线怎么保持与版本一致：** 已写入的日子不会再变（RealShort 写快照用 `ON CONFLICT DO NOTHING`），但快照历史不是只追加：RealShort 会删掉 90 天以前的观测（`rs:src/lib/observe/snapshot.ts:23` 的 `OBSERVATION_RETENTION_DAYS=90`，`:96-104`），镜像截掉的点无法再回填，所以截断点必须按上面的规则留足。
- 资料页读曲线的窗口与 RealShort 对齐：`day >= (版本.as_of AT TIME ZONE 'UTC')::date - 90 AND day <= 版本.latest_snapshot`。起点等于 `utcDayOffset(90, asOf)`，共 91 个日期，与 RealShort 的 `observed_on >= utcDayOffset(SERIES_DAYS=90)`（`rs:src/lib/observe/queries.ts:873`、`rs:src/lib/observe/metrics.ts:28`）一致。原稿的 `day > latest_snapshot - 90` 只有 90 个日期，当前版本也少一个点。
- 窗口起点早于 `series_state.trimmed_before` 时，证据页曲线上方写「早于 X 的曲线点已清理」，不让曲线悄悄变短。
- 折叠还没追上时，曲线只到 `series_state.through`，页头按 2.5 第 9 条写较早的那个日期。

### 3.3 每版一个 schema：`pickm_vNNNNNN`

- DDL 模板是 `customizations/pick-workbench/ggwork_pick/mirror/ddl.sql`，改写自 `rs:scripts/sql/catalog-tables.sql` 和 `rs:scripts/sql/catalog-p1.sql`。
- schema 名只由 `versions.id` 生成，经正则校验后才替换进模板。
- 日期类的列沿用 RealShort 的 `YYYY-MM-DD` 文本格式，比较和 ORDER BY 的写法照搬不变。

| 表 | 主键 | 列 | 明确不存在的列 | 索引 | 行数 |
|---|---|---|---|---|---|
| `catalog_rows` | `row_key` | platform, source_table, title, title_cn, lang, kind, origin, tags, listed_on, episodes, pay_start, youtube, merged_rows, off_on, reoff_note（已清洗）, title_key, in_site_ids text[], legacy_only, site_other, has_signal, latest_evidence_on, imported_at, has_pan boolean NOT NULL（13.3：RealShort 导出时由 `pan_url ~* '^https?://'` 算出，与旧页「网盘 ↗ / 无网盘」的判断相同，4.4） | `pan_url`、`pan_pw`、`creator`（旧页不显示） | (platform, lang)、(has_signal, latest_evidence_on)、(title_key)，与 RealShort 相同 | 约 4.17 万（2026-09-22 feed 报 41,661） |
| `catalog_signals` | (row_key, kind, ord) | evidence_on, rank, grade, note, payload jsonb（按键白名单：`d`、`w`、`weeks`、`best`、`days`、`first`、`h`、`qy`、`pid`；含名次历史 `h`，日榜的 `h[*][2]` 是逐日备注） | 白名单以外的 payload 键 | (kind, evidence_on) | 3–4 千 |
| `catalog_posted` | `sd` | 与 RealShort 相同：feishu_record, title, title_key, lang, platform, life, scheduled, online_on, why, note, archived, post_count, last_post_on, views_total, sources[], cats[], who[], accounts[], created_on, updated_on, first_post_on, metric_at, sched_count, views_count, posts jsonb, row_keys[], drama_ids[], imported_at | 无 | 主键 | 约 180 |
| `catalog_accounts` | `id` | 与 RealShort 相同：name, url, grp, form, niche, status, fans, as_of, imported_at | 无 | 主键 | 几十 |
| `rs_rows` | `row_key`（`'reelshort-'` 加 id） | 分三组，见表下说明 | 所有 `bill_usd*`、`book_promotion_link`、`app_promotion_link`、`promotion_code`、`group_key` | PK；UNIQUE(drama_id)；(has_signal, latest_evidence_on)；(locale)；(publish_at) | 约 3.2 万（待 manifest 确认） |
| `rs_ids` | `id` | canonical_id, locale, slug, title, chapter_count, pay_start, is_public_canonical | 其余 dramas 列 | (canonical_id) | dramas 全表行数，以 manifest 为准 |
| `rs_clicks14` | (drama_id, day) | human, bot。这是 (as_of − 14 天, as_of] 内的点击，按兄弟行汇总到正典 id。只收正典 id 在 `rs_rows` 里（即公开正典）的行：整组都不公开的剧，点击会汇总到一个不在 `rs_rows` 的 id 上，RealShort 的证据页也找不到它，导出时直接去掉 | 原始点击行（UA、referer、country） | 主键 | 1–3 万 |
| `rs_bill_orders` | (bill_date, book_id, promotion_type) | canonical_id, book_title, order_cnt（对 promotion_value 汇总）, source_rows int（组内 order_cnt > 0 的原始行数）, same_day_clicks（只数 `created_at <= as_of` 的点击，4.2）。先对原始行过滤 order_cnt > 0，再分组 | `revenue_usd`、`promotion_value` | (canonical_id, bill_date DESC) | 几百 |
| `meta` | `key` | value jsonb | 无 | 主键 | 约 10 |

**清洗覆盖所有自由文本：** 上表的文本值，包括 text[] 的每个元素和 jsonb 里的每个字符串叶子（`payload.h[*][2]`、`posts[].url`、`posts[].note`、`catalog_accounts.url`、`sources`/`cats`/`who`、title、title_cn、description 等），导出前都过一遍 `scrubPanText`（4.6）。只有一份固定的豁免清单不过清洗：标识与派生字段 `row_key`、`sd`、`feishu_record`、`id`、`drama_id`、`canonical_id`、`book_id`、`slug`、`title_key`、id 数组（`in_site_ids`、`row_keys`、`drama_ids`）、日期列，以及 v1 的 `source_id`、`detail_url`、`source_ref`。改写这些字段会破坏主键与链接（实测 row_key `goodshort-K10JEicNmxOWhQPwdg3zdw==` 就会被宽松的提取码正则命中，4.6）。豁免清单写在 `export-v2-map.ts`，新增字段默认要清洗。

**`has_pan` 与 `bill_rank` 为什么不算禁止字段（13.3）：** 前者只是一个布尔，后者只是整数名次；两者都由 RealShort 在自己的 SQL 里从禁止列算出，输出里不含任何网盘文本和金额。4.6 的 SQL 文本自检只为这两处派生按名字豁免。

**`rs_rows` 的三组列：**
- 与 `catalog_rows` 同形的列（`has_pan` 恒为 false：`reelshortBranch` 的 pan 列本来就是空字面量），外加七列：`rs_clk`、`rs_bill`、`rs_gsc`、`rs_clk_on`、`rs_bill_on`、`rs_gsc_on`、`drama_id`。
- `ObserveRow` 的列：locale, slug, publish_at, chapter_count, pay_start_raw, rr, promoters_cnt, metrics_valid, synced_at, search_impressions, search_data_at, detail_synced_at, tag_list text[], description。
- 快照与出站、账单的列：
  - baseline1_at、baseline7_at、baseline15_at；
  - 有效性过滤过的 rr1, p1, rr7, p7, rr15, p15；
  - 未过滤的原值 s1_rr, s1_p, s7_rr, s7_p，供排序用；
  - clicks7, last_click_on, bill_orders, last_bill_on；
  - `bill_rank int`（13.3）：按 RealShort 的 bill 排序口径（`b.usd DESC NULLS LAST`，`rs:src/lib/observe/queries.ts:258-259`）算出的名次，没有账单时为 NULL。只有序号，没有金额，算法见 4.4。

**`meta` 的 key：**
- `freshness`
- `rs_counts`
- `growth_baseline`（窗口 1 天和 7 天）
- `sources`
- `rules`
- `control`
- `fingerprint`
- `scrub_counts`
- `latest_snapshot`
- `source_revision`
- `warnings`：manifest 带来的告警，例如剧单导入失败或僵死（4.5）

**rs_rows 为什么同时存两组快照值：**
- `rs:src/lib/observe/queries.ts` 的 `orderBy` 对 d1、d7、dp1、dp7 四种排序用的是未过滤的 `s1.*` 和 `s7.*`。对应 `rs_rows` 的 s1_rr、s1_p、s7_rr、s7_p。
- 显示和 `comparableOnly` 用的是有效性过滤过的值。对应 rr1、p1、rr7、p7。
- 以 d1 为例，`comparableOnly` 等价于 `rr1 IS NOT NULL`：`rr1` 只在 s1 有效且本行 metrics_valid 时才非空。
- 所以两组都要存，排序才能与 RealShort 逐行一致。

**`rs_ids` 覆盖哪些查询：** 旧代码里有三处 `FROM dramas`：
- `rs:src/lib/pick/queries.ts:263`：改为读 meta。
- `rs:src/lib/pick/queries.ts:329`：剧单行 in_site_ids 的查找。
- `rs:src/lib/pick/queries-posted.ts:220`：发布记录 drama_ids 的查找。

后两处传进来的 id 可能是非正典 id，原 SQL 也不过滤公开状态。所以 `rs_ids` 收 dramas 全表的这几列，改成读它之后结果与原来逐字相同。ReelShort 证据页里「非正典 id → 正典」的解析也改用 `rs_ids.canonical_id`。

**构建顺序：**
1. COPY 数据；
2. 建索引；
3. 每张表执行 ANALYZE。新表没有统计信息，而 RealShort 的 `IN (子查询)` 执行计划依赖它；
4. 在发布事务里执行 GRANT（见 3.5）。

在此之前，reader 对构建中的 schema 没有 USAGE 权限，看不到它。

### 3.4 体积

| 表 | 每版约占（含索引） |
|---|---|
| catalog_rows | 25 MB |
| catalog_signals | 4 MB（最大一条 payload.h 约 2.5 KB） |
| catalog_posted + catalog_accounts | 1 MB |
| rs_rows | 30–35 MB（其中剧集简介约一半） |
| rs_ids | 4–6 MB |
| rs_clicks14 + rs_bill_orders + meta | 小于 3 MB |
| **合计** | **约 65–75 MB，首个版本出来后实测** |

**总量估算：**
- 常态保留 3–5 个版本，约 0.2–0.4 GB；达到 10 个版本上限时约 0.75 GB。
- 曲线约 50 MB。
- ggwp 的 JSON 批次沿用现有保留规则（最新 3 份，加 30 天内被引用的），最多约 0.4 GB。
- 宿主表和 checkpoint 另外增长。

Supabase Pro 含 8 GB 磁盘，够用。Free 套餐只有 500 MB，闲置还会暂停，不可用。

### 3.5 版本状态机与原子切换

状态流转：`building` 转为 `published` 或 `failed`，`published` 最终转为 `dropped`；`failed` 的版本会立即删掉它的 schema。

**发布事务：** 在 gateway 上执行，复用 `PickRepository._write()` 的 `pg_advisory_xact_lock`，与其它 ggwp 写入串行：

```sql
GRANT USAGE ON SCHEMA pickm_v000123 TO pick_board_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA pickm_v000123 TO pick_board_reader;
UPDATE ggwp_import_batches SET status = 'published', published_at = :t
  WHERE id IN (:catalog_batch_id, :knowledge_batch_id) AND status IN ('importing', 'published');
UPDATE pick_mirror.versions SET superseded_at = :t
  WHERE status = 'published' AND superseded_at IS NULL;
UPDATE pick_mirror.versions SET status = 'published', published_at = :t
  WHERE id = :version_id AND status = 'building';
```

- 两边用同一个 `:t`。`ggwp_import_batches.published_at` 是 ISO 字符串，与 `current_batch()` 的排序规则一致。
- 去重复用的是旧批次时，把它的 `published_at` 更新为 `:t`，它就重新成为当前批次。现有 `_reuse` 处理 A→B→A 用的也是这个做法。
- **stage 模式下 `_reuse` 的写入推迟到发布事务里。** 现有 `_reuse`（`repository.py:172`）一发现重复就立刻改旧批次的 `published_at`、`source_as_of`、`validation_json`。stage 模式下这些值只算出来、不写，由发布事务一并写入；否则智能体会在镜像发布之前就看到新的 `source_as_of`。
- 同一事务里消费 `pick_mirror.control.accept_empty_once`（用到了就置回 false），并把 `consecutive_failures` 清零。
- 事务提交之前，智能体和资料页都看不到新数据。提交之后，两边同时切换。
- 降级发布（2.5 第 1 条）只执行其中的 `ggwp_import_batches` 那一句，版本表不动，`consecutive_failures` 加 1。

### 3.6 保留

每次发布之后清理一次版本。保留的版本：
- 当前版本和它之前的 2 个已发布版本；
- 近 7 天内被候选卡引用过的版本：版本 id 出现在近 7 天的 `ggwp_candidate_sets.mirror_version` 里。按查询当时记下的版本号算，不按批次算：去重后很多版本共用一个批次，按批次算会把它们全部留下，经常顶到上限；
- 被取代（superseded）不满 1 小时的版本，防止正在翻页的人撞上 DROP；
- 总数上限 10 个，超出时从最旧的被引用版本开始清。

**清理方式：**
- 每个版本在自己的事务里执行：`BEGIN; SET LOCAL lock_timeout = '5s'; DROP SCHEMA pickm_vN CASCADE; UPDATE pick_mirror.versions SET status = 'dropped' …; COMMIT`。用 `SET LOCAL`，不用会话级 `SET`，否则设置会留在连接上。
- 拿不到锁就回滚跳过，下次再试。
- 用 DROP SCHEMA，就不会有 DELETE 留下的膨胀。

ggwp 批次的保留规则不变（最新 3 份，加 30 天内被引用的）。所以旧卡片的批次可能还在、镜像版本却已清理。这种情况下资料页显示「该版本已清理」；回放的名单与顺序仍然可用（它读批次），行数据按 2.5 第 4 条改从当前版本取并注明。

### 3.7 ggwp 表的变化

- **迁移 0005**（两种方言都执行）：
  - 给 `ggwp_candidate_sets` 加可空 JSON 列 `excluded_json`：查询时实际排除的 identity 列表，回放要用。
  - 给 `ggwp_candidate_sets` 加可空整数列 `mirror_version`：查询当时配对的镜像版本（2.5 第 5 条），加索引 `(mirror_version, created_at)` 供保留规则用。SQLite 上永远为空。
  - 给 `ggwp_candidate_sets` 加可空 JSON 列 `data_as_of_json`：查询当时冻结的 `data_as_of`（2.5 第 5、9 条）。不回填：PG 从零开始（第 1 节决定 2），P2 之前的结果为空，读取时回退到批次，保持现有行为。
  - 给 `ggwp_sync_runs` 加可空 JSON 列 `details_json`：记录镜像版本号、各阶段耗时、清洗计数、降级原因和告警。
  - `result_view`（`selection.py:29`）是按键白名单输出的，这几个新列都不作为键进它（`data_as_of_json` 只替换 `data_as_of` 的取值来源，键集合不变）；前端 `pickResultSchema` 是 `.strict()`（`frontend/src/core/pick/types.ts:99`），多一个键就整份拒收。P2-8 加合同测试守住。
- **迁移 0006**（只在 PG 执行；SQLite 上不做任何事）：
  - `CREATE SCHEMA IF NOT EXISTS pick_mirror`，建 3.2 的四张表；
  - 角色 `pick_board_reader` 存在时，授予它 USAGE 和 SELECT。
- **不改的部分：**
  - 迁移 0001–0004 保持不变。
  - `ggwp_batch_status` 约束已经允许 `importing` 状态（0003），不用改。
- 现有的 `sa.JSON` 列在 PG 上建成 `json` 而不是 `jsonb`。`json` 接受 JSON 转义形式的 NUL（反斜杠加 u0000），这些列上也不跑 jsonb 运算符，所以保持 `json`，不要「升级」成 `jsonb`。

## 4. RealShort 侧导出接口（feed v2）

### 4.1 通用约定

**路由**
- 位置：`rs:src/app/api/pick-feed/v2/[resource]/route.ts`。
- 设置：`dynamic = "force-dynamic"`、`maxDuration = 60`、`cache-control: no-store`。

**鉴权**
- `PICK_EXPORT_TOKEN` 未配置时整条路由返回 404。
- Bearer 不对时返回 401。比较复用 `feedAuthorized`。

**版本**
- 每个响应都带 `version: "pick-export-v2"`。
- 形状有任何变化都要升这个版本号，工作台严格校验。

**查询参数**
- `as_of`：ISO 时间，精确到分钟，必须不晚于 now 且不早于 now − 30 分钟。工作台在等完 busy 之后才选 as_of，选定后整次拉取受 900 秒总时限约束（5.1），所以 30 分钟够用，不放宽。
- `fp`：manifest 给出的 fingerprint。
- `cursor`：不透明的 base64url 字符串。
- `limit`：每页条数。

**每页响应**
- 格式：`{ok, version, resource, asOf, fingerprint, rows, nextCursor}`。
- 分页只用 keyset。
- 每页同时受两个上限约束：条数上限，和 3 MB 序列化预算（Vercel 响应体上限是 4.5 MB）。每页至少返回 1 行。
- 单行超预算：一行连同信封的 UTF-8 序列化体积超过 3 MB、但不超过 4 MB 硬上限时，这一页只返回这一行；超过 4 MB 时返回 500 `row_too_large`，正文只写资源名和主键。按现有数据这种行不会出现（最大的 posted 行和 catalog 行都约 4.3 KB，单个 posted 行要约 6,500 条发布才到 3 MB），这里只是把「至少 1 行」和「不超预算」的矛盾定下来。
- 每页都**先读数据、后算 fingerprint**，再与 `fp` 比较（4.3）。

**错误**

| 状态码 | 含义 |
|---|---|
| 400 | 参数错 |
| 409 `source_changed` | 服务端重算的 fingerprint 与 `fp` 不同 |
| 503 `source_busy`，带 `Retry-After` | 有来源正在写（见 4.3） |
| 503 `read_failed` | 读取失败；只打印固定日志，不带细节 |
| 500 `row_too_large` | 单行超过 4 MB 硬上限；带资源名和主键，不带字段值。工作台当作镜像侧失败降级，不重试（5.5） |

### 4.2 `asOf` 钉住

以下时间相关的逻辑都加上可选参数 `asOf`：

- `rs:src/lib/observe/queries.ts`：
  - `utcDayOffset(days, asOf?)`；
  - `billBySibling(asOf?)`：账单窗口 d1、d7、d15、d30；
  - `clicks7BySibling(asOf?)`：条件改成 `created_at > asOf - 7d AND created_at <= asOf`；
  - `observeCtes(asOf?)`：快照日 s1、s7、s15；
  - `bucketFilter(bucket, asOf?)`；
  - `loadRows(req, {…, asOf, fullText})`；
  - `loadRowsByIds(ids, {asOf, fullText})`；
  - `loadRsCounts(asOf?)`；
  - `loadGrowthBaseline(days, asOf?)`；
  - `loadDramaDetail(id, asOf?)`：14 天出站与 90 天曲线；
  - `loadBillRows(limit, asOf?)`：`same_day` 子查询（`:771-775`）目前数的是账单日当天到 now 为止的全部出站，加上 `created_at <= asOf`；
  - `loadBillTotals(asOf?)`。
- `rs:src/lib/pick/queries-reelshort.ts`：`reelshortBranch(asOf?)`、`unionRows(asOf?)`、`loadReelshortDetail(id, asOf?)`。
- `rs:src/lib/pick/queries.ts`：`loadFreshness(asOf?)`、`loadFacets(req, asOf?)`、`loadPickRows(req, asOf?)`。
- `rs:src/lib/pick/queries-rank.ts`：`loadRankMeta(req, asOf?)`、`loadRankRows(req, meta, asOf?)`、`loadRsRank(req, rank, asOf?)`、`loadGrowthDiagnosis(req, asOf?)`。

这份清单要覆盖导出用到的函数，以及 P4-3 快照脚本在 `--as-of` 下调用的全部页面 loader。P1-1 加一条源码扫描测试：上面这些文件里，`now()`、`Date.now()`、`new Date()` 只能出现在「asOf 缺省」的分支里。

**默认行为不变：**
- 不传 `asOf` 时，SQL 里仍然是字面的 `now()`，JS 侧仍用 `Date.now()`，线上选剧台的行为不变。
- 测试把改动前后默认路径编译出的 SQL 文本逐字比对（P1-1）。
- `fullText` 默认 false。为 true 时取完整 tags 和 description，不再截到 `tags[1:8]`、description 也不再置空。

### 4.3 fingerprint 与忙标记

**fingerprint：** 以下各项合在一起做规范化 JSON，再算 sha256。每页请求都重算一次，与 `fp` 比较。

| 表 | 取值 |
|---|---|
| catalog_rows | `count(*)`、`max(imported_at)` |
| catalog_signals | `count(*)` |
| catalog_posted | `count(*)`、`max(imported_at)` |
| catalog_accounts | `count(*)` |
| dramas | `count(*)`、`max(synced_at)`、`sum(extract(epoch FROM detail_synced_at))::numeric`、`max(search_data_at)` |
| cps_bill_daily | `count(*)`、`max(fetched_at)` |
| drama_observations | `max(observed_on)`，以及该日的行数 |
| observe_sources | 每行的 (source, status, attempt_id, completed_at) |
| 服务端构建 | `VERCEL_GIT_COMMIT_SHA`（本地为 null） |
| 规则 | `meta.rules`（4.5）的规范化 JSON 的 sha256 |

- outbound_clicks 只追加，已经由 `created_at <= asOf` 钉住，不进 fingerprint。
- **`detail_synced_at` 用求和，不用 max。** RealShort 的详情阶段用 4 个并发 worker（`rs:src/lib/sync.ts:51`、`:733`），每个 worker 先取 `now`（`:272`），写完章节才写 `detail_synced_at=now`（`:325`），同一条 UPDATE 还改了 publishAt、chapterCount、payStart（v1 与镜像的 listed_on、episodes、pay_start 都从这里来）。worker A（now=T1）晚于 worker B（now=T2>T1）提交时，max 不变，这次写入就漏过去了。每次详情写入都把一个值从 NULL 或至少 24 小时前的时间改成新的 `now`，所以求和无论提交顺序如何都会变大，扫描成本与已有的 `count(*)` 相同。
- **构建与规则也进 fingerprint。** mirror 的规则取自 manifest 的 `meta.rules`，智能体的规则取自 v1 首页那一刻构造的规则 Markdown（`rs:src/lib/pick/feed-map.ts:253-280`，由 `feed.ts:67` 调用），v1 每行的 theater 与 `channel_rules.youtube` 也取自当时的 `PLATFORM_RULES`（`feed-map.ts:200`、`:228`、`:233`）。v2 与 v1 相隔几分钟拉取，中间 RealShort 若有生产发布，镜像保留旧规则、智能体拿到新规则，5.3 没有一道闸门比对规则，这一对会被当作一致发布。加上这两项之后，发布后的第一页就返回 409 `source_changed`，现有的 90 秒重来会在新构建上重选 as_of。
- **先读后核。** 每个 v2 与 v1 页都在读完本页数据之后再算 fingerprint 并与 `fp` 比较。这样一次写入如果落在本页读取期间，本页自己就能发现；落在本页核对之后的，由下一页发现；最后一页之后的写入不影响已经读到的数据。
- 同步与导出只在手动或补跑时才可能重叠（任一边）：定时的镜像（03:40、15:40 UTC，`ggwork_pick/schedule.py:15`）与 RealShort 的 `0 */6 * * *` 同步（`maxDuration 600`，`rs:vercel.json`）在时间上错开。

**忙标记：**
- 剧单导入的写入彼此不在同一个事务里（`rs:scripts/import-catalog.ts:111-150`）：行按每 1000 行一次 upsert，约 42 次，再删掉旧批次的行；然后信号在一个 `db.batch` 里整表替换；最后替换发布记录。信号是整表替换，替换前后行数可能相同，所以只看 fingerprint 可能把新行和旧信号配在一起。
- 为此，`rs:scripts/import-catalog.ts` 的写入前后加上 `beginSource('pick_catalog')` / `completeSource` / `failSource`。
- `observe_sources.source` 的 CHECK 约束要加一个值：新增 `rs:scripts/sql/observe-source-pick-catalog.sql`，按 RealShort 惯例手工执行，不用 `db:push`。
  - 原约束是列内定义、没有显式名字的（`rs:scripts/sql/observe-source-state.sql:4`），名字由 PG 生成。新 SQL 用一个 DO 块：按 `pg_constraint` 里 `conrelid = 'observe_sources'::regclass AND contype = 'c'` 且定义里含 `source` 的那一条取实际名字，DROP 后再以显式名字 `observe_sources_source_check` 重建，整段在一个事务里。
- `SOURCE_LABELS` 的类型是 `Record<ObserveSource, string>`，Sources 组件按它的键逐行渲染（`rs:src/components/admin/pick/sources.tsx:10, 50`）。`ObserveSource` 加了 `pick_catalog` 就必须加标签「剧单导入（pick_catalog）」，旧选剧台的「采集来源状态」会多出这一行。这是有意的：运营能直接看到导入是否卡在 running。
- 导出时，只要任何来源处于 `running`，就返回 503 `source_busy`。超过 45 分钟的 `running` 视为僵死，不再拦截，只在 manifest 里记一条告警。
- `pick_catalog` 为 `failed`，或是僵死的 `running` 时，剧单可能只写了一半（`rs:scripts/import-catalog.ts:291-294` 的行、信号、发布记录分三次提交）。这时同样不拦截：v1 与镜像读的是同一份静态状态，两边一致；导入是整表替换，RealShort 也给不出「上一份完整的剧单」；拦住只会让智能体的数据一直停在旧的，直到有人重跑导入（13.2 取新鲜度优先）。只做可见性：manifest 的 `meta.warnings` 记一条 `catalog_import_incomplete`（带状态与开始时间），版本 meta 随之保存（镜像降级时也写进运行记录的 `details_json`，SyncStatus 照样提示），资料页横幅和 SyncStatus 显示「剧单导入失败或中断，这一版的剧单可能只写了一半，请重跑 pnpm catalog-import」（5.7）。运行手册写明：恢复方式永远是整次重跑导入，不手工改 `observe_sources`。

### 4.4 资源清单

| 资源 | 游标（主键） | 每页上限 | 内容 | 估算 |
|---|---|---|---|---|
| `manifest` | — | 1 | 见 4.5 | 约 80 KB |
| `catalog_rows` | row_key | 5000 | 3.3 所列的显式列；`has_pan` 在 SQL 里算成 `COALESCE(pan_url ~* '^https?://', false)`，与旧页 `ResourceCell` 的 `/^https?:\/\//i` 判断相同（`rs:src/components/admin/pick/cells.tsx:390`）；除 3.3 豁免清单外的文本都经过清洗 | 约 17 MB，9 页 |
| `catalog_signals` | (row_key, kind, ord) | 5000 | 全部列；`payload` 只挑 3.3 白名单里的键（导入时 `...rest` 会收下 build.py 输出的任何未知键，`rs:src/lib/pick/catalog-import.ts:337-349`）；除 3.3 豁免清单外的文本叶子都经过清洗，含 `h[*][2]` 日榜备注 | 约 2 MB，1 页 |
| `catalog_posted` | sd | 1000 | 全部列，外加 title_key、imported_at；除 3.3 豁免清单外（sd、feishu_record、row_keys、drama_ids 等）的文本叶子都经过清洗，含 `posts[].url`、`sources`/`cats`/`who` | 约 0.6 MB，1 页 |
| `catalog_accounts` | id | 1000 | 全部列 | 1 页 |
| `rs_rows` | drama_id | 2000 | 3.3 所列的列 | 约 29 MB，16 页 |
| `rs_ids` | id | 10000 | 3.3 所列的列 | 约 4 MB |
| `rs_clicks14` | (drama_id, day) | 20000 | 3.3 所列的列；只收正典 id 属于 `publicCanonical()` 的行 | 1–2 页 |
| `rs_bill_orders` | (bill_date, book_id, promotion_type) | 5000 | 3.3 所列的列：`WHERE order_cnt > 0` 之后按键分组，`sum(order_cnt)`、`count(*) AS source_rows` | 1 页 |
| `rs_series_day?day=YYYY-MM-DD` | drama_id | 40000 | 当天所有 metrics_valid 快照行的 drama_id、revenue_cents、promoters_cnt，不按正典过滤（3.2） | 每天约 1.6–3 MB，1–2 页，P1-6 实测 |

**`rs_rows` 一页怎么算：**
1. 按 keyset 取这一页的正典 id：`SELECT id FROM dramas WHERE <publicCanonical()> AND id > $cursor ORDER BY id LIMIT 2000`。
2. 对这批 id 做三件事：
   - 用 `reelshortBranch(asOf)` 过滤出同形列；
   - 用 `loadRowsByIds(ids, {asOf, fullText: true})` 取指标列；
   - 从 `drama_observations` 按主键查 s1 和 s7 两天的原值。
3. 按 id 合并，再过一遍列白名单，USD 列就此丢掉。
4. `bill_rank`（13.3）：在同一条 SQL 里对**全体**公开正典算，不是只对本页算：`CASE WHEN b.usd IS NOT NULL THEN rank() OVER (ORDER BY b.usd DESC NULLS LAST) END`，其中 `b` 是 `billBySibling(asOf)`，范围是 `publicCanonical()` 的全部行，再按本页 id 取值。金额相同名次相同，资料页按 `bill_rank ASC NULLS LAST, drama_id ASC` 排，与 RealShort 的 `b.usd DESC NULLS LAST, dramas.id ASC` 逐行一致。输出只有整数。

**`manifest` 只在首次调用时算 fingerprint。** 它必须在其它资源之前取；其它资源都要带上它给的 `fp`。

### 4.5 manifest 内容

- **版本与计数：**
  - `version`、`asOf`、`fingerprint`、`sourceRevision`（`VERCEL_GIT_COMMIT_SHA`）；
  - `counts`：每个资源在 as_of 时点的精确行数；
  - `latestSnapshot`，以及最近 93 天每天的快照行数。
- **`meta.freshness`：** `loadFreshness(asOf)` 的全部字段。
- **`meta.rsCounts`：** `loadRsCounts(asOf)`。
- **`meta.growthBaseline`：** `{1: loadGrowthBaseline(1, asOf), 7: loadGrowthBaseline(7, asOf)}`。
- **`meta.sources`：** `readSources()`，details 只保留白名单里的 key。
- **`meta.rules`：**
  - PLATFORM_RULES、IN_USE、依据标签、BASIS_DATE_LABEL；
  - ReelShort 榜标签、YOUTUBE_LABEL；
  - GLOSSARY、规则提示、LANG_LOC、发布池地址、SORT_LABELS。
- **`meta.control`（控制总数）：** 用 RealShort 自己的函数在 as_of 时点算出：
  - 选剧 tab 默认条件下的 `loadFacets`，全部剧库默认条件下的 `loadFacets`；
  - `loadRankMeta(默认).counts`；
  - `loadPostedStats()`，以及四个发布状态的计数；
  - `ledger`：`{rows, orders}`。`rows` 是有订单的原始账单行数，等于 `loadRsCounts` 的 `ledger`（`rs:src/lib/observe/queries.ts:516`），也等于镜像 `rs_bill_orders` 的 `sum(source_rows)`；`orders` 是订单总数。不用 `loadBillTotals`：它返回 `usd`、`matchedUsd`。
- **`meta.scrub`：** 各字段被清洗的次数。
- **`meta.warnings`：** 告警列表，例如僵死的来源、`catalog_import_incomplete`（4.3）。
- **meta 的键白名单：** `meta.*` 每一层都按白名单挑键输出（写在 `export-v2-map.ts`），不整对象透传 RealShort 函数的返回值；白名单里没有任何金额键。

### 4.6 禁止字段的保障

保障落在**输出**上：RealShort 在自己的 SQL 里读到禁止列是可以的（复用的 loader 本来就读），禁止的是它们出现在响应里。

RealShort 一侧有四道：

1. **显式列清单。** 每个资源的列白名单写在纯模块 `rs:src/lib/pick/export-v2-map.ts` 里。`export-v2.ts` 自己写的 SQL 用这些名字拼成，不许 `SELECT *` 和 `别名.*`（`count(*)` 不在此列）；输出也只挑白名单里的 key，manifest 的 `meta.*` 同样按键白名单输出（4.5）。
2. **白名单与 SQL 片段自检。** 测试断言：所有白名单列名和输出键，以及 **`export-v2.ts` 自己写的 SQL 片段**，都不匹配 `/pan_|promotion_value|promotion_code|promotion_link|revenue_usd|bill_usd|usd/i`。
   - 复用的 RealShort loader 不在检查范围内：`reelshortBranch`（字面的 `'' AS pan_url`，`rs:src/lib/pick/queries-reelshort.ts:65-66`）、`loadRowsByIds`/`loadRows`（读 `b.usd AS bill_usd`，`rs:src/lib/observe/queries.ts:437`）、`loadFacets`（经 `catalogBranch` 读 pan 列）、`loadRsCounts`（经 `billBySibling`）、`loadRankMeta`、`loadBillTotals`。它们为了「控制总数与页面函数同源」（决策 3）必须原样复用，编译出的 SQL 必然含这些名字。
   - `export-v2.ts` 里只允许两处点名的派生（13.3）：`has_pan` 的表达式（读 `pan_url`，4.4）和 `bill_rank` 的 `rank()`（读 `billBySibling` 的 `usd`，4.4）。测试按名字豁免这两个片段，其余片段照查。
3. **输出过滤与哨兵值。** 用故意带禁止 key 的夹具行喂给映射函数，递归扫描输出，这些 key 必须全部被丢掉。另加一条需要数据库的用例（4.8）：种子库写入哨兵值（`revenue_usd` 987654.32、`pan_url` `https://pan.baidu.com/s/SENTINEL`、`pan_pw` `zz9q`、`promotion_value` `SENTINELPV`），取每个 v2 资源（含 manifest）和每页 v1，序列化后的正文里不出现任何哨兵值；`has_pan` 是布尔，`bill_rank` 是整数或 null。
4. **自由文本清洗。** `scrubAllText(value)` 递归走遍一行输出的文本：字符串、数组元素、对象里的每个字符串叶子（含 jsonb 的 `payload.h[*][2]`、`posts[].url`、`catalog_accounts.url`、`sources`/`cats`/`who`）。3.3 的豁免清单（row_key、sd、feishu_record、各类 id 与 id 数组、title_key、日期列、v1 的 source_id/detail_url/source_ref）不过清洗，其余叶子都过一遍 `scrubPanText`，把网盘 URL 和提取码替换成「[网盘信息已移除]」，替换次数按资源和字段路径计入 manifest。豁免按名字点，清洗不按名字点：新字段默认要清洗。
   - 网盘 URL：域名 pan.baidu.com、pan.quark.cn、aliyundrive.com、alipan.com、115.com、123pan、lanzou 系列、drive.uc.cn、cloud.189.cn。域名部分大小写不敏感，scheme 可有可无；匹配到的 URL 连同路径和 query（含 `?pwd=`）整段替换。
   - 提取码：`(?<![A-Za-z0-9])(?:提取码|密码)\s*[:：=]\s*[A-Za-z0-9]{4,8}(?![A-Za-z0-9])`。关键字只有「提取码」「密码」两个，后面必须有显式分隔符（`:`、`：`、`=`）；不设大小写不敏感标志（关键字是中文，码的字符集已写全）。不收单独的 `code`，`pwd=` 只在网盘 URL 的 query 里随 URL 一起替换。原稿的「提取码/密码/pwd/code 后面跟 4–8 位」按字面实现会命中真实剧名（`Code Name Reaper II`、`Cheat Code Champion`、`Codename: Judicator`、`Code Queen`）、主键 `goodshort-K10JEicNmxOWhQPwdg3zdw==`（`Pwdg3zdw`）和英文简介里的 `secret code that`，现有 `scripts/juyuantai/build.py:67` 的 `PW_RE` 也只收提取码、密码、pwd 和恰好 4 位。
   - 正则只有一份来源：`rs:tests/fixtures/pan-scrub-cases.json`（正例、反例、期望输出）。反例至少包括上面四个剧名、那个 row_key、一段含 `secret code that` 的英文简介，以及 youtube 等普通链接。RealShort 的 `scrubPanText` 和工作台的 Python 闸门各跑这份夹具，工作台把它复制到 `customizations/pick-workbench/tests/fixtures/pan_scrub_cases.json`，文件头注明来源 commit，两边结果必须逐条一致。
   - 首次真实 manifest 的 `meta.scrub` 按字段看一遍（P1-6）：title、title_cn、description 上有任何命中，都当作正则的 bug，修好再打开镜像。
   - 金额的禁令是按字段执行的（第 1 节决策 4、13.3 的封闭清单），自由文本只按网盘模式清洗，不按「收益」「分成」这类语义清洗：选剧池里的推荐理由会引用 ReelShort 的公开指标，那是允许同步的内容。

工作台一侧再加三道（见 5.3）：
1. pydantic 模型设 `extra="forbid"`，出现未知字段就整批拒收。`payload` 这类 jsonb 也建成严格的子模型，不用 `dict[str, Any]`，否则 `extra="forbid"` 管不到它的键；
2. 构建 schema 的 `information_schema.columns` 不得有禁止列名；
3. 自由文本列（含 `jsonb::text`）做正则扫描，网盘命中数必须为 0。与 RealShort 用同一份豁免清单（3.3）和同一份夹具正则，不扫标识与派生字段，否则一个主键误命中就会让每次运行都降级。

v1 也要顺手修两处：
- `rs:src/lib/pick/feed.ts:75` 目前是 `SELECT rows.*`，改成显式列清单。在 RealShort 内部读到 pan_url、pan_pw 本身不是泄露（上面说过，保障在输出上）；改的理由是让 v1 的输出来源一目了然，并让「不许 `SELECT *` 和 `别名.*`」的源码扫描（4.8）也覆盖 `feed.ts`。
- v1 的 `toFeedRow`（`rs:src/lib/pick/feed-map.ts:193-235`）把 `catalog_signals.note` 经 `clipText(s.note, 1000)`（`:208`）原样写进智能体批次，没有清洗。改为输出前对整行调用 `scrubAllText`（先清洗、后截断；`source_id`、`detail_url`、`source_ref` 按 3.3 豁免）。只改值、不改形状，仍算只增不改。否则同一条备注在候选卡证据里是原文、在资料页是「已移除」，两边对不上，网盘信息也进了 `ggwp_drama_versions`。

### 4.7 v1 的改动（只增不改）

- `parseFeedQuery` 接受可选的 `as_of` 和 `fp`，校验规则同 4.1。
- `loadFeedPage` 把 `asOf` 传给 `unionRows`、`loadFreshness`、`feedRulesMarkdown`。
- 传了 `as_of` 时 `capturedAt = asOf`；没传时仍是 `new Date()`（`rs:src/lib/pick/feed.ts:72`）。工作台把 `capturedAt` 当作批次的 `source_as_of`（`sync.py:180`），这样它就等于镜像版本的 as_of。
- 响应顶层新增 `fingerprint` 字段，**每一页都带**，不只是首页。
- 传了 `fp` 时，每一页都在读完本页行之后重算 fingerprint（先读后核，4.3）：不同返回 409 `source_changed`；有来源在 running 返回 503 `source_busy`。不传 `fp` 时行为与今天相同。
- 行输出经过 `scrubAllText`（4.6，豁免清单同 3.3）。
- 每页已经带 `sourceRevision`（`rs:src/lib/pick/feed.ts:88`），不改。
- `FEED_VERSION` 保持 `pick-feed-v1`。行合同和 `DramaInput` 都不变；工作台现有的 `fetch_feed` 只读取它认识的顶层 key，不受影响。

### 4.8 RealShort 测试

以下测试都在 `pnpm test`（`node --import tsx --test tests/*.test.ts`）里运行：

| 文件 | 类型 | 覆盖内容 |
|---|---|---|
| `rs:tests/pick-export-v2.test.ts` | 纯函数 | 白名单自检（列名、输出键、`meta.*` 键白名单；喂一个带 `usd`、`matchedUsd` 的对象给 ledger 映射，输出只剩 `{rows, orders}`）；`scrubPanText` 跑共享夹具 `pan-scrub-cases.json`（反例含 youtube 等普通链接、四个带 Code 的剧名、row_key `goodshort-K10JEicNmxOWhQPwdg3zdw==`、含 `secret code that` 的英文简介）；`scrubAllText` 能清到嵌套数组和 jsonb 叶子（`h[*][2]`、`posts[].url`、`who[]`），且不动 3.3 豁免清单里的字段；payload 白名单丢掉未知键；as_of、cursor、limit 解析；按字节截页：不超过 3 MB 预算，除非单行本身超预算；单行 3–4 MB 时单独成页，超过 4 MB 返回 `row_too_large`；fingerprint 规范化 JSON 稳定，库内聚合相同而构建 SHA 或规则摘要不同时 fingerprint 不同 |
| `rs:tests/pick-export-v2-sql.test.ts` | 纯函数（用 `PgDialect` 编译 SQL） | `export-v2.ts` 自己写的 SQL 片段不含禁止标识符（按名字豁免 `has_pan`、`bill_rank` 两个派生片段，4.6 第 2 条），也不含 `SELECT *` 或 `别名.*`；复用的 RealShort loader 不在检查范围；传了 asOf 的 SQL 里有字面时间，没传的仍是 `now()`；改动前后默认路径的 SQL 文本逐字相同（夹具存改动前的编译结果） |
| `rs:tests/pick-export-v2-db.test.ts`（新增） | 需要数据库（设了 `OBSERVE_TEST_DATABASE_URL` 才跑） | 哨兵值用例（4.6 第 3 条）：每个 v2 资源和每页 v1 的正文里都没有哨兵值，`has_pan` 是布尔、`bill_rank` 是整数或 null；两条 `detail_synced_at` 写入乱序提交后 fingerprint 仍然改变；库不变、构建 SHA 或规则摘要在 manifest 之后改变时，后面的 v2 页和 v1 页都返回 409；同一 (bill_date, book_id, promotion_type) 下两个 promotion_value 都有订单时合并成一行、`source_rows=2`，`control.ledger.rows` 等于 `loadRsCounts().ledger`；`bill_rank` 排出的顺序（含金额相同的并列）与 `loadRows(sort=bill)` 逐行相同；`has_pan` 与 `/^https?:\/\//i.test(pan_url)` 逐行相同 |
| `rs:tests/pick-feed.test.ts`（追加） | 纯函数 | v1 的 `as_of`、`fp` 解析；`fingerprint` 是新增字段且每页都有；传了 as_of 时 `capturedAt` 等于它；带网盘链接的 signal note 经 `toFeedRow` 后被清洗，且与 v2 的同一行清洗结果相同；`source_id`、`detail_url` 不被清洗；行 key 集合不变；`feed.ts` 不再出现 `rows.*` |
| `rs:tests/admin-contracts.test.ts`（追加） | 合同 | 白名单只允许 v2 路由 import `@/lib/pick/export-v2` |
| `rs:tests/pick-import.test.ts`（追加） | 纯函数（注入假 db） | `pick_catalog` 标记在第一次写之前 begin，最后一次写之后 complete，出错时 fail |
| `rs:tests/pick-sources.test.ts`（新增） | 纯函数 | `SOURCE_LABELS` 覆盖 `ObserveSource` 的每个值；`pick_catalog` 有标签；`sources` 里没有这个 key 时该行显示「—」，不报错 |
| `rs:tests/observe-db.test.ts`（追加） | 需要数据库（设了 `OBSERVE_TEST_DATABASE_URL` 才跑） | 在 T−1 天、T+1 分钟各造一条点击：`asOf=T` 时后者被排除；账单窗口和快照日按 T 取；默认路径结果与改动前相同 |

### 4.9 一次拉取的成本

- **请求数：** v2 约 35 个，v1 约 8 个。
- **数据量：** 原始约 65 MB，gzip 后约 12–15 MB。RealShort 在 Neon Launch 套餐上，每月含 500 GB 传输，这部分可以忽略。
- **Neon compute：** 这是要盯的成本。估算每次拉取占用 30–60 秒数据库时间，每天两次。
  - 大头是 rs_rows 的 16 页：每页都会跑一遍 MATERIALIZED 的 b、c 两段 CTE，每页 0.5–1.5 秒；
  - 其次是 manifest 的控制总数，约 3–6 秒；
  - 再次是 v1 的 8 页。
- **曲线：** 每天增量约 1.6–3 MB（不再按正典过滤，3.2）；首次回填 90 天约 90–180 个请求、145–270 MB。
- 这些都在 P1-6 实测。门槛见该任务。

## 5. 工作台同步

### 5.1 调度

- 时间表不变：03:40 和 15:40 UTC，另有 12 小时补跑。RealShort 在 00:00 UTC 前后写入快照和账单，所以 03:40 那一轮会带上新的一天。
- 开关：
  - `PICK_MIRROR_ENABLED=1`：走镜像流程（5.2），镜像失败时自动降级（5.5）；
  - 其他值：完全走现有的 v1 流程，行为与今天相同。RealShort v2 长期故障时就用这个开关停掉镜像，省掉每次白拉。
  - 原先的 `PICK_MIRROR_OPTIONAL` 取消：降级已是默认行为。
- 手动同步按钮和现有的冷却规则不变：上次成功或正在进行时隔 5 分钟，上次失败隔 1 分钟。
- **时限：**
  - 等待 `source_busy` 发生在选 as_of 之前，累计最多 20 分钟，不计入总时限。
  - 选定 as_of 之后，拉取、写入、闸门、发布合计受总时限约束，从 600 秒（`sync.py:30` 的 `DEADLINE_SECONDS`）放宽到 900 秒；单个请求 60 秒。
  - **镜像阶段有自己的截止时刻：** 选定 as_of 之后 750 秒（900 秒减去 150 秒的降级预留）。镜像阶段指 v2 拉取、COPY、建索引、ANALYZE 和只作用于镜像的闸门。原因：每条语句各有超时（COPY 每页 60 秒、ANALYZE 与闸门各 120 秒），加起来远超 900 秒，Micro 上数据库一慢，总时限就可能在没有任何一条语句失败的情况下耗尽；而现有写法（`sync.py:173` 的 `asyncio.timeout`，`:151` 的 `except TimeoutError`）超时就让整次运行失败，v1 批次已经暂存也不发布，也不计入连续失败。镜像截止时刻一到就按 5.5「总时限耗尽于镜像阶段」降级，智能体批次在预留的 150 秒里发布。
  - 因 fingerprint 漂移重来时重新选 as_of，总时限和镜像截止时刻也都重新计。最多重来一次。
  - 这样 as_of 到最后一页的间隔不超过 15 分钟，落在 RealShort 30 分钟的窗口内。

### 5.2 一次运行的步骤

实现在 `ggwork_pick/mirror/run.py` 的 `MirrorSync.run(trigger)`。

1. **取锁，开专用连接。**
   - 先取进程内的 `sync_lock`；
   - 再用 `asyncpg.connect(PICK_DATABASE_URL, server_settings={"search_path": "deerflow"}, command_timeout=None)` 开一条**不属于任何连接池**的独立连接，在它上面取 `pg_try_advisory_lock(hashtext('ggwp:mirror-sync'))`，一直持有到结束。镜像的 DDL、COPY、建索引、ANALYZE、闸门扫描都在这条连接上执行，每条语句用 `timeout=` 显式给超时（COPY 每页 60 秒、ANALYZE 与闸门 120 秒）。
   - 结束时在 `finally` 里先 `pg_advisory_unlock`，再 `close()` 这条连接。不能把持锁的连接放回池里：会话级 advisory lock 不随事务结束释放，放回池后锁一直挂着，之后每次同步都返回 `already_running`。
   - 拿不到锁就返回 `already_running`。如果最近一次 running 的运行记录已超过 30 分钟，状态里标 `lock_stuck`，并记下持锁连接的 pid（`pg_locks` 查得到），供操作员 `pg_terminate_backend`。
2. **遗留清理、保留清理与容量检查。** 都在拿到锁之后做。
   - 先按 5.5 最后几行清理上次中断留下的 `building` 版本和本流程建的 `importing` 批次；
   - 再按保留规则清掉旧版本和旧批次（3.6），腾出空间；
   - 最后检查 `pg_database_size()` 小于 `PICK_DB_SIZE_CAP_BYTES`（默认 6 GiB），并且 /data 剩余空间大于 500 MB（blob 仍在卷上）。
3. **选 as_of，读 manifest。** as_of 取当前时刻，取整到分钟再减 2 分钟。manifest 返回 `source_busy` 时按 Retry-After 等，累计不超过 20 分钟（5.1）；**每次等完都重新选 as_of** 再读 manifest。manifest 成功之后，任何一页再返回 `source_busy` 或 `source_changed`，都按漂移处理：丢掉本次已写的版本，90 秒后重新选 as_of 重来一次。
4. **建版本。** 在 `pick_mirror.versions` 插入一行 `building`，按 ddl.sql 建 `pickm_vN`。
5. **拉 v1 并暂存（排在 v2 之前）。** 带上同一个 `as_of` 和 `fp`，**每一页**都核对 fingerprint，每一页都可能返回 409 或 503（4.7）；每页的 `sourceRevision` 还要等于 manifest 的（附加检查，fingerprint 已含构建 SHA，4.3）。v1 行先过 NUL 和网盘正则检查（同一份共享夹具的正则与豁免清单），再经现有的 Importer 以 stage 模式写成 `importing` 状态的剧库批次；规则 Markdown 写成知识批次。Importer 用 ORM 池里的会话。先拉 v1 是为了让 v1 永远不等 v2：镜像阶段再慢，智能体批次也已经暂存好，降级时直接发布（5.1、5.5）。
6. **拉 v2 各资源，逐页处理。** 从这一步起是镜像阶段，受 5.1 的镜像截止时刻约束：
   1. 用 pydantic 严格校验；
   2. 做字符检查：出现 NUL 或孤立代理项就整批失败，UTF-8 严格编码；
   3. 核对这一页的 fingerprint 与 manifest 相同；
   4. 用专用连接的 `copy_records_to_table(table, schema_name=f"pickm_v{N:06d}", records=…, columns=…)` 写入。参数按列的真实类型准备：timestamptz 传 `datetime`（带时区），date 列在镜像里是 `YYYY-MM-DD` 文本、传 `str`，jsonb 传 JSON 字符串（asyncpg 默认的 jsonb 编码器收的是 str），text[] 传 `list[str]`，boolean 传 `bool`。

   内存里始终只有一页数据。
7. **建索引，执行 ANALYZE**（镜像阶段）。
8. **过闸门**（5.3）。只作用于镜像的闸门属于镜像阶段；镜像截止时刻到了还没过完，就按 5.5 降级。
9. **发布**（3.5）：两边都过时配对发布；只有镜像侧失败时降级为只发布智能体批次（2.5 第 1 条）。
10. **清理**：按保留规则再清一次。
11. **曲线折叠**（5.6）。仍在同一把锁、同一条专用连接上。折叠失败不影响已经发布的版本，只记在运行记录的 `details_json` 里。
12. **收尾**：写 `ggwp_sync_runs` 的 `status`、`rows`、两个批次 id、`source_as_of` 和 `details_json`（含降级原因、`consecutive_failures`）。然后释放锁、关闭专用连接。

### 5.3 校验闸门

所有闸门都在发布之前执行，按下表顺序。「作用于」一列写明失败时影响哪一边：只作用于镜像的闸门失败时按 2.5 第 1 条降级；作用于 v1 的闸门失败时两边都不发布。

| 闸门 | 作用于 | 不通过时 |
|---|---|---|
| 每页的 fingerprint（v2 与 v1 的每一页）都等于 manifest | 两边 | 整次丢弃，90 秒后重新选 as_of 重来一次；再不通过，镜像判失败，v1 也不发布（它同样漂了） |
| v1 行：NUL、孤立代理项、网盘正则（共享夹具，豁免清单同 3.3）命中数为 0 | v1 | 两边都不发布 |
| 每个资源的实收行数等于 manifest 的 counts | 镜像 | 降级 |
| 构建 schema 的 `information_schema.columns` 里没有匹配禁止模式的列名（模式同 4.6；`has_pan`、`bill_rank` 不匹配） | 镜像 | 降级 |
| 自由文本列和 `jsonb::text` 里没有网盘 URL 或提取码模式。正则与豁免清单都与 4.6、3.3 相同，不扫 row_key、sd 等标识与派生列 | 镜像 | 降级 |
| 引用完整：signals.row_key 都在 catalog_rows 里；rs_rows.drama_id 都在 rs_ids 里；rs_clicks14 的 drama_id 都在 rs_rows 里（导出时已过滤，4.4） | 镜像 | 降级。bill 的 book_id 允许不在 rs_ids 里，此时同 RealShort 一样回退到 book_title |
| 控制总数：在构建 schema 上用 SQL 重算下列各项，逐项相等：freshness 的各计数、rsCounts 的各计数（其中 `ledger` 取 `rs_bill_orders` 的 `sum(source_rows)`，不取合并后的 `count(*)`，否则同一天同一推广类型有两个 promotion_value 时永远对不上）、每个信号种类的 distinct row_key 数、发布记录统计与四个状态计数、`control.ledger` 的 rows 与订单总数 | 镜像 | 降级。facets 这类要用移植后 TS 查询才能算的，由 P4-3 的核对脚本检查 |
| 与 v1 一致（2.5 第 2 条） | 镜像 | 降级 |
| 与当前版本相比，某张表从非空变成空 | 镜像 | 降级。操作员确认后经 `railway ssh` 执行 `cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.admin accept-empty`（写法与环境检查同 6.5、6.8 第 8 步），它把 `pick_mirror.control.accept_empty_once` 置真；下一次发布事务消费后自动置回（3.5）。环境变量做不到「只放行一次」，所以不用环境变量 |

### 5.4 原子发布

见 3.5 的事务。发布成功后 `ggwp_sync_runs.details_json` 写入版本号。配对关系只在 `pick_mirror.versions` 里（2.5 第 1 条），批次的 `validation_json` 不写 `mirror_version`。

### 5.5 失败与恢复

| 情况 | 行为 |
|---|---|
| 镜像侧失败：v2 的 HTTP 错误（含 500 `row_too_large`，不重试）、校验失败、镜像闸门失败 | 1. 版本标为 `failed`，执行 `DROP SCHEMA`<br>2. v1 已在 v2 之前拉取并暂存（5.2 第 5 步，带 manifest 的 as_of 与 fp）；v1 闸门通过就按 3.5 最后一条降级发布<br>3. `pick_mirror.control.consecutive_failures` 加 1，记 `last_failure_at`<br>4. 运行记录只写安全错误文本：状态码、异常类名、字段位置（`row_too_large` 时写资源名和主键）。不含 token、响应正文和字段值 |
| 总时限耗尽于镜像阶段：镜像截止时刻（5.1）到了，v2 拉取、COPY、ANALYZE 或镜像闸门还没做完 | 1. 取消专用连接上正在执行的镜像语句（asyncpg 的超时取消，必要时 `pg_cancel_backend`），版本标为 `failed`，`DROP SCHEMA`<br>2. 在 150 秒预留里发布已暂存的智能体批次（3.5 最后一条），`behind` 按 2.5 第 1 条的口径<br>3. `consecutive_failures` 加 1，照常触发 `mirror.alert`<br>4. v1 在 5.2 第 5 步就已暂存，所以这里一定有批次可发；若 v1 本身耗尽了总时限，属于 v1 侧失败（下面一行） |
| manifest 本身失败（不是 busy） | 没有 as_of 与 fp 可用，本次退回现有 v1 流程（不带 as_of），按降级处理 |
| v1 侧失败：HTTP、校验或 v1 闸门失败 | 两边都不发布：版本 `failed` 并删 schema；**本次运行新建的** `importing` 批次标为 `failed` 并删掉行，`content_hash` 改为 `failed-<id>`，释放去重槽位；上一对仍是当前版本 |
| fingerprint 漂移（导出途中 RealShort 在同步或导入） | 90 秒后重新选 as_of，整次重来一次 |
| `source_busy` | 读 manifest 时按 Retry-After 等待并重新选 as_of；拉取途中出现则按漂移处理 |
| 进程被取消 | 用 shield 写失败记录，并执行清理；`finally` 里释放 advisory lock、关闭专用连接 |
| 进程重启 | 下一次运行拿到 `ggwp:mirror-sync` 锁之后，才把遗留的 `building` 版本和本流程遗留的 `importing` 批次判为失败并清理（5.2 第 2 步）。不在启动时无锁清理：备用的回填命令或另一个进程可能正持锁写入 |
| 连续失败 | `consecutive_failures` 达到 3（约 1.5 天）时，`/api/pick/sync` 的 `mirror.alert` 为真，资料页和 SyncStatus 显示红色提示，日志打 ERROR。成功配对发布后清零 |
| RealShort v2 长时间故障 | 自动降级已经保证智能体照常更新；想停掉白拉就把 `PICK_MIRROR_ENABLED` 改成 0 |
| `PICK_MIRROR_ENABLED` 不为 1 | 走现有 v1 流程 |

**`fail_staged` 的边界：** 只处理本次运行自己新建、且仍是 `importing` 的批次，按运行里记下的 batch id 操作，不按状态批量扫。去重可能直接返回一个已发布的批次（甚至就是当前批次）：它不能被标失败，行也不能删，stage 模式下推迟的 `_reuse` 写入（3.5）直接丢弃即可。

### 5.6 曲线折叠与回填

**常规折叠：** `manifest.latestSnapshot` 晚于 `series_state.through` 时：
1. 拉取缺失的日子，每次运行最多 3 天；
2. 数据先进临时表；
3. 用一个事务执行 `INSERT ... ON CONFLICT (drama_id) DO UPDATE`：追加新点，截掉 cutoff 之前的点（cutoff 按 3.2：第 10 步保留清理之后仍为 published 的版本里最早的 `as_of` 日期减 90 天，且不晚于 `through` − 92 天），并更新 `through` 和 `trimmed_before`。

同一天重复折叠是幂等的。

常规折叠和回填都是对数组「读出、修改、写回」，所以两者都必须持有同一把 `ggwp:mirror-sync` advisory lock：常规折叠在同步的锁内执行（5.2 第 11 步）；回填命令自己开一条专用连接 `pg_try_advisory_lock`，拿不到就退出并提示「同步正在进行」。

**首次回填：**
- 用命令 `cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.series --backfill 90`，经 `railway ssh` 在 gateway 容器里执行。执行前按 6.8 第 8 步先确认 ssh 会话里有 `PICK_DATABASE_URL` 和 `PGSSLMODE`（只看有没有，不打印值）。
- 入口不依赖 cwd 和 `get_app_config` 的默认查找，缺变量时非零退出并说明缺哪个（6.5）；不打印 token。
- 只开一条 asyncpg 连接，不初始化宿主 engine，连接预算见 6.4。

### 5.7 状态展示

- `GET /api/pick/sync` 新增 `mirror` 字段，内容包括：
  - 当前版本的 id、`as_of`、`latest_snapshot`、`series_through`；
  - 最近一次失败的原因和 `consecutive_failures`；
  - `behind` 标记：当前版本的 (`agent_catalog_batch_id`, `agent_knowledge_batch_id`) 不等于当前共享的 (剧库批次, 知识批次)（口径见 2.5 第 1 条）。降级发布了新批次时为真；降级运行去重复用的正是当前版本配对的批次时为假；
  - `warnings`：当前版本 meta 里的告警，例如 `catalog_import_incomplete`（4.3），资料页横幅与 SyncStatus 显示「剧单导入失败或中断，这一版的剧单可能只写了一半，请重跑 pnpm catalog-import」；
  - `alert` 标记：见 5.5「连续失败」；
  - `lock_stuck` 标记：见 5.2 第 1 步。
- `SyncStatus` 组件显示这些信息。

## 6. DeerFlow 宿主与选剧扩展迁到 Supabase

### 6.1 Supabase 项目设置

- **项目：** 名称 `ggwork-workbench`，放在 Pro 组织下，区域 East US（North Virginia），Postgres 17，compute Micro（13.1，见 6.4）。2026-09-23 已建好并实测：PostgreSQL 17.6，`max_connections=60`，`shared_buffers` 256 MB，`work_mem` 3500 kB，库的 collation 为 `en_US.UTF-8`；session pooler 主机是 `aws-0-us-east-1.pooler.supabase.com`（`aws-1` 对本项目不可用），本机经 IPv4 可连。
- **安全：**
  - 开启 Enforce SSL。
  - 关闭 Data API，或者把 Exposed schemas 清空。
  - 不使用 anon key 和 service_role key，也不配置 Auth。
- **数据库密码：**
  - 在本机用 `openssl rand -hex 32` 生成，写进 scratchpad 里权限为 600 的文件。不用 `-base64`：它会生成 `/`、`+`、`=`，放进连接串 URL 要做百分号编码，容易出错。
  - 然后存进密码管理器，不进 git，也不贴进对话。
- **连接池：** Database Settings → Connection pooling 里的 Pool Size 设为 20，与两个角色的 `CONNECTION LIMIT` 相等，理由见 6.4。
- **时区：** 数据库时区保持 UTC。

### 6.2 bootstrap

- 以 postgres 身份执行 3.1 的 SQL，连接走 session pooler，用户名写成 `postgres.<ref>`。
- 在同一个 psql 会话里用 `\password` 设置两个角色的密码。
- 执行完成后记录这些检查结果：
  - 脚本的完整输出里没有任何 `WARNING` 行（psql 在只警告、未授权时照样打印 `GRANT`，3.1）；
  - `SELECT rolname, rolconfig, rolconnlimit FROM pg_roles WHERE rolname IN (...)`：两个 `rolconnlimit` 都是 20，reader 的 rolconfig 含 `default_transaction_read_only=on` 和 `statement_timeout=8s`；
  - `\dn+`：deerflow 和 pick_mirror 的属主是 deerflow_app，pick_mirror 的权限里有 `pick_board_reader=U/deerflow_app`；
  - `SELECT has_schema_privilege('pick_board_reader', 'pick_mirror', 'USAGE')` 为真；
  - `SELECT has_database_privilege('deerflow_app', 'postgres', 'CREATE')` 为真（脚本里的 DO 块已经硬检查过，这里留档）；
  - `SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'postgres'`：记下库的属主，供以后排查。

### 6.3 连接：IPv4、pooler、SSL、search_path

**Railway 走 Supavisor 的 session 模式**
- Supabase 的直连主机 `db.<ref>.supabase.co` 默认只有 IPv6。Railway 的出站按 IPv4 处理，这一点要在演练时实测。
- 所以连接串用 `postgresql://deerflow_app.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`（本项目实测是 `aws-0`，6.1），从控制台的 Connect 面板复制。
- DeerFlow 需要的几项在 session 模式下都能正常工作：
  - psycopg 的 `prepare_threshold=0`；
  - asyncpg 的语句缓存；
  - 宿主 bootstrap 用的 `pg_advisory_lock`；
  - COPY；
  - 连接级的 search_path。
- 备选方案：购买 IPv4 add-on 后直连。

**SSL：连接串里不写任何 SSL 参数**
- 同一个连接串同时交给 asyncpg（经 SQLAlchemy）和 libpq（psycopg）使用：
  - 写 `?sslmode=` 会让 asyncpg 报未知参数（SQLAlchemy 2.0.49 会把 query 原样传给 `asyncpg.connect`）；
  - 写 `?ssl=` 会让 libpq 报错。
- 改为设置 Railway 环境变量 `PGSSLMODE=require`，两个驱动都会读取它。
- 在 entrypoint 里校验：URL 带 `sslmode` 或 `ssl` 参数时直接拒绝启动。
- 演练时用 `pg_stat_ssl` 确认两类连接都已加密。
- 证书校验（verify-full）作为后续加固项，见第 10 节。

**search_path**
- DeerFlow 已经为两个驱动设置了 search_path：asyncpg 用 `server_settings`，psycopg 在 DSN 里注入 `options=-c search_path`。
- 角色级默认值作兜底。
- 演练时两个驱动各执行一次 `SHOW search_path`，结果都应为 `deerflow`。
- 如果 Supavisor 拒绝 `options` 启动参数，改为 `postgres_schema: ""`，只依赖角色的默认 search_path。这时 DeerFlow 不再执行 CREATE SCHEMA，表仍然落在 deerflow。

**Vercel 走 transaction 模式**
- 连接串：`postgresql://pick_board_reader.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:6543/postgres`，同样不带 sslmode。
- TLS 由代码指定：`ssl: { rejectUnauthorized: true, ca: PICK_MIRROR_CA_PEM }`，CA 从 Supabase 控制台下载。
- pg 驱动用无名语句，每条查询都在自己的事务里执行，与 transaction 模式兼容。

### 6.4 连接预算

| 连接方 | 模式 | 上限 | 来源 |
|---|---|---|---|
| gateway ORM（asyncpg） | session | 13（pool_size 3 + overflow 10） | `database.pool_size`；DeerFlow 没有暴露 max_overflow，用 SQLAlchemy 默认的 10。Importer 的会话也从这里取 |
| checkpointer（psycopg AsyncConnectionPool） | session | 4 | psycopg_pool 默认 min_size 是 4 |
| store | session | 1 | |
| readiness 探针 | session | 1 | `backend/app/gateway/health.py` |
| 启动迁移 | session | 短时 2–3 | 宿主 bootstrap 的锁连接、反射连接和 alembic 自建的 engine，见下面建账号一行 |
| 镜像同步的专用连接（锁 + DDL + COPY + 曲线） | session | 1 | 5.2 第 1 步，不走池 |
| 经 `railway ssh` 跑的命令：回填、accept-empty、cleanup | session | 1 | 各自只开一条 asyncpg 连接，不初始化宿主 engine；回填与同步互斥（同一把锁） |
| 经 `railway ssh` 跑的命令：建账号（`create_user`） | session | 短时 2–3 | 它经 `init_engine`（`backend/packages/harness/deerflow/persistence/engine.py:209-218`）跑宿主 bootstrap（同目录 `bootstrap.py`）：advisory lock 连接（`:525` 的 `_postgres_lock`）、反射连接（`:624`）、alembic 用裸 URL 自建的 engine（`:309` 的 `_get_alembic_config`），持续几秒 |
| deerflow_app 合计 | | 常态约 9–10（ORM 3、checkpointer 4、store 1、同步专用连接 1）；各项同时到顶的理论峰值约 25 | 角色 `CONNECTION LIMIT 20`，等于 Pool Size 20；超出的客户端在 Supavisor session 模式里排队（13.1 接受） |
| Vercel reader | transaction | 每个实例的 Pool max 为 3；服务端连接由 Supavisor 复用，上限是 Pool Size | 角色 `CONNECTION LIMIT 20`，等于 Pool Size 20 |
| Supabase 内部服务 | | 约 10–15 | |

- Supavisor 的 Pool Size 是按「用户 + 库」分别计的。两个业务用户各 20，加内部服务约 10–15，最坏约 50–55 个服务端连接，在 Micro 实测的 `max_connections=60` 之内。
- compute 选 Micro（用户决定，13.1），不选 Small：接受 gateway 高峰时 session 模式排队（Supavisor 在池满时让客户端最多排队约一分钟）。要到 20 个服务端连接，需要 10 个以上 ORM overflow 会话同时在用，单副本、小团队下很少出现。
- 排队不影响 readiness：Railway 只在部署开始时调用 `/health/ready`，上线后不再探测；挂着 `/data` 卷时新部署启动前旧部署已停，那一次探测发生在 gateway 零负载时。
- 角色的 `CONNECTION LIMIT` 必须不小于 Pool Size：否则并发一高，Supavisor 去开超过角色上限的服务端连接时会直接报错，不会排队。所以两者都是 20。
- 演练时同时跑一次对话、一次同步、一次资料页渲染，用 `SELECT usename, count(*) FROM pg_stat_activity GROUP BY 1` 看峰值，超出预算时再议升级 Small（13.1）。P3-6 另做一次 reader 并发压测（2.4、P3-6）。

### 6.5 配置与代码改动

- **`docker/Dockerfile.pick-gateway`：** 构建步骤改为 `uv sync --locked --no-dev --extra postgres`。asyncpg、psycopg、psycopg-pool、langgraph-checkpoint-postgres 都已在 `backend/uv.lock` 里，只是现在没有安装；不改的话，`init_engine` 会报 ImportError。
- **`backend/app/gateway/pick_entrypoint.py`：**
  - `prepare_config(home, template, backend)` 多一个显式参数，不在函数里读环境变量，现有测试 `backend/tests/test_pick_cloud_entrypoint.py` 只需补上 `backend="sqlite"`。
  - `main()` 读 `PICK_DB_BACKEND`，取值只能是 `sqlite` 或 `postgres`；没设或取值不对，就以非零状态退出。
  - `postgres` 时：`PICK_DATABASE_URL` 必须存在，URL 不能带 `sslmode`/`ssl` 参数，否则退出。然后写入下面的 database 段，并去掉 `sqlite_dir`。
  - `sqlite` 时保持现在的行为，本地开发和回滚都走这条路。

  ```yaml
  database:
    backend: postgres
    postgres_url: $PICK_DATABASE_URL   # 字面占位符，由 AppConfig 解析；/data 上的运行时 yaml 不含密钥
    postgres_schema: deerflow
    pool_size: 3
    pool_recycle: 300
    command_timeout: 30
    checkpoint_channel_mode: full
  ```

- **不写 `checkpointer:` 段。** checkpointer 和 store 从 `database:` 推导：AsyncPostgresSaver 用 psycopg 连接池（autocommit、`prepare_threshold=0`、keepalive），另加 AsyncPostgresStore。它们第一次启动时，经 search_path 在 deerflow 下建表。
- **`config.pick.example.yaml` 不改**，仍是 sqlite，供本地使用。
- **`railway.toml` 不改**，仍是 `numReplicas = 1`，健康检查仍是 `/health/ready`。
- **新增 `backend/app/gateway/auth/create_user.py`：** 宿主只有 `/auth/initialize`（首个管理员）和 `/auth/register`，后者被 `config.pick.example.yaml` 的 `allow_registration: false` 关掉，没有「管理员建账号」的接口或页面。照 `reset_admin.py` 的写法做一个命令行工具：`python -m app.gateway.auth.create_user --email <邮箱> [--role user|admin]`，经 `LocalAuthProvider.create_user` 建号，随机初始密码写进 `$DEER_FLOW_HOME/credentials/<邮箱>.txt`（权限 0600），不打印；邮箱已存在时报错退出。切换后经 `railway ssh` 为其他成员建号（6.8 第 8 步）。
  - **不依赖 `get_app_config` 的默认查找。** `pick_entrypoint.main()` 只在自己的进程里设 `DEER_FLOW_HOME`、`DEER_FLOW_CONFIG_PATH`、`DEER_FLOW_PROJECT_ROOT`、`DEER_FLOW_EXTENSIONS_CONFIG_PATH`（`pick_entrypoint.py:30-35`），Railway 变量里没有它们；镜像的 WORKDIR 是 `/app`，只有 CMD 里 `cd backend`（`docker/Dockerfile.pick-gateway`）。单独开的 `railway ssh` 会话里，直接跑会先因为 `app` 不在导入路径上报 ModuleNotFoundError；`cd backend` 之后又会因为找不到 `config.yaml` 报 FileNotFoundError（`backend/packages/harness/deerflow/config/app_config.py:392-412`、同目录 `runtime_paths.py:16,34-41`），credentials 也会落到 cwd 下的 `.deer-flow`，不在 `/data` 上。
  - 做法：从 `pick_entrypoint` 抽出一个共用函数，`main()` 和 `create_user` 都调用它。变量没设时补缺省值：`DEER_FLOW_HOME=/data`，`DEER_FLOW_CONFIG_PATH=$DEER_FLOW_HOME/pick-runtime.yaml`，`DEER_FLOW_EXTENSIONS_CONFIG_PATH=$DEER_FLOW_HOME/extensions_config.json`，`DEER_FLOW_PROJECT_ROOT` 与 `main()` 相同。`create_user` 不重写运行时 yaml，文件不存在时非零退出并说明「先启动一次 gateway」；credentials 写到 `$DEER_FLOW_HOME/credentials`。
  - 同一规则用于 P2 经 `railway ssh` 跑的命令（`ggwork_pick.mirror.admin accept-empty`/`cleanup`、`ggwork_pick.mirror.series --backfill`，5.3、5.6、第 9 节第 6 步）：命令写全 `cd /app/backend && DEER_FLOW_HOME=/data python -m …`，入口不依赖 cwd，缺变量时非零退出。

### 6.6 checkpointer 与 store

- 保持 full 模式。
- 上线后每周记录一次 `pg_total_relation_size` 的 `deerflow.checkpoints`、`deerflow.checkpoint_blobs`、`deerflow.checkpoint_writes`，以及 `pg_database_size('postgres')`，写进运行手册。
- 告警阈值：数据库超过 5 GB（Pro 含 8 GB 磁盘；5.2 第 2 步的同步容量闸门在 6 GiB）。
- 超过阈值时的处理：经线程的 DELETE 接口删掉不再需要的旧会话（`backend/app/gateway/routers/threads.py:775-776` 调 `checkpointer.adelete_thread`），和/或在控制台扩磁盘。
- 不写「启用 checkpoint retention」：`backend/app/gateway/checkpoint_retention.py` 没有生产触发点（`:26-29`，仓库里也没有调用 `enforce_thread_retention` 的地方），它还保护恢复点的整条祖先链，只剪只有 duration 的叶子和（可选的）叶子兄弟分支，长对话的主链一条不剪。要接上它是单独一项需要评审的任务：触发时必须持有 `_checkpoint_thread_lock`（`:364-372`）。

### 6.7 扩展的 PG 兼容审计

| 项目 | 现状 | 处理 | 测试 |
|---|---|---|---|
| text 列里的 NUL | SQLite 能存；PG 的 text 拒收 `\x00` | `StrictInput` 加 `model_validator(mode="before")`，递归拒绝含 `\x00` 的字符串，覆盖 SaveInput、UpdateInput、PickConditions | PG 上带 NUL 的 note 返回 422，不再是 500 |
| 孤立代理项 | Python 的 JSON 解码会把 `\ud800` 这类转义还原成孤立代理项；asyncpg 和 psycopg 按严格 UTF-8 编码参数，遇到它直接抛 `UnicodeEncodeError`（与 JS 驱动不同，JS 会悄悄换成 U+FFFD） | 同一个校验器拒绝；feed 路径已有 `errors="replace"` | 同上 |
| JSON 列 | `sa.JSON` 在 PG 上是 `json` | 保持 `json`，它接受 JSON 转义形式的 NUL | 带转义 NUL 的 conditions 能写能读 |
| 宿主表与宿主 JSONB（消息、run events、checkpoint metadata） | jsonb 拒收转义的 NUL；孤立代理项在驱动编码时就失败。本机 PG 17 用宿主的序列化器实测：字符串消息里的 NUL、字符串与字典消息里的孤立代理项都写不进去，换成 U+FFFD 后都能写 | 条件已成立，P0 就做：pick 入口服务 `app.gateway.pick_asgi:app`，网关外包一层纯 ASGI 的 `JsonBodySanitizer`，只对 JSON 请求体把 NUL 和孤立代理项（转义与直接的 UTF-8 字节两种）替换为 U+FFFD；模型输出不经过这一层，是已知限制 | `backend/tests/test_json_body_sanitizer.py`；演练时仍发一条含 NUL、一条含孤立代理项的消息各走完一次选剧对话，预期都成功 |
| 会话级设置残留 | ORM 连接放回池后仍保留会话级 `SET` 和 search_path | 代码里只用 `SET LOCAL`（3.6）；镜像写入不走 ORM 池（5.2） | 连接放回池再取出后，`SHOW lock_timeout` 是默认值，`SHOW search_path` 仍是 deerflow |
| VARCHAR 长度 | SQLite 不检查，PG 检查 | 按 PG 限制做边界测试：String(64) thread_id、String(128) run_id 和 tool_call_id、String(256) message_id、String(512) identity、String(500) 知识标题。真实 id 可能超长时，在 0005 里放宽 | `test_pg_limits.py` |
| 迁移 0001–0004 | `render_as_batch` 和 `has_table` 检查；约束有名字 | 在 PG 上原样执行，无需修改 | 从空库升级到 head，再升一次保持幂等 |
| `_write` 的锁 | 已有 postgresql 分支，用 `pg_advisory_xact_lock` | 无需修改 | 在 PG 上并发保存，只留下一条 |
| ISO 字符串时间戳排序 | 字典序比较 | 所有写入方都必须用 `datetime.now(UTC).isoformat()` | 已有测试在 PG 上再跑一遍 |
| 排序规则 | ORDER BY text 受数据库 collation 影响 | Supabase 已实测为 `en_US.UTF-8`（6.1），还要查 Neon 的 `SHOW lc_collate`。不一致时，只影响资料页的「剧名」排序，由 P4-3 的核对脚本判断 | 核对脚本 |
| 每次工具调用整批加载 | `repository.catalog_rows` 每次读约 5.4 MB JSON，库搬到网络上之后更慢 | 每个进程加一个 LRU，最多 2 个批次，键为 batch_id。只在 `_require_batch` 通过之后读缓存；prune 时驱逐；调用方不得修改返回的行 | 第二次调用不再读库；越权用户即使有缓存也拿到 LookupError；`matching_rows` 和 `candidate_item` 调用前后，行数据深比较相同 |

### 6.8 切换步骤

约 30 分钟的窗口。开始前 P0-1 到 P0-5 必须全部完成。

1. **公告。** 说明旧会话、已保存的选择、候选卡和回答核对都不会带到新库，所有人需要重新登录。
2. **备份。** 用 `railway ssh` 进入 gateway 容器执行下面的命令。镜像里没有 sqlite3 命令行工具，所以用 Python。`/data/backup` 目前不存在，`sqlite3.connect` 不会建目录，所以先建：

   ```bash
   mkdir -p /data/backup && chmod 700 /data/backup
   python - <<'PY'
   import sqlite3
   src = sqlite3.connect("/data/data/deerflow.db")
   dst = sqlite3.connect("/data/backup/deerflow-sqlite-20260923.db")
   src.backup(dst)
   print(dst.execute("PRAGMA integrity_check").fetchone())
   PY
   sha256sum /data/backup/deerflow-sqlite-20260923.db
   ```

   sha256 记进 `docs/pick-workbench/supabase.md`。原文件和 blob 目录都不动。
3. **在本机先建管理员。** 这一步是为了堵住空库时任何人都能调 `/api/v1/auth/initialize` 抢注管理员的窗口。
   0. 先确认正式项目 `deerflow` schema 是空的（P0-5 末尾的查询），不空就停下排查。
   1. **本机代码必须与要部署的镜像同一个 commit。** 用 `git worktree add <scratch>/gw-cutover <镜像的 SHA>` 检出，在那里装 backend 环境（`--extra postgres`）。本机代码如果更新（例如已含 0005/0006），库里的 alembic head 会领先于 Railway 镜像，镜像启动就会失败。
   2. 设置 `PICK_DB_BACKEND=postgres`、`PICK_DATABASE_URL`、`PGSSLMODE=require`，以及 `DEER_FLOW_HOME=<scratch>/gw-cutover-home`：`main()` 里 `DEER_FLOW_HOME` 缺省是 `/data`（`backend/app/gateway/pick_entrypoint.py:30`），macOS 上建不了。不设 feed token，这样不会触发同步。另设三个占位值 `AZURE_OPENAI_DEPLOYMENT=unused`、`AZURE_OPENAI_BASE_URL=http://127.0.0.1:9`、`AZURE_OPENAI_API_KEY=unused`：运行时 yaml 从 `config.pick.example.yaml` 抄来模型段的 `$AZURE_OPENAI_*`，`AppConfig.resolve_env_variables` 遇到没设的变量就抛 ValueError，`create_app()` 和 lifespan 都读配置，gateway 起不来。开发机上是 `app_config.py` 的 `load_dotenv()` 往上找到仓库根被 git 忽略的 `.env` 才把它盖住，新 worktree 上面没有 `.env`。`/setup` 不调模型，真实密钥不必上本机（评审 P2；运行手册 8.3.3 与第 9 节同步，`backend/tests/test_pick_cloud_entrypoint.py` 只用运行手册那一步 export 的变量、在不读 `.env` 的子进程里建一次 gateway 应用）。
   3. 运行 `python -m app.gateway.pick_entrypoint`。启动过程会完成宿主建表、checkpointer 和 store 建表，以及 ggwp 迁移 0001–0004。
   4. 本机起前端（`pnpm dev`，`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8001`），打开 `/setup` 建管理员。
   5. 确认 `GET /api/v1/auth/setup-status` 返回 `needs_setup=false`，然后停掉本机网关，删掉 worktree 和 scratch home（里面有 JWT 密钥文件）。
4. **设置 Railway 变量。** 在控制台 Variables 的 Raw Editor 里粘贴，不走命令行参数：
   - `PICK_DB_BACKEND=postgres`
   - `PICK_DATABASE_URL`
   - `PGSSLMODE=require`
5. **部署新镜像。** `/health/ready` 必须返回 200。
6. **立即用管理员登录。** 如果登录不上，或者 setup-status 显示需要初始化，立刻停服务并排查。
7. **确认补跑同步。** 新库里没有成功的同步记录，所以 gateway 启动 60 秒后会补跑一次，共享剧库因此重新同步。在 `/api/pick/sync` 确认同步成功、行数合理。
8. **重建其他账号。** 宿主没有建账号的后台页面，用 P0-3 新增的命令行工具：`railway ssh` 进 gateway 容器，先确认变量在这个会话里可见（只看有没有，不打印值）：`for v in PICK_DATABASE_URL PGSSLMODE PICK_DB_BACKEND; do printenv "$v" >/dev/null && echo "$v set" || echo "$v MISSING"; done`，有缺的就停下排查（Railway 的 ssh 文档没写会话是否继承服务变量，P0-5 先在演练项目上确认），不在命令行里手打连接串。然后逐个执行 `cd /app/backend && DEER_FLOW_HOME=/data python -m app.gateway.auth.create_user --email <邮箱>`（入口会补齐 `DEER_FLOW_CONFIG_PATH=/data/pick-runtime.yaml` 等缺省值，6.5；也可以显式写出）。初始密码从 `/data/credentials/<邮箱>.txt` 取出、经密码管理器发给本人后删掉文件。本地自助注册保持关闭。`PICK_E2E_EMAIL` 那个账号只在本机 QA 实例上用（`docs/pick-workbench/local-run.md:97`：自动测试不能指向个人业务实例），不在生产库重建。
9. **验证：**
   - deerflow 下的表齐全，public 下没有这些表；
   - 两个驱动的 `SHOW search_path` 都是 deerflow；
   - `pg_stat_ssl` 显示连接已加密；
   - 一次选剧对话端到端跑通（含工具调用），`checkpoints` 表有行；
   - 保存并导出一次选择；
   - NUL 消息测试通过；
   - 按 `docs/pick-workbench/acceptance.md` 重跑 10 题验收；
   - 用 `pg_stat_activity` 查看连接峰值。

### 6.9 旧 SQLite 备份

- 原文件 `/data/data/deerflow.db` 和备份 `/data/backup/deerflow-sqlite-<日期>.db` 都保留至少 30 天，备份的 sha256 记在运行手册里。
- 备份只能用于回滚，不能在应用里浏览。

### 6.10 回滚

- **48 小时内：** 把 `PICK_DB_BACKEND` 改成 `sqlite`，重新部署。新镜像在 sqlite 模式下行为与旧版相同，会重新打开原来的 SQLite 文件。这段时间写进 PG 的数据不会合并回来，本来就是重新开始，可以接受。
- **之后：** 只保留 SQLite 备份。

## 7. 前端「选剧资料」页

### 7.1 子 tab 与旧选剧台的对应

| 子 tab（`?tab=`） | 对应旧 tab | 数据 | 差异 |
|---|---|---|---|
| 选剧（`pick`，默认） | 选剧 | `loadPickRows`、`loadFacets` | 带 `&result=` 时是回放视图（2.5 第 4 条） |
| 全部剧库（`all`） | 全部剧库 | 同上 | 无 |
| 榜单（`rank`） | 榜单：13 张剧场榜、7 张 ReelShort 榜、分成对账 | `loadRankMeta`、`loadRankRows`、`loadRsRank` | rs_bill 榜按 RealShort 导出的分成名次 `bill_rank` 排，顺序与旧页相同，但不显示金额；「分成对账」改名「订单对账」，没有金额，同一天同一推广类型合并成一行（「行数」合计仍按原始行数，另列合并后的行数，7.4） |
| 发布记录（`posted`，`&sd=` 为单条） | 发布记录 | `loadPostedList`、`loadPostedStats`、`loadAccounts`、`loadPostedRecord` | 无 |
| 剧场规则（`rules`） | 剧场规则，含术语表 | 版本的 `meta.rules` | 规则与所用版本同一次采集 |
| 证据页（`row`，`&row=`） | 证据页 | `loadRowDetail`、`loadReelshortDetail` | 网盘区块只显示有无（`has_pan`），链接和提取码换成说明加 RealShort 链接；没有 USD 区块；曲线早于 `trimmed_before` 的点已清理时注明（3.2） |
| 同步与导入（`imports`） | 无（工作台原有页面） | 浏览器调 `/api/pick/*` | 新增镜像版本状态 |

### 7.2 URL 参数

- **沿用 RealShort 的参数，名字和含义都不变：**
  - `tab`、`sort`、`platform`、`lang`、`basis`、`posted`、`off`、`sig`、`w`、`yt`、`dated`、`inuse`、`q`、`size`、`page`；
  - `row`、`from`、`rk`、`day`、`week`、`grade`、`rs`、`rl`、`bk`、`pst`、`sd`。

  所以旧的 `/admin/pick?…` 链接，把路径换成 `/workspace/pick-data` 就能打开。
- **新增的参数：**
  - `v`：版本 id，只接受 1–9 位数字；
  - `result`：智能体结果 id，只接受 32 位十六进制；
  - `tab=imports`。
- **`pickQuery` 的写回规则：**
  - 版本已解析时总是写上 `v`；
  - `result` 只在 `tab=pick` 时写。

### 7.3 从 RealShort 复用的文件

**纯模块，放到 `frontend/src/core/pick-board/`：**

| 源 | 目标 | 改动 |
|---|---|---|
| `rs:src/lib/pick/request.ts`（502 行） | `request.ts` | TABS 加 `imports`；PickRequest 加 `v`、`result`；`parsePickRequest` 和 `pickQuery` 同步修改；其余不动 |
| `rs:src/lib/pick/platforms.ts` | `platforms.ts` | 保留类型（`YoutubeRule`、`PlatformRule`）、`YOUTUBE_LABEL` 和 `POSTED_POOL_URL`。`youtubeStatus` 改成显式接收规则的纯函数 `youtubeStatus(rules, platform, onList)`，不再读模块常量（原 `:186-193` 读 `PLATFORM_RULES`）。`PLATFORMS`、`PLATFORM_LABELS`、`IN_USE` 其实在 `rs:src/lib/pick/request.ts:164-192`，随 `request.ts` 移植。`PLATFORM_RULES` 与 `IN_USE` 不再是模块常量，改为按请求从版本的 `meta.rules` 构造（见下方 `queries.ts` 一行），组件经 props 拿到（见组件表）。`doc` 字段里的站内相对链接 `/admin/pick?…`（如 `:46` 的 `/admin/pick?tab=rank&rk=rs_rr`）在构造时改写成 `/workspace/pick-data?…` 并带上 `v`。`meta.rules` 里出现本地 `PLATFORMS` 没有的键时，显示「RealShort 新增了剧场，部分标签可能过时」 |
| `rs:src/lib/pick/glossary.ts` | `glossary.ts` | 只保留类型和 `filterGlossary`，数据来自 meta |
| `rs:src/lib/pick/spark-geometry.ts` | `spark-geometry.ts` | 原样 |
| `rs:src/lib/pick/growth-diagnosis.ts` | `growth-diagnosis.ts` | 原样 |
| `rs:src/lib/observe/metrics.ts` | `metrics.ts` | `:210` 的「分成对账」改成「订单对账」（7.1）；`:221` 的排序标签「预估分成」不改：它按导出的名次 `bill_rank` 排，顺序与旧页相同（13.3）。其余原样 |
| `rs:src/lib/observe/chart.ts` | `chart.ts` | 原样 |
| `rs:src/lib/observe/source-types.ts` | `source-types.ts` | 加 `pick_catalog` |
| `rs:src/lib/cps/lang.ts` | `lang.ts` | 只取 `LOCALE_CODES` 和 `localeFromCode` |
| `rs:src/lib/site.ts` | `site.ts` | 只取 `dramaPath`，返回 `https://dramashortstv.com/...` 绝对地址 |
| `rs:src/components/ui/pager.tsx` | `pager.ts` | 只取纯函数 `pageSequence` |
| `rs:src/lib/pick/catalog-import.ts` | `catalog-lang.ts` | 只取 `LANG_LOC` 和 `RawPost` 类型 |

**服务端代码，放到 `frontend/src/server/pick-board/`，每个文件第一行都是 `import "server-only"`：**

| 源 | 目标 | 改动 |
|---|---|---|
| `rs:src/db/index.ts` | `db.ts` | 重写，见 7.5 |
| `rs:src/lib/pick/queries.ts`（384 行） | `queries.ts` | SQL 文本不改。`loadFreshness` 改读 meta；`:329` 的 `FROM dramas` 改为 `rs_ids`。模块常量 `YT_BLOCKED`、`YT_LIST_ONLY`（`:86-87`）和 `filtersFor` 里用的 `IN_USE`（`:106`）改成从本请求的版本规则现算：`const rules = boardScope().rules`，筛选出 `yt === "no"`、`yt === "only"` 的剧场。否则 RealShort 一改规则，资料页的 yt、inuse 筛选就和 RealShort、和智能体不一致。toolbar 和 rules-tab 里的 `IN_USE` 同样改读版本规则。新增 `loadRowsByKeys(keys)` 供回放用，放在文件末尾，注明不是 RealShort 原有 |
| `rs:src/lib/pick/queries-shared.ts` | `queries-shared.ts` | ROW_COLUMN_NAMES 和 PickRow 去掉 pan_url、pan_pw，换成 `has_pan`/`hasPan`（13.3） |
| `rs:src/lib/pick/queries-posted.ts` | `queries-posted.ts` | 只改 `:220` 的 `FROM dramas`，换成 `rs_ids` |
| `rs:src/lib/pick/queries-rank.ts`（342 行） | `queries-rank.ts` | 剧场榜的一半原样照搬，包括 jsonb LATERAL 日榜和 kw 周榜；rs 榜的一半改调 `rs-queries.ts` |
| `rs:src/lib/pick/queries-reelshort.ts` | `queries-reelshort.ts` | 重写：`reelshortBranch()` 改成 `SELECT <同形列> FROM rs_rows`；`catalogBranch()` 的 pan 列换成 `has_pan`；`loadReelshortDetail` 改读 rs_rows、rs_ids（先解析正典）、series（按正典 id，`unnest` 成逐日行，3.2）、rs_clicks14、rs_bill_orders |
| `rs:src/lib/observe/queries.ts` 里用到的部分（原文件 925 行） | `rs-queries.ts`（约 350 行） | 重写成对物化列的过滤和排序，见下文 |
| `rs:src/lib/observe/source-state.ts` 的 `readSources` | `source-state.ts` | 改读 `meta.sources` |
| 无（新建） | `version.ts`、`auth.ts`、`gateway.ts`、`replay.ts`、`index.ts`、`PORTED_FROM` | 见 P3、P4 |

`rs-queries.ts` 要提供的函数：
- `loadRows`：原 `orderBy` 的各分支按列逐一对应。
  - `bill` 排序改成 `bill_rank ASC NULLS LAST, drama_id ASC`，与原来的 `b.usd DESC NULLS LAST, dramas.id ASC` 逐行一致（4.4）；
  - 每条排序都以 `drama_id` 收尾，同原来以 `dramas.id` 收尾。
- `loadRowsByIds`；
- `loadRsCounts`：对 rs_rows 做 count FILTER；`ledger` 取 `rs_bill_orders` 的 `sum(source_rows)`（原始行数），这样 rs_ledger 的计数与 RealShort 相同；
- `loadGrowthBaseline`：读 meta；
- `loadBillRows`、`loadBillTotals`：只含订单，不含金额；合计的「行数」取 `sum(source_rows)`，合并后的行数另给（7.4）；
- `loadDramaDetail`。

**组件，放到 `frontend/src/components/workspace/pick-board/`：**

| 源（`rs:src/components/admin/pick/`） | 改动 |
|---|---|
| rank-table、posted-record、accounts-table、spark、reelshort-spark | 只改 import，`<Link>` 加 `prefetch={false}` |
| rows-table | 同上，另外把 `rules` prop 传给 `ResourceCell`（原 `:51`、`:80` 不传），所以不只是改 import |
| rank-filters | 同第一行，另外 GET 表单的 `action` 改成 `/workspace/pick-data`，并加隐藏的 `v` |
| posted-table | 同 rank-filters；`POSTED_POOL_URL` 仍从保留的常量取（`:4`、`:54`） |
| toolbar | `pickHref` 前缀改为 `/workspace/pick-data`；Tabs 加「同步与导入」；Filters 表单同样加隐藏的 `v` |
| cells | `ResourceCell` 多一个 `rules` prop，YouTube 标签用 `youtubeStatus(rules, …)` 算（原 `:383` 读模块常量）；网盘部分按 `has_pan` 显示「有网盘」或「无网盘」，不显示链接和提取码，「有网盘」旁给 RealShort 证据页链接；「分成」标签换成「订单 N 笔」 |
| sources | `SOURCE_LABELS` 加 `pick_catalog`（与 P1-5 同步） |
| row-detail | `rules[row.platform]` 和 `youtubeStatus` 都从 props 拿（原 `:49-50`），YouTube、报备、标签、解禁、素材、回传、文档、核对日期这几项（`:90-127`）随版本规则；「素材」一格的网盘链接与提取码换成按 `has_pan` 的「有网盘（链接与提取码不同步，到 RealShort 证据页查看）」或原文「这一行剧单没附网盘」 |
| reelshort-table | 删掉 5 列 USD；分成排序按 `bill_rank` |
| reelshort-cells | 删掉金额单元格 `Money` |
| reelshort-detail | `rules.reelshort` 从 props 拿（原 `:73`，素材与回传在 `:125`）；删掉我方分成的金额块；明细只显示订单，不显示 promotion_value |
| bill-table | 重写为 `orders-table.tsx`。列：日期、剧、推广类型、订单数、同日站内出站。合计：行数（`sum(source_rows)`，与 RealShort 的原始行数相同）、合并后的行数、订单数、同日有出站的行数 |
| rules-tab | 改从 `meta.rules` 渲染 |
| glossary（client） | 数据改从 props 传入 |
| queyu-button（client） | 原样 |

**版本规则怎么到组件：** 视图从 `page.tsx` 的 `view(req, meta, asOf)` 拿到 `meta.rules`，作为 `rules` prop 一层层传给 rows-table、cells、row-detail、reelshort-detail、rules-tab。组件不调 `boardScope()`：P3-4 规定组件从 server 模块只能 import 类型。只靠构建报错发现不了「保留静态常量」这种实现，所以 P3-4 加 DOM 用例守住。

**页面：**
- `rs:src/app/admin/(protected)/pick/page.tsx`（334 行）改写为 `frontend/src/app/workspace/pick-data/page.tsx`。
- 各视图拆到 `frontend/src/components/workspace/pick-board/views/`：`list-view.tsx`、`rank-view.tsx`、`posted-view.tsx`、`detail-view.tsx`、`replay-view.tsx`、`imports-view.tsx`。
- 每个文件不超过 400 行。

**移植的测试，放到 `frontend/tests/unit/core/pick-board/`：**
- 来源：`rs:tests/` 下的 `pick-request.test.ts`、`pick-request.regression-1.test.ts`、`pick-rank-week.test.ts`、`pick-spark.test.ts`、`pick-growth-diagnosis.test.ts`、`pick-sort-hints.test.ts`。`pick-queyu.test.ts` 不移植：它读 `scripts/juyuantai/*`、`vercel.json` 和 `package.json` 的 scripts（`:10-15`、`:63-66`），这些都不搬，留在 RealShort 用它自己的 `pnpm test` 跑。
- 通用转换：把 `node:test` 的 `test` 换成 `@rstest/core` 的 `test`，`node:assert/strict` 保留。
- 逐个文件的规则（「原样通过」只对第一类成立）：
  - `pick-request`、`pick-spark`、`pick-growth-diagnosis` 里的纯函数用例：原样移植。
  - 源码形状测试（`pick-request.regression-1` 的 `:55-66`、`pick-sort-hints` 的 `:11-13`、`pick-rank-week` 里的接线测试 `:51-58`）：按 cwd 读的路径改写成 `frontend/src/{components/workspace/pick-board,core/pick-board,server/pick-board}`，rowHref 的扫描范围包括 `views/`。
  - `pick-rank-week` 里断言 `src/lib/ask/tools.ts` 的部分（`:61-70`）删掉：问答不移植。
  - `pick-sort-hints` 断言 `glossary.ts` 条目文字的部分（`:14`、`:85-86`）：移植后术语表数据来自 meta，改为对 meta 夹具断言，或者留在 RealShort。
  - `pick-growth-diagnosis` 断言 `observe/queries.ts` SQL 文本的部分（`:67-76`，如 `dramas.promoters_cnt - s1.promoters_cnt`、`s1.valid = 'true' …growth_dp1`）：`rs-queries.ts` 是重写的，这些断言改成 P3-3 `queries.integration.test.ts` 里对夹具结果的断言（dp1 排序、comparableOnly、growth_dp1 计数）。
- 验收命令分两个仓库：工作台 `frontend/` 下 `pnpm test`；RealShort 根目录 `pnpm test`（`pick-queyu` 和留下的断言在那边跑）。

**不移植：**
- 问答相关组件：`ask-drawer.tsx`、`ask-turn.tsx`、`ask-markdown.tsx`、`ask-steps.ts`、`ask-store.ts`、`ask-hotkeys.ts`；
- `rs:src/app/admin/(protected)/pick/ask-actions.ts`；
- `rs:src/lib/ask/*`；
- `rs:src/lib/pick/observe-redirect.ts`。

**工作台现有文件的改动：**

| 文件 | 改动 |
|---|---|
| `frontend/src/app/workspace/pick-data/page.tsx` | 整体改写 |
| `frontend/src/components/workspace/pick/data-imports.tsx`、`sync-status.tsx` | 去掉外框和 h1；SyncStatus 加显示镜像版本 |
| `frontend/src/components/workspace/pick/pick-welcome.tsx:35`、`frontend/tests/e2e-pick/personal-selection.spec.ts:84` | 链接改指 `?tab=imports` |
| `frontend/src/components/workspace/workspace-sidebar.tsx` | 「选剧资料」入口加 active 状态 |
| `frontend/src/core/auth/server.ts` | 导出 `getServerSideUserCached = cache(getServerSideUser)` |
| `frontend/src/app/workspace/layout.tsx` | 改用 `getServerSideUserCached`，与页面去重 |
| `frontend/src/env.js` | server 段加可选的 `PICK_MIRROR_READER_URL`、`PICK_MIRROR_CA_PEM` |
| `frontend/src/styles/globals.css` | `@import "./pick-board.css"` |
| `frontend/performance-budgets.json` | 加 `/workspace/pick-data`，数值取首次构建实测值加 10% |
| `frontend/package.json` | 加 `drizzle-orm`（与 RealShort 同一主版本）、`pg`、`@types/pg`、`server-only`、`@vercel/functions` |
| `frontend/rstest.config.ts` | 仅在测试里把 `server-only` 别名到一个空模块 |
| `frontend/src/core/pick/types.ts`、`candidate-view.tsx`、`pick-tool-card.tsx` | 核对链接，见 P4-1 |

### 7.4 改写要点

- **与旧页的差异写在页上。** 页脚固定一段「与 RealShort 选剧台的差异」：
  - 网盘只显示有没有，不显示链接和提取码；
  - 没有分成金额；分成排序按 RealShort 导出的名次，顺序与旧页相同；
  - 订单对账同一天同一推广类型合并成一行（不同 promotion_value 合并）；合计的「行数」仍是原始行数，与旧页和 rs_ledger 计数相同，合并后的行数另列；
  - 数据截至 as_of，不是实时数据。
- **空态文案。** 原来的「先跑 scripts/juyuantai …」改为「镜像里还没有剧单，看「同步与导入」」。
- **鹊娱链接和 ReelShort 公开页链接保留。** `dramaPath` 改成绝对地址。
- **页头：** h1 为「选剧资料」，下面是新鲜度一行（2.5 第 9 条）和版本横幅。
- **`generateMetadata`：** 标题为「选剧资料 · <tab 名>」。

### 7.5 数据库层草图（`frontend/src/server/pick-board/db.ts`）

**版本范围怎么传：** 用 React `cache()` 做一个按请求隔离的容器，不用 AsyncLocalStorage。

- 原稿的 `withVersion(scope, () => view(...))` 只包住了 `view()` 的同步调用，而 `view()` 只是返回 `<ListView/>` 这类元素。ListView、RankView、DetailView、PostedView 都是异步组件（`rs:src/app/admin/(protected)/pick/page.tsx:72-85` 与各视图定义），要等 React 在自己的调度里才调用，那时已经在 `run()` 之外，`getStore()` 为空，每个 tab 都会抛错。只用假 client 的单测发现不了。
- React 的 `cache()` 在一次 RSC 渲染里对同一个参数返回同一个对象，不同请求之间不共享。页面在返回任何 JSX 之前调 `setBoardScope()` 写入一次，之后子组件里的查询都从这里读。
- 没有 React 渲染的场合（P4-3 核对脚本、单测）另给一个 `withScriptScope(scope, fn)`：用 AsyncLocalStorage，只在 `fn` 里直接 await 查询时有效。`getDb()` 先看它，再看 React 容器。

```ts
import "server-only";
import { AsyncLocalStorage } from "node:async_hooks";
import { cache } from "react";
import { attachDatabasePool } from "@vercel/functions";
import type { SQL } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";
import { Pool, types } from "pg";

const SCHEMA = /^pickm_v\d{6}$/;
// 与 drizzle-orm 0.45.2 neon-http 的 initMappers 同一份清单（node_modules/drizzle-orm/neon-http/driver.js）：
// timestamptz、timestamp、date、interval，以及 numeric[]、timestamp[]、timestamptz[]、interval[]、date[] 都原样返回字符串。
// 少一个，移植的查询拿到的形态就与 RealShort 不同（例如 toDate(String(Date)) 把新鲜度时间变成 null）
const TEXT_OIDS = new Set([1184, 1114, 1082, 1186, 1231, 1115, 1185, 1187, 1182]);
const typeOverrides = {
  getTypeParser: (oid: number, format?: "text" | "binary") =>
    TEXT_OIDS.has(oid) ? (v: string) => v : types.getTypeParser(oid, format),
};

export interface VersionScope { schema: string; asOf: string; versionId: number; rules: BoardRules }

/** 一次 RSC 渲染内是同一个对象；跨请求不共享 */
const requestHolder = cache((): { scope: VersionScope | null } => ({ scope: null }));
const scriptScope = new AsyncLocalStorage<VersionScope>();
const dialect = new PgDialect();
let pool: Pool | null = null;

function checked(s: VersionScope): VersionScope {
  if (!SCHEMA.test(s.schema)) throw new Error("bad mirror schema");
  return Object.freeze({ ...s });
}

/** 页面在返回任何 JSX 之前调用一次；同一请求里再设成别的版本就报错 */
export function setBoardScope(s: VersionScope): void {
  const holder = requestHolder();
  if (holder.scope && holder.scope.versionId !== s.versionId) throw new Error("board scope already set");
  holder.scope = checked(s);
}

/** 只给脚本和测试用：没有 React 渲染，fn 里直接 await 查询 */
export function withScriptScope<T>(s: VersionScope, fn: () => Promise<T>): Promise<T> {
  return scriptScope.run(checked(s), fn);
}

export function boardScope(): VersionScope {
  const s = scriptScope.getStore() ?? requestHolder().scope;
  if (!s) throw new Error("pick-board query outside a version scope");
  return s;
}

function getPool(): Pool {
  if (pool) return pool;
  const url = process.env.PICK_MIRROR_READER_URL;
  if (!url) throw new MirrorUnavailable();
  if (/[?&](sslmode|ssl)=/.test(url)) throw new Error("PICK_MIRROR_READER_URL 不能带 SSL 参数");
  pool = new Pool({
    connectionString: url, max: 3, idleTimeoutMillis: 5_000, connectionTimeoutMillis: 5_000,
    types: typeOverrides, ssl: { rejectUnauthorized: true, ca: process.env.PICK_MIRROR_CA_PEM },
  });
  attachDatabasePool(pool);
  return pool;
}

async function run<R>(searchPath: string, query: SQL): Promise<{ rows: R[] }> {
  const { sql: text, params } = dialect.sqlToQuery(query);
  const client = await getPool().connect();
  try {
    await client.query(`BEGIN READ ONLY; SET LOCAL search_path TO ${searchPath}`);
    const res = await client.query({ text, values: params as unknown[] });
    await client.query("COMMIT");
    return { rows: res.rows as R[] };
  } catch (err) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw err;
  } finally {
    client.release();
  }
}

/** 只给 resolveVersion 用：不带版本，只能看 pick_mirror */
export function controlDb() {
  return { execute: <R,>(query: SQL) => run<R>("pick_mirror", query) };
}

/** 移植的查询只用 execute；每条 SQL 一个只读事务，版本不可变所以并行安全 */
export function getDb() {
  return {
    execute: <R,>(query: SQL) => run<R>(`${boardScope().schema}, pick_mirror`, query),
  };
}
```

**`page.tsx` 的执行顺序：**
1. `requireBoardUser()`：鉴权，不通过时跳转或只显示提示（2.4）。
2. `parsePickRequest(searchParams)`：解析 URL 参数。
3. `tab === "imports"` 时直接渲染 ImportsView。这个 tab 不查镜像库，所以 Preview 部署也能用。
4. `resolveVersion(req.v)`：经 `controlDb()` 只查 `pick_mirror.versions` 和版本的 `meta.rules`，得到 schema、as_of、规则、是否当前版本、当前版本 id、是否已清理。
5. `setBoardScope(scope)`：在返回任何 JSX 之前写入。
6. 返回 `view(req, meta, asOf)` 以及横幅、新鲜度和 Sources。子组件里的查询在 React 调度它们时从容器读到版本；规则类数据（`meta.rules`）经 props 传给组件（7.3）。
7. `generateMetadata` 不查镜像库，只用 `parsePickRequest` 得到 tab 名。

### 7.6 颜色 token

**需要映射的 token：** 在 `frontend/src/styles/pick-board.css` 里用 Tailwind v4 的 `@theme inline`，把这些名字映射到工作台变量，亮色和暗色都要给值：
- 文字：helper、ink-1、ink-2、ink-dim；
- 品牌：brand、brand-hover、on-brand、gold；
- 线条与底色：line、line-strong、panel、panel-hover、raised；
- 状态：warning-surface、warning-ink、warning-line、success-surface、success-ink、danger-surface、danger-line、info-surface、info-ink、violet-surface、violet-ink。

**组件里直接写的原始变量也要定义：** `var(--brand)`、`var(--on-brand)`、`var(--helper)`、`var(--ink-1)`、`var(--ink-dim)`、`var(--line)`、`var(--line-strong)`、`var(--panel)`、`var(--raised)`、`var(--chart-series-revenue)`、`var(--chart-series-promoters)`。这些原始变量只定义在资料页最外层的 `.pick-board` 容器上，不放到 `:root`，避免和工作台其它页面冲突。

以上按 RealShort 非问答组件（`rs:src/components/admin/pick/` 下除 `ask-*` 以外的文件）实际用到的 class 和 `var(--…)` 统计。`web-*` 只出现在 `ask-turn.tsx`，那是问答组件，不移植，所以不映射。P3-4 加一条源码扫描测试：移植后的组件里出现的颜色 class 和 `var(--…)` 都必须在 `pick-board.css` 里有定义。

**冲突检查：** 工作台 `globals.css` 里没有同名 token，不冲突。

## 8. 分阶段任务清单

**各阶段怎么上线：**
- P0 单独上线。
- P1 在 RealShort 仓库单独上线；v2 没人调用时不产生影响。
- P2 默认关闭（`PICK_MIRROR_ENABLED` 不为 1），打开后只多出镜像版本和配对发布。
- P3 是资料页的一次性完整上线。
- P4 加链接、回放和验收。

**依赖关系：**
- P0 与 P1 可以并行。
- P2 需要 P1 已经部署。
- P3 的纯模块部分可以提前做；页面联调需要 P2 已有版本数据。

**实现与部署约定：**
- 每个任务都先写测试。
- 改完扩展源码后，按 `docs/pick-workbench/local-run.md` 运行 `deerflow extensions upgrade … --yes`，刷新 `backend/extensions/sources/ggwork-pick`，并把托管副本和源码一起提交。
- `customizations/pick-workbench/pyproject.toml` 要把 `ggwork_pick/mirror/ddl.sql` 列为 package data。

### P0：Supabase 与宿主迁移（3–4 人日，可单独上线，v1 同步不变）

#### P0-1：Postgres 测试夹具与 CI

**Files:**
- Modify：`customizations/pick-workbench/tests/conftest.py`。
- Create：`customizations/pick-workbench/tests/pg.py`、`tests/test_pg_migrations.py`、`.github/workflows/pick-workbench-tests.yml`。

**先写的测试：**
- 在空的 PG schema 上把 ggwp 迁移从 0001 升到 head；第二次启动保持幂等；约束 `ggwp_batch_status` 存在。
- 现有的 repository、selection、routes 测试改成参数化夹具，在 SQLite 和 PG 上各跑一遍。`PICK_TEST_PG_URL` 未设时，跳过 PG 那一份。
- `_write` 在 PG 上走 `pg_advisory_xact_lock` 分支。

**实现要点：**
- 每个测试用一个独立的数据库，不用随机 schema：0006 建的 `pick_mirror` 和 `pickm_v*` 是库内全局的名字，advisory lock 也按库区分，同一个库里的测试即使串行跑也会互相留下状态。
  - 会话开始时建一个模板库，把 ggwp 迁移升到 head；每个测试 `CREATE DATABASE t_<随机> TEMPLATE pick_tpl`，结束时 `DROP DATABASE … WITH (FORCE)`。
  - 角色是整个集群共享的。测试里的只读角色名由设置项 `PICK_MIRROR_READER_ROLE`（默认 `pick_board_reader`）决定，每个测试用带随机后缀的名字，结束时删掉。
- CI 用 `postgres:17` service，环境装 `--extra postgres`，命令同 AGENTS.md。

**Verify:** CI 上两种方言都通过；本机用 `docker run --rm -e POSTGRES_PASSWORD=… -p 5433:5432 postgres:17` 能复现。

#### P0-2：扩展的 PG 兼容修复与批次缓存

**Files:**
- Modify：`ggwork_pick/contracts.py`、`ggwork_pick/repository.py`、`ggwork_pick/routes.py`（SaveInput、UpdateInput 定义在 23、30 行）。
- Create：`tests/test_pg_compat.py`、`tests/test_batch_cache.py`、`tests/test_pg_limits.py`。

**先写的测试：** 6.7 表里的每一行都有对应测试。

**Verify:** PG 上没有 500，只有 422；缓存命中后读库次数为 0（用 SQLAlchemy 的 `before_cursor_execute` 事件计数）；prune 后缓存被驱逐。

#### P0-3：入口、镜像与配置

**Files:**
- Modify：`backend/app/gateway/pick_entrypoint.py`、`docker/Dockerfile.pick-gateway`、`backend/tests/test_pick_cloud_entrypoint.py`（在现有测试上扩展，不另建文件）。
- Create：`backend/app/gateway/auth/create_user.py`、`backend/tests/test_create_user_cli.py`。

**先写的测试：**
- 现有用例改为显式传 `backend="sqlite"`，断言不变。
- `main()` 缺 `PICK_DB_BACKEND` 时非零退出。
- postgres 模式下缺 URL 时退出；URL 带 `sslmode` 时退出，并给出说明。
- 运行时 yaml 里只有字面的 `$PICK_DATABASE_URL`，没有密钥；文件权限为 0600。
- sqlite 模式的输出与现在逐字相同。
- `create_user`：新邮箱建出 `system_role=user`、`needs_setup` 为真的账号；密码只写进 0600 文件、stdout 里没有；邮箱重复时非零退出。
- `create_user` 的子进程用例（照 `railway ssh` 的环境）：清掉全部 `DEER_FLOW_*`，cwd 设为一个没有 `config.yaml` 的目录（先 `cd` 到 backend 副本以便 `app` 可导入），只给 `DEER_FLOW_HOME=<临时目录>`：能找到 `<临时目录>/pick-runtime.yaml` 并建号，credentials 落在 `<临时目录>/credentials`；运行时 yaml 不存在时非零退出，提示先启动一次 gateway，且没有碰数据库。
- `main()` 与 `create_user` 用同一个补缺省值的函数（6.5），各自的用例断言补出来的路径相同。

**Verify:** 在构建出的镜像里执行 `python -c "import asyncpg, psycopg, psycopg_pool, langgraph.checkpoint.postgres"` 能成功。

#### P0-4：Supabase bootstrap 与运行手册

**Files:**
- Create：`docs/pick-workbench/supabase/bootstrap.sql`、`docs/pick-workbench/supabase.md`（运行手册：6.1–6.10、变量清单、回滚）、`customizations/pick-workbench/tests/test_bootstrap_sql.py`（只在 PG 上跑）。

**先写的测试：** 在本机 postgres:17 上模拟 Supabase：先用超级用户建替身角色 anon、authenticated，再建一个 `NOSUPERUSER CREATEROLE` 的角色 `sb_postgres`，让它持有容器里的 `postgres` 库（`ALTER DATABASE postgres OWNER TO sb_postgres`；每个用例起一个新容器，或者用例之间恢复属主）。然后**以 sb_postgres 身份**执行 bootstrap.sql。不能用 docker 里的真超级用户跑：超级用户跳过 SET ROLE 检查，缺那一句 GRANT 也会误判为通过。检查：
- 完整脚本以 exit 0 结束，输出里没有任何 `WARNING` 行（psql 在只警告、未授权时照样打印 `GRANT`）。
- 删掉 `GRANT deerflow_app TO CURRENT_USER WITH … SET TRUE` 那一句时，脚本在 `CREATE SCHEMA … AUTHORIZATION` 处失败（证明这句必要）；加上后成功。
- 删掉 `SET ROLE deerflow_app` / `RESET ROLE` 时，脚本在第一句 `REVOKE` 处报 `permission denied for schema deerflow`（证明这两句必要，3.1）。
- `has_schema_privilege('pick_board_reader', 'pick_mirror', 'USAGE')` 为真；`\dn+` 里 pick_mirror 的权限含 `pick_board_reader=U/deerflow_app`。
- reader 的 `rolconfig` 含 `default_transaction_read_only=on` 和 `statement_timeout=8s`；两个角色的 `rolconnlimit` 都是 20。
- 第二个用例模拟 Supabase「只警告、不报错」的环境：sb_postgres 另外被授予 `pg_read_all_data` 和 `pg_write_all_data`，再跑一遍上面几条断言，仍然没有 WARNING、reader 仍有 USAGE。
- 把库的属主换成别的角色、sb_postgres 没有 grant option 时，脚本在 DO 块处硬失败，而不是只报 WARNING 继续。
- deerflow_app 执行 `CREATE SCHEMA IF NOT EXISTS deerflow` 成功。这个用例专门覆盖缺 CREATE 权限的失败模式。
- reader 不能读 deerflow 下的表，不能建 schema。
- reader 的 `SHOW default_transaction_read_only` 为 on。

**Verify:** 测试通过；运行手册里不含任何密钥。

#### P0-5：演练

**环境：** 只用独立的临时项目 `ggwork-rehearsal`（按小时计费，演练完删除），不在正式项目里演练。

原先的备选（正式项目里建 `deerflow_staging` schema）取消：演练要测的恰好是 Supavisor 会不会丢掉 search_path，一旦丢了，表和演练用的管理员会按角色默认值落进正式项目的 `deerflow`，带进正式切换。临时项目开不出来时，演练顺延，不降级。正式切换时，在 6.8 第 3 步本机建表之前另查一次：正式项目 `deerflow` schema 里没有任何表（`SELECT count(*) FROM pg_tables WHERE schemaname = 'deerflow'` 为 0）。

**步骤：**
1. 先按 6.8 第 3 步对临时项目在本机建管理员（新 worktree，照抄运行手册 8.3.3 的命令，不靠仓库的 `.env`），再用新镜像连 session pooler：启动不报 alembic 版本错误，能用这个管理员登录。正式切换前只有这里跑得到这条路径。
2. 执行 6.8 第 9 步的全部检查。
3. 另外核对：
   - 宿主 bootstrap（create_all 加 alembic stamp）；
   - ggwp 迁移；
   - `/health/ready` 的 postgres checkpointer 探针；
   - 连接峰值；
   - 两个驱动的 search_path；
   - SSL；
   - NUL 和孤立代理项测试；
   - 一次手动同步；
   - bootstrap 以真实 Supabase 的 `postgres` 完整执行：exit 0、输出里没有 `WARNING`，6.2 的检查全部通过（权限事实已在正式项目上实测，3.1；这里确认整份脚本，包括 SET ROLE 那一段）；
   - 经 `railway ssh` 按 6.8 第 8 步的写法跑一次 `create_user`：ssh 会话里看得到 `PICK_DATABASE_URL`、`PGSSLMODE`，账号建在演练库里，credentials 落在 `/data/credentials`；
   - Supavisor session 模式下客户端断开后会话状态是否被重置：一条连接取 advisory lock 并 `SET lock_timeout`，然后直接断开；新连接查 `pg_locks` 里没有这把锁，`SHOW lock_timeout` 是默认值。不满足时，5.2 的 `finally` 解锁是唯一保障，运行手册里写明 `lock_stuck` 的处理。
4. 测一次工具调用延迟，与 SQLite 对比，确认 LRU 缓存生效。
5. 演练结束后删除临时项目。

**Verify:** 结果记进 `docs/pick-workbench/supabase.md`。有一项不通过，就不进入 P0-6。

#### P0-6：正式切换

按 6.8 执行；回滚按 6.10。

**Verify:**
- 管理员能登录；
- 补跑的同步成功；
- 10 题验收一致；
- 更新 `docs/pick-workbench/progress.md`。

### P1：RealShort feed v2（3–4 人日，RealShort 仓库，单独 PR）

分支从 `origin/main` 新开（`git switch -c feat/pick-export-v2 origin/main`），不在本地检出当前的 `fix/pick-feed-reelshort-dates` 上继续。

#### P1-1：`asOf` 贯穿

**Files:** `rs:src/lib/observe/queries.ts`、`rs:src/lib/pick/queries-reelshort.ts`、`rs:src/lib/pick/queries.ts`、`rs:src/lib/pick/queries-rank.ts`、`rs:src/lib/pick/feed.ts`。

**先写的测试：** 4.8 表里的 `pick-export-v2-sql` 默认路径逐字比对；`observe-db` 的 asOf 用例（含 `loadBillRows` 的 same_day 不数 as_of 之后的点击）；4.2 末尾的源码扫描。

**Verify:**
- `pnpm test`、`pnpm lint`、`pnpm typecheck` 全部通过；
- 线上选剧台各 tab 的 SQL 文本不变。

#### P1-2：导出映射与清洗（纯模块）

**Files:** Create `rs:src/lib/pick/export-v2-map.ts`、`rs:tests/pick-export-v2.test.ts`。

**先写的测试：** 白名单自检（含 `meta.*` 键白名单）、清洗用例（跑共享夹具 `rs:tests/fixtures/pan-scrub-cases.json`，反例含 4.6 列出的剧名、row_key 和英文简介）、`scrubAllText` 的递归覆盖与豁免清单、payload 键白名单、参数解析、按字节截页（含单行超预算与 `row_too_large`）、cursor 编解码。

**Verify:** 测试通过；模块不 import 任何 server-only 模块。

#### P1-3：导出查询、manifest、fingerprint

**Files:** Create `rs:src/lib/pick/export-v2.ts`（server-only）。

**先写的测试：**
- `export-v2.ts` 自己写的 SQL 片段不含禁止标识符，只按名字豁免 `has_pan` 与 `bill_rank` 两个派生；复用的 RealShort loader 不查（4.6 第 2 条）；
- 需要数据库的用例（`rs:tests/pick-export-v2-db.test.ts`，4.8）：在种子库上，rs_rows 某页与 `loadRowsByIds(ids, {asOf})` 的对应字段逐一相等（USD 除外）；哨兵值不出现在任何响应里；`rs_bill_orders` 把同键的两个 promotion_value 合并成一行、`source_rows=2`；`bill_rank` 排序与 `loadRows(sort=bill)` 逐行相同；乱序提交的详情写入改变 fingerprint；库不变而构建 SHA 或规则摘要改变时返回 409；
- 控制总数与页面函数同源；`control.ledger` 只有 `{rows, orders}`，`meta.*` 按键白名单输出。

**Verify:** 在本地种子库上完整拉一遍，行数等于 manifest。

#### P1-4：路由与 v1 增量

**Files:**
- Create：`rs:src/app/api/pick-feed/v2/[resource]/route.ts`。
- Modify：`rs:src/app/api/pick-feed/route.ts`、`rs:src/lib/pick/feed.ts`（改显式列）、`rs:src/lib/pick/feed-map.ts`、`rs:tests/pick-feed.test.ts`、`rs:tests/admin-contracts.test.ts`。

**先写的测试：** 状态码矩阵（404、401、400、409、503、500 `row_too_large`），v1 带 `fp` 时每一页的 409/503（含库不变、只有构建 SHA 或规则摘要变化的 409，4.3），每页先读后核，以及 v1 的变化只增不改。

**Verify:** 在 preview 部署上，用 curl 带 token 能拿到 manifest，不带 token 拿到 404 或 401。
- RealShort 的 Preview 在 Vercel 部署保护（SSO）后面，Bearer 过不了这一层，请求会被重定向到 `vercel.com/sso-api`（`rs:docs/plans/2026-09-22-watch-single-url.md:459`）。要在 RealShort 项目里开「Protection Bypass for Automation」，curl 和 dry-run 客户端都带 `x-vercel-protection-bypass` 头。这个值同样只写进 scratchpad 里 0600 的临时文件，用完删掉。
- dry-run 客户端（P2-2）支持从文件读这个头，不从命令行参数传。

#### P1-5：剧单导入忙标记

**Files:**
- Create：`rs:scripts/sql/observe-source-pick-catalog.sql`、`rs:tests/pick-sources.test.ts`。
- Modify：`rs:scripts/import-catalog.ts`、`rs:src/lib/observe/source-types.ts`、`rs:src/components/admin/pick/sources.tsx`（加标签）、`rs:tests/pick-import.test.ts`。

**先写的测试：**
- 标记的先后顺序；
- `SOURCE_LABELS` 覆盖 `ObserveSource` 的全部值；旧选剧台的 Sources 多出「剧单导入」一行，`sources` 里没有这个 key 时显示「—」，不报错。原计划写的「不多显示一行」与 `Record<ObserveSource, string>` 的类型冲突，改成这个预期（4.3）；
- SQL 文件：DO 块按定义找到原约束名再 DROP，在一份按 `observe-source-state.sql` 建的本地库上执行两次都成功。

**上线顺序：** 先在生产库执行 SQL，再部署代码。

#### P1-6：实测

**做法：**
1. **先确认 Preview 的 `DATABASE_URL` 指向哪个库**（Vercel 项目的环境变量页看 Preview 那一行指向的 Neon 分支或主机名，不读值）。
   - 指向生产库的主分支：在 Preview 上测，第 2 步起照做。数字就是生产的 compute 增量。
   - 指向别的分支或别的库：Preview 上测出的 compute 没有意义。改为 P1 合并后在 Production 上测：v2 在 `PICK_EXPORT_TOKEN` 未配置时返回 404，是惰性的；只在测量窗口里配上 token，测完删掉。
2. 在 RealShort 项目里临时配置 `PICK_EXPORT_TOKEN`（Preview 或 Production，按第 1 步），用临时文件重定向写入。在 Preview 上测时，同时开 Protection Bypass for Automation（P1-4）。
3. 在工作台本机运行 `python -m ggwork_pick.mirror.client --dry-run --base-url <地址> --bypass-header-file <文件>`（P2-2 提供）。
4. 记录每页的耗时和字节数，并在 Neon 控制台看 compute 的增量。
5. 测完删掉临时配置的 token 和 bypass secret。

**门槛：**
- 每页少于 15 秒、少于 3 MB；
- 整次少于 3 分钟；
- 数据库时间少于 90 秒；
- 首个真实 manifest 的 `meta.scrub` 按字段记下清洗次数：title、title_cn、description 上有任何命中，都当作正则 bug，修好之前不进入第 9 节第 6 步（不打开镜像）。

超出门槛时：先把 rs_rows 的每页上限降到 1000；还不行，就把 rs_rows 改成每天只拉一次。

**Verify:** 数字写进 `rs:docs/` 下对应的设计记录，以及工作台的 `docs/pick-workbench/realshort-sync.md`。

### P2：工作台镜像写入（5–6 人日，`PICK_MIRROR_ENABLED` 默认关）

#### P2-1：迁移 0005 与 0006

**Files:** `ggwork_pick/migrations/versions/0005_mirror_columns.py`、`0006_pick_mirror.py`、`ggwork_pick/models.py`、`tests/test_pg_migrations.py`。

**先写的测试：**
- 两种方言都能升级和降级；
- SQLite 上没有 pick_mirror；`ggwp_candidate_sets.mirror_version` 和 `data_as_of_json` 在两种方言上都存在且可空；
- PG 上 reader 角色存在时有授权，不存在时不报错；`pick_mirror.control` 只有一行；`pick_mirror.series_state` 有 `trimmed_before` 列。

#### P2-2：合同与客户端

**Files:**
- Create：`ggwork_pick/mirror/__init__.py`、`mirror/contracts.py`、`mirror/client.py`（含 `--dry-run` 命令行入口）。
- 夹具：`tests/fixtures/export_v2/*.json`，由 RealShort 合同测试的输出复制过来，文件头注明来源 commit；`tests/fixtures/pan_scrub_cases.json` 从 `rs:tests/fixtures/pan-scrub-cases.json` 复制。

**先写的测试：**
- 未知字段或禁止 key 出现时整批失败，错误信息里没有字段值；`payload` 里出现白名单以外的键同样失败；
- Python 的网盘正则跑共享夹具，结果与夹具期望逐条一致（含 4.6 的反例）；
- v1 每一页都带 `fp`，任一页 409 抛 DriftError、503 按漂移处理；某页的 `sourceRevision` 与 manifest 不同时同样抛 DriftError；
- 500 `row_too_large` 抛镜像侧错误，不重试，错误文本带资源名和主键、不带正文；
- `--bypass-header-file` 从文件读头，不接受命令行明文；
- 第 3 页的 fingerprint 不同时抛出 DriftError；
- cursor 出现循环时终止；
- 503 时用假时钟验证按 Retry-After 等待；
- 错误文本里不含 token。

#### P2-3：版本 DDL 与 COPY 写入

**Files:** `mirror/ddl.sql`、`mirror/versions.py`、`mirror/writer.py`、`tests/mirror/test_writer.py`（只在 PG 上跑）。

**先写的测试：**
- 按 ddl.sql 建出的表和列与 3.3 一致，而且没有任何禁止列；
- COPY 用真实列类型的夹具行（timestamptz 传带时区的 datetime、jsonb 传 JSON 字符串、text[] 传 list），经 `schema_name` 写进版本 schema，读回逐字段相等；类型传错时报错而不是悄悄写入；
- COPY 1 万行合成数据时，峰值内存有上限（用 tracemalloc 断言）；
- 所有写入都在专用连接上：ORM 池的连接数在 COPY 前后不变；
- schema 名的正则校验生效；
- ANALYZE 之后 `last_analyze` 不为空；
- 发布之前 reader 没有 USAGE 权限。

#### P2-4：闸门

**Files:** `mirror/gates.py`、`tests/mirror/test_gates.py`。

**先写的测试：** 5.3 的每个闸门各有一个构造的失败用例，包括：
- 用 `ALTER TABLE … ADD COLUMN pan_url` 模拟出现禁止列；
- 往 note、`payload.h[*][2]`、`posts[].url`、`who[]` 各塞一个网盘链接；
- 往 v1 某行的 signal note 里塞网盘链接：两边都不发布；
- 让控制总数差 1；
- 让 v1 集合比镜像多一行。

另有两个必须**通过**的用例：
- 同一 (bill_date, book_id, promotion_type) 下两个 promotion_value 都有订单（`rs_bill_orders` 一行、`source_rows=2`，manifest 的 `control.ledger.rows` 与 `rsCounts.ledger` 都是 2）：所有闸门通过，不降级；
- 标题为 `Code Name Reaper II`、row_key 为 `goodshort-K10JEicNmxOWhQPwdg3zdw==`、简介含 `secret code that` 的行：文本闸门不命中，不降级。

#### P2-5：配对发布与失败清理

**Files:**
- Create：`mirror/run.py`。
- Create：`mirror/admin.py`（`accept-empty`、`cleanup` 两个命令；`cleanup` 先取同一把锁，再清遗留版本和批次）。
- Modify：`sync.py`（打开开关时委托给 `MirrorSync`；v1 拉取带上 `as_of` 与 `fp`，`DEADLINE_SECONDS` 按 5.1）、`repository.py`（新增 `stage_import`、`publish_mirror_pair`、`publish_agent_only`、`fail_staged`、`current_pin`；`_reuse` 支持推迟写入）、`imports.py`（新增 stage 参数）、`service.py`（新增 `PICK_REALSHORT_EXPORT_TOKEN`、`PICK_MIRROR_ENABLED` 两个设置；advisory lock 与专用连接）、`context.py` 与 `tools.py`（钉住批次时在同一次读取里一并钉住镜像版本并算出 data_as_of，2.5 第 5 条）、`selection.py`（`pinned_versions`、`_parent_versions`、`_current_versions` 扩成 (catalog, knowledge, mirror) 三元组，由 `_scope` 一起选定；写 `mirror_version` 和 `data_as_of_json`）。`mirror/admin.py` 的入口按 6.5 的规则不依赖 cwd。

**先写的测试：**
- 正常路径：在第二个会话里观察，事务提交之前两边都看不到新数据，提交之后同时出现；
- 镜像侧失败（COPY 之后、镜像闸门、一致性闸门各一例）：schema 被删，智能体批次降级发布，`consecutive_failures` 加 1。两轮用不同的 v1 夹具，降级发布的是新批次，`behind` 为真；
- 镜像阶段耗尽时限（假时钟，停在第 8 步的闸门里；v1 夹具是新内容）：v1 已在第 5 步暂存，智能体批次在预留时间里发布，`behind` 为真，`consecutive_failures` 加 1，`mirror.alert` 按次数触发，schema 被删，锁被释放；
- v1 先于 v2 拉取：v2 的第一页请求发生在 v1 批次暂存之后；
- v1 侧失败：两边都不发布；本次新建的批次标为 failed、去重槽位被释放，当前版本不变；
- **v1 去重复用了当前批次之后镜像失败：** 当前批次仍是 published，行都还在，`source_as_of`、`validation_json` 没被提前改写；
- v1 内容与已有批次相同且两边都成功：复用旧批次，并且仍然切换为当前；
- 等 busy 之后重新选 as_of：用假时钟让 manifest 先返回两次 503，再成功，断言第三次请求的 as_of 晚于第一次；
- 重启时有遗留数据：下一次拿到锁之后才被清理；锁被别的连接持有时不动遗留数据；
- 被取消：会被清理，锁被释放，专用连接已关闭；
- 持锁连接用完之后：新开的连接能立刻拿到同一把锁；
- `accept-empty`：设置后下一次放行、发布事务里置回 false，再下一次仍然拦截；
- 钉住版本（口径见 2.5 第 1 条）：
  - 运行开始时当前批次有配对版本，记下版本号；
  - 新建批次降级发布，或当前批次是个人导入的：记空，降级那一种 `behind` 为真；
  - 降级运行去重复用的正是当前版本配对的那对批次：记该版本，`behind` 为假；
- 换一批时镜像版本跟着数据版本走（2.5 第 5 条）。前提：绑定的旧卡用批次 A、版本 vA，当前批次 B 配 vB：
  - `exclude_previous` 加 `use_latest`：结果的 `catalog_batch_id=B`、`mirror_version=vB`；
  - 同上但 B 是降级发布的：`mirror_version` 为空；
  - 同一轮先带 `use_latest` 查询、再不带 `use_latest` 换一批（`versions_refreshed` 已为真）：仍是 vB；
  - 本轮没有刷新过、不带 `use_latest` 的换一批：批次 A、版本 vA，`data_as_of_json` 也取旧卡的；
- 冻结的 data_as_of 写入（2.5 第 5 条）：批次 A 先与 v1（as_of=t1）配对，这时生成的结果 C1 记下 v1，`data_as_of_json` 的 `source_as_of` 为 t1、新鲜度取 v1 的 meta；下一轮 v1 内容相同去重到 A、与 v2（t2）配对发布之后，C1 这两列不变；降级且去重时，新结果写的是 v1 的时点。读取一侧的测试在 P2-8。

#### P2-6：保留

**Files:** `mirror/retention.py`、`tests/mirror/test_retention.py`。

**先写的测试：** 3.6 的每条规则；另外，拿不到锁时跳过，并且不影响 ggwp 批次的保留。

#### P2-7：曲线

**Files:** `mirror/series.py`、`tests/mirror/test_series.py`。

**先写的测试：**
- 追加新的一天；
- 截断点按 3.2 算：取第 10 步保留清理之后仍为 published 的版本里最早的 `as_of` 日期减 90 天，且不晚于 `through` − 92 天；`series_state.trimmed_before` 随之更新；
- 一个 6 天前被候选卡引用的版本，连续 6 次每日折叠之后，它的窗口（`as_of` 日期 − 90 天到 `latest_snapshot`）仍然完整；
- 「换一批」续上引用之后，这个版本保留下来，窗口同样完整；
- 当前版本的曲线与 RealShort 规则逐点相同，包括 `as_of − 90` 那一天（共 91 个日期）；
- 折叠之后 `series` 里最早的点不早于 `trimmed_before`，被截掉的点都早于它（资料页据此提示，P3-5）；
- 同一天重复折叠保持幂等；
- 中间缺一天时记录下来；
- 非正典 id 的点也被收下；按版本 `rs_ids` 解析出的正典 id 读出的曲线，与夹具里按 RealShort 规则算出的逐点相同；
- 回填命令在同步持锁时直接退出，不写任何行；
- 命令行入口缺少环境变量时直接退出；清掉 `DEER_FLOW_*`、cwd 不在 backend 下时（照 `railway ssh`）行为相同（6.5）。

#### P2-8：同步状态、回放接口、排除集合、冻结的 data_as_of

**Files:** `routes.py`（`GET /api/pick/sync` 新增 `mirror`；新增 `GET /api/pick/replay`；`status_view` 先读 `data_as_of_json`）、`tools.py`（query、detail 工具先读 `data_as_of_json`，count 用本轮钉住的快照）、`selection.py`（查询时写入 `excluded_json`）、`repository.py`、`tests/test_replay.py`、`tests/test_frontend_contract.py`。

**先写的测试：**
- 回放按 owner 隔离，其他用户拿到 404；
- 批次已清理时返回 410；
- `excluded_json` 为空时返回 `excluded_reproducible: false`；
- 回放列表的前 N 个等于当时 `SelectionService.query` 的结果；
- 不可映射的条件都列出来；
- 列表长度上限 2000，超出时带 `truncated`；
- 合同测试：`result_view` 的输出里没有 `excluded_json`、`mirror_version`、`data_as_of_json`，`data_as_of` 的键集合仍是 `DATA_AS_OF_KEYS`，能被前端 `.strict()` 的 `pickResultSchema` 接受（沿用 `tests/test_frontend_contract.py` 的做法）；
- 读冻结值（接 P2-5 的场景）：批次 A 被 v1、v2 先后共用之后，旧结果 C1 的 `status_view`、query/detail 工具输出和回放接口仍显示 t1 与 v1 的新鲜度；count 用本轮钉住的快照；`data_as_of_json` 为空的旧行回退到批次；
- `/api/pick/sync` 的 `mirror` 字段含 `behind`、`alert`、`lock_stuck`、`series_through`、`warnings`；`behind` 按 2.5 第 1 条的口径（降级且去重到当前配对批次时为假）。

**Verify（P2 整体）：**
1. 在 Railway 设置 `PICK_REALSHORT_EXPORT_TOKEN` 和 `PICK_MIRROR_ENABLED=1`，手动同步一次：版本发布成功，两个批次配对，各闸门都有记录，批次的 `source_as_of` 等于最近一次配对版本（这一次）的 `as_of`；首个版本的 `meta.scrub` 里 title、title_cn、description 没有命中（P1-6）。
2. 执行曲线回填。
3. 智能体 10 题验收保持一致。
4. 同步结束后 `SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'` 为 0。

### P3：前端资料页（8–10 人日，一次上线全部 tab）

#### P3-1：依赖与数据库层

**Files:** `frontend/package.json`、`src/env.js`、`src/server/pick-board/{db,version,auth,gateway}.ts`、`rstest.config.ts`、`tests/unit/server/pick-board/{db,version,auth}.test.ts`。

**先写的测试：**
- db：用假 pg client 记录收到的语句，依次应为 `BEGIN READ ONLY; SET LOCAL search_path TO pickm_v000123, pick_mirror`、查询本身、`COMMIT`；出错时执行 `ROLLBACK`，并且一定 release 连接；7.5 清单里的 9 种 OID 都解析成字符串；不在 scope 内调用时抛错；同一请求里设两个不同版本时抛错；非法 schema 名被拒绝；URL 带 sslmode 时被拒绝。
- version：不传 `v` 时取当前版本；`v` 指向已删除的版本时取当前版本并标记 pruned；`v` 不是数字时忽略；building 和 failed 状态的版本不能被选中。
- auth：`authenticated`、`unauthenticated`、`needs_setup`、`system_setup_required`、`gateway_unavailable`、`config_error` 六个分支都有用例；用户 id 为 `default` 或 `static-website-user` 时，不论环境变量如何都被拒绝。

需要数据库的集成测试（设了 `PICK_BOARD_TEST_PG_URL` 才跑）：用 `customizations/pick-workbench/ggwork_pick/mirror/ddl.sql` 加夹具数据建一个版本，然后以只读角色执行查询（经 `withScriptScope`）。

**真渲染测试（专门抓版本范围传不到子组件的问题）：** 新建 `frontend/tests/e2e-pick/pick-data-board.spec.ts`，在 P3 就作为门槛，不等 P4：
- 本机起 postgres:17 并开 SSL（自签 CA，`PICK_MIRROR_CA_PEM` 指向它），用 ddl.sql 和夹具建一个已发布的版本，前端 `pnpm build && pnpm start` 连它，代码路径与生产相同；
- 登录后逐个打开选剧、全部剧库、榜单（剧场榜一张、rs 榜一张、rs_bill 分成榜、订单对账）、发布记录列表和单条、剧场规则、两类证据页，断言每页都有夹具里的行，页面里没有 `outside a version scope` 和错误边界；
- 夹具建两个已发布版本，数据有区别；同时发 10 个请求，`v` 在两个版本之间交替，断言每页显示的版本号和行都与各自请求的版本一致（验证容器按请求隔离）。

#### P3-2：纯模块与移植测试

**Files:** 7.3 的纯模块表，以及 `frontend/tests/unit/core/pick-board/*.test.ts`。

**先写的测试：**
- 6 份移植过来的 RealShort 测试按 7.3 的逐文件规则通过：纯函数用例原样通过；源码形状测试改写路径后通过；问答与 SQL 文本断言按 7.3 删去或挪到 P3-3；`pick-queyu.test.ts` 不移植，仍在 RealShort 的 `pnpm test` 里通过；
- 另加 `v`、`result`、`imports` 的解析和写回用例。

#### P3-3：查询移植

**Files:** 7.3 的服务端表，以及 `tests/unit/server/pick-board/queries.integration.test.ts`。

**先写的测试（在夹具版本上）：**
- 选剧默认条件只返回 has_signal 且未下架的行，排序同 RealShort；
- facets 的计数；
- 日榜 LATERAL 和周榜；
- 发布记录的列表、单条和统计；
- 剧场行和 ReelShort 行的证据页，包括用非正典 id 找到正典行；
- rs 七张榜和订单对账；rs_bill 分成榜按 `bill_rank` 排（含名次并列时按 drama_id）；订单对账合计的「行数」等于 `sum(source_rows)`，rs_ledger 计数与 manifest 的 `control.ledger.rows` 相同；
- 从 `pick-growth-diagnosis` 挪过来的断言（7.3）：dp1 排序、`comparableOnly`、`growth_dp1` 计数都在夹具结果上成立；
- 冻结时间下的上线分桶；
- 新鲜度不为 null。

#### P3-4：组件移植与改写

**Files:** 7.3 的组件表，以及 `tests/unit/components/workspace/pick-board/*.dom.test.tsx`、`tests/unit/server/pick-board/contracts.test.ts`。

**先写的测试：**
- DOM 测试：页面上不出现网盘链接或提取码，不出现「$」和 USD；`has_pan` 为真的行显示「有网盘」、为假的显示「无网盘」，两者都没有指向网盘域名的链接；所有链接都以 `/workspace/pick-data` 开头并带 `v`；表单里有隐藏的 `v`。
- 版本规则跟着版本走（7.3）：夹具版本把某个剧场的 yt 规则改掉（例如 `ok` 改成 `no`），`ResourceCell` 的 YouTube 标签，以及 row-detail 的 YouTube、报备、解禁几项都跟着变；reelshort-detail 的素材与回传取自版本的 `rules.reelshort`。
- 源码扫描：
  - `pick-board` 三个目录里不出现 `pan_url`、`panUrl`、`billUsd`、`revenue_usd`、`promotion_value`；
  - 只有 `src/server/pick-board` 能 import `pg`；
  - 所有 `<Link` 都带 `prefetch={false}`；
  - 组件从 server 模块只能 import 类型；
  - 组件里出现的颜色 class 和 `var(--…)` 都在 `pick-board.css` 里有定义（7.6）；
  - 移植代码里不再出现 `/admin/pick`（`platforms.ts` 的 `doc` 链接在构造规则时改写，7.3）。

#### P3-5：页面、横幅、子 tab、样式、侧栏

**Files:** `frontend/src/app/workspace/pick-data/page.tsx`、`components/workspace/pick-board/views/*`、`src/styles/pick-board.css`、7.3 里列出的工作台现有文件。

**先写的测试：**
- 页面 DOM 测试（mock 掉数据层）：
  - 各 tab 走对应的分支；
  - 未连接镜像库时，只有 imports tab 可用；
  - 钉住版本、版本已清理、个人导入、镜像落后（`behind`）、连续失败（`alert`）、剧单导入不完整（`warnings` 里的 `catalog_import_incomplete`，4.3）六种横幅；
  - 证据页曲线的窗口起点早于 `series_state.trimmed_before` 时，显示「早于 X 的曲线点已清理」（3.2）；
  - 规则漂移提示；
  - 新鲜度一行里曲线日期取 `min(latest_snapshot, series_through)`；
  - yt、inuse 筛选按版本规则算：夹具版本把某剧场的 yt 从 `ok` 改成 `no`，筛选结果随之变化。
- 更新 e2e 断言：`personal-selection.spec.ts` 改指 `?tab=imports`。

#### P3-6：上线

1. **函数区域已确认。** 2026-09-23 用 Vercel API 查过线上生产部署 `dpl_3etmSjcedYVC7kioyXbLBzp3ae8z`：`regions` 为 `["iad1"]`，不用加 `vercel.json`。该部署是从本地 `work` 分支用 CLI 发布的（`source: cli`），所以前端上线用 `vercel deploy --prod`（在仓库根目录，项目已由 `.vercel/project.json` 关联到 ggwork-deerflow，rootDirectory=frontend）。部署后再核一次新部署的 `regions`。
2. **写入环境变量。** 值从密码管理器取出，用 `printf '%s'` 写进 scratchpad 里权限为 600 的文件（不带末尾换行），再重定向输入，写完删掉文件。CA 证书是 PEM 多行文本，原样保存即可：

   ```bash
   umask 077
   printf '%s' "<从密码管理器粘贴>" > "$SCRATCH/reader-url.txt"
   vercel env add PICK_MIRROR_READER_URL production < "$SCRATCH/reader-url.txt"
   vercel env add PICK_MIRROR_CA_PEM production < "$SCRATCH/supabase-ca.pem"
   rm -f "$SCRATCH/reader-url.txt"
   ```

3. **部署后验证：**
   - 登录后 6 个数据 tab 都有数据；
   - 未登录时拿不到任何数据：直接请求页面会被重定向，带 `RSC: 1` 头的请求也拿不到数据；
   - p95 渲染时间少于 2.5 秒；
   - `pnpm perf:check` 通过；
   - reader 并发压测：20 个并发渲染（各 tab 混合）跑 2 分钟，没有连接错误，`pg_stat_activity` 里 reader 的峰值记进运行手册；
   - 前端上线用 `vercel deploy --prod`，见第 1 步。

### P4：一致性链接与验收（3–4 人日）

#### P4-1：`data_as_of.mirror_version` 与核对链接

**部署顺序：先前端，后后端。**
- 前端：`frontend/src/core/pick/types.ts` 的 `pickDataAsOfSchema` 加一个可选字段 `mirror_version`。这个 schema 是 `.strict()`，旧前端会拒绝新字段，所以必须先上前端。
- 先上前端只保护新加载的页面。前端部署之前就打开的标签页还是旧的解析器（仓库没有部署偏移处理，`/api/pick` 直接走 gateway），后端开始发 `mirror_version` 之后，它们的 `getPickResult`/`listPickResults`（`frontend/src/core/pick/api.ts:102-118`）直接 `.parse` 抛 ZodError：候选卡显示红色错误，「查看候选」不可点，刷新后恢复。不影响库里的数据。操作上：两次部署之间留出间隔（例如后端第二天或非工作时间部署），并通知大家刷新页面。可选：后端用一个环境变量控制是否输出 `mirror_version`，间隔过后再打开。不做能力协商。
- 后端：结果级的 `data_as_of.mirror_version` 直接取 `ggwp_candidate_sets.mirror_version`（2.5 第 5 条，查询当时记下的），不再按时间反推。`detail` 取所属结果行的这一列；`count` 不保存结果行，取 `_scope` 同时选出的镜像版本：派生统计（exclude_previous，count 固定 `use_latest=False`，`selection.py:266`）取父结果的，独立统计取本轮钉住的（与 2.5 第 5 条同一个三元组改法）。

**Files:**
- 前端：`frontend/src/core/pick/types.ts`、`references.ts`（base64url 解码）、`candidate-view.tsx`、`pick-tool-card.tsx`，以及对应的 dom 测试。
- 后端：`repository.py`、`tools.py`（count、detail 的版本号）、`tests/test_frontend_contract.py`。

**先写的测试：**
- identity 解码成 row_key；
- source 不是 `realshort-pick` 的个人批次不显示链接；
- 版本号取自结果行，降级发布时为空；
- 字段为空时不显示链接；
- count 的版本：没有任何候选卡时直接统计，取本轮钉住的；绑定了旧卡但做独立统计，取本轮钉住的；沿旧卡换一批再统计，取父结果的。

#### P4-2：回放视图

**Files:** `views/replay-view.tsx`、`src/server/pick-board/replay.ts`、`queries.ts` 里的 `loadRowsByKeys`，以及对应的测试。

**先写的测试：**
- 渲染顺序与回放接口返回的顺序相同，前 `limit` 个高亮；
- vN 已清理时行取自当前版本，横幅注明，当前版本已无的 row_key 逐条列出（2.5 第 4 条）；
- 近似筛选链接：语种代码反查成 `LANG_LOC` 里的中文名、剧场显示名反查成 platform 键，反查不到的进「不可映射」清单；
- 不可映射条件的清单；
- 回放接口返回 410 或 404 时显示相应提示；
- 智能体默认条件（不带任何筛选）的候选集合，与选剧 tab 默认视图的 row_key 集合完全相同；
- 智能体 `sort=rank` 且 `signal_kind` 为日榜（kd/qc/qr）时的前 N 个，与榜单 tab 该榜最新一天去掉已下架行之后的相对顺序相同。榜单 tab 含已下架行（`rs:src/lib/pick/queries-rank.ts:54`），智能体候选池不含；同名次时智能体按 identity、榜单按剧名，这两处差异写进页面的「与智能体语义不同」说明。

#### P4-3：核对脚本与移植漂移检查

**Files:**
- `rs:scripts/pick-board-snapshot.ts`：只读，由操作员用 RealShort 自己的 `.env.local` 运行，命令 `tsx --conditions=react-server`。它在给定的 `--as-of` 和 `--fp` 下，用页面自己的 loaders（4.2 清单里的全部函数都接受 asOf）跑约 40 个 URL 用例。每个用例输出 `{total, facets, 前 50 行按顺序的全部字段}`：不只比 row_key，`rs-queries.ts` 是重写的，要逐字段比，包括 rr1/p1/rr7/p7、s1_*/s7_*、clicks7、各类日期（listed_on、latest_evidence_on、publish_at、baseline*_at）和标志位（has_signal、off_on、youtube、legacy_only、site_other、rs_clk/rs_bill/rs_gsc）。fingerprint 对不上就退出。
- `frontend/scripts/pick-board-parity.ts`：用 reader 连接串，在同一个版本上跑移植后的 loaders（经 `withScriptScope`），与上面的 JSON 逐字段比对，打印差异。允许的差异写在白名单里：被删掉的字段（pan_url、pan_pw、USD，镜像另有 `has_pan` 与 `bill_rank`）、对账合并、网盘单元格（只显示有无）；如果 collation 不同，还包括剧名排序。分成排序按 `bill_rank`，与 RealShort 顺序相同，不进白名单。
- `frontend/scripts/pick-board-drift.sh`：对 `PORTED_FROM` 里列出的文件执行 `git -C <rs> diff 9fc159c..origin/main -- …`。

**约 40 个用例覆盖：**
- 选剧和全部剧库的默认视图；
- 每个剧场，和每种依据；
- 三种发布状态筛选，yt、inuse、dated、off；
- 每张剧场榜：日榜两天、周榜两周、评级两档；
- rs 七张榜，以及涨幅榜的四种排序；
- 发布记录的四种状态和两条单条记录；
- 证据页：剧场行、ReelShort 行各两条。

**Verify:** 同一次采集下没有白名单以外的差异。这个脚本在每次 RealShort 发版前，和 drift 检查一起运行。

#### P4-4：验收与文档

1. 逐个 tab 对照 RealShort 旧选剧台：计数、facet 标签、榜单顺序、单条发布记录、两类证据页。
2. 用 Playwright 把每个 tab 走一遍：`frontend/tests/e2e-pick/pick-data-board.spec.ts`（P3-1 已建）在本机 QA 实例上运行，用 QA 账号 `PICK_E2E_EMAIL`，不指向生产实例（`docs/pick-workbench/local-run.md:97`）。生产上的逐 tab 核对由第 1 步人工完成。
3. 从 3 张真实候选卡进入核对和回放，确认列表与卡片一致。
4. 更新文档：
   - `docs/pick-workbench/realshort-sync.md`：链路、变量、失败行为、保留规则；
   - `docs/pick-workbench/progress.md`；
   - `docs/pick-workbench/supabase.md`。

**工作量：** 合计约 22–28 人日，加上前两轮评审修复约 25–30 人日；本轮评审修订（降级路径、专用连接、建账号命令、真渲染 e2e、逐字段核对）再加约 3–4 人日，合计约 28–34 人日。第 13 节的补充字段和第 14 节的审计处置（镜像版本三元组与冻结 data_as_of、镜像阶段时限、曲线截断点、清洗正则与豁免清单、哨兵用例）再加约 3 人日，合计约 31–37 人日。一个人做，日历上约 7 周。

## 9. 部署与切换顺序

| 步骤 | 仓库/平台 | 做什么 | 回滚 |
|---|---|---|---|
| 1 | Supabase | Pro 项目 `ggwork-workbench` 已建（us-east-1，compute Micro，2026-09-23）。打开 Enforce SSL，关闭 Data API，Pool Size 设 20，执行 bootstrap（两个角色的 `CONNECTION LIMIT` 都是 20，3.1）并设置两个角色的密码，按 6.2 留档（含「输出无 WARNING」与 reader 的 USAGE） | 删除项目 |
| 2 | ggwork | 合并 P0-1 到 P0-4。**先**在 Railway 设好 `PICK_DB_BACKEND=sqlite`，**再**部署新镜像：新镜像缺这个变量会直接退出。部署后行为不变 | 回到上一个镜像 |
| 3 | Supabase + ggwork | P0-5 演练（只在临时项目） | 删除临时项目 |
| 4 | ggwork | P0-6 切换：备份，本机建管理员，改 Railway 变量，部署，验证 | 6.10 |
| 5 | RealShort | P1 的 PR 合并前：在生产库执行 `observe-source-pick-catalog.sql`。P1-6 实测：Preview 指向生产库时在合并前做，否则合并后在 Production 的测量窗口做（见 P1-6 第 1 步）。实测通过后用临时文件重定向写入正式的 `PICK_EXPORT_TOKEN`（Production）。未过门槛不进入第 6 步 | 回滚 PR（v2 没人调用；v1 的变化只增不改）。token 可以单独删除 |
| 6 | ggwork | 前提：P1-6 已确认首个真实 manifest 的 title、title_cn、description 没有清洗命中。合并 P2，部署；在 Railway 设置 `PICK_REALSHORT_EXPORT_TOKEN`，确认后再设 `PICK_MIRROR_ENABLED=1`；手动同步一次；执行曲线回填（命令写法见 5.6） | 把 `PICK_MIRROR_ENABLED` 改成 0，回到 v1 流程；遗留的 schema 由下一次拿到锁的镜像同步清理，或经 `railway ssh` 手动执行一次 `cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.admin cleanup`（写法与环境检查同 6.8 第 8 步） |
| 7 | ggwork frontend | 合并 P3；在 Vercel 写入 `PICK_MIRROR_READER_URL`、`PICK_MIRROR_CA_PEM`（Production）；在仓库根目录 `vercel deploy --prod` | Vercel 即时回滚到上一个部署；或者删掉 reader 变量，资料页只剩 imports |
| 8 | ggwork | 先部署 P4-1 的前端，间隔一段时间（例如第二天或非工作时间）再部署后端，并通知大家刷新页面：先上前端只保护新加载的页面，之前打开的标签页在后端开始发 `mirror_version` 后会解析失败，直到刷新（P4-1）。然后上 P4-2 到 P4-4 | 字段都是可选的，按反序回滚即可 |

**环境变量清单（这里只列名字，值不进仓库）：**

| 位置 | 变量 | 用途 |
|---|---|---|
| Railway gateway | `PICK_DB_BACKEND` | `postgres` 或 `sqlite`，必须显式设置 |
| Railway gateway | `PICK_DATABASE_URL` | session pooler 连接串，角色 deerflow_app，不带 SSL 参数 |
| Railway gateway | `PGSSLMODE` | `require` |
| Railway gateway | `PICK_REALSHORT_FEED_URL`、`PICK_REALSHORT_FEED_TOKEN` | 已有，v1 用 |
| Railway gateway | `PICK_REALSHORT_EXPORT_TOKEN` | v2 用，与 RealShort 的 `PICK_EXPORT_TOKEN` 是同一个值 |
| Railway gateway | `PICK_MIRROR_ENABLED`、`PICK_DB_SIZE_CAP_BYTES` | 开关与阈值。原先的 `PICK_MIRROR_OPTIONAL`（降级已是默认）和 `PICK_MIRROR_ACCEPT_EMPTY`（改为库里的一次性标记，5.3）取消 |
| RealShort Vercel Production | `PICK_EXPORT_TOKEN` | v2 的 Bearer；未配置时 v2 路由返回 404 |
| ggwork Vercel Production | `PICK_MIRROR_READER_URL` | transaction pooler 连接串，角色 pick_board_reader |
| ggwork Vercel Production | `PICK_MIRROR_CA_PEM` | Supabase CA 证书；公开信息，但作为配置放在环境变量里 |

**两条规则：**
- 所有 token 和数据库密码都用 `openssl rand -hex 32` 在本机生成，存进密码管理器。不用 `-base64`：`/`、`+`、`=` 放进连接串要百分号编码。写给 `vercel env add` 的临时文件用 `printf '%s'`，不带末尾换行。
- 轮换 v2 token 的顺序：先改 RealShort 的值并重新部署，再改 Railway 的值，最后手动同步一次确认。

## 10. 风险与待确认事项

### 10.1 风险

| 风险 | 缓解 |
|---|---|
| Railway 之外多了一条数据库访问路径（Vercel 上的 reader） | 角色只读、只能访问镜像 schema，有语句超时和连接上限；只配 Production；关闭 Data API，对 anon、authenticated、PUBLIC 执行 REVOKE。以后如果有人重新打开 Data API 或暴露这些 schema，内部数据（推荐人、飞书 id、指标）会被公开，所以运行手册里写明禁止这样做 |
| 只靠 layout 做鉴权，数据会从直接的 RSC 请求漏出去 | 页面自己先做鉴权，再查库，失败时关闭；P3-6 专门验证带 `RSC: 1` 头的请求。每次渲染多一次 `/auth/me`，所以资料页的可用性依赖 gateway |
| 镜像没装 postgres extra | P0-3 改 Dockerfile，并在镜像里做 import 冒烟测试 |
| Supavisor 的连接上限 | 预算见 6.4：compute 选 Micro（13.1，`max_connections=60`），Pool Size 与两个角色上限都是 20，接受 gateway 高峰时 session 模式排队；演练时实测峰值，超出再议升级 Small；P3-6 做 reader 并发压测 |
| PG 对 NUL 和孤立代理项更严格 | 6.7 的校验器和测试；演练时做 NUL 和孤立代理项测试 |
| 镜像和智能体数据漂移（导出途中 RealShort 正在同步、导入或发版） | fingerprint（v1、v2 每一页都先读后核；`detail_synced_at` 用与提交顺序无关的求和；含构建 SHA 和规则摘要，4.3）、`pick_catalog` 忙标记、一致性闸门、配对发布；镜像失败时降级并显示横幅。余下的窗口只在手动或补跑的同步与导出重叠时出现。RealShort 如果打开 Vercel Rolling Releases，流量分在两个构建之间，各页会在构建之间来回跳、反复 409：要么保持关闭，要么把导出钉在一个部署上 |
| 镜像拖累智能体更新 | 镜像侧失败自动降级为只发布智能体批次（2.5 第 1 条，13.2）；v1 先于 v2 拉取并暂存，镜像阶段有自己的截止时刻，慢而不失败时同样降级（5.1、5.5）；连续 3 次失败告警（5.5） |
| 剧单导入中途失败，剧单只写了一半 | 不拦截（导入是整表替换，拦住只会让智能体停在旧数据）；v1 与镜像读同一份状态，两边一致；版本 meta 记 `catalog_import_incomplete`，横幅提示重跑导入（4.3、5.7） |
| 曲线点被截掉后无法回填 | RealShort 90 天后删观测；截断点按仍保留的最早版本算，并记 `trimmed_before`，钉住的旧版本曲线不会悄悄变短（3.2） |
| 持锁连接或会话设置残留在池里 | 镜像写入用不入池的专用连接，`finally` 解锁并关闭；清理只用 `SET LOCAL`；演练验证 Supavisor 断开后的会话重置（P0-5） |
| Supabase 上 bootstrap 权限不足，或授权只报 WARNING 没生效 | 实测 postgres 是库属主、有 CREATE（10.2 第 10 条已关闭）；脚本补 SET 权限、硬检查 CREATE 权限，对 deerflow_app 名下的 schema 在 `SET ROLE deerflow_app` 之下授权（3.1）；P0-4 用非超级用户替身测试并断言输出无 WARNING、reader 有 USAGE；P0-5 在真实 Supabase 上跑完整脚本 |
| 影响 RealShort 生产 | asOf 默认路径的 SQL 逐字不变，有测试保证；P1-6 实测 compute；超门槛时降页大小或降频 |
| 禁止字段漏出 | RealShort 侧四道、工作台侧三道（4.6），保障落在输出上：键白名单（含 `meta.*`）、哨兵值用例；清洗覆盖豁免清单以外的所有文本叶子，v1 也清洗；TS 与 Python 正则共用一份夹具；v1 的 `SELECT rows.*` 顺手修掉 |
| 清洗正则误伤剧名、主键和简介 | 提取码正则写死（4.6：只收「提取码」「密码」加显式分隔符，不收单独的 code）；标识与派生字段豁免；夹具含真实反例；首个 manifest 按字段看清洗次数，标题和简介有命中就先修正则再开镜像（P1-6） |
| 与 USD 有关的部分无法原样复刻 | 已导出由 USD 推出的名次 `bill_rank`（13.3），分成排序与旧页一致；金额列与金额块仍不显示，页面写明差异（7.4） |
| 规模 | ReelShort 正典约 3.2 万（全部剧库 7.36 万减去剧单 4.17 万），4,559 只是 ReelShort 候选数。体积按 3.4 估算，以首个 manifest 为准 |
| pg 的类型解析与 neon-http 不同 | 7.5 的字符串解析器照抄 drizzle neon-http 的 9 个 OID，加单元测试；曲线数组用 `unnest` 读成逐日行 |
| 版本范围传不到异步子组件 | 用 React `cache()` 容器而不是 AsyncLocalStorage（7.5）；P3-1 的真渲染 e2e 作为门槛 |
| 钉住的版本被清理后，深链失效 | 横幅回退到当前版本；回放的名单与顺序读批次，行改从当前版本取并注明；保留规则按查询当时记下的版本号（3.6） |
| 移植的代码与 RealShort 渐行渐远 | 规则、术语表以及 yt/inuse 用的剧场规则常量都按版本数据构造；`PORTED_FROM` 记录来源 commit，加 drift 脚本；RealShort 每次发版前跑 P4-3（逐字段比对） |
| 从零开始、管理员抢注 | 本机先建管理员（6.8 第 3 步）；公告；SQLite 备份 |
| 冻结时间与 RealShort 实时页的数字不同 | 页头写「截至 as_of」；每行保留 RealShort 链接 |
| 剧名排序受 collation 影响 | Supabase 实测 `en_US.UTF-8`，再核对 Neon 的 `lc_collate`；核对脚本判断 |
| checkpoint 增长 | 每周记录三张 checkpoint 表和整库的大小，整库超过 5 GB 告警；处理是经线程 DELETE 接口删旧会话和/或扩磁盘（6.6）。`checkpoint_retention.py` 没有生产触发点，也不剪主链，接上它是单独一项评审任务 |
| 旧标签页在 P4-1 后端上线后解析失败 | 两次部署之间留间隔并通知刷新（P4-1、第 9 节第 8 步）；可选用环境变量延后输出 `mirror_version` |
| Vercel 函数与 Supabase 不在同一区域，往返次数被放大 | 已确认线上是 iad1（P3-6）；每条查询一个事务，往返约 3 次，同区域时约几毫秒 |
| TLS 只做 require、不校验证书（Railway 侧） | 后续加固：`PGSSLMODE=verify-full`，加上 `PGSSLROOTCERT` 指向 Supabase CA。先在演练里验证两个驱动都支持 |
| Supabase pooler 的证书链不能用下载的 CA 校验 | 在 P3-6 验证；不通过时暂时退回 `rejectUnauthorized:false`，靠 Enforce SSL 加密，并在风险表里记录 |

### 10.2 待确认事项

需要用户或操作员给出答案（第 1–4、10、11 条已定或已关闭，保留原文备查）：

1. ~~**Supabase 组织与套餐**~~：**已定（13.1）。** compute 选 Micro，不选 Small；Supavisor Pool Size 与 `deerflow_app`、`pick_board_reader` 的 `CONNECTION LIMIT` 都设 20，接受 gateway 高峰时 session 模式排队，演练实测峰值，超出再议升级。项目 `ggwork-workbench` 已在 Pro 组织下建好（us-east-1，实测 `max_connections=60`）。原稿选 Small 的理由（两个用户各 30 的池加内部服务约 75 个连接，超过 Micro 的 60）随池降到 20 不再成立（6.4）。余下两项是控制台设置，不影响方案，由操作员在 P0-4 的运行手册里记下选择：
   - 是否需要 PITR（付费附加）。Pro 默认每日备份，保留 7 天。
   - Spend cap 开还是关。开启时，超出配额（例如磁盘超过 8 GB）会被限制。
2. ~~**订单笔数与账号台账能否同步**~~：**已定同步（13.3 第 1 条）。** rs_bill 的订单数、对账表里的订单笔数和同日出站，以及 catalog_accounts 的账号地址和粉丝数（3.3、4.4）。
3. ~~**`has_pan` 布尔能否同步**~~：**已定同步（13.3 第 2 条）。** 只导出布尔，资料页还原「有网盘 / 无网盘」，仍不显示链接和提取码（3.3、4.4、7.3）。
4. ~~**rs_bill 按分成排序**~~：**已定导出名次（13.3 第 3 条）。** `rs_rows.bill_rank` 只有序号没有金额，分成排序与旧页一致（3.3、4.4、7.3）。
5. **保留期是否够用。** 当前加前 2 个版本，加 7 天内被引用的版本，上限 10 个。更早的回答只能靠卡片里冻结的依据和回放（读批次）核对。
6. **RealShort Neon compute 的可接受增量。** P1-6 按每次不超过 90 秒数据库时间、每天两次设了门槛，需要确认这个门槛可以接受。
7. ~~Vercel 项目 ggwork-deerflow 的函数区域~~：已关闭。线上生产部署 `dpl_3etmSjcedYVC7kioyXbLBzp3ae8z` 的 `regions` 是 `["iad1"]`（2026-09-23 用 Vercel API 查得）。
8. **Supavisor 的实际行为。** 由演练确认：是否转发 `options=-c search_path`、asyncpg 的 `server_settings`，Railway 出站的 IPv4 连通性（本机经 IPv4 连 session pooler 已通，6.1），以及客户端断开后是否重置会话（advisory lock、`SET`）。另外确认 `railway ssh` 会话里能看到服务变量（6.8 第 8 步）。
9. **是否有必须保留的线上对话。** 例如验收记录里引用的会话。如果有，在切换之前导出截图或文本，因为切换后旧会话在应用里看不到。
10. ~~**Supabase 的 `postgres` 给不出 `CREATE ON DATABASE` 时怎么办**~~：**已关闭。** 2026-09-23 在正式项目上实测：`postgres` 是 `postgres` 库的属主（datdba=postgres），`has_database_privilege(CREATE)` 为真，`GRANT CREATE ON DATABASE postgres TO <role>` 与 `GRANT <role> TO current_user WITH INHERIT FALSE, SET TRUE` 都在一个回滚的事务里成功（3.1）。审计另外发现的问题（授权要在 `SET ROLE deerflow_app` 之下做，否则报错或只警告）已在 3.1 修掉。以下是原文，仅在以后换项目时参考：3.1 的 DO 块会让 bootstrap 硬失败。宿主启动的 `CREATE SCHEMA IF NOT EXISTS` 和 writer 建 `pickm_v*` 都依赖这个权限。备选有两个：请 Supabase 支持处理库的属主；或者改用预建的固定槽位 schema（例如 10 个 `pickm_s01…s10`，由 postgres 预建、属主 deerflow_app，版本在槽位里轮转），宿主改成 `postgres_schema: ""` 不再建 schema。后者要改 3.3、3.5、3.6，需另行评审。P0-5 确认前不动。
11. ~~**镜像失败时自动降级是否可以接受**~~：**已定采用自动降级（13.2）**，不改回严格配对。原文：本计划按评审改成：镜像侧任何失败都不拦智能体更新，资料页显示落后横幅，连续 3 次失败告警。如果更看重「智能体的回答永远能在资料页核对」，可以改回严格配对（镜像失败则两边都不发布）。

## 11. 验证命令

```bash
# 工作台后端（仓库根目录）
backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q
PICK_TEST_PG_URL=postgresql://postgres:<pw>@127.0.0.1:5433/postgres \
  backend/.venv/bin/python -m pytest customizations/pick-workbench/tests -q
backend/.venv/bin/ruff check customizations/pick-workbench
backend/.venv/bin/python -m pytest backend/tests/test_pick_cloud_entrypoint.py backend/tests/test_create_user_cli.py -q

# 前端（frontend/）
pnpm check
pnpm test
PICK_BOARD_TEST_PG_URL=postgresql://... pnpm test pick-board
pnpm build
pnpm perf:check
pnpm exec playwright test -c playwright.pick.config.ts

# RealShort（rs: 根目录）
pnpm test && pnpm lint && pnpm typecheck
OBSERVE_TEST_DATABASE_URL=postgresql://... pnpm test
```

说明：
- 单元测试通过不等于链路可用。每个阶段的 Verify 都要求在真实环境（演练项目、Preview 或生产）上做一次端到端检查，并把结果写进 `docs/pick-workbench/progress.md`。
- 合成夹具和真实数据的验收要分别记录。

## 12. 评审修订（2026-09-23，完整性评审）

逐条对照两边代码核实后修订。行号以 `9fc159c` 为准；评审里有两处行号偏了几行（`rs:src/lib/pick/feed-map.ts` 的 note 截断在 `:208`，不是 `:217`；`rs:src/lib/pick/queries.ts` 的 `YT_BLOCKED`/`YT_LIST_ONLY`/`IN_USE` 在 `:86-87`、`:106`），事实本身成立。

### 12.1 采纳并已改进正文

**会失败或会泄露的 8 条：**
1. 版本范围改用 React `cache()` 按请求隔离的容器，脚本另用 `withScriptScope`；P3-1 加真渲染 e2e（两个版本交替并发）作为门槛（2.2、7.5、P3-1）。
2. v1 的 `toFeedRow` 也清洗；v1 行进 stage 之前过同一个正则闸门；TS 与 Python 共用一份清洗夹具（4.6、4.7、5.2、5.3、P2-2）。
3. 清洗改为递归覆盖所有文本叶子（含 `h[*][2]`、`posts[].url`、账号 url、`sources/cats/who`），payload 按键白名单；镜像侧失败自动降级为只发布智能体批次，记连续失败次数并在 3 次时告警，取消 `PICK_MIRROR_OPTIONAL`（2.5、3.3、4.4、5.1、5.3、5.5）。与评审不同的一点：v1 自己的闸门（NUL、网盘正则、漂移）失败时仍两边都不发布。
4. bootstrap 补 `GRANT deerflow_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE`，用 DO 块硬检查 CREATE 权限；P0-4 改用 `NOSUPERUSER CREATEROLE` 替身执行；给不出权限时的退路列为 10.2 第 10 条（3.1、6.2、P0-4）。后续：权限事实已在正式项目上实测（10.2 第 10 条关闭）；gpt-6-astra 审计发现 INHERIT FALSE 之后 postgres 不能对 deerflow_app 名下的 schema 授权，脚本在两句 CREATE SCHEMA 之后加了 `SET ROLE deerflow_app` … `RESET ROLE`，P0-4 断言输出无 WARNING、reader 有 USAGE（第 14 节 host-1）。
5. 每次等完 busy、每次漂移重来都重新选 as_of；busy 等待不计入 900 秒总时限（5.1、5.2）。
6. Preview 在 SSO 后面：用 Protection Bypass for Automation，头的值写临时文件；先确认 Preview 的 `DATABASE_URL`，不指向生产库就改在 Production 的测量窗口实测（P1-4、P1-6、第 9 节第 5 步）。
7. 切换步骤：本机 `DEER_FLOW_HOME` 指到 scratch；本机代码用镜像同一 commit 的 worktree；新增 `app.gateway.auth.create_user` 命令行工具代替不存在的「后台建账号」（6.5、6.8、P0-3）。
8. 备份前 `mkdir -p /data/backup`；演练只用独立临时项目，取消正式项目里的 `deerflow_staging` 备选，正式切换建表前断言 `deerflow` 为空（6.8、P0-5）。

**高风险 13 条：**
1. 清理改 `SET LOCAL`；镜像写入用不入池的专用 asyncpg 连接，`finally` 解锁并关闭，超时逐条显式指定；演练验证 Supavisor 断开后的会话重置（3.1、3.6、5.2、6.7、P0-5）。
2. 连接预算补上专用连接和 `railway ssh` 命令；两个角色上限都设为等于 Pool Size；P3-6 加 reader 并发压测（2.4、6.4、10.2 第 1 条）。当时的数值是 30 和 Small，后按用户决定改为 20 和 Micro（13.1），正文已统一。
3. vN 已清理时回放的行改从当前版本取并注明，纠正「回放照样可用」的说法（2.5、3.6、P4-2）。
4. 查询当时把镜像版本写进 `ggwp_candidate_sets.mirror_version`，保留与深链都按这一列（2.5、3.6、3.7、P2-5、P4-1）。
5. 传了 as_of 时 `capturedAt = asOf`（4.7）。
6. v1 接受 `fp`，每一页都核 fingerprint、都可能 409/503（4.7、5.2、5.3）。
7. `YT_BLOCKED`、`YT_LIST_ONLY`、`IN_USE`、`PLATFORM_RULES` 按请求从版本 `meta.rules` 构造（7.3、P3-5）。
8. asOf 补上 `loadRsRank`、`loadGrowthDiagnosis`、`loadReelshortDetail`、`loadBillRows`（same_day 加 `created_at <= asOf`）、`loadBillTotals`、`loadRankRows`，加源码扫描测试（4.2、P1-1）。
9. P4-3 前 50 行逐字段比对（P4-3）。
10. 类型解析器照抄 drizzle neon-http 的 9 个 OID；曲线数组用 `unnest` 读成行（3.2、7.5）。
11. PG 测试改为每个测试独立数据库，reader 角色名可配置（P0-1）。
12. 遗留清理在拿到锁之后做；`fail_staged` 只处理本次新建的 `importing` 批次；stage 模式下 `_reuse` 的写入推迟到发布事务（3.5、5.2、5.5、P2-5）。
13. 回填与折叠共用一把锁；曲线日期取 `min(latest_snapshot, series_state.through)`；`rs_series_day` 不再按导出当天的正典过滤，资料页按版本 `rs_ids` 解析正典后再读，与 RealShort 逐点一致（2.5、3.2、4.4、5.6）。

**中低问题：** P1-5 的测试预期改为「多出一行剧单导入」，约束按实际名字 DROP；入口测试在 `test_pick_cloud_entrypoint.py` 上扩展，第 9 节第 2 步先设变量再部署；密码和 token 用 `openssl rand -hex 32`，`vercel env` 临时文件用 `printf '%s'`；颜色补 panel-hover、brand-hover、violet-*、info-* 和原始变量；`requireBoardUser` 穷举六个分支并按用户 id 拒绝 `default`/`static-website-user`；`platforms.ts` 的 `/admin/pick` 链接改写；`rs_clicks14` 导出时只留公开正典；`result_view` 加合同测试；孤立代理项纳入演练和中间件；payload 键白名单；COPY 按真实列类型传参并用 `schema_name`；`accept-empty` 改为库里的一次性标记；近似筛选做语种和剧场的反查；剧单导入写入方式的描述改正；P1 从 `origin/main` 开分支；区域已确认 iad1，前端用 `vercel deploy --prod`。

**第四节要补的测试：** 9 组都已写进对应任务（P3-1 真渲染；P0-4 替身角色；4.8 与 P2-4 的 v1/v2 同一条网盘备注；P2-5 的 busy 后重选 as_of、去重复用当前批次后镜像失败；P4-2 的 rank 顺序与默认候选集合；P3-6 的 reader 并发；6.7 与 P2-5 的会话残留和锁释放）。

### 12.2 驳回或改动后采纳

| 评审点 | 处理 | 理由 |
|---|---|---|
| 第一节第 7 条：`PICK_E2E_EMAIL` 账号也要在新库重建 | 驳回 | `docs/pick-workbench/local-run.md:97` 规定自动测试只打本机 QA 实例、不能指向个人业务实例；生产验收用主账号。只在 P4-4 写明 e2e 跑在本机 QA |
| 第一节第 5 条：as_of 窗口放宽到 45 分钟 | 不采纳 | 改成等完 busy 再选 as_of、选定后受 900 秒总时限约束，as_of 到最后一页最多 15 分钟，30 分钟窗口够用；放宽只会削弱 RealShort 侧的约束 |
| 第三节颜色：补 `web-*` | 驳回 | `web-*` 只出现在 `rs:src/components/admin/pick/ask-turn.tsx`，问答组件不移植 |
| 第二节第 11 条：「并行跑就会冲突」 | 改动后采纳 | 仓库没有 pytest-xdist，测试是串行的，前提不成立；但串行时 `pick_mirror` 等全局名字也会在测试之间留下状态，所以仍改为每个测试独立数据库 |
| 第一节第 3 条：镜像失败自动降级 | 改动后采纳 | 镜像侧失败才降级；v1 自己的闸门（网盘正则、NUL、漂移）失败时两边都不发布，否则网盘文字会进智能体批次。是否接受降级本身列为 10.2 第 11 条 |
| 第二节第 2 条：reader 上限不小于池大小，或做压测 | 两者都做，另加结论 | 当时另把 compute 直接定为 Small：两个用户各 30 的池加内部服务超过 Micro 的连接上限。用户后来选 Micro，池与两个角色上限都降到 20（13.1） |
| 第三节：`result_view` 不能带出 `excluded_json` | 只补测试 | `result_view`（`selection.py:29`）按键白名单输出，现状不会带出；加合同测试守住即可 |
| 第一节第 4 条、第 1 条中标为推断的部分 | 保留推断标记 | 第 1 条已由代码结构确认（`view()` 只返回元素）；第 4 条的修法在推断不成立时也无害，由 P0-4、P0-5 实测定论 |

## 13. 开工前的用户决定（2026-09-23）

与正文冲突时以本节为准。2026-09-23 已把正文统一改过来：13.1 落到 2.4、3.1、6.1、6.4、第 9 节第 1 步、10.1、10.2 第 1 条；13.2 落到 2.5 第 1 条、5.5、10.2 第 11 条；13.3 落到第 1 节决策 4、2.3、3.3、4.4、4.6、7.1、7.3、7.4、10.1、10.2 第 2–4 条和对应任务的测试。

### 13.1 compute 选 Micro

- 不选 Small。连接预算按 10.2 第 1 条的退路：Supavisor Pool Size 设 20，`deerflow_app` 与 `pick_board_reader` 的 `CONNECTION LIMIT` 都设 20（等于 Pool Size）。
- 接受 gateway 高峰时 session 模式排队。6.4 的预算表、3.1 的 bootstrap、第 9 节第 1 步、10.1 的连接上限一行都按 20 执行。
- 演练（P0-5）实测峰值连接数；超出时再议升级。

### 13.2 镜像失败自动降级

- 采用正文 2.5 第 1 条与 5.5 的自动降级（10.2 第 11 条的默认），不改回严格配对。

### 13.3 补充同步的字段

以下三项都同步（10.2 第 2、3、4 条都选「同步」）：
1. 订单笔数与账号台账：rs_bill 的订单数、对账表里的订单笔数和同日出站、`catalog_accounts` 的账号地址与粉丝数。
2. `has_pan` 布尔：只导出是否有网盘，不导出 `pan_url`、`pan_pw`。资料页的网盘单元格按它还原「有网盘 / 无网盘」的区分，仍不显示链接和提取码。
3. rs_bill 按分成排的名次：RealShort 导出时按分成 USD 排好，只输出名次序号，不输出任何金额；资料页的分成榜按这个名次排。

禁止同步的清单不变：`pan_url`、`pan_pw`、`cps_bill_daily.promotion_value`、分成 USD、分成对账里的金额。

### 13.4 执行范围

- 批准开工 P0 与 P1。正式切换生产库（P0-6）前再向用户确认一次。
- 用户要求开工前请 gpt-6-astra 审计本方案；审计结论经核实后并入正文。


## 14. gpt-6-astra 审计处置（2026-09-23）

审计按五个方向出稿（宿主迁移、RealShort 导出、镜像写入、资料页、缺口），原始输出在 scratchpad 的 `codex-plan/out-{host,export,mirror,board,gaps}.md`。每条都回到两边代码和本机 PG 上逐条核实：能复现的复现（host-1 在本机 PG 17.10 上用替身角色跑了原脚本和修正后的脚本），看错代码或把正文已写明的取舍重报为缺陷的，降级或驳回。结论：审计标的 P1 里有三条成立（host-1、host-2、mirror-3），其余成立的多是接缝问题（配对口径、时点、截断窗口、计数口径）。

「核实」一列：成立 = 按原文实现会出错；部分成立 = 机制属实，但后果或范围比审计说的小；驳回 = 不成立。「严重度」是核实后的定级。

| id | 核实 | 严重度 | 改了什么（位置） | 备注 |
|---|---|---|---|---|
| host-1 | 成立 | P1 | bootstrap 在两句 CREATE SCHEMA 之后 `SET ROLE deerflow_app`，两句 REVOKE 和 reader 的 USAGE 在属主身份下做，再 `RESET ROLE` 做 ALTER ROLE（3.1）；6.2 留档加「输出无 WARNING」「`pick_board_reader=U`」「has_schema_privilege」；P0-4 加无 WARNING、USAGE、rolconfig 断言和「postgres 另有 pg_read_all_data/pg_write_all_data」的第二个用例；P0-5、10.1、12.1 第 4 条同步 | 本机复现：原脚本报 permission denied 且后续 ALTER ROLE 全没执行；另一种环境只报 WARNING、exit 0，reader 悄悄没有 USAGE |
| host-2 | 成立 | P1 | `create_user` 与 `pick_entrypoint` 共用补缺省值的函数，缺省 `DEER_FLOW_HOME=/data` 等，运行时 yaml 不存在就非零退出（6.5）；6.8 第 8 步写全命令并先检查 ssh 会话里的变量；P0-3 加清空 `DEER_FLOW_*` 的子进程用例；P0-5 经 `railway ssh` 跑一次；P2 的 ssh 命令同样写法（5.3、5.6、第 9 节第 6 步、P2-5、P2-7） | 按原文，第一次真实调用就在正式切换当天，且会失败 |
| mirror-3 | 成立 | P1 | 镜像版本与剧库、知识批次一起由 `_scope` 选定，`pinned_versions` 与 `_parent_versions`/`_current_versions` 扩成三元组；只有真的沿用父结果数据（`derived and not use_latest`）时才沿用父结果的版本（2.5 第 5 条，P2-5 加四个用例） | 原文「换一批沿用父结果的版本号」遇到 use_latest 或本轮已刷新时，会生成批次 B 配版本 vA 的结果 |
| mirror-2=board-1=gaps-3 | 成立 | P2 | 0005 加 `ggwp_candidate_sets.data_as_of_json`，钉住时在同一次读取里算出；`status_view`、query/detail 工具、回放先读冻结值，为空回退批次（2.5 第 1、5、9 条，3.7，P2-1，P2-5，P2-8）；P2 Verify 第 1 条改为「等于最近一次配对版本的 as_of」 | 不采纳「停止改写 source_as_of」：新卡会显示旧时点。不回填：P2 之前的行为空时保持现有行为 |
| mirror-4=gaps-4 | 成立 | P2 | 曲线截断点按仍 published 的最早版本算，至少留 93 天，记 `series_state.trimmed_before`；读取窗口改成 `as_of 日期 − 90` 到 `latest_snapshot`（91 个日期）；证据页注明已清理的点；更正「快照历史只追加」（3.2、5.6、7.1、P2-1、P2-7、P3-5、10.1） | RealShort 90 天后删观测，截掉的点回填不回来 |
| mirror-5 | 成立 | P2 | 镜像阶段有自己的截止时刻（选定 as_of 后 750 秒，留 150 秒降级）；v1 改到 v2 之前拉取暂存；5.5 加「总时限耗尽于镜像阶段」一行；P2-5 加假时钟用例（2.5 第 1 条、5.1、5.2 第 5–8 步、5.5、10.1） | 顺序对调之后，镜像到期时 v1 一定已暂存，不再需要审计提的「退回 manifest 失败路径、重新计时」 |
| gaps-2 | 成立 | P2 | fingerprint 加 `VERCEL_GIT_COMMIT_SHA` 和 `meta.rules` 的 sha256（4.3）；v1 每页的 `sourceRevision` 附加核对（5.2 第 5 步、P2-2）；4.8、P1-3、P1-4 加「库不变、构建或规则变化 → 409」；10.1 记 Rolling Releases | 导出途中 RealShort 发版会让镜像与智能体的规则不同，且没有闸门比对 |
| export-3-adj | 成立 | P2 | 提取码正则写死：只收「提取码」「密码」加显式分隔符，不收单独的 code，pwd 只随网盘 URL 替换；清洗和工作台文本闸门都豁免标识与派生字段（3.3、4.4、4.6、4.7、5.2、5.3）；夹具加真实反例；P1-6 看首个 manifest 的逐字段清洗次数，标题与简介有命中先修正则（P1-2、P1-6、P2-2、P2-4、第 9 节第 6 步、10.1） | 原文按字面实现会改掉真实剧名、一个主键和英文简介，或者让每次运行都降级 |
| export-5 | 成立 | P2 | SQL 文本自检只查 `export-v2.ts` 自己写的片段，复用的 RealShort loader 不查，只按名字豁免 `has_pan`、`bill_rank` 两个派生；「不含 *」改成「不许 SELECT * 和 别名.*」；保障落在输出上：`meta.*` 键白名单、`control.ledger` 只有 `{rows, orders}`、哨兵值数据库用例（4.5、4.6、4.8 新增 `pick-export-v2-db.test.ts`、P1-3）；v1 去掉 `rows.*` 的理由改写（4.6） | 按原文，P1-3 的测试永远红不转绿；输出一侧原本没有明确的 meta 白名单 |
| export-4=gaps-1 | 部分成立 | P2 | `rs_bill_orders` 加 `source_rows`，先过滤 `order_cnt > 0` 再分组；`control.ledger.rows` 与 rsCounts 的 `ledger` 闸门都按 `sum(source_rows)`；订单对账合计的「行数」用原始行数、另列合并后的行数（3.3、4.4、4.5、5.3、7.1、7.3、7.4、P1-3、P2-4、P3-3） | 同键两个 promotion_value 的情况还没在真实数据里出现过，但一旦出现就会永远降级 |
| board-4 | 部分成立 | P2 | `youtubeStatus(rules, platform, onList)` 改成显式接收规则的纯函数；`rules` 经 props 传到 rows-table、cells、row-detail、reelshort-detail；更正 `PLATFORMS`、`PLATFORM_LABELS`、`IN_USE` 在 `request.ts`；P3-4 加规则跟着版本走的 DOM 用例（7.3、7.5、P3-4） | 构建会立刻报错，不会悄悄出错；但为了让构建变绿而保留静态常量，没有测试能发现 |
| mirror-1 | 部分成立 | P3 | 配对只记在 `pick_mirror.versions`，批次侧的 `validation_json.mirror_version` 删去；统一当前版本、`behind`、钉住版本三个口径（按剧库与知识两个批次比对）；P2-5 的测试拆成「新建批次降级 → behind 为真」与「降级去重到当前配对批次 → 记该版本、behind 为假」（2.5 第 1 条、5.4、5.7、P2-5、P2-8） | 数据不会不一致，只是口径互相矛盾。不采纳审计的「要求本次发布的配对标记非空」：那样会丢掉一个有效链接 |
| board-2=gaps-5 | 部分成立 | P3 | count 的版本由 `_scope` 选定：派生统计取父结果的，独立统计取本轮钉住的；detail 取所属结果行的（2.5 第 5 条、P4-1 加三个用例） | count 不生成核对链接，最坏只是 JSON 里版本号为空 |
| board-3 | 部分成立 | P3 | P4-1 与第 9 节第 8 步写明「先上前端只保护新加载的页面」，两次部署之间留间隔并通知刷新；可选用环境变量延后输出 `mirror_version`（P4-1、第 9 节、10.1） | 不做审计提的能力协商或分版本响应，与影响不相称 |
| board-5 | 成立 | P3 | `pick-queyu.test.ts` 不移植；「原样通过」改成逐文件规则：纯函数原样、源码形状测试改路径、问答断言删掉、glossary 文字断言挪到 meta 夹具或留在 RealShort、SQL 文本断言改成 P3-3 的结果断言；写明两个仓库的验收命令（7.3、P3-2、P3-3） | 只影响验收措辞，跑 P3-2 时立刻暴露 |
| host-3 | 部分成立 | P3 | 6.4 的 ssh 命令拆成两行：建账号短时 2–3 个连接（宿主 bootstrap 的锁、反射和 alembic 连接），其余 1 个；启动迁移同为 2–3；注明 Railway 只在部署时探 `/health/ready`，那时旧实例已停，接受的高峰排队不影响 readiness（6.4） | 常态约 9–10 个连接，排队到 20 需要 10 个以上 ORM overflow 会话同时在用 |
| gaps-6 | 部分成立 | P3 | 删掉「启用 checkpoint retention」；改为每周记录三张 checkpoint 表与整库大小，整库超过 5 GB 告警，处理是经线程 DELETE 接口删旧会话和/或扩磁盘；注明 `checkpoint_retention.py` 没有生产触发点、不剪主链，接上它另行评审（6.6、10.1） | 慢、有监控，不造成失败或不一致 |
| export-1 | 部分成立 | P3 | dramas 的 `max(detail_synced_at)` 换成与提交顺序无关的 `sum(extract(epoch FROM detail_synced_at))`；每页先读后核（4.1、4.3、4.7）；4.8 加乱序提交用例；10.1 注明余下窗口只在手动或补跑时出现 | 实际只有详情阶段末尾的乱序提交会漏，且后续页会再核；可达的症状是控制总数不一致，已安全降级 |
| export-2 | 部分成立 | P3 | 只加可见性，拦截行为不变：`pick_catalog` 为 failed 或僵死 running 时 manifest 记 `catalog_import_incomplete`，版本 meta 保存，资料页横幅与 SyncStatus 提示重跑导入；运行手册写明恢复方式只能是整次重跑（3.3、4.3、4.5、5.7、P3-5、10.1） | 不拦截的理由见 4.3：导入是整表替换，拦住只会让智能体停在旧数据 |
| export-6 | 部分成立 | P3 | 定义单行超预算：3–4 MB 单独成页，超过 4 MB 返回 500 `row_too_large`（带资源名和主键），工作台当作镜像侧失败降级、不重试（4.1、4.8、5.5、P1-2、P1-4、P2-2） | 按真实数据最大行约 4.3 KB，不会触发，只是把规格里的矛盾定下来 |
| export-3 | 驳回 | 无 | 只在 4.6 与第 1 节决策 4 加一句：金额禁令按字段执行，自由文本只按网盘模式清洗 | 审计把「分成金额」读成内容类别，而决策 4 与 13.3 定的是封闭的字段清单。实测真实快照：133 条发布记录文本、414 条信号文本里没有我方分成金额；提到「收益」和数字的都是 ReelShort 公开指标，按决策 4 允许同步。按语义清洗金额反而会误伤这些允许的内容 |

**第 13 节的决定与实测一并落地的位置：** Micro、池 20、两个角色上限 20 见 2.4、3.1、6.1、6.4、第 9 节第 1 步、10.1、10.2 第 1 条；`has_pan` 与 `bill_rank` 见 3.3、4.4、4.6、7.1、7.3、7.4、P1-3、P3-3、P3-4、P4-3；订单笔数与账号台账见 3.3、10.2 第 2 条；自动降级见 2.5 第 1 条、5.1、5.5、10.2 第 11 条；10.2 第 10 条由 scratchpad 里 `supabase-facts.md` 的实测关闭（postgres 是库属主、有 CREATE，相关授权语句在回滚事务里都成功；`max_connections=60`；collation `en_US.UTF-8`；pooler 主机 `aws-0`）。
