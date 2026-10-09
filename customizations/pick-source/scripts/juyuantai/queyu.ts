/**
 * 鹊娱汇聚台（cps-distribution.zwnet.cn）的每日采集：两张跨剧场榜 + 剧库全量。
 *
 *   pnpm queyu -- --login       # 第一次：开有头浏览器，人自己登录鹊娱（脚本永远不输任何凭据），登录态存进 profile 目录
 *   pnpm queyu                  # 之后每天：无头，读榜 + 读剧库，写 scripts/juyuantai/queyu/
 *   pnpm queyu -- --no-library  # 只读榜，不翻剧库（84 页，1–2 分钟）
 *
 * 【只驱动站点自己的 Vue 应用取数，不打它的接口】鹊娱的接口（cpspromotion-api.zwnet.cn）请求头带 Token + Sign，
 * 既定规矩是不复刻签名；这里做的是「打开网站去爬」——在页面里调它自己的 startSearch / handlePageChange /
 * onTabPositionInput，请求由它自己发，我们只读 $data。2026-09-12 在 Chrome 里逐个验过：
 * 剧库 pageInfo.page_size 设 500 后一页真给 500 行（total 41,672 → 84 页），handlePageChange({page}) 翻页零重叠；
 * 排行榜组件名 Ranking，onTabPositionInput(1|2) 切「7 日转化率 / 7 日总收入」，各 25 行，updateTime 是榜单日期。
 *
 * 【永远不碰写操作】createTask / submitTask / showDetailDrawer 一个都不调——鹊娱在本仓库的约束一直是只读
 * （不创建推广任务、不创建链接和口令、不创建锚点）。tests/pick-queyu.test.ts 钉着这个文件里不出现那几个名字。
 *
 * 【登录态存在 queyu-state.json，不是 profile 目录】鹊娱的登录 token 是名为 authorization 的 session cookie，
 * Chrome 的持久化 profile 默认不保存 session cookie——进程一关就没，profile 里只剩 localStorage 的 userInfo
 * （那里面没有任何 token，只是 partner_id / account 这些身份显示信息）。所以 --login 结束前用 storageState
 * 把 cookie 连 session 的一起导出，之后每次跑先 addCookies 注回去。2026-09-11 在 Mac mini 上实测：
 * 不注回去时无头与有头都被踢到 /login，注回去后无头直接进 /promotion/index。
 *
 * 【登录态是唯一会静默过期的东西】落到登录页时 exit 2，运行器把它当失败发飞书；不会自己重试，重试也过不去。
 *
 * 产出（都不进仓库，见本目录 .gitignore）：
 *   queyu/rank-<榜单日期>.json  {date, conv: [...25], rev: [...25]}   同名已存在就不重写（榜一天一版，幂等）
 *   queyu/library.json          剧库全量，每天覆盖；本轮只存不用，剧单切源时拿它验
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type Page } from "playwright";

// 完整性判定全在这个纯函数模块里，测试直接跑它的行为（理由见那个文件的头注释）
import { EXPECTED_RANK_ROWS, LIBRARY_PAGE_SIZE, absorbPage, rankProblems, shortfallProblem, totalProblem, type RankRow, type RankSnapshot } from "./queyu-checks";

const HERE = process.env.PICK_SOURCE_WORKDIR ?? dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(HERE, "queyu");
const PROFILE_DIR = process.env.QUEYU_PROFILE_DIR ?? join(homedir(), ".realshort", "queyu-profile");
/** 登录态的真正落点，见下方注释：profile 目录存不住鹊娱的 session cookie，--login 时另存一份 */
const STATE_FILE = process.env.QUEYU_STATE_FILE ?? join(homedir(), ".realshort", "queyu-state.json");
const PROMOTION_URL = "https://cps-distribution.zwnet.cn/promotion/index";
/** 组件 loading 回 false 的等待上限：一页 500 行带封面，渲染本身要几秒 */
const LOADING_TIMEOUT_MS = 60_000;
/**
 * 打开站点的超时与重试。【2026-09-16 那轮就挂在这里】：goto 当时没给 timeout、吃 Playwright
 * 默认的 30 秒，日志是 `page.goto: Timeout 30000ms exceeded`，整轮作废。这个页面是 Vue SPA，
 * domcontentloaded 要等主 bundle 下完，网络抖一下就过不去；而这一步恰恰是全链路上唯一
 * 值得重试的（瞬时网络故障，重试能过）。daily.sh 头上那句「会失败的只有两处登录过期，
 * 重试过不去」说的只是登录，对打开站点不成立。
 */
