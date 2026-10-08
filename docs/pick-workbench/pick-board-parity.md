# 资料页核对与漂移检查（P4-3）运行手册

资料页（`/workspace/pick-data`）的查询是从 RealShort 选剧台移植过来的，读的是工作台镜像。这里有两件事：

- **逐字段核对**：同一次采集下，资料页的 loaders 读镜像，RealShort 的 loaders 读 RealShort 生产库，两边结果除白名单外应当完全一致。
- **漂移检查**：移植以来，RealShort 的 main 有没有改动被移植的文件。

什么时候跑：RealShort 每次发版之前；P4-4 验收时（合成稿第 5.4 节）。三个脚本都只读。

| 脚本 | 在哪跑 | 读什么 |
|---|---|---|
| `frontend/scripts/pick-board-snapshot.rs.ts`（纯函数部分在 `pick-board-snapshot-core.rs.ts`，两个文件一起用） | 临时复制进 RealShort 检出 | RealShort 生产 Neon（`.env.local` 的 `DATABASE_URL`） |
| `frontend/scripts/pick-board-parity.ts`（比对规则在 `pick-board-parity-compare.ts`，collation 放行在 `pick-board-parity-collation.ts`） | 工作台 `frontend/` | 镜像，以 `pick_board_reader` 身份，经 `PICK_MIRROR_READER_URL` 与 `PICK_MIRROR_CA_PEM` |
| `frontend/scripts/pick-board-drift.sh` | 工作台，任意目录 | RealShort 检出的 git 历史 |

快照脚本的正本在工作台仓库，**不在 RealShort 仓库提交、不开 PR**：往 RealShort 提交会触发它的生产部署，也会改变 fingerprint 里的 `VERCEL_GIT_COMMIT_SHA`（批判 A5）。

## 需要的东西（只列名字）

- RealShort 检出（下文 `$RS_REPO`）、它的 `.env.local`（含 `DATABASE_URL`），以及 `node_modules`（里面有 `dotenv` 与 `tsx`）。
- 镜像 reader 连接串（6543 transaction pooler，角色 `pick_board_reader`，不带查询参数，格式见 `supabase.md`）与 Supabase 的 CA 证书，各放一个 0600 文件。
- `tsx`：工作台 `frontend` 没有把它列为依赖。`pnpm exec tsx` 用的是 PATH 上的 tsx（本机是全局安装的 4.x）；没有的话，用 `$RS_REPO/node_modules/.bin/tsx` 代替。
- 一个临时目录（下文 `$SCRATCH`），`mktemp -d` 建，0700。

## 1. 选窗口

- 挑一次镜像同步刚发布完的时候。
- 避开以下时段（快照脚本会拒绝在这些时段里启动，`--ignore-window` 可强行跳过）：
  - RealShort 同步：00:00、06:00、12:00、18:00 UTC，之前 15 分钟到之后 45 分钟；
  - GSC 采集：05:17 UTC 前后；
  - 工作台镜像同步：03:40、15:40 UTC 前后。
- 博客发版也会改变 fingerprint，发版时不要跑。
- 快照要在生产 Neon 上串行跑 70 多个用例，涨幅榜的 d1 单条就要 8–9 秒。取满 50 行的列表用例还要为补全并列组往后多读几页，每个用例至多 3 页（见下文「LIMIT 边界上的换行」的代价一条）。总耗时会打印出来，记进 compute 预算。

## 2. 取版本信息

在 Supabase SQL editor 里执行（`N` 是要核对的版本号，schema 名是 `pickm_v` 加 6 位补零的 N）：

```sql
SELECT id, schema_name, fingerprint,
       to_char(as_of AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS as_of_iso
FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1;

SELECT value #>> '{}' AS source_revision FROM pickm_v00000N.meta WHERE key = 'sourceRevision';
```

记下 `N`、`as_of_iso`、`fingerprint`、`source_revision`。

