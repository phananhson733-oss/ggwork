import type { SourceAttempt } from "@/lib/observe/source-types";
import {
  beginSource,
  completeSource,
  failSource,
  sourceGuardSql,
} from "@/lib/observe/source-state";
import "server-only";
import { checkSyncDeadline, withSyncDeadline } from "@/lib/sync-deadline";

import { and, eq, gte, isNull, lt, or, sql } from "drizzle-orm";

import { getBookDetail, iterateAllBooks } from "@/lib/cps/client";
import { localeFromCnName } from "@/lib/cps/lang";
import type { CpsBookDetail, CpsBookListItem } from "@/lib/cps/types";
import { resolveReviewedDramaGroupKeyFromDetail } from "@/lib/drama-group-overrides";
import {
  orderIndexNowSyncIds,
  type IndexNowSyncOutcome,
} from "@/lib/indexnow-plan";
import { legacyTitleKey } from "@/lib/legacy-url";
import { ingestBill } from "@/lib/observe/bill";
import {
  hasSnapshotToday,
  pruneObservations,
  snapshotDramas,
} from "@/lib/observe/snapshot";
import { buildSlug } from "@/lib/slug";
import { getDb } from "@/db";
import {
  dramas,
  outboundClicks,
  syncRuns,
  type NewChapter,
  type NewDrama,
} from "@/db/schema";

/** 详情接口每剧一次调用，并发太高会被上游限流 */
const DETAIL_CONCURRENCY = 4;
/** 详情多久重拉一次（剧集会追更，pay_start 也会调整） */
const DETAIL_TTL_HOURS = 24;
/**
 * 观测日更（快照 + 账单）最多等多久。超过就取消本次观测 I/O，再继续同步。
 *
 * 它挂在同步【开头】，所以这个预算防的不是「本轮跑太久」，而是「上游账单接口挂住」
 * 拖累后面的列表阶段。非首轮只有一次主键查询，首轮实测约 2.5 秒，30 秒是十倍余量。
 */
const OBSERVE_BUDGET_MS = 30_000;

export interface SyncOptions {
  /** HTTP calls stop before the platform limit; CLI may omit this. */
  timeBudgetMs?: number;
  /** 只同步指定语言（中文名，如 "英语"）。不传则全量。 */
  language?: string;
  /** 本轮最多拉多少个详情，防止单次函数执行超时 */
  detailBudget?: number;
  /** 跳过详情阶段，只刷新列表元数据 */
  skipDetails?: boolean;
  /**
   * 跳过列表阶段，只补详情。
   * 全库轮一遍详情需要几十轮，每轮都重跑一次约 500 秒的全量列表纯属浪费。
   * 铺库阶段用它，日常 Cron 不要开——列表阶段负责发现新剧。
   */
  skipList?: boolean;
}

export interface SyncResult {
  dramasUpserted: number;
  chaptersUpserted: number;
  skippedUnknownLang: number;
  detailsFetched: number;
  durationMs: number;
  /**
   * 本轮首次拿到详情（= 首次进 published() 和 sitemap）的剧数。
   *
   * 【这是详情阶段的事件计数，不是提交结果】：IndexNow 的提交由
   * indexnow_submissions 里的内容版本驱动，与这两个数无关。只有列表阶段改了
   * slug 的那一轮，两个计数都是 0 而 drain 照样提交。要看推了什么看
   * indexNowSubmitted / indexNowStatus，或者直接查那张表。
   */
  newlyPublished: number;
  /** 本轮内容发生变化的已上线剧数（追更、改付费边界、改简介…）。同上，是事件计数 */
  contentChanged: number;
  /** 实际发给 IndexNow 的 URL 条数。没配 INDEXNOW_KEY 或提交失败时是 0 */
  indexNowSubmitted: number;
  /** 最后一批的 HTTP 状态码：200 已提交、202 key 校验待完成、0 没发出去 */
  indexNowStatus: number;
  /** 本轮写进 drama_observations 的快照行数。当天第二轮起是 0（已经写过了） */
  observed: number;
  /** 本轮 upsert 进 cps_bill_daily 的账单行数。当天第二轮起是 0 */
  billRows: number;
}

function toDate(unixSeconds: number): Date | null {
  if (!unixSeconds || unixSeconds <= 0) return null;
  return new Date(unixSeconds * 1000);
}

