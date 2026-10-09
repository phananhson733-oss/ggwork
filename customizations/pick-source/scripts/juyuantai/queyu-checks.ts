/**
 * 鹊娱采集的完整性判定，2026-09-16 抽出来的【纯函数】模块。
 *
 * 【为什么单独一个文件】queyu.ts 顶上 import playwright、底下 main() 是模块级就跑的，
 * 测试 import 它会当场开一个浏览器。判定逻辑留在那边的话，tests/pick-queyu.test.ts 就只能
 * 正则比对源码文本、断言「代码长这样」而不是「行为是这样」——本仓库在 sitemap-request.ts
 * 上吃过这个亏（断言全绿、注释还写着已经盖住，实际漏了一整类输入）。
 * 【所以这个文件里不许出现任何 import】，加了它就进不了 node --test。
 *
 * 判定的共同立场：证明不了对就作废本轮。宁可当天不更新、明确非零退出并告警，
 * 也不要写出一份看不出错的错数据——后者会被「同一天不重写」冻住，而且下游一路报成功。
 */

/** 榜里一行只留会用到的字段 */
export interface RankRow {
  id: number;
  theater: string;
  theaterId: number;
  playletId: string;
  title: string;
  language: string;
  productionType: string;
  publishTime: string;
  labels: string;
  settleScope: number[];
  rank: number;
}

export interface RankSnapshot {
  date: string;
  conv: RankRow[];
  rev: RankRow[];
}

/** 两张榜各自的行数。少一行都算没读全：残缺榜一旦落盘，同日不重写会让它赖到第二天 */
export const EXPECTED_RANK_ROWS = 25;

/**
 * 榜单的结构校验，返回空数组才算通过。
 *
 * 【两张榜逐行相同那条是这里最重要的，也是行数检查盖不住的】readTab(2) 的请求要是还没回来，
 * untilLoaded 会拿着上一张榜的 tableData 就返回，于是 rev 是 conv 的逐字拷贝：
 * 两张榜【都正好 25 行】、日期也对，所以「日期 + 各 25 行」那版校验一样会放行。
 * 文件写出去、还被同日不重写冻一整天，build.py 把转化率的名次当成收入榜挂到剧上——
 * 页面不报错、飞书报成功、选剧台显示的是错的。
 */
export function rankProblems(ranks: RankSnapshot): string[] {
  const problems: string[] = [];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(ranks.date)) problems.push(`榜单日期不是 YYYY-MM-DD：${JSON.stringify(ranks.date)}`);
  for (const [name, list] of [["转化率", ranks.conv], ["总收入", ranks.rev]] as const) {
    if (list.length !== EXPECTED_RANK_ROWS) {
      problems.push(`${name}榜 ${list.length} 行，应为 ${EXPECTED_RANK_ROWS} 行`);
      continue;
    }
    const got = list.map((r) => r.rank).slice().sort((a, b) => a - b).join(",");
    const want = list.map((_, i) => i + 1).join(",");
    if (got !== want) problems.push(`${name}榜的名次不是连续的 1..${EXPECTED_RANK_ROWS}（有重号或缺号）`);
    const broken = list.filter((r) => !Number.isFinite(r.id) || !r.title).length;
    if (broken) problems.push(`${name}榜有 ${broken} 行缺 id 或 title`);
    if (!list.some((r) => r.theater)) problems.push(`${name}榜一行都没有剧场名，build.py 会全部挂不上`);
  }
  if (ranks.conv.length > 0 && ranks.conv.length === ranks.rev.length && ranks.conv.every((r, i) => r.id === ranks.rev[i]?.id)) {
    problems.push("两张榜逐行相同——第二张多半没读到，读的还是第一张的 tableData");
  }
  return problems;
}

/** 剧库一页多少行：500 实测可用。放在这里是因为缺口容差要拿它当上限，见 libraryDrift */
export const LIBRARY_PAGE_SIZE = 500;
/** 剧库行数缺口容差：翻页期间上游下架剧会让 offset 分页漏掉几行，个位数属正常，1% 就不是 churn 了 */
export const LIBRARY_DRIFT_FLOOR = 20;
export const LIBRARY_DRIFT_RATIO = 0.01;
/**
 * 一页里允许多少行没有 id。
 * 【这条防的是去重被静默关掉】：判重全靠 row.id，上游要是把这个字段改名，每一行都拿不到 id，
 * 原来的写法只在末尾 console.warn 一句，而翻页重叠保护从此形同虚设、没有任何东西会失败。
 */
export const MAX_NO_ID_RATIO = 0.05;