## 3. RealShort 那边：拍快照

```bash
umask 077
cd "$RS_REPO"
git status --short                          # 应当是空的
git fetch origin
git checkout --detach <source_revision>
cp <工作台>/frontend/scripts/pick-board-snapshot.rs.ts \
   <工作台>/frontend/scripts/pick-board-snapshot-core.rs.ts scripts/   # 保留原名：前者按这个名字 import 后者

VERCEL_GIT_COMMIT_SHA=<source_revision> pnpm exec dotenv -e .env.local -- \
  pnpm exec tsx --conditions=react-server scripts/pick-board-snapshot.rs.ts \
  --as-of <as_of_iso> --fp <fingerprint> --out "$SCRATCH/snap.json"

rm scripts/pick-board-snapshot.rs.ts scripts/pick-board-snapshot-core.rs.ts
git checkout -                              # 回到原来的分支
git status --short                          # 又是空的
```

- `source_revision` 为 null 的版本（本地导出的）：不设 `VERCEL_GIT_COMMIT_SHA`。
- 脚本先核对：当前目录是 RealShort 检出；`VERCEL_GIT_COMMIT_SHA` 等于 HEAD；不在忙时段。然后在开头和结尾各调一次 `export-v2` 的 `checkSource`（内部读 `readSourceSnapshot`）：fingerprint 不是 `--fp`（409），或有来源正在写（503），就退出 3，不写文件。
- `--only <正则>` 只跑 id 匹配的用例（`globals` 总会跑），用来补跑或省预算。
- 每个列表截到 50 行；选剧、全部剧库（缺省排序）和剧场榜的第 1 页再补全第 50 行所在的并列组，见下文「LIMIT 边界上的换行」。快照格式是 `pick-board-snapshot/3`。
- 快照是 0600、不覆盖已有文件（`--out` 已存在时开跑前就退出 2）。里面有 RealShort 的业务数据，但没有网盘链接、提取码和任何金额：
  - 网盘字段与金额字段在落盘前换成占位，网盘只留 `hasPan`；
  - 其余每个文本值都过一遍 RealShort 自己的 `scrubPanText`（导出清洗用的同一个；开跑前拿一条网盘链接自检，认不出就退出 2），认出网盘信息的整串换成「[快照清洗：网盘信息]」；
  - 对象的键（行键、来源名这类标识）不过清洗器。
- 结尾会打印清洗了多少格，以及并列组的计数：几个用例取满 50 行、查了并列组，其中几个真补了行、共补几行，几个超过上限没补全（只有数字）。
- 复制进去的那段时间不要跑 RealShort 的测试：`tests/admin-contracts.test.ts` 会扫 `scripts/`。

退出码：0 成功；1 未预期的错误；2 参数、目录、检出或时段不对；3 fingerprint 核对不通过。

## 4. 工作台这边：比对

```bash
umask 077
cd <工作台>/frontend
PICK_MIRROR_READER_URL="$(cat "$SCRATCH/reader-url.txt")" \
PICK_MIRROR_CA_PEM="$(cat "$SCRATCH/supabase-ca.pem")" \
  pnpm exec tsx --conditions=react-server scripts/pick-board-parity.ts --v <N> --snapshot "$SCRATCH/snap.json"
```

- 连接串和 CA 经环境变量从 0600 文件读入，不出现在命令行参数里。
- 读库走的是页面同一条路径：`db.ts` 的读连接池（按 CA 校验 TLS）、`resolveBoard` 与 `withScriptScope`。
- 先读快照：格式必须是 `pick-board-snapshot/3`。旧脚本拍的快照（格式 2，只截 50 行、不补并列组）直接拒绝，退出 2，用当前的两个脚本重拍。
- 再核对：vN 是否已发布且 reader 读得到；快照的 fingerprint、as_of（按毫秒比）、sourceRevision 是否都与 vN 相同。
- 再在 vN 上照快照里的 query 逐个重放移植后的 loaders，逐字段比。