function dedupeTags(item: { tag: string[]; show_tag: string[] }): string[] {
  return Array.from(
    new Set(
      [...item.show_tag, ...item.tag].map((t) => t.trim()).filter(Boolean),
    ),
  );
}

/** 列表项 → 待写入行。列表接口拿不到章节，chapterCount / payStart 以详情为准。 */
function listItemToRow(item: CpsBookListItem): NewDrama | null {
  const locale = localeFromCnName(item.lang);
  if (!locale) return null;

  return {
    id: item.id,
    slug: buildSlug(item.title, item.id),
    // 承接旧域名 URL 用的匹配 key，见 lib/legacy-url.ts。
    // 它和 slug 是两套算法，【不能】互相代用
    titleKey: legacyTitleKey(item.title),
    locale: locale.code,
    langCn: item.lang,
    title: item.title,
    description: item.desc,
    coverUrl: item.pic,
    bookType: item.book_type,
    isDub: item.is_dub === 1,
    isValid: item.is_valid,
    payStart: item.pay_start,
    chapterCount: item.chapter_count,
    tags: dedupeTags(item),
    publishAt: toDate(item.publish_at),
    promotersCnt: item.promoters_cnt,
    metricsValid: item.metrics_valid ?? false,
    recentRevenueCents: String(item.recent_revenue ?? 0),
    // 同一部剧先自成一组，等详情里的 relation_books 回来再归并
    groupKey: item.id,
    syncedAt: new Date(),
    updatedAt: new Date(),
  };
}

/**
 * 列表阶段。只写元数据，不碰章节。
 * 注意 chapterCount / payStart 用 GREATEST 合并——列表接口这两个字段常年返回 0，
 * 直接覆盖会把详情阶段拿到的真值抹掉。
 */
async function syncList(
  options: SyncOptions,
  attempt: SourceAttempt,
  progress: { dramasUpserted: number },
): Promise<{ upserted: number; skipped: number }> {
  const db = getDb();
  let upserted = 0;
  let skipped = 0;

  for await (const batch of iterateAllBooks({ language: options.language })) {
    const rows = batch
      .map(listItemToRow)
      .filter((r): r is NewDrama => r !== null);
    skipped += batch.length - rows.length;
    if (rows.length === 0) continue;

    const write = db
      .insert(dramas)
      .values(rows)
      .onConflictDoUpdate({
        target: dramas.id,
        set: {
          slug: sql`excluded.slug`,
          titleKey: sql`excluded.title_key`,
          locale: sql`excluded.locale`,
          langCn: sql`excluded.lang_cn`,
          title: sql`excluded.title`,
          description: sql`excluded.description`,
          coverUrl: sql`excluded.cover_url`,
          bookType: sql`excluded.book_type`,
          isDub: sql`excluded.is_dub`,
          isValid: sql`excluded.is_valid`,
          payStart: sql`GREATEST(${dramas.payStart}, excluded.pay_start)`,
          chapterCount: sql`GREATEST(${dramas.chapterCount}, excluded.chapter_count)`,
          tags: sql`excluded.tags`,
          publishAt: sql`COALESCE(excluded.publish_at, ${dramas.publishAt})`,
          promotersCnt: sql`CASE WHEN excluded.metrics_valid THEN excluded.promoters_cnt ELSE dramas.promoters_cnt END`,
          metricsValid: sql`excluded.metrics_valid`,
          recentRevenueCents: sql`CASE WHEN excluded.metrics_valid THEN excluded.recent_revenue_cents ELSE dramas.recent_revenue_cents END`,
          syncedAt: sql`excluded.synced_at`,
          /**
           * 【必须条件化，不能无条件写 now()】。
           *
           * `updated_at` 是 sitemap 的 `<lastmod>` 来源，而列表阶段每 6 小时
           * 对**全库**跑一次这个 upsert。原来无条件 `excluded.updated_at`（= now()）
           * 的后果实测如下：
           *
           * - 全部 30,824 行的 `updated_at` 落在同一个 86 分钟窗口内，
           *   只有 45 个不同的分钟值；`/sitemap/0.xml` 里 20,001 条 lastmod 同一天。
           * - 未变化页面反复报新时间会降低 lastmod 的可信度；
           *   同一天的真实更新本身不代表 Google 必然忽略所有 lastmod。
           *
           * 这里逐列比较真正会影响页面输出的字段（标题/描述/封面/slug/locale/
           * 标签/付费边界/集数）。`synced_at` 仍然每轮更新——那是运维用的"何时跑过"，
           * 与"内容何时变过"是两件事，不要合并。
           *
           * 【locale 是 2026-09-20 补上的，不要拿掉】：上游把一部剧从 en 改成 es 时
           * URL 从 `/en/drama/x` 整体变成 `/es/drama/x`，而这个 CASE 原本不比较它——
           * 那条 URL 换了位置，sitemap 的 lastmod 却说没变过，IndexNow 也拿不到通知。
           *
           * `promoters_cnt` 与 `recent_revenue_cents` 刻意【不】计入：
           * 它们每轮都在动，但页面上不展示（CLAUDE.md 的「不显示播放量」），
           * 算进去等于又回到"全库天天变"。
           */
          updatedAt: sql`CASE WHEN
            ${dramas.title} IS DISTINCT FROM excluded.title
            OR ${dramas.description} IS DISTINCT FROM excluded.description
            OR ${dramas.coverUrl} IS DISTINCT FROM excluded.cover_url
            OR ${dramas.slug} IS DISTINCT FROM excluded.slug
            OR ${dramas.locale} IS DISTINCT FROM excluded.locale
            OR ${dramas.tags} IS DISTINCT FROM excluded.tags
            OR ${dramas.payStart} IS DISTINCT FROM GREATEST(${dramas.payStart}, excluded.pay_start)
            OR ${dramas.chapterCount} IS DISTINCT FROM GREATEST(${dramas.chapterCount}, excluded.chapter_count)
          THEN excluded.updated_at ELSE ${dramas.updatedAt} END`,
        },
      });
    await db.batch([db.execute(sourceGuardSql("catalog", attempt)), write]);
    upserted += rows.length;
    progress.dramasUpserted = upserted;
  }

  return { upserted, skipped };
}

