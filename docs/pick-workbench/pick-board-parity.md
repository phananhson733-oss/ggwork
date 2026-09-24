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
- 快照要在生产 Neon 上串行跑 70 多个用例，涨幅榜的 d1 单条就要 8–9 秒。总耗时会打印出来，记进 compute 预算。

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
- 快照是 0600、不覆盖已有文件（`--out` 已存在时开跑前就退出 2）。里面有 RealShort 的业务数据，但没有网盘链接、提取码和任何金额：
  - 网盘字段与金额字段在落盘前换成占位，网盘只留 `hasPan`；
  - 其余每个文本值都过一遍 RealShort 自己的 `scrubPanText`（导出清洗用的同一个；开跑前拿一条网盘链接自检，认不出就退出 2），认出网盘信息的整串换成「[快照清洗：网盘信息]」；
  - 对象的键（行键、来源名这类标识）不过清洗器。
- 结尾会打印清洗了多少格。
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
- 先核对：vN 是否已发布且 reader 读得到；快照的 fingerprint、as_of（按毫秒比）、sourceRevision 是否都与 vN 相同。
- 再在 vN 上照快照里的 query 逐个重放移植后的 loaders，逐字段比。

输出依次是：

- 本地剧场键差集，即版本 `platformRules` 的键减去本地 `PLATFORMS`。不为空说明 RealShort 新加了剧场，要补移植。
- 两边库的 collation。
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
  - 主排序键相同：选剧与全部剧库是证据日期与剧单日期；日榜是名次；周榜是周数；评级榜是评级与剧单日期；其余剧场榜是证据日期与剧单日期；语种计数是条数；账号列表没有主排序键；
  - RealShort 的先后正是按 RealShort 的 collation 比剧名、平台、行键得出的先后（剧场榜不比平台，语种计数只比语种名，账号比分组、名字、id），镜像的先后也正是按镜像的 collation 得出的。
  - collation 的模型：`C`、`POSIX`、`C.UTF-8`、`ucs_basic` 按码点比，其余用 ICU（`Intl.Collator`）近似 glibc。近似不准时只会多报，这时在两边库上各用 `SELECT '甲' < '乙'` 核一下那一对。

不在白名单里的：

- `clicks7` 的 0 与 null（批判 B16）。
- ReelShort 预估分成榜（`rs_bill`，按 `bill_rank` 排）的顺序。
- 同一行在榜单里出现两次时的先后。这种列表按下标比，不按行键比。

### LIMIT 边界上的换行（collation 不同时，要人工核）

快照和镜像都只留每个列表的前 50 行。两边 collation 不同时，第 50 行所在的那组并列行（主排序键相同）按剧名排的先后不一样，截到 50 行时两边会各有几行不同：parity 报成成对的「镜像少了这一项」「镜像多了这一项」。

脚本不放行这种差异：只看快照判断不出镜像里缺的那一行数据是不是也变了。逐个用例人工核，四条都成立才算 collation 造成：

1. 两边都是 50 行，少的条数等于多的条数。
2. 镜像里缺的每一行，在镜像库里的剧名、主排序键与快照相同（选剧类列表查 `catalog_rows` / `rs_rows` 的 `latest_evidence_on`、`listed_on`；剧场榜查 `catalog_signals` 按 `row_key, ord` 取第一条的 `evidence_on`，再加 `listed_on`）。
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

## 本机演练（合成数据）

`db.ts` 只接受按 CA 校验的 TLS，而共用的测试集群没有开 SSL。所以本机要演练的话：

1. 用 `openssl` 自签一个 CA 和一张 `127.0.0.1` 的服务端证书，另起一个 `ssl = on` 的 PG17。
2. 在上面跑 `board_fixture.py up`。
3. 在临时目录里搭一个假的 RealShort 检出：`package.json` 的 name 写 realshort，`src/lib/pick/{queries,request,export-v2}.ts` 与 `src/db/index.ts` 用镜像 loaders 按 RealShort 的输出形状造数据。
4. 先跑快照脚本，再对同一版本跑 parity。

P4-3 实现时在 fixture 的 v3 上这样跑过：73 个用例，没有白名单外差异；改动镜像里一个剧名后退出 1；换成 v2 时退出 3。这些文件都不进仓库。
假 RealShort 检出还要有 `src/lib/pick/export-v2-map.ts`，导出 `scrubPanText(text) → {text, hits}`。