输出依次是：

- 本地剧场键差集，即版本 `platformRules` 的键减去本地 `PLATFORMS`。不为空说明 RealShort 新加了剧场，要补移植。
- 两边库的 collation。
- 第 50 行所在的并列组，两边各一句：几个用例取满 50 行、查了并列组，其中几个真补了行、共补几行，几个超过上限没补全。只有数字，不列行。
- 白名单外差异，逐条列出：用例、路径、原因。
  - RealShort 那边的文字只打印长度和摘要，因为清洗器没认出的网盘写法也可能在里面。
  - 网盘字段和金额字段两边都不打印值。
- 白名单内差异，按规则计数。
- 清洗占位的用量：每个 `meta.scrub` 路径上观察到的格子数，和 `meta.scrub` 记的次数。

退出码：0 没有白名单外差异；1 有；2 参数、环境或读库出错；3 快照与版本对不上。

### 白名单（合成稿 P4-3，按批判 B16 修正）

- **被删字段**：分成金额（`billUsd*`、`revenueUsd`、`usd`、`matchedUsd`）、推广值（`promotionValue`）、订单对账的上线日期（`publishAt`）。
- **网盘单元格**：`panUrl` 与 `panPw` 不比；`hasPan` 要比，判断与导出的 `has_pan` 相同。
- **对账合并**：
  - RealShort 的原始账单行按（账单日, book_id, 推广类型）合并后再比，比较项包括 `orderCnt` 与 `sourceRows`；
  - 镜像多出的 `canonicalId`，以及合计里的 `mergedRows`、`mergedWithClicks`、`rowsWithClicks`；
  - 证据页订单明细的 `sameDayClicks`：RealShort 恒为 0。
- **账单明细的顺序与 LIMIT 边界**：
  - 订单对账和证据页订单明细不比顺序；
  - 哪边取满了 LIMIT（对账 200 行，明细 50 行），那边最早的账单日及更早的行可以缺，那一天的 `orderCnt` 与 `sourceRows` 可以不同；
  - 证据页的 `billTruncated` 只容许「RealShort 为真、镜像为假」。
- **清洗占位**：镜像是「[网盘信息已移除]」的格子，只在 `meta.scrub` 记过的字段路径上放行。快照那边通常是「[快照清洗：网盘信息]」；也可能是原文，因为导出清洗的是它自己规范化过的值（比如整串标签），拆开以后未必认得出。
  - 快照是「[快照清洗：网盘信息]」、镜像却不是清洗占位：白名单外，两边的文字都只打印长度和摘要；
  - 同一格子在几个用例里出现，只算一次；
  - 总数不得超过 `meta.scrub` 记的次数；
  - 榜单行的 `dayNote` 记到它取自的那个信号 `payload.h` 格子上。
  - manifest meta 文本（比如来源说明）的清洗不计入 `meta.scrub`，出现就是白名单外，需要人工确认。