const GOTO_TIMEOUT_MS = 90_000;
const GOTO_ATTEMPTS = 3;
const GOTO_RETRY_WAIT_MS = 20_000;
/** 首屏（剧库列表或登录页）出现的等待上限。超了算「页面没出来」，与 goto 超时同类，一起重试 */
const APP_SETTLE_TIMEOUT_MS = 45_000;

const args = new Set(process.argv.slice(2));
const LOGIN = args.has("--login");
const NO_LIBRARY = args.has("--no-library");

/**
 * 页面侧的三个小工具，用 addInitScript 装到 window.__qy 上（每次导航都会重新装）。
 * 这段是浏览器里的 JS 字符串，不能引用 Node 侧的变量；evaluate 里的闭包只拿 window.__qy 用。
 */
const PAGE_HELPERS = `
  // tsx（esbuild）开着 keepNames，会把每个函数包成 __name(fn, "名字")。传进 page.evaluate /
  // waitForFunction 的那些箭头函数一并带上这层包装，而 __name 只存在于 Node 侧的模块作用域，
  // 页面里没有，于是一调就 ReferenceError: __name is not defined。这里补个同名直通函数。
  // 2026-09-11 在 node 25.9 / tsx 4.23.13 上必现；换 node 版本不一定复现，但装着不碍事。
  window.__name = window.__name || function (f) { return f; };
  window.__qy = {
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    findComp(pred) {
      for (const el of document.querySelectorAll("*")) {
        let v = el.__vue__;
        for (let i = 0; v && i < 8; i++) { if (pred(v)) return v; v = v.$parent; }
      }
      return null;
    },
    // 先等 loading 翻上来，再等它落下去，返回有没有真的看到这一轮请求。
    // 【旧版是 sleep(400) 之后直接看 loading】：请求要是还没发出去，loading 一直是 false，
    // 函数立刻返回，调用方读到的是上一页/上一张榜的 tableData——不报错，数据是错的。
    // 没看到上升沿不当场抛错（极快的响应可能被 20ms 轮询错过），返回 false 交给调用方的判重兜底。
    async untilLoaded(comp, timeoutMs) {
      const t0 = Date.now();
      let started = false;
      while (Date.now() - t0 < 5000) {
        if (comp.$data.loading) { started = true; break; }
        await this.sleep(20);
      }
      if (!started) return false;
      while (comp.$data.loading) { if (Date.now() - t0 > timeoutMs) throw new Error("组件 loading 超时"); await this.sleep(100); }
      return true;
    },
  };
`;

type Vue = { $data: Record<string, unknown>; $options: { name?: string }; $parent?: Vue };
interface PageHelpers {
  sleep: (ms: number) => Promise<void>;
  findComp: <T extends Vue>(pred: (v: Vue) => boolean) => T | null;
  untilLoaded: (comp: Vue, timeoutMs: number) => Promise<boolean>;
}
/*
 * 【evaluate 里的闭包不能引用这个文件里的任何函数或常量】——Playwright 把闭包源码序列化到页面里跑，
 * 外面的东西在那边不存在；所以下面每个闭包里都自己写一遍 window.__qy 的取法与 theaterList 判断。
 */

/**
 * 登录态原子落盘。直接 writeFileSync 覆盖的话，写到一半崩掉会留下半截 JSON，
 * 下一轮解析不了；rename 在同一文件系统上是原子的，要么旧的要么新的。
 */
