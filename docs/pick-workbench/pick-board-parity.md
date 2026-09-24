# 资料页核对与漂移检查（P4-3）运行手册

资料页（`/workspace/pick-data`）的查询是从 RealShort 选剧台移植过来的，读的是工作台镜像。这里有两件事：

- **逐字段核对**：同一次采集下，资料页的 loaders 读镜像，RealShort 的 loaders 读 RealShort 生产库，两边结果除白名单外应当完全一致。
- **漂移检查**：移植以来，RealShort 的 main 有没有改动被移植的文件。

什么时候跑：RealShort 每次发版之前；P4-4 验收时（合成稿第 5.4 节）。三个脚本都只读。

| 脚本 | 在哪跑 | 读什么 |
|---|---|---|
| `frontend/scripts/pick-board-snapshot.rs.ts` | 临时复制进 RealShort 检出 | RealShort 生产 Neon（`.env.local` 的 `DATABASE_URL`） |
| `frontend/scripts/pick-board-parity.ts`（比对规则在 `pick-board-parity-compare.ts`） | 工作台 `frontend/` | 镜像，以 `pick_board_reader` 身份，经 `PICK_MIRROR_READER_URL` 与 `PICK_MIRROR_CA_PEM` |
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
cp <工作台>/frontend/scripts/pick-board-snapshot.rs.ts scripts/pick-board-snapshot.ts

VERCEL_GIT_COMMIT_SHA=<source_revision> pnpm exec dotenv -e .env.local -- \
  pnpm exec tsx --conditions=react-server scripts/pick-board-snapshot.ts \
  --as-of <as_of_iso> --fp <fingerprint> --out "$SCRATCH/snap.json"

rm scripts/pick-board-snapshot.ts
git checkout -                              # 回到原来的分支
git status --short                          # 又是空的
```

- `source_revision` 为 null 的版本（本地导出的）：不设 `VERCEL_GIT_COMMIT_SHA`。
- 脚本先核对：当前目录是 RealShort 检出；`VERCEL_GIT_COMMIT_SHA` 等于 HEAD；不在忙时段。然后在开头和结尾各调一次 `export-v2` 的 `checkSource`（内部读 `readSourceSnapshot`）：fingerprint 不是 `--fp`（409），或有来源正在写（503），就退出 3，不写文件。
- `--only <正则>` 只跑 id 匹配的用例（`globals` 总会跑），用来补跑或省预算。
- 快照是 0600、不覆盖已有文件。里面有 RealShort 的业务数据，但没有网盘链接、提取码和任何金额：这些字段在落盘前换成占位，网盘只留 `hasPan`。
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
  - RealShort 那边的文字只打印长度和摘要，因为原文可能带网盘信息。
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
- **清洗占位**：镜像是「[网盘信息已移除]」、RealShort 是原文的格子，只在 `meta.scrub` 记过的字段路径上放行。
  - 同一格子在几个用例里出现，只算一次；
  - 总数不得超过 `meta.scrub` 记的次数；
  - 榜单行的 `dayNote` 记到它取自的那个信号 `payload.h` 格子上。
  - manifest meta 文本（比如来源说明）的清洗不计入 `meta.scrub`，出现就是白名单外，需要人工确认。
- **payload、posts 里白名单之外的键**：导出只保留 `SIGNAL_PAYLOAD_KEYS` 与 `POSTED_POST_KEYS`；单测钉住这两个列表与 `contracts.py` 一致。
- **「分成」改名「订单」**。
- **timestamptz 按毫秒比**。
- **collation 不同时的剧名排序**：两边 `datcollate` 不同时才放行，并且只放行剧场行列表（先后不同的两项剧名不同）与语种计数（语种名不同）里的先后差异。

不在白名单里的：

- `clicks7` 的 0 与 null（批判 B16）。
- ReelShort 预估分成榜（`rs_bill`，按 `bill_rank` 排）的顺序。
- 同一行在榜单里出现两次时的先后。这种列表按下标比，不按行键比。

## 5. 漂移检查

```bash
git -C "$RS_REPO" fetch origin
RS_REPO="$RS_REPO" <工作台>/frontend/scripts/pick-board-drift.sh
```

- 读 `frontend/src/server/pick-board/PORTED_FROM`：第一行 `commit <sha>` 是移植基准（816ca2e），其余每行一个 RealShort 路径。可以用环境变量 `PORTED_FROM` 换一个文件。
- 对这些路径执行 `git diff --stat <sha>..origin/main`。路径按字面匹配，所以带 `(protected)`、`[resource]` 的路径也不会被当成通配符。
- 脚本自己不 fetch，也不改检出。

退出码：0 没有变化；1 有变化（打印 diff --stat，要评估是否补移植）；2 缺 `RS_REPO`、`PORTED_FROM` 格式不对，或检出里没有基准 commit 或 `origin/main`（先 fetch）。

816ca2e 目前不在 RealShort 的 main 上（批判 C34），所以 main 合并 feed v2 之前，这里的差异里也会包括 feed v2 分支自己的改动。

## 6. 收尾

```bash
rm -f "$SCRATCH/snap.json" "$SCRATCH/reader-url.txt" "$SCRATCH/supabase-ca.pem"
rmdir "$SCRATCH"
git -C "$RS_REPO" status --short     # 空
```

结论记进 `docs/pick-workbench/progress.md`，只写版本号、用例数、差异条数与耗时，不贴数据。

## 本机演练（合成数据）

`db.ts` 只接受按 CA 校验的 TLS，而共用的测试集群没有开 SSL。所以本机要演练的话：

1. 用 `openssl` 自签一个 CA 和一张 `127.0.0.1` 的服务端证书，另起一个 `ssl = on` 的 PG17。
2. 在上面跑 `board_fixture.py up`。
3. 在临时目录里搭一个假的 RealShort 检出：`package.json` 的 name 写 realshort，`src/lib/pick/{queries,request,export-v2}.ts` 与 `src/db/index.ts` 用镜像 loaders 按 RealShort 的输出形状造数据。
4. 先跑快照脚本，再对同一版本跑 parity。

P4-3 实现时在 fixture 的 v3 上这样跑过：73 个用例，没有白名单外差异；改动镜像里一个剧名后退出 1；换成 v2 时退出 3。这些文件都不进仓库。