- **payload、posts 里白名单之外的键**：导出只保留 `SIGNAL_PAYLOAD_KEYS` 与 `POSTED_POST_KEYS`；单测钉住这两个列表与 `contracts.py` 一致。
- **「分成」改名「订单」**：只在标签字段（`label`、`…Label`）上；剧名、备注这类数据里出现要照报。
- **timestamptz 按毫秒比**：只在时间字段（`…At`、`…_at`）上。
- **collation 不同时的剧名排序**：两边 `datcollate` 都知道并且不同时才放行。只看剧场行列表（选剧、全部剧库、剧场榜）、语种计数与账号列表，先后对调的每一对都要同时满足：
  - 主排序键相同：选剧与全部剧库是证据日期与剧单日期；日榜是名次；周榜是周数；评级榜是评级在 SSS…D 里的位置与剧单日期（不在里面的评级都排最后、算同一档，同 `array_position(…) NULLS LAST`）；其余剧场榜是证据日期与剧单日期；语种计数是条数；账号列表没有主排序键。剧场行的主排序键由快照核心的 `tiePrimary` 给出，补全并列组用的也是它；单测对着 `queries.ts`、`queries-rank.ts` 的 ORDER BY 钉住；
  - RealShort 的先后正是按 RealShort 的 collation 比剧名、平台、行键得出的先后（剧场榜不比平台，语种计数只比语种名，账号比分组、名字、id），镜像的先后也正是按镜像的 collation 得出的。
  - collation 的模型：`C`、`POSIX`、`C.UTF-8`、`ucs_basic` 按码点比，其余用 ICU（`Intl.Collator`）近似 glibc。近似不准时只会多报，这时在两边库上各用 `SELECT '甲' < '乙'` 核一下那一对。「顺序不同」的原因里除了第一处错位，还会点出第一对 collation 解释不了的行（只写行键），核的就是这一对。

不在白名单里的：

- `clicks7` 的 0 与 null（批判 B16）。
- ReelShort 预估分成榜（`rs_bill`，按 `bill_rank` 排）的顺序。
- 同一行在榜单里出现两次时的先后。这种列表按下标比，不按行键比。

### LIMIT 边界上的换行（并列补全）

每个列表截到 50 行，页面每页也是 50 行，所以截断处就是查询自己的 LIMIT。第 50 行所在的那组并列行（主排序键相同，见白名单里「collation 不同时的剧名排序」那一条），在两边库的 collation 不同时按剧名、平台、行键排出的先后不一样。只截 50 行的话，两边会各留下几行不同的行，parity 报成成对的「镜像少了这一项」「镜像多了这一项」。只看这 50 行又判断不出镜像里缺的那一行数据是不是也变了，所以不能直接放行。

快照（格式 `pick-board-snapshot/3`）和 parity 跑的是同一段代码（`pick-board-snapshot-core.rs.ts` 的 `completeTies`）：截到 50 行以后，接着把第 50 行所在的并列组补全。

- 往后取主排序键与第 50 行相同的行：先用这一页里 50 行以外的行（用例设了更大的 `size` 时才有），不够就照原 query 只换页码往后翻，碰到键不同的行或没有更多行时停。翻页走的是同一个 loader：RealShort 那边是它自己的 loaders（`asOf` 在闭包里），镜像那边在 `withScriptScope` 里；剧场榜用第一页那份 meta。
- 为什么这样就够：主排序键只有日期、名次、周数、评级位置这些与 collation 无关的列。数据相同时，两边的主排序键是同一组值，第 50 行的键在两边也相同。补全以后两边留下的都是「排在这个键前面的所有行，加上这个键的整个并列组」，是同一组行。组内的先后差异由上面的 collation 规则放行；换过位置的每一行两边都有，照常逐字段比。补全以后还剩下的「少了 / 多了」就是真差异。
- 只补选剧、全部剧库（缺省排序「证据时间」）和剧场榜。ReelShort 榜、发布记录和别的排序仍只截 50 行：它们的顺序不在 collation 规则的模型里。
- 只补第 1 页、每页不少于 50 行的用例。query 带 `page=2` 及以后时，页头也是一条 LIMIT 边界（OFFSET），那里的并列组补不到；`size=20` 时截断在第 20 行，不在第 50 行。这两种只截断，两边 collation 不同时边界上的换行照报成「少了 / 多了」。现有用例（固定清单和推导出来的）都不带 `page`、`size`、`sort`，单测钉住；以后要加这样的用例，先改 `completeTies`。
- 补过的页多一个 `ties` 字段：`extra` 是第 50 行之后补了几行，`capped` 见下。两边数据相同时它也相同，照常比。
- 代价：每个取满 50 行、后面还有行的这类用例，在 RealShort 生产库上至少多读一页。缺省每页 50 行时至多多读 3 页，即第 2 到第 4 页：第 2、3 页两页满页就补满 100 行，第 4 页只在 count 偏大（第 3 页不满、`hasMore` 却说还有）时才读，读到的是空页；每页 100 行时至多多读 1 页（同样情况 2 页）。多读的每一页都是一整次 loader 调用：选剧类是列表查询、count，再挂信号、发布记录，这一页有 ReelShort 行时还要查一次 ReelShort 指标（`loadRowsByIds`）；剧场榜是列表查询、count 和发布记录。快照总耗时比格式 2 长，会打印出来，记进 compute 预算。脚本只在开跑前查一次忙时段，GSC 采集和镜像同步之前只留了 10 分钟；跑得久、跑进了写库的时段时，只要来源在这期间变了或正在写，结尾那次 `checkSource` 就退出 3、不写文件，不会留下一份前后不一致的快照。
- 上限：第 50 行之后最多补 `TIE_ROWS_MAX` = 100 行。补满 100 行时并列还没断（第 151 行也并列，或者第 151 行在还没读的那一页上），就停下并记 `ties.capped`，不再为看第 151 行多读一页。所以正好 100 行、第 151 行在没读的那一页上的组也记成 capped，偏保守；每页 100 行时第 151 行已经读到，这样的组算补全了。这时剩下的「少了 / 多了」照样算白名单外；主排序键等于那一边第 50 行的行，原因里多一句「并列组超过 100 行，没有补全，可能只是 collation 换行」，按下面的方法人工核。别的行（比如边界上面的）没有这句，就是真差异，照普通差异查。