function saveState(state: unknown): void {
  const tmp = `${STATE_FILE}.tmp`;
  writeFileSync(tmp, JSON.stringify(state));
  chmodSync(tmp, 0o600);
  renameSync(tmp, STATE_FILE);
}

/**
 * 产出也要原子写，理由同 saveState。【库文件 50 MB，直接 writeFileSync 被杀就留半个】，
 * 而下游解析半个 JSON 报的错跟真实原因毫无关系。临时文件带 pid，两轮并行也不会互相覆盖。
 */
function writeAtomic(target: string, content: string): void {
  const tmp = `${target}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, content);
    renameSync(tmp, target);
  } catch (error) {
    try {
      if (existsSync(tmp)) unlinkSync(tmp);
    } catch {
      /* 清不掉临时文件不该盖住真实错误 */
    }
    throw error;
  }
}

/** 落地结果：剧库出来了 / 被踢到登录页 / 什么都没出来 */
type Landing = "app" | "login" | "";

/** 首屏判定：剧库列表出来了，还是被踢到登录页 */
async function waitForLanding(page: Page): Promise<Landing> {
  return page
    .waitForFunction(
      () =>
        location.pathname.includes("/login")
          ? "login"
          : (window as unknown as { __qy: PageHelpers }).__qy.findComp((v) => Boolean((v.$data as { theaterList?: unknown }).theaterList))
            ? "app"
            : "",
      undefined,
      { timeout: APP_SETTLE_TIMEOUT_MS, polling: 500 },
    )
    .then((h) => h.jsonValue() as Promise<Landing>)
    .catch(() => "" as Landing);
}

/**
 * 打开推广台并等首屏。【重试的边界是「瞬时故障」，不是「哪一行代码」】：
 *   - goto 抛错（2026-09-16 10:45 那次）与「首屏 45 秒没出来」是同一类，都重试；
 *   - 被踢到登录页【一次都不重试】——那是登录态过期，重试确实过不去。
 *
 * 【这两件事必须分开报】旧版把 landed !== "app" 一律打成「鹊娱登录失效或页面没加载出来」并 exit 2，
 * 于是「页面慢」会被告警成「去重新登录」。今天排查就是被这类措辞带偏的：健康检查那条也写着
 * 「鹊娱网页登录失效」，而真实原因是网络超时。代码手里本来就有区分两者的信息，别丢掉。
 */
async function openPromotionPage(page: Page, retryBlank: boolean): Promise<Landing> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= GOTO_ATTEMPTS; attempt++) {
    try {
      await page.goto(PROMOTION_URL, { waitUntil: "domcontentloaded", timeout: GOTO_TIMEOUT_MS });
      const landed = await waitForLanding(page);
      if (landed === "app" || landed === "login" || !retryBlank) {
        if (attempt > 1) console.log(`打开 ${PROMOTION_URL} 第 ${attempt} 次成功（落地：${landed || "空"}）`);
        return landed;
      }
      lastError = new Error(`页面打开了但 ${APP_SETTLE_TIMEOUT_MS / 1000} 秒内既没出剧库也没跳登录页（${page.url()}）`);
      console.warn(`打开 ${PROMOTION_URL} 第 ${attempt}/${GOTO_ATTEMPTS} 次：${(lastError as Error).message}`);
    } catch (error) {
      lastError = error;
      const why = error instanceof Error ? error.message.split("\n")[0] : String(error);
      console.warn(`打开 ${PROMOTION_URL} 第 ${attempt}/${GOTO_ATTEMPTS} 次失败：${why}`);
    }
    if (attempt < GOTO_ATTEMPTS) await page.waitForTimeout(GOTO_RETRY_WAIT_MS);
  }
  throw lastError;
}

async function readRankings(page: Page): Promise<RankSnapshot & { sawLoad: boolean[] }> {
  // 排行榜是 el-tabs 的第二个 pane，点一下才挂载 Ranking 组件
  await page.locator(".el-tabs__item", { hasText: "排行榜" }).first().click();
  await page.waitForFunction(() => Boolean((window as unknown as { __qy: PageHelpers }).__qy.findComp((v) => v.$options?.name === "Ranking")), undefined, {
    timeout: 30_000,
  });
  return page.evaluate(
    async ({ loadingTimeout }) => {
      const h = (window as unknown as { __qy: PageHelpers }).__qy;
      type Ranking = Vue & { onTabPositionInput: (n: number) => void };
      const rk = h.findComp<Ranking>((v) => v.$options?.name === "Ranking");
      if (!rk) throw new Error("找不到 Ranking 组件");
      if (typeof rk.onTabPositionInput !== "function" || !Array.isArray(rk.$data.tableData)) {
        throw new Error("Ranking 组件形状对不上（缺 onTabPositionInput 或 tableData），上游多半改了前端");
      }
      const pick = (r: Record<string, unknown>): RankRow => ({
        id: Number(r.id),
        theater: String(r.theater_name ?? ""),
        theaterId: Number(r.theater_id),
        playletId: String(r.playlet_id ?? ""),
        title: String(r.title ?? ""),
        language: String(r.language ?? ""),
        productionType: String(r.production_type_name ?? ""),
        publishTime: String(r.publish_time ?? ""),
        labels: String(r.labels ?? ""),
        settleScope: Array.isArray(r.settle_scope) ? (r.settle_scope as number[]) : [],
        rank: Number(r.rank),
      });
      const ids = () => (rk.$data.tableData as Record<string, unknown>[]).map((r) => String(r.id)).join(",");
      /*
       * 【第 2 张榜要等内容真的换掉，光看 loading 落下不够】2026-09-18 14:39 实测：
       * 点开「排行榜」页签时 Ranking 挂载会自己发一次第 1 张的请求，readTab(1) 紧接着调
       * onTabPositionInput(1) 又发一次——两个都是第 1 张，谁后回来谁把 tableData 覆盖一遍、
       * 把 loading 翻回 false。切到第 2 张（B）之后，迟到的那个第 1 张响应先落地：loading 落下、
       * tableData 仍是第 1 张，untilLoaded 如约返回，读到的 rev 就是 conv 的逐字拷贝；B 随后才回来，
       * 已经没人读了。上升沿 true/true、两张各 25 行、日期也对，只有 rankProblems 的「逐行相同」
       * 拦得住。旧版 sleep(400) 碰巧盖住了这个洞，09-16 改成等边沿之后第一次真跑就撞上。
       * 所以拿着上一张的 id 序列等到它换掉为止（等不到就交给 rankProblems 判作废，仍是 fail-closed）；
       * 比对与拷贝在同一个同步 tick 里做，中间不许 await——否则迟到的响应能在检查之后把表覆盖回去。
       */
      const readTab = async (n: number, prevIds?: string) => {
        rk.onTabPositionInput(n);
        const saw = await h.untilLoaded(rk, loadingTimeout);
        if (prevIds !== undefined) {
          const t0 = Date.now();
          while ((ids() === prevIds || rk.$data.loading) && Date.now() - t0 < loadingTimeout) await h.sleep(200);
        }
        const rows = (rk.$data.tableData as Record<string, unknown>[]).map(pick);
        return { rows, ids: ids(), saw };
      };
      const conv = await readTab(1);
      const rev = await readTab(2, conv.ids);
      return { date: String(rk.$data.updateTime ?? ""), conv: conv.rows, rev: rev.rows, sawLoad: [conv.saw, rev.saw] };
    },
    { loadingTimeout: LOADING_TIMEOUT_MS },
  );
}

/** 剧库一行里留下的字段；本轮只存不用，剧单切源时拿它验 */
const LIBRARY_FIELDS = [
  "id", "theater_id", "theater_name", "playlet_id", "playlet_code", "title", "contract_title", "language_name",
  "production_type_name", "publish_time_string", "labels", "tagsList", "material_zip_url", "start_pay_chapter",
  "chapter_price", "is_support_anchor", "promotion_note", "recommend_type_name", "settle_scope", "status_name",
  "distribution_status_name", "last_sync_time", "cover", "introduce",
];

async function readLibrary(page: Page, onPage: (page: number, pages: number) => void): Promise<Record<string, unknown>[]> {
  await page.locator(".el-tabs__item", { hasText: "剧库" }).first().click();
  await page.waitForFunction(() => Boolean((window as unknown as { __qy: PageHelpers }).__qy.findComp((v) => Boolean((v.$data as { theaterList?: unknown }).theaterList))), undefined, {
    timeout: 30_000,
  });
  type Lib = Vue & {
    $data: { pageInfo: { page: number; page_size: number; total: number }; tableData: Record<string, unknown>[]; theaterList: unknown[] };
    startSearch: () => void;
    handlePageChange: (t: { page: number }) => void;
  };
  const readTotal = (reset: boolean) =>
    page.evaluate(
      async ({ size, loadingTimeout, doReset }) => {
        const h = (window as unknown as { __qy: PageHelpers }).__qy;
        const lib = h.findComp<Lib>((v) => Boolean((v.$data as { theaterList?: unknown }).theaterList));
        if (!lib) throw new Error("找不到剧库组件");
        if (typeof lib.startSearch !== "function" || typeof lib.handlePageChange !== "function" || !Array.isArray(lib.$data.tableData)) {
          throw new Error("剧库组件形状对不上（缺 startSearch / handlePageChange / tableData），上游多半改了前端");
        }
        if (doReset) {
          lib.$data.pageInfo.page_size = size;
          lib.startSearch(); // 它自己会把 page 归 1
          await h.untilLoaded(lib, loadingTimeout);
        }
        return Number(lib.$data.pageInfo.total);
      },
      { size: LIBRARY_PAGE_SIZE, loadingTimeout: LOADING_TIMEOUT_MS, doReset: reset },
    );
  const total = await readTotal(true);
  const badTotal = totalProblem(total);
  if (badTotal) throw new Error(badTotal);
  const pages = Math.ceil(total / LIBRARY_PAGE_SIZE);
  const rows: Record<string, unknown>[] = [];
  /* 翻页失败时组件把上一页的 tableData 原样留着，只比总数看不出来，按 id 去重才看得出 */
  const seenIds = new Set<string>();
  let noId = 0;
  let duplicates = 0;
  let missedEdge = 0;
  for (let p = 1; p <= pages; p++) {
    const chunk = await page.evaluate(
      async ({ pageNo, loadingTimeout, fields }) => {
        const h = (window as unknown as { __qy: PageHelpers }).__qy;
        const lib = h.findComp<Lib>((v) => Boolean((v.$data as { theaterList?: unknown }).theaterList));
        if (!lib) throw new Error("找不到剧库组件");
        let saw = true;
        if (lib.$data.pageInfo.page !== pageNo) {
          lib.handlePageChange({ page: pageNo }); // 与分页器发的事件同形：{page}
          saw = await h.untilLoaded(lib, loadingTimeout);
        }
        const out = lib.$data.tableData.map((r) => {
          const o: Record<string, unknown> = {};
          for (const k of fields) if (r[k] !== undefined) o[k] = k === "introduce" ? String(r[k] ?? "").slice(0, 400) : r[k];
          return o;
        });
        return { rows: out, saw, landed: Number(lib.$data.pageInfo.page) };
      },
      { pageNo: p, loadingTimeout: LOADING_TIMEOUT_MS, fields: LIBRARY_FIELDS },
    );
    if (!chunk.saw) missedEdge++;
    const absorbed = absorbPage(seenIds, chunk, p, pages);
    if (absorbed.problem) throw new Error(absorbed.problem);
    duplicates += absorbed.dupInPage;
    noId += absorbed.noIdInPage;
    rows.push(...absorbed.fresh);
    onPage(p, pages);
    await page.waitForTimeout(300);
  }
  /* 收尾再读一次 total：翻页期间上游下架剧会让 offset 分页漏行，按两次 total 里小的那个算缺口。
     旧版这里只 console.warn，于是「漏了一万行」和「下架了三部」长得一模一样。 */
  const totalAfter = await readTotal(false);
  const short = shortfallProblem(rows.length, total, totalAfter);
  if (short) throw new Error(short);
  if (noId) console.warn(`剧库有 ${noId} 行没有 id，这些行没法参与翻页去重校验`);
  if (duplicates || missedEdge || rows.length !== total) {
    console.log(`剧库核对：${rows.length} 行 / 开始 total ${total} / 结束 total ${totalAfter} / 去重丢弃 ${duplicates} 行 / ${missedEdge} 页没看到 loading 上升沿`);
  }
  return rows;
}

async function main(): Promise<number> {
  mkdirSync(OUT_DIR, { recursive: true });
  mkdirSync(PROFILE_DIR, { recursive: true });
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: !LOGIN,
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
  });
  await ctx.addInitScript(PAGE_HELPERS);
  // profile 存不住 session cookie，登录态要从 --login 存下的 storageState 注回来（文件头有原委）
  if (!LOGIN && existsSync(STATE_FILE)) {
    /* 文件损坏时降级成「没登录」，走下面那条 exit 2 去告警；
       这段原本在 try 之前，解析一抛错 ctx 就漏在外面不关了 */
    try {
      const saved = JSON.parse(readFileSync(STATE_FILE, "utf-8")) as { cookies?: Parameters<typeof ctx.addCookies>[0] };
      if (saved.cookies?.length) await ctx.addCookies(saved.cookies);
    } catch (error) {
      console.warn(`登录态文件读不动，按没登录处理（${STATE_FILE}）：${error instanceof Error ? error.message : String(error)}`);
    }
  }
  try {
    const page = ctx.pages()[0] ?? (await ctx.newPage());
    // --login 时不重试「首屏空白」：那一支交给人，下面会等他最多 10 分钟
    const landed = await openPromotionPage(page, !LOGIN);
    const appLoaded = () => Boolean((window as unknown as { __qy: PageHelpers }).__qy.findComp((v) => Boolean((v.$data as { theaterList?: unknown }).theaterList)));
    if (LOGIN) {
      if (landed !== "app") {
        console.log("在打开的窗口里登录鹊娱（脚本不会替你输任何东西）。登录完成、看到剧库列表后会自动继续……");
        await page.waitForFunction(appLoaded, undefined, { timeout: 10 * 60_000, polling: 1000 });
      }
      const state = await ctx.storageState();
      saveState(state);
      const authed = state.cookies.some((c) => c.name === "authorization");
      console.log(`登录态已存进 ${STATE_FILE}（cookie ${state.cookies.length} 条${authed ? "" : "，但没看到 authorization，多半没真登上"}），之后 pnpm queyu 无头跑即可`);
      return authed ? 0 : 2;
    }
    /* exit 2 只留给【真的被踢到登录页】。页面没出来的那一支已经在 openPromotionPage 里
       重试过 3 次，仍然不行就抛错走 exit 1——那不是登录问题，不该告警成「去重新登录」。 */
    if (landed === "login") {
      console.error(`鹊娱登录失效（落在 ${page.url()}；登录态 ${existsSync(STATE_FILE) ? STATE_FILE : "还没生成"}）。去运行器上跑：pnpm queyu -- --login`);
      return 2;
    }

    const read = await readRankings(page);
    const ranks: RankSnapshot = { date: read.date, conv: read.conv, rev: read.rev };
    const problems = rankProblems(ranks);
    if (problems.length) {
      console.error(
        `榜单作废（date=${ranks.date} conv=${ranks.conv.length} rev=${ranks.rev.length} 各应 ${EXPECTED_RANK_ROWS} 行，上升沿=${read.sawLoad.join("/")}）：`,
      );
      for (const why of problems) console.error(`  - ${why}`);
      return 3;
    }
    const rankFile = join(OUT_DIR, `rank-${ranks.date}.json`);
    const payload = JSON.stringify(ranks, null, 1);
    const rankExisted = existsSync(rankFile);
    /* 【同日不重写，但只在内容真的一致时】旧版只看文件在不在：第一次采到的要是坏的，
       当天重跑也救不回来，坏数据被冻一整天。现在按内容比，不一致就按新的覆盖。 */
    let rankSame = false;
    if (rankExisted) {
      try {
        rankSame = readFileSync(rankFile, "utf-8") === payload;
      } catch {
        rankSame = false; // 读不动就当它坏了，按新的覆盖
      }
    }
    if (rankSame) {
      console.log(`榜单 ${ranks.date} 已采过且内容一致（${rankFile}），不重写`);
    } else {
      writeAtomic(rankFile, payload);
      console.log(
        `榜单 ${ranks.date}：转化率 ${ranks.conv.length} 行 / 总收入 ${ranks.rev.length} 行 -> ${rankFile}${rankExisted ? "（同日旧版本内容有变，已覆盖）" : ""}`,
      );
    }

    let libraryRows: number | null = null;
    let libraryProblem: string | null = null;
    if (!NO_LIBRARY) {
      /*
       * 【剧库作废不拖停整条链，但也不许静默】——2026-09-16 对抗审计唯一活下来的那条。
       * 榜单此时已经原子落盘，而 build.py / posted.py / catalog-import 要的正是它；
       * library.json 目前【全仓库零消费者】（只有这里写它，注释说的「剧单切源时拿它验」还没发生）。
       * 让一个没人读的文件把当天的选剧台刷新整个打掉，代价和收益完全不成比例。
       *
       * 【但「catch 之后 return 0」是错的】：daily.sh 成功那条走 notify.ts --ok，
       * 而 --ok 分支只发 SUMMARY 那一行、一个字的日志都不带，于是 console.error 会落进
       * 一个按设计只在已经失败时才有人看的文件里。所以原因要写进 last-run.json，
       * 由 daily.sh 拼进 SUMMARY 发出去——失败仍然响亮，只是不再阻断。
       *
       * 失败时【不写 library.json】：留着昨天那份（它自带 fetchedAt），
       * 好过覆盖成一份证明不了完整的新文件。
       */
      try {
        const rows = await readLibrary(page, (p, pages) => {
          if (p === 1 || p % 10 === 0 || p === pages) console.log(`剧库 ${p}/${pages} 页`);
        });
        writeAtomic(join(OUT_DIR, "library.json"), JSON.stringify({ fetchedAt: new Date().toISOString(), rows }));
        console.log(`剧库 ${rows.length} 行 -> queyu/library.json`);
        libraryRows = rows.length;
      } catch (error) {
        libraryProblem = error instanceof Error ? error.message : String(error);
        console.error(`剧库本轮作废（榜单已落盘，下游照常跑）：${libraryProblem}`);
      }
    }

    /* 本轮实际用的是哪张榜。daily.sh 的摘要读这里，不再靠 ls 挑磁盘上最大的文件名——
       鹊娱榜若晚于 10:45 才更新，那种挑法会把昨天的日期写进「刷新成功」里 */
    writeAtomic(
      join(OUT_DIR, "last-run.json"),
      JSON.stringify(
        {
          at: new Date().toISOString(),
          rankDate: ranks.date,
          rankFresh: !rankExisted,
          rankRewritten: rankExisted && !rankSame,
          conv: ranks.conv.length,
          rev: ranks.rev.length,
          library: libraryRows,
          /* null = 正常（或 --no-library 没采）；有值 = 采了但作废，daily.sh 会把它拼进飞书那一行 */
          libraryProblem,
        },
        null,
        1,
      ),
    );

    /* 跑成功了就把当前登录态存回去：鹊娱若轮换或延长 token，本地这份才跟得上 */
    try {
      const refreshed = await ctx.storageState();
      if (refreshed.cookies.some((c) => c.name === "authorization")) saveState(refreshed);
    } catch {
      /* 刷新失败不影响这一轮的产出，下一轮还用旧的那份 */
    }
    return 0;
  } finally {
    await ctx.close();
  }
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  },
);