function detailToChapterRows(detail: CpsBookDetail): NewChapter[] {
  return detail.chapters.map((chapter, index) => {
    const parsed = Number.parseInt(chapter.t_chapter_id, 10);
    const serial = Number.isFinite(parsed) && parsed > 0 ? parsed : index + 1;
    return {
      id: chapter.chapter_id,
      dramaId: detail.id,
      serial,
      videoPic: chapter.video_pic,
      // play_url 刻意不写库：带签名且有时效，落库等于缓存一个必然过期的地址
      iframeSrc: chapter.iframe_src,
      updatedAt: new Date(),
    };
  });
}

/**
 * 写入顺序很重要：【先写章节，再标记 detailSyncedAt】。
 *
 * neon-http 驱动不支持 db.transaction()（调用会直接抛错），所以这两次写库
 * 无法做成原子操作。那就必须让失败方向是安全的：
 * 若先标记 detailSyncedAt 再写章节，中途失败会让这部剧被 published() 判为可展示，
 * 却一条章节都没有——列表页、sitemap、详情页全都出现它，点进去全是 404，
 * 而且 24 小时内 selectStaleDramas 不会再捡起它重试。
 * 反过来先写章节，最坏情况只是"章节已入库但没标记完成"，下一轮会重来，无害。
 */