格式 2 的快照（只截 50 行）parity 直接拒绝，退出 2。

#### 并列组超过 100 行、没有补全时（人工核）

逐个用例人工核，四条都成立才算 collation 造成：

1. 两边的行数相同（50 行加补满的 100 行），少的条数等于多的条数。
2. 镜像里缺的每一行，在镜像库里的剧名、主排序键与快照相同。主排序键按列表种类取，与快照核心的 `tiePrimary`、白名单里「collation 不同时的剧名排序」那一条一致：
   - 选剧、全部剧库：`catalog_rows` / `rs_rows` 的 `latest_evidence_on`、`listed_on`；
   - 日榜（`kd`、`qc`、`qr`）：只有当天名次。那一行这类信号的 `payload->'h'` 里，日期（第 0 格）等于用例那一天（快照里这个用例 `meta.day`）的那一格的名次（第 1 格）；
   - 周榜（`kw`）：只有周数。在用例那一周（`meta.week`）上榜的这类信号里，`payload->>'weeks'` 最大的那条（一样大取 `ord` 最小的）的 `weeks`；
   - 评级榜（`sm`、`mg`）：按 `row_key, ord` 取第一条信号（评级档的用例只看那一档）的 `grade` 在 SSS…D 里的位置，不在里面的都算最后一档；再加 `catalog_rows.listed_on`；
   - 其余剧场榜：按 `row_key, ord` 取第一条信号的 `evidence_on`，再加 `catalog_rows.listed_on`。
3. 少的、多的，连同快照末行那组并列行，主排序键全都相同。
4. 在镜像库里把这组行放进 `VALUES`，按列表自己的 ORDER BY 尾部排序（选剧类是剧名、平台、行键，剧场榜是剧名、行键）：加 `COLLATE "C"` 时，前 n 个（n 是快照里这组的行数）正好是快照里的那几行；不加时正好是镜像列表里的那几行。

第 4 条用的是镜像库自己的两种 collation，不是 ICU 近似。

## 5. 漂移检查

```bash
git -C "$RS_REPO" fetch origin
RS_REPO="$RS_REPO" <工作台>/frontend/scripts/pick-board-drift.sh
```

