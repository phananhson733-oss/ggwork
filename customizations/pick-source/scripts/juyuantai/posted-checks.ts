/**
 * 发布记录（catalog_posted）写库前的主键检查，2026-09-18 从 scripts/import-catalog.ts 的 preflight()
 * 抽出来的【纯函数】模块。
 *
 * 【为什么单独一个文件】import-catalog.ts 顶上 import 了 @/db，node --test 加载不了，判定留在那边就永远
 * 测不到——PR #39 加的两道检查就是这么裸奔了一周。同 queyu-checks.ts：【这个文件里不许出现任何 import】。
 *
 * 【为什么一次报全】2026-09-18 10:59 那轮：飞书选剧池里 09-17 13:45 一次多行粘贴溢出，多出 8 条脏行
 * （4 条空编号、4 条前导换行的 `\nSD-0001xx`，其中 3 条 strip 后与真实行同号）。原版 preflight 空编号先抛，
 * 重复被挡在后面看不见——运营删完 4 条空行，第二天会再撞 3 条重复、再停一天。所以三类问题一次全列，
 * 每条带 record_id、剧名、创建日期与帖子数，让人一趟改完、并且知道重复的两条该留哪条。
 *
 * 【「剧ID」是手填的 text 列，不是自动编号】2026-09-18 用 lark-cli field-list 实测（type: "text"，表上也没有
 * workflow）。PR #39 里「多半是自动编号还没落地，等几分钟重跑」那句是错的方向指引：空着就一直空着，
 * 只能人去补或删。
 */

/**
 * preflight 只看这几列；与 CatalogPostedInsert 的同名字段同义。
 * 【sd 按 string | null | undefined 收】：RawPostedDrama.sd 的类型写的是 string，但 posted.py 的 text()
 * 对空值返回 None、落成 JSON null，toPostedRows 原样透传。2026-09-18 --dry 实测第一版在这里
 * .trim() 炸成 "Cannot read properties of null"——守门自己崩了，一条都报不出来。
 */
export interface PostedKeyRow {
  sd: string | null | undefined;
  feishuRecord: string;
  title: string;
  postCount: number;
  schedCount: number;
  createdOn: string | null;
}

/**
 * 编号的合法形状。【必须与 src/lib/pick/request.ts 的 cleanSd 逐字相同】——那边是证据页 ?sd= 的判定，
 * 导入放行了一个证据页拒绝的编号，那条记录的页面就永远打不开而列表照常 200。
 * tests/posted-checks.test.ts 按源码文本钉着两份一致。
 */
export const SD_SHAPE = /^[A-Za-z0-9_-]{1,40}$/;

/** 报告里最多列多少条 record，防一次把几百行全灌进飞书告警 */
const MAX_LISTED = 20;

function describe(r: PostedKeyRow): string {
  const title = r.title.trim() ? `「${r.title.trim()}」` : "（无剧名）";
  const created = r.createdOn ? `建于 ${r.createdOn}` : "建于 ?";
  const posts =
    r.schedCount > 0 ? `帖子 ${r.postCount}（另 ${r.schedCount} 条待公开）` : `帖子 ${r.postCount}`;
  return `${r.feishuRecord || "(无 record)"} ${title} ${created} ${posts}`;
}

/** 控制字符转义后再印：一格里粘了两行的编号，原样印出来会把报告拆成两行 */
function visible(sd: string): string {
  return JSON.stringify(sd).slice(1, -1);
}

/** 归一后的编号：null / undefined / 纯空白都算空串 */
function keyOf(r: PostedKeyRow): string {
  return (r.sd ?? "").trim();
}

function byCreated(a: PostedKeyRow, b: PostedKeyRow): number {
  return (a.createdOn ?? "").localeCompare(b.createdOn ?? "") || a.feishuRecord.localeCompare(b.feishuRecord);
}

function listed<T>(items: T[], render: (t: T) => string): string[] {
  const lines = items.slice(0, MAX_LISTED).map((t) => `    ${render(t)}`);
  if (items.length > MAX_LISTED) lines.push(`    …另 ${items.length - MAX_LISTED} 条略`);
  return lines;
}

/**
 * 三类主键问题一次全查：空编号、形状不合法、编号重复。返回空数组才算通过；
 * 非空时每个元素是报告的一行，调用方 join("\n") 直接当错误文案。
 * 判定一律用 trim 后的值——posted.py 的 text() 本来就 strip 过，这里守的是 json 从别处来时同样的语义。
 */
export function postedKeyProblems(rows: PostedKeyRow[], poolUrl?: string): string[] {
  const blank = rows.filter((r) => !keyOf(r)).sort(byCreated);
  const shape = rows.filter((r) => keyOf(r) && !SD_SHAPE.test(keyOf(r))).sort(byCreated);
  const groups = new Map<string, PostedKeyRow[]>();
  for (const r of rows) {
    const key = keyOf(r);
    if (!key) continue;
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  const dup = [...groups.entries()].filter(([, v]) => v.length > 1);

  const total = blank.length + shape.length + dup.length;
  if (!total) return [];

  const out: string[] = [
    `发布记录有 ${total} 处过不了主键检查，不写库（库里旧数据原样留着）：`,
  ];
  if (blank.length) {
    out.push(`  空编号 ${blank.length} 条：`, ...listed(blank, describe));
  }
  if (shape.length) {
    out.push(
      `  形状不合法 ${shape.length} 条（编号只许字母数字与 _ -，证据页 ?sd= 会拒绝它们）：`,
      ...listed(shape, (r) => `${visible(r.sd ?? "")} ← ${describe(r)}`),
    );
  }
  if (dup.length) {
    out.push(
      `  编号重复 ${dup.length} 组（主键撞了，导入分不出该留哪条）：`,
      ...listed(dup, ([sd, hits]) => `${sd} ← ${[...hits].sort(byCreated).map(describe).join(" 与 ")}`),
    );
  }
  out.push(
    "处置：选剧池的「剧ID」是手填的文本列，不是自动编号，空着不会自己补上。去飞书把上面这些行删掉或改对编号——",
    "  没剧名、没帖子的多半是误建的空行（一次多行粘贴会溢出成好几行），直接删；重复的留有帖子的那条。",
    "  改完在 Mac mini 上 pnpm catalog-refresh 重跑一轮。",
  );
  if (poolUrl) out.push(`  表：${poolUrl}`);
  return out;
}