async function syncOneDetail(
  bookId: string,
): Promise<{ rows: number; changed: boolean }> {
  const db = getDb();
  const detail = await getBookDetail(bookId);
  const now = new Date();
  const publishAt = toDate(detail.publish_at);
  const groupKey = resolveReviewedDramaGroupKeyFromDetail(detail);

  const rows = detailToChapterRows(detail);
  // Selection needs detail metadata; playback chapters remain in the original backup.


  const [updated] = await db
    .update(dramas)
    .set({
      payStart: detail.pay_start,
      chapterCount:
        detail.chapter_count > 0
          ? detail.chapter_count
          : detail.chapters.length,
      bookPromotionLink: detail.book_promotion_link,
      appPromotionLink: detail.app_promotion_link,
      promotionCode: String(detail.promotion_code ?? ""),
      /*
       * 【上线日期只有详情接口给】。列表接口的 publish_at 恒为 0
       * （同 chapter_count / pay_start 那条坑），所以列表阶段那句
       * COALESCE 永远在拿 NULL 兜 NULL，库里 31,950 条全是空的——
       * 而上游一直在给，是我方这一处 set 漏了这列。
       *
       * 「列表接口给不了」是实测的：连打三页 all-book 共 120 条，
       * publish_at 无一非零（2026-09-09）。不要因为它便宜就再去试一次。
       *
       * 写法必须是"上游给了才写"：详情偶发返回 0 时
       * `toDate` 给 null，无条件写会把已经拿到的日期抹成空。
       *
       * 【刻意不进下面那个 updatedAt 的 CASE】。只补观测字段时，
       * 不应把全部记录当成公共页面实质更新；耗时取决于当轮候选与响应速度。
       *
       * 这条已实测：一批 296 部的例行重刷里，`updated_at` 被刷的是 0 部。
       * 【往那个 CASE 里加列之前先重跑这条断言】——加错一列，
       * 下一次全量回填就是 3 万条 lastmod 同一天。
       */
      publishAt: publishAt ?? sql`${dramas.publishAt}`,
      groupKey,
      description: detail.desc || sql`${dramas.description}`,
      detailSyncedAt: now,
      /**
       * 详情阶段同样条件化。24 小时是过期阈值，实际轮转仍受每轮预算限制；
       * 无条件写 now() 会让 lastmod 变成"最近一次拉详情的时间"而不是
       * "内容最近一次变化的时间"，效果和列表阶段那条一样糟。
       *
       * 这里比较的是详情阶段真正会影响页面或导航的字段：付费边界、总集数、
       * 简介，以及决定跨语种 canonical/hreflang 的 groupKey。
       * 推广链接和推广码仍保存，但只换归因账号不改变这些页面的实质内容，
       * 不应刷新 sitemap lastmod 或触发 IndexNow；出站路由实时读库取目标。
       * `detail_synced_at` 照常每轮更新——它是调度用的时间戳，不是内容时间戳。
       */
      updatedAt: sql`CASE WHEN
        ${dramas.payStart} IS DISTINCT FROM ${detail.pay_start}
        OR ${dramas.chapterCount} IS DISTINCT FROM ${
          detail.chapter_count > 0
            ? detail.chapter_count
            : detail.chapters.length
        }
        OR (${detail.desc || ""} <> '' AND ${dramas.description} IS DISTINCT FROM ${detail.desc || ""})
        OR ${dramas.groupKey} IS DISTINCT FROM ${groupKey}
      THEN ${now} ELSE ${dramas.updatedAt} END`,
    })
    .where(eq(dramas.id, bookId))
    /*
     * 【把 updatedAt 取回来，用它判断「这一轮内容真的变了没有」】——
     * 上面那个 CASE 已经精确表达了这件事（付费边界 / 总集数 / 简介 /
     * groupKey 任一变化才写 now），所以返回值等于 now
     * 就等价于「命中了 THEN 分支」。
     *
     * 【为什么不另发一次查询去比对旧值】detailBudget 每轮 150 部，
     * 多一次查询就是每轮多 150 次往返——而 Neon 的账单大头正是 compute。
     * RETURNING 是零额外往返的。
     *
     * 这一条服务的是需求文档里「已上线剧目新增集数 → 必须触发 IndexNow」：
     * 一部剧追更之后 payStart / chapterCount 变化，会多出若干条免费集播放页，
     * 那些是**新 URL**，不通知的话只能等 Bing 自然重爬。
     */
    .returning({ updatedAt: dramas.updatedAt });

  const changed = updated?.updatedAt?.getTime() === now.getTime();
  return { rows: rows.length, changed };
}

/**
 * 选出需要拉详情的剧：从没拉过的优先，其次是过期的。
 *
 * language 必须在这里也生效。它原本只传给了列表阶段，配上 skipList 之后就完全失效——
 * 表面在铺某个语种，实际按全库顺序乱铺。铺库阶段这个差别是几小时。
 */