/**
 * 缺口容差。【上限必须小于一页】——不然剧库涨大之后这道检查会自己失明：
 * 1% 在 total = 50,000 时正好是 500 行，也就是一整页；丢掉一整页算出来的缺口恰好等于容差，
 * 判据是严格小于，于是静默放行。今天 total 41,672（容差 417），离那个临界点只差 8,328 行，
 * 而剧库每天都在涨（09-15→09-16 涨了 190）。所以夹在 LIBRARY_PAGE_SIZE - 1。
 */
export function libraryDrift(total: number, totalAfter: number): number {
  const hi = Math.max(Number.isFinite(total) ? total : 0, Number.isFinite(totalAfter) ? totalAfter : 0);
  return Math.min(LIBRARY_PAGE_SIZE - 1, Math.max(LIBRARY_DRIFT_FLOOR, Math.ceil(hi * LIBRARY_DRIFT_RATIO)));
}

/** 一行的判重键。剧库每行都有 id；个别行没有只能放行并计数，整页都没有就是字段改名了 */
export function rowKey(row: Record<string, unknown>): string {
  const id = row.id;
  return id === undefined || id === null || id === "" ? "" : String(id);
}

export interface PageAbsorbResult {
  fresh: Record<string, unknown>[];
  dupInPage: number;
  noIdInPage: number;
  problem: string | null;
}

/**
 * 吃掉一页：判重、挑出没见过的行，顺手回答这一页是不是读串了。
 *
 * 【旧版只比 rows.length 与 total，那个检查从原理上就发现不了翻页读串】——
 * 漏一页 + 重一页，两者相抵，行数一模一样。所以判据必须是行 id 的唯一性，不是行数。
 *
 * 【整页全重复 vs 零星重复要分开】采集期间上游新增剧会把后面的行往下挤，造成个位数重复，
 * 那是 offset 分页的正常代价，去重放行即可；整页 500 行全是见过的，只可能是这一页的请求
 * 还没回来就读了上一页的 tableData。
 *
 * 【没有 id 的行放行但要盯着比例】理由见 MAX_NO_ID_RATIO。
 */
export function absorbPage(
  seen: Set<string>,
  chunk: { rows: Record<string, unknown>[]; landed: number },
  pageNo: number,
  pages: number,
): PageAbsorbResult {
  const empty = { fresh: [] as Record<string, unknown>[], dupInPage: 0, noIdInPage: 0 };
  if (chunk.landed !== pageNo) {
    return { ...empty, problem: `剧库翻到第 ${pageNo} 页，组件停在第 ${chunk.landed} 页，本轮作废` };
  }
  const fresh: Record<string, unknown>[] = [];
  let dupInPage = 0;
  let noIdInPage = 0;
  for (const row of chunk.rows) {
    const key = rowKey(row);
    if (!key) {
      noIdInPage++;
      fresh.push(row); // 没 id 参与不了判重，但它是真数据，不丢
      continue;
    }
    if (seen.has(key)) {
      dupInPage++;
      continue;
    }
    seen.add(key);
    fresh.push(row);
  }
  if (chunk.rows.length > 0 && noIdInPage > Math.max(1, Math.ceil(chunk.rows.length * MAX_NO_ID_RATIO))) {
    return {
      ...empty,
      problem: `剧库第 ${pageNo}/${pages} 页 ${chunk.rows.length} 行里有 ${noIdInPage} 行没有 id——判重失效了，上游多半改了字段名，本轮作废`,
    };
  }
  if (chunk.rows.length > 0 && dupInPage === chunk.rows.length) {
    return {
      ...empty,
      dupInPage,
      problem: `剧库第 ${pageNo}/${pages} 页 ${chunk.rows.length} 行全部与前面的页重复——读到的是上一页的数据，本轮作废`,
    };
  }
  return { fresh, dupInPage, noIdInPage, problem: null };
}

/** total 本身不可信时不发布空剧库 */
export function totalProblem(total: number): string | null {
  return Number.isFinite(total) && total > 0 ? null : `剧库 total 读出来是 ${total}，不发布空剧库`;
}

/** 收尾核对：拿到的行数对不对得上开始与结束两次读到的 total */
export function shortfallProblem(rowCount: number, total: number, totalAfter: number): string | null {
  const after = Number.isFinite(totalAfter) && totalAfter > 0 ? totalAfter : total;
  const lo = Math.min(total, after);
  const drift = libraryDrift(total, after);
  if (rowCount < lo - drift) {
    return `剧库只拿到 ${rowCount} 行，开始 total ${total} / 结束 total ${totalAfter}，缺口超过容差 ${drift}，本轮作废`;
  }
  return null;
}