- 读 `frontend/src/server/pick-board/PORTED_FROM`：第一行 `commit <sha>` 是移植基准（816ca2e），其余每行一个 RealShort 路径。可以用环境变量 `PORTED_FROM` 换一个文件。
- 对这些路径执行 `git diff --stat <sha>..origin/main`。路径按字面匹配，所以带 `(protected)`、`[resource]` 的路径也不会被当成通配符。
- 脚本自己不 fetch，也不改检出。

退出码：0 没有变化；1 有变化（打印 diff --stat，要评估是否补移植）；2 缺 `RS_REPO`、`PORTED_FROM` 格式不对，或检出里没有基准 commit 或 `origin/main`（先 fetch）。

2026-09-25 核对：本机 RealShort 检出的 origin/main（c45c520，2026-09-24）已经包含 816ca2e（批判 C34 写的是当时还不在），所以差异只来自 816ca2e 之后 main 上的改动。基准不在 origin/main 上时，差异里会混进基准所在分支自己的改动；每次跑之前可以先确认：

```bash
git -C "$RS_REPO" merge-base --is-ancestor <基准 commit> origin/main && echo 基准在 main 上
```

## 6. 收尾

```bash
rm -f "$SCRATCH/snap.json" "$SCRATCH/reader-url.txt" "$SCRATCH/supabase-ca.pem"
rmdir "$SCRATCH"
git -C "$RS_REPO" status --short     # 空
```

结论记进 `docs/pick-workbench/progress.md`，只写版本号、用例数、差异条数与耗时，不贴数据。

## 核对记录

**2026-09-24（P4-4，镜像 v1，as_of 18:30 UTC，sourceRevision c45c520）**：

- 在 20:50–21:00 UTC 之间拍快照：79 个用例，RealShort 那边 240.1 秒，开头和结尾的 fingerprint 核对都通过，清洗 0 格。本机 RealShort 检出有未提交的改动，所以没有 checkout，而是用 `git worktree add --detach` 在临时目录检出 c45c520，`node_modules` 用符号链接指到原检出，`.env.local` 用绝对路径传给 dotenv；跑完删掉 worktree。
- 镜像这边耗时约 166 秒。collation：RealShort 是 C.UTF-8，镜像是 en_US.UTF-8。本地剧场键差集为空，`meta.scrub` 为空。
- 第一次跑，白名单外有 25 条：
  - 1 条是账号列表顺序。镜像用 `COLLATE "C"` 重排后，19 个账号与 RealShort 完全一致。比对规则随后补上账号列表（0a0c5af）。
  - 24 条是分页边界上的换行，在 8 个用例里：pick、all、pick.basis.sm、pick.basis.gn、pick.inuse、pick.dated、pick.off、rank.gn。按上面「LIMIT 边界上的换行」逐个核过，四条全部成立。
- 补规则后重跑：白名单外 24 条，全部是上面核过的边界换行；白名单内 7,961 处，其中 collation 18 处。结论：没有数据差异。
- 漂移检查：基准 816ca2e 在 origin/main 上，移植路径没有变化，退出 0。

**2026-09-25（并列补全上线后的第一次，镜像 v4，as_of 06:45 UTC，sourceRevision c45c520，脚本 fe155eb）**：

- 06:47 UTC 手动同步发布 v4（RealShort 06:00 同步之后 45 分钟），06:50–07:03 拍快照：79 个用例，RealShort 那边 788.7 秒，开头和结尾的 fingerprint 核对都通过，清洗 0 格。做法同上一条（临时 worktree）。
- 耗时比 v1 那次（238 秒）长很多。不补并列组的用例（ReelShort 榜、发布记录、证据页等）这次也慢了 1.7 倍（93 → 155 秒），是 RealShort 生产库当时本身就慢；补并列组的用例慢了 4.4 倍（145 → 631 秒），按 1.7 倍折算，补全本身约多出 380 秒：每翻一页，count、挂信号 / 发布记录 / ReelShort 指标的查询都要再跑一遍。
- 并列补全：两边都是 30 个用例取满 50 行，其中 19 个补了共 155 行，0 个超过 100 行没补全。
- 第一次 parity：白名单外 5 条，都是镜像这边整个用例出错（`MirrorBusy`，驱动层取连接超时，没有 PG 错误码）；同一时段 Vercel 生产没有 `query failed`，reader 只有 3 个连接。整份重跑一次就没有了。
- 重跑：镜像这边 227 秒，退出 0，白名单外 0 条；白名单内 9,105 处，其中 collation 16 处（含账号列表）。v1 那次要人工核的 24 条边界换行，这次由并列补全消掉了。
- 漂移检查：退出 0。