async function selectStaleDramas(
  budget: number,
  language?: string,
  listSince?: Date,
): Promise<{ id: string; isNew: boolean }[]> {
  const db = getDb();
  const staleBefore = new Date(Date.now() - DETAIL_TTL_HOURS * 3600 * 1000);

  const filters = [
    or(isNull(dramas.detailSyncedAt), lt(dramas.detailSyncedAt, staleBefore)),
  ];
  // A completed list is the refresh candidate set. Keep absent historical rows
  // and URLs, but do not spend the daily detail budget on them. If they return
  // in a later list, syncedAt makes them eligible again. Explicit skip-list
  // backfills keep their existing selection because no list was collected.
  if (listSince) filters.push(gte(dramas.syncedAt, listSince));
  // 上游按中文语言名过滤，库里 langCn 存的就是原样值，直接比即可
  if (language) filters.push(eq(dramas.langCn, language));

  const rows = await db
    /*
     * 【isNew 必须在这里取，不能事后补】：这一列的值在 syncOneDetail 写完
     * detailSyncedAt 之后就永远变成 false 了。它区分的是两件语义完全不同的事——
     * `detailSyncedAt IS NULL` = 这部剧**从没拉过详情**，本轮之后它才首次
     * 通过 published()、首次出现在页面和 sitemap 里，那才是「新内容」；
     * 而 24 小时过期重刷的那批只是例行刷新，把它们提交给 IndexNow
     * 是在浪费配额，还会让「只提交真正变更的 URL」那条协议要求变成空话。
     */
    .select({ id: dramas.id, detailSyncedAt: dramas.detailSyncedAt })
    .from(dramas)
    .where(and(...filters))
    .orderBy(sql`${dramas.detailSyncedAt} ASC NULLS FIRST`)
    .limit(budget);

  return rows.map((r) => ({ id: r.id, isNew: r.detailSyncedAt === null }));
}

/**
 * 一轮最多取多少部待提交的剧。
 *
 * 每部最多展开 1 + freeEpisodeCount 条 URL（实测全库免费集最多 20 集），
 * 2000 部的上界是 42,000 条，远大于 MAX_URLS_PER_RUN——真正的闸是 planDrain
 * 的预算，这个数只是别把几万行一次拉回内存。稳态下待提交每轮只有几十部。
 */
const PENDING_LIMIT = 2000;

/**
 * 把待提交的剧提交给 IndexNow，成功之后记下它们的内容版本。
 *
 * 【提交由持久状态驱动，不由本轮的内存事件驱动】。旧实现只提交「本轮首次拿到详情」
 * 与「本轮详情内容变了」的剧：提交失败、或进程里没有 INDEXNOW_KEY（本地 pnpm sync），
 * 详情照样写入 detail_synced_at，这部剧此后**永远**不会再被判成新剧。
 * 列表阶段改了 slug / locale 更是从头到尾没有触发点。
 * 设计见 docs/plans/2026-09-20-indexnow-durable-submission.md。
 *
 * 【播放页每部剧最多一条】（2026-09-22）只有 `/watch/{slug}/1` 这一个播放页，
 * 且只在第 1 集免费时提交：第 1 集就是付费集时那一页是 noindex，
 * 提交它等于主动要求搜索引擎收录自己标了 noindex 的页面。
 * 判据是 freeEpisodeCount() >= 1，与 sitemap 的 watch 家族同源。
 *
 * 【URL 一律经 dramaPath / watchPagePath 构造】slug 含西里尔/泰/日/阿拉伯文，
 * 手拼模板串会漏掉百分号编码——那是 CLAUDE.md 里的一条铁律。
 *
 * 【永不抛错】它挂在同步末尾，抛出去会把一轮已经写完库的同步标记成 failed。
 * 表还没建（SQL 还没跑）也只记一条日志。
 */
async function runPool<T>(
  items: readonly T[],
  size: number,
  task: (item: T) => Promise<number>,
) {
  const queue = [...items];
  let total = 0;
  const workers = Array.from(
    { length: Math.min(size, queue.length) },
    async () => {
      while (queue.length > 0) {
        checkSyncDeadline();
        const item = queue.shift();
        if (item === undefined) break;
        try {
          const completed = await task(item);
          total += completed;
        } catch (error) {
          checkSyncDeadline();
          // A canceled request leaves this batch incomplete, not successful.
          if (
            error instanceof Error &&
            ["TimeoutError", "AbortError"].includes(error.name)
          )
            throw error;
          console.error("[sync] 详情同步失败", item, error);
        }
      }
    },
  );
  const outcomes = await Promise.allSettled(workers);
  const failed = outcomes.find((outcome) => outcome.status === "rejected");
  if (failed?.status === "rejected") throw failed.reason;
  return total;
}

/**
 * 出站点击的保留期上限，单位月。
 *
 * 隐私政策（src/content/pages/en/privacy.md）对外承诺"最多保留 24 个月后自动删除"。
 * 那句话【必须由这段代码兑现】——写了不做就是虚假声明，GDPR 下比不写更糟。
 * 改这个数字必须同步改那篇文案。
 */
const CLICK_RETENTION_MONTHS = 24;

/**
 * 清理过期的出站点击记录。挂在每轮同步末尾，不单独排一条 cron——
 * 多一条定时任务就多一个会静默失效的地方，而同步本来就是 6 小时一轮。
 */
async function pruneOutboundClicks(): Promise<void> {
  await getDb().execute(
    sql`DELETE FROM ${outboundClicks}
        WHERE ${outboundClicks.createdAt} < now() - interval '${sql.raw(String(CLICK_RETENTION_MONTHS))} months'`,
  );
}

/**
 * 观测台的日更（片库快照 + 分成账单），【挂在同步开头，不在末尾】。
 *
 * 2026-09-10 实测：它原本排在整轮同步之后、由一道 260 秒的门槛把关，
 * 而线上一轮同步 274–293 秒（09-09 至 09-10 五轮 Cron 全部如此），四轮全被门槛跳过，
 * drama_observations 只有两天手工写的快照，涨幅榜恒空。「快照一天有四次机会」
 * 在 260 秒之外一次都没有——那不是数据没到，是链路没跑。
 * 挪到开头之后它不再依赖本轮剩多少余量：非首轮只花一次主键查询
 * （今天已有快照就跳过整段 INSERT ... SELECT），首轮约 2.5 秒。
 *
 * 三条约束：
 * - 永不抛出。它在 catalog 阶段之前，抛出去会把一轮还没开始的同步记成 failed。
 * - 有时间预算。上游账单接口挂住不能拖累列表阶段，超过 OBSERVE_BUDGET_MS 就
 *   取消观测请求并等待退出，再继续同步。
 * - 快照口径不变：仍是「每个 UTC 日的第一轮同步」，只是取该轮开始时的值
 *   （= 前一轮同步写入的值），不是该轮结束时的值。
 */
async function observeDaily(): Promise<{ observed: number; billRows: number }> {
  const out = { observed: 0, billRows: 0 };
  try {
    await withSyncDeadline(OBSERVE_BUDGET_MS, async () => {
      try {
        if (!(await hasSnapshotToday())) {
          out.observed = await snapshotDramas();
          await pruneObservations();
        }
      } catch (error) {
        console.error("[sync] 片库快照失败（不影响本轮同步）", error);
      }
      try {
        // ingestBill 内部已经吞掉所有失败并记了日志，这里再兜一层
        out.billRows = (await ingestBill()).upserted ?? 0;
      } catch (error) {
        console.error("[sync] 分成账单失败（不影响本轮同步）", error);
      }
    });
  } catch {
    console.warn("[sync] 观测数据超过预算或未完成，已停止本次采集，下一轮重试");
  }
  return out;
}