## 本机演练（合成数据）

`db.ts` 只接受按 CA 校验的 TLS，而共用的测试集群没有开 SSL。所以本机要演练的话：

1. 用 `openssl` 自签一个 CA 和一张 `127.0.0.1` 的服务端证书，另起一个 `ssl = on` 的 PG17。
2. 在上面跑 `board_fixture.py up`。
3. 在临时目录里搭一个假的 RealShort 检出：`package.json` 的 name 写 realshort，`src/lib/pick/{queries,request,export-v2}.ts` 与 `src/db/index.ts` 用镜像 loaders 按 RealShort 的输出形状造数据。
4. 先跑快照脚本，再对同一版本跑 parity。

P4-3 实现时在 fixture 的 v3 上这样跑过：73 个用例，没有白名单外差异；改动镜像里一个剧名后退出 1；换成 v2 时退出 3。这些文件都不进仓库。
假 RealShort 检出还要有 `src/lib/pick/export-v2-map.ts`，导出 `scrubPanText(text) → {text, hits}`。

## Gateway common-query cutover (T4-ui)

The app-facing exports in `frontend/src/server/pick-board/index.ts` route the five
declared candidate/catalog/rank/posted/rules domains through `common-loaders.ts`
and authenticated `POST /api/pick/query`. The original `queries*.ts` / `rs-queries.ts`
SQL remains readable as the differential oracle, not an automatic fallback after
query failure. Specialized row/detail, authenticated result replay and Trends
retain their dedicated contracts. Historical replay without its original mirror
cannot substitute current rows; users explicitly start a newest-data query.

The adapter preserves old board defaults, filtering, complete counts and facets,
ordered page rows, per-date rank signals (including multiple signals for one row),
legacy growth top50 and ledger200 limits, full-text single-RS decorations, source
states and immutable version pairing. `language_order` retains PostgreSQL ordering
of language facets even for numeric strings. A completely absent period renders
an explicit source notice rather than a configuration error or fabricated zero;
missing/ambiguous requested periods with available alternatives keep their actual
period and resolution. Invalid legacy calendar bookmarks use the old missing-period
fallback while continuing to display that the requested period was missing.

Run `frontend/scripts/pick-common-query-parity.py` using the repository's Python
runtime, with `--cluster-file` pointing to metadata for an explicitly marked
loopback throwaway PostgreSQL cluster and `--output` pointing to a private evidence
directory. It publishes synthetic versions through the production writer, launches
the actual Gateway with no models, creates real local sessions, checks CSRF and
owner isolation, and runs the original TypeScript SQL alongside index-exported
common-query readers against the same database. The default gate requires real
query traffic for all five domains. `--candidate-adapters` is only the pre-cutover
gate; `--gateway-root` supports development against a separate service checkout,
while final acceptance uses the assembled checkout.

The fixture covers two historical versions,230 extra source rows, real pagination,
unknown/numeric languages, tied order keys, archived posts, cross-year weekly labels,
all21 ranking types and version/authentication failures. Credentials remain in0600 local files;
the harness removes its generated database/reader role and stops its own Gateway.
No production database, source provider, or model call is used. These tests do not
establish deployment or production source freshness.