export async function runSync(options: SyncOptions = {}): Promise<SyncResult> {
  const startedAt = Date.now();
  const [run] = await getDb(AbortSignal.timeout(10_000))
    .insert(syncRuns)
    .values({})
    .returning({ id: syncRuns.id });

  let catalogAttempt: SourceAttempt | undefined;
  let catalogComplete = false;
  const detailOutcomes = new Map<string, IndexNowSyncOutcome>();
  const progress = {
    stage: "observe",
    dramasUpserted: 0,
    detailsCompleted: 0,
    chaptersUpserted: 0,
  };
  const stage = (name: string) => {
    checkSyncDeadline();
    progress.stage = name;
    console.log("[sync] progress", {
      runId: run.id,
      ...progress,
      durationMs: Date.now() - startedAt,
    });
  };
  try {
    const result = await withSyncDeadline(options.timeBudgetMs, async () => {
      stage("observe");
      const observe = {observed:0,billRows:0}; // Restored metrics are never stamped as today before a fresh list.
      stage("list");
      if (!options.skipList) catalogAttempt = await beginSource("catalog");
      const list = options.skipList
        ? { upserted: 0, skipped: 0 }
        : await syncList(options, catalogAttempt!, progress);

      if (catalogAttempt)
        await completeSource("catalog", catalogAttempt, {
          scope: options.language ?? "全部语种",
          rows: list.upserted,
          partial: Boolean(options.language),
        });

      catalogComplete = true;
      if (!options.skipList) Object.assign(observe, await observeDaily());
      stage("details");
      let chaptersUpserted = 0;
      let detailsFetched = 0;
      /* 本轮成功同步、且首次拿到详情（= 首次进 published() 和 sitemap）的剧。 */
      let newlyPublished: string[] = [];
      /*
       * 【已上线的剧这一轮内容变了】——需求文档把「已上线剧目新增集数」列为
       * **必须**触发 IndexNow：一部剧追更之后 payStart / chapterCount 变化，
       * 会多出若干条免费集播放页，那是实打实的新 URL。
       *
       * 判据来自 syncOneDetail 的 RETURNING（见那里的注释），
       * 与 sitemap 的 lastmod 用的是**同一个** CASE 表达式——
       * 两处给出不同答案的话，就会出现「sitemap 说这部剧变了、IndexNow 说没变」。
       */
      let contentChanged: string[] = [];
      if (!options.skipDetails) {
        const stale = await selectStaleDramas(
          options.detailBudget ?? 120,
          options.language,
          catalogAttempt?.at,
        );
        detailsFetched = stale.length;
        const isNew = new Set(stale.filter((r) => r.isNew).map((r) => r.id));
        /*
         * 只在 syncOneDetail 成功后写 outcome；runPool 吞掉的失败项自然缺席。
         * Map 的插入顺序是并发完成顺序，最终数组必须等 pool 完成后按 stale 顺序派生。
         */
        chaptersUpserted = await runPool(
          stale.map((r) => r.id),
          DETAIL_CONCURRENCY,
          async (id) => {
            const { rows, changed } = await syncOneDetail(id);
            progress.detailsCompleted++;
            progress.chaptersUpserted += rows;
            detailOutcomes.set(
              id,
              isNew.has(id) ? "new" : changed ? "changed" : "unchanged",
            );
            return rows;
          },
        );
        ({ newlyPublished, contentChanged } = orderIndexNowSyncIds(
          stale,
          detailOutcomes,
        ));
      }

      stage("cleanup");
      await pruneOutboundClicks();

      /*
       * 【IndexNow 挂在这里，不挂在 build 期】。参照站 astrologywiki 那版是
       * 「构建期扫 sitemap 里 lastmod=当天的 URL」，照搬到本站会静默失效——
       * 本站 sitemap 是动态路由，构建期根本不存在。理由详见 lib/indexnow.ts 顶部。
       *
       * 【submitUrls 永不抛错】，所以这里不用 try/catch：告警/通知通道自己挂掉
       * 不能反过来把整轮同步标记成 failed（同 lib/alert.ts 的约定）。
       */
      /*
       * 【新剧排在追更之前】：planSubmission 超限时从尾部截断，
       * 而一部全新的剧整套页面对搜索引擎都是未知的，优先级高于
       * 一部已收录的剧多了几集。
       */
      stage("indexnow");
      const indexNow = {submitted: 0, status: 0}; // GGWork owns data, not public-site indexing.

      const result: SyncResult = {
        dramasUpserted: list.upserted,
        chaptersUpserted,
        skippedUnknownLang: list.skipped,
        detailsFetched,
        durationMs: Date.now() - startedAt,
        newlyPublished: newlyPublished.length,
        contentChanged: contentChanged.length,
        indexNowSubmitted: indexNow.submitted,
        indexNowStatus: indexNow.status,
        observed: observe.observed,
        billRows: observe.billRows,
      };

      stage("complete");
      return result;
    });
    await getDb(AbortSignal.timeout(10_000))
      .update(syncRuns)
      .set({
        finishedAt: new Date(),
        status: "success",
        dramasUpserted: result.dramasUpserted,
        chaptersUpserted: result.chaptersUpserted,
        skippedUnknownLang: result.skippedUnknownLang,
      })
      .where(eq(syncRuns.id, run.id));

    return result;
  } catch (error) {
    // Preserve a completed list source when a later detail phase times out.
    if (catalogAttempt && !catalogComplete) {
      await failSource("catalog", catalogAttempt).catch(() => undefined);
    }
    const detail = `stage=${progress.stage}; durationMs=${Date.now() - startedAt}; dramas=${progress.dramasUpserted}; details=${progress.detailsCompleted}; chapters=${progress.chaptersUpserted}`;
    const failure = new Error(
      `${error instanceof Error ? error.message : String(error)}; ${detail}`,
    );
    await getDb(AbortSignal.timeout(10_000))
      .update(syncRuns)
      .set({
        finishedAt: new Date(),
        status: "failed",
        error: failure.message,
        dramasUpserted: progress.dramasUpserted,
        chaptersUpserted: progress.chaptersUpserted,
      })
      .where(eq(syncRuns.id, run.id));
    throw failure;
  }
}
