import { sql } from "drizzle-orm";
import {
  boolean,
  index,
  integer,
  numeric,
  pgTable,
  serial,
  smallint,
  jsonb,
  text,
  timestamp,
  uniqueIndex,
} from "drizzle-orm/pg-core";

/** Temporary HMAC-keyed anti-abuse counters, with no raw IP or episode history. */
export const playbackRateLimits = pgTable(
  "playback_rate_limits",
  {
    key: text("key").primaryKey(),
    hits: integer("hits").notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  },
  (t) => [index("playback_rate_limits_expiry_idx").on(t.expiresAt)],
);

/**
 * 只落"稳定的元数据"。带签名的 play_url 一律不入库——它有时效，
 * 落库会导致页面缓存住一个几分钟后就 403 的地址。播放地址在请求时现取。
 */
export const dramas = pgTable(
  "dramas",
  {
    /** CPS 的 book_id */
    id: text("id").primaryKey(),
    /** URL 用，形如 the-alpha-kings-bride-691439b62925e01c310e6d01 */
    slug: text("slug").notNull(),
    /**
     * 标题归一化后的匹配 key，【只用于承接旧域名 URL】，不出现在任何链接里。
     *
     * 域名前任 dramashortstv.com 的剧目 URL 是 /{loc}/detail/{它的自增id}/{标题slug}，
     * 那个 id 是它自己的主键、与 CPS book_id 无关，唯一能对上的只有标题。
     * 算法是【刻意复刻它的缺陷版】，见 lib/legacy-url.ts —— 不要用 slug 列去匹配，
     * 两者对泰/印地/阿拉伯语的处理不同，会大面积漏配。
     */
    titleKey: text("title_key").notNull().default(""),
    locale: text("locale").notNull(),
    /** 上游原始中文语言名，同步时按它回查 */
    langCn: text("lang_cn").notNull().default(""),

    title: text("title").notNull().default(""),
    description: text("description").notNull().default(""),
    coverUrl: text("cover_url").notNull().default(""),

    bookType: integer("book_type").notNull().default(0),
    isDub: boolean("is_dub").notNull().default(false),
    isValid: boolean("is_valid").notNull().default(false),

    /** 第几集开始付费。0 表示全免费 */
    payStart: integer("pay_start").notNull().default(0),
    chapterCount: integer("chapter_count").notNull().default(0),

    tags: text("tags").array().notNull().default([]),
    publishAt: timestamp("publish_at", { withTimezone: true }),

    promotersCnt: integer("promoters_cnt").notNull().default(0),
    metricsValid: boolean("metrics_valid"),
    /** 上游单位是美分 */
    recentRevenueCents: numeric("recent_revenue_cents", {
      precision: 14,
      scale: 2,
    })
      .notNull()
      .default("0"),

    bookPromotionLink: text("book_promotion_link").notNull().default(""),
    appPromotionLink: text("app_promotion_link").notNull().default(""),
    promotionCode: text("promotion_code").notNull().default(""),

    /**
     * 同一部剧的多语言版本共享一个 groupKey，由 relation_books 归并得到。
     * hreflang 与"切换语言"直接按它查。
     */
    groupKey: text("group_key").notNull(),

    /**
     * Google Search Console「效果 - 网页」维度按剧聚合后的**展示数**。
     *
     * 【是展示不是点击】。这条货架回答的是「多少人搜到了它」，
     * 而点击 = 展示 × CTR，混进了排名位置与标题吸引力。详见 page-cache.ts。
     *
     * 【不是 CPS 数据，也不由同步写入】。它由 `pnpm import-gsc` 从人工导出的
     * CSV 导入，`sync.ts` 的 upsert 是显式列举字段的（schema 里新增的列
     * 不在那张 set 表里），所以两条写入路径互不覆盖。
     *
     * 【为什么加列而不是加第 6 张表】：这是「每部剧一个数」的一对一关系，
     * 加表要多一次 join 才能填进 CARD_COLUMNS，而首页那条查询的成本
     * 是要按 21 语种 × 每天 4 次算的（见 page-cache.ts 的成本注释）。
     * 加表只有在需要保留逐 URL 的采集明细时才值。
     *
     * 【值归到「正典」那一行】。同一部剧在同一语种下常有多条记录，
     * 导入脚本按 queries.ts 的 CANONICAL_ORDER 挑正典再写——写到非正典行上，
     * 首页卡片就会指向一个会 308 的 slug，给全站最高价值的内链平白多一跳。
     */
    searchImpressions: integer("search_impressions").notNull().default(0),
    /**
     * 上面那个数是什么时候导入的。
     *
     * 【必须有】：人工维护的数据会静默过期，而一个三个月前的搜索榜
     * 长得和今天的一模一样，页面上看不出任何异常。有了这个字段，
     * 陈旧程度才是可查的（`/api/health` 的 catalog 项将来可以顺带看它）。
     */
    searchDataAt: timestamp("search_data_at", { withTimezone: true }),

    /** 详情接口是否已拉取过。列表接口拿不到章节和真实 payStart。 */
    detailSyncedAt: timestamp("detail_synced_at", { withTimezone: true }),
    syncedAt: timestamp("synced_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("dramas_locale_slug_idx").on(t.locale, t.slug),
    // 【非唯一】：旧站同名不同 id 的剧有 3,698 条，标题 key 天然会重复
    index("dramas_locale_title_key_idx").on(t.locale, t.titleKey),
    index("dramas_locale_idx").on(t.locale),
    /*
     * sitemap 两个家族都按 (locale, id) 排序再 OFFSET 到 30,000+，且只取这 6 列。
     * 【必须是覆盖索引，不能"简化"成 (locale, id)】——2026-09-02 两版都在线上实测过：
     * 只有 (locale, id) 时 planner 对 offset ≥ 约 10,000 仍选全表 Seq Scan + 外排落盘
     * （offset 30,000：8,789 buffer、3.6 MB 落盘、63 ms），因为 32,000 次随机堆访问
     * 比顺序读全表贵；把查询列全放进索引后是 Index Only Scan、零堆访问、无 Sort：
     * offset 30,000 → 533 buffer、6.4 ms。代价 4.3 MB。partial 条件与 published() 一致。
     */
    index("dramas_sitemap_idx")
      .on(t.locale, t.id, t.slug, t.payStart, t.chapterCount, t.updatedAt)
      .where(sql`${t.detailSyncedAt} IS NOT NULL`),
    /*
     * 首页「搜索最多」那条货架的唯一查询：按 locale 过滤、按 search_impressions 倒序取前几。
     *
     * partial 条件把索引压到只含有真实搜索数据的行——实测非零行是每语种几十条量级，
     * 而 dramas 有 3 万行。少了它，一条本该秒回的取前 10 会在 6 小时一次的
     * 首页 MISS 上跑全表扫描（这条路径与 dramas_sitemap_idx 那次是同一个教训）。
     */
    index("dramas_search_impressions_idx")
      .on(t.locale, t.searchImpressions)
      .where(sql`${t.searchImpressions} > 0`),
    index("dramas_group_idx").on(t.groupKey),
    index("dramas_publish_idx").on(t.publishAt),
    index("dramas_valid_idx").on(t.isValid),
  ],
);

/**
 * 每部剧最近一次 IndexNow 提交时的内容版本。
 *
 * 【为什么是独立的表，不是 dramas 上的一列】queries.ts 的 `DETAIL_COLUMNS` 是
 * `{ ...getTableColumns(dramas), ... }`，schema.ts 里给 dramas 新增的列会**自动**
 * 进入详情投影；代码先于 SQL 上线就让剧目页 / 播放页每次缓存 MISS 都 500。
 * 这张表不被任何渲染查询引用，失败面只有同步末尾的 drain。
 *
 * 【重推某部剧 = 删掉它那一行】。
 */
export const indexnowSubmissions = pgTable("indexnow_submissions", {
  dramaId: text("drama_id").primaryKey(),
  /**
   * 提交那一刻这部剧的内容版本，来自 `lib/indexnow-state.ts` 的
   * `CONTENT_VERSION_SQL`（`updated_at` 的 UTC 定长文本）。
   *
   * 【必须是 text，不能是 timestamptz】：读写会话的 DateStyle 不同时
   * `::text` -> `::timestamptz` 的往返不可逆；而读成 JS Date 再写回去会丢微秒
   * （实测 730 部剧的 updated_at 带微秒），那一批会每轮重复提交、永不收敛。
   */
  contentVersion: text("content_version").notNull(),
  submittedAt: timestamp("submitted_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  /** 200 或 202；NULL 表示迁移基线写的行，从未真正提交过。 */
  httpStatus: smallint("http_status"),
  urlCount: integer("url_count").notNull().default(0),
});

export const chapters = pgTable(
  "chapters",
  {
    /** CPS 的 chapter_id */
    id: text("id").primaryKey(),
    dramaId: text("drama_id")
      .notNull()
      .references(() => dramas.id, { onDelete: "cascade" }),
    /** 第几集，从 1 开始 */
    serial: integer("serial").notNull(),
    videoPic: text("video_pic").notNull().default(""),
    /** 官方嵌入地址，内含 cps_id 与 code，无时效，可安全落库 */
    iframeSrc: text("iframe_src").notNull().default(""),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("chapters_drama_serial_idx").on(t.dramaId, t.serial),
    index("chapters_drama_idx").on(t.dramaId),
  ],
);

/** 每次同步跑一条，用来排查"某天数据没更新"这类问题 */
export const syncRuns = pgTable("sync_runs", {
  id: serial("id").primaryKey(),
  startedAt: timestamp("started_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  finishedAt: timestamp("finished_at", { withTimezone: true }),
  status: text("status").notNull().default("running"),
  dramasUpserted: integer("dramas_upserted").notNull().default(0),
  chaptersUpserted: integer("chapters_upserted").notNull().default(0),
  skippedUnknownLang: integer("skipped_unknown_lang").notNull().default(0),
  error: text("error"),
});

/**
 * 出站点击。CPS 后台只看得到成交，看不到"哪个页面送出去的"，
 * 这张表是唯一能把收入回溯到具体剧目页与流量来源的地方。
 */
export const outboundClicks = pgTable(
  "outbound_clicks",
  {
    id: serial("id").primaryKey(),
    dramaId: text("drama_id").notNull(),
    locale: text("locale").notNull().default(""),
    /** 从第几集点出去的，付费墙点击为 payStart */
    serial: integer("serial"),
    /** book_link | app_link */
    target: text("target").notNull(),
    /** 仅记录 paywall 点击；其他来源不落库 */
    placement: text("placement").notNull().default(""),
    referer: text("referer"),
    userAgent: text("user_agent"),
    country: text("country"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index("clicks_drama_idx").on(t.dramaId),
    index("clicks_created_idx").on(t.createdAt),
  ],
);

/**
 * 域名前任 dramashortstv.com 的自增 id → 它当时那条 URL 的 (locale, 标题 key)。
 *
 * 【为什么需要这张表】
 * 承接旧 URL 本来只靠 slug 段查 `dramas.title_key`，但 GSC 实测发现
 * **流量最大的那批 URL 里有一整类根本没有 slug 段**：
 *
 *   /detail/38000                    818 次点击
 *   /detail/49020                    359 次点击
 *   /bg/detail/38000?id=38000        266 次点击
 *
 * 旧站接受这种写法（它按自己的 id 查库，slug 只是装饰），我方 `[[...slug]]`
 * 拿到空 slug 就无从查起，只能 404。这张表把那个 id 变回可查的东西。
 *
 * 【为什么不挂在 dramas 上】
 * 实测 3,261 部剧对应多个旧 id（旧站同一部剧在不同时期建过多条记录），
 * 最多的一部有 5 个。多对一放不进 dramas 的一个列。
 *
 * 【为什么存 (locale, title_key) 而不是直接存 book_id】
 * 存 book_id 就是把解析结果固化了，片库一变就要重跑导入；
 * 存 key 则让解析继续走 `getDramaByLegacyKey()` 这条唯一路径，
 * 与带 slug 的 URL **共用同一套匹配与排序规则**，不会出现
 * 「同一部剧，带 slug 的跳 A、不带 slug 的跳 B」这种摇摆。
 *
 * 数据来自旧站的 drama-detail-sitemap.xml（39,562 条，id 全局唯一、零重复），
 * 由 `pnpm import-legacy` 一次性导入。**旧站下线后这份数据就无法再获取**，
 * 所以不要清空这张表。
 */
export const legacyUrls = pgTable(
  "legacy_urls",
  {
    /** 旧站的自增主键，实测范围 7870–59935 */
    legacyId: integer("legacy_id").primaryKey(),
    /** 已归一到我方 locale（in→id、zh-TW→zh-hant、zh-CN→zh、fil→tl） */
    locale: text("locale").notNull(),
    /** 旧站 slug 过 legacyTitleKey() 的结果，与 dramas.title_key 同算法 */
    titleKey: text("title_key").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [index("legacy_locale_key_idx").on(t.locale, t.titleKey)],
);

export type Drama = typeof dramas.$inferSelect;
export type NewDrama = typeof dramas.$inferInsert;
export type Chapter = typeof chapters.$inferSelect;
export type NewChapter = typeof chapters.$inferInsert;
export type OutboundClick = typeof outboundClicks.$inferSelect;

/** One daily Feishu usage-report claim; no Vercel tokens or webhooks are stored here. */
export const vercelUsageReports = pgTable("vercel_usage_reports", {
  deliveryDate: text("delivery_date").primaryKey(),
  status: text("status").notNull().default("preparing"),
  message: text("message"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

/**
 * 每日片库快照。观测台的「日增 / 7 天增量」全部由相邻两行相减得出——
 * 上游【只给当前值，不给历史】，不自己留快照就永远算不出增量。
 *
 * 【一行一部剧一天，写入是纯 SQL 的 INSERT ... SELECT】：源数据就在同一个库的
 * dramas 表里，让它在服务端自己搬，一次往返、零行数据过线。改成"先查回来再写回去"
 * 是 3 万行来回两趟，而 Neon 的账单大头正是这种往返。
 *
 * 【主键含 observed_on，配 ON CONFLICT DO NOTHING】：同步每 6 小时一轮，
 * 当天第一轮写入、后三轮空转。所以快照口径固定是"每个 UTC 日的第一轮同步"，
 * 不会因为跑了几轮就变成一天四个值。
 *
 * 保留 90 天，由 pruneObservations() 在同步末尾清理。
 */
export const dramaObservations = pgTable(
  "drama_observations",
  {
    /** UTC 日期。date 类型，不带时区——快照本来就是"哪一天的" */
    observedOn: text("observed_on").notNull(),
    capturedAt: timestamp("captured_at", { withTimezone: true }).defaultNow(),
    metricsValid: boolean("metrics_valid"),
    dramaId: text("drama_id").notNull(),
    /** 上游 recent_revenue，单位【美分】，与 dramas.recentRevenueCents 同源同单位 */
    recentRevenueCents: numeric("recent_revenue_cents", {
      precision: 14,
      scale: 2,
    })
      .notNull()
      .default("0"),
    /** 平台累计推广人数，只增不减 */
    promotersCnt: integer("promoters_cnt").notNull().default(0),
    chapterCount: integer("chapter_count").notNull().default(0),
    payStart: integer("pay_start").notNull().default(0),
  },
  (t) => [
    uniqueIndex("drama_observations_pk").on(t.observedOn, t.dramaId),
    /** 单剧 90 天曲线走这条 */
    index("drama_observations_drama_idx").on(t.dramaId, t.observedOn),
  ],
);

/**
 * CPS 按日分成账单，来自 bill/app-estimate。
 *
 * 【落库而不是每次现查】：上游这个接口不滚动（08-20 是账号首次活动日，
 * 不是 90 天窗口的左端），但它是外部依赖，观测台每次打开都现查等于把
 * 页面可用性绑在上游身上，而这些数字一旦结算就不再变。
 *
 * 【主键带 promotion_value】：上游文档说粒度是 date × book_id × promotion_type，
 * 实测也是。但同一本书同一天出现两条不同短链在协议上并非不可能，
 * 而主键漏一列的后果是静默丢行——多这一列不损失任何东西。
 */
export const cpsBillDaily = pgTable(
  "cps_bill_daily",
  {
    /** 结算日，上游给的是 YYYY-MM-DD 字符串 */
    billDate: text("bill_date").notNull(),
    bookId: text("book_id").notNull(),
    /** link_book / code / link_app，实测这三种 */
    promotionType: text("promotion_type").notNull().default(""),
    /** 短链或推广口令。这是【账号级】的值，贴在哪里上游并不知道，见观测台注释 */
    promotionValue: text("promotion_value").notNull().default(""),
    /** 上游随账单给的标题，可能与我方 dramas.title 不同语种，仅作对照 */
    bookTitle: text("book_title").notNull().default(""),
    orderCnt: integer("order_cnt").notNull().default(0),
    /**
     * 我方分成，单位【美元】——注意与本文件里 recentRevenueCents 的单位【不同】。
     * 判据见 lib/cps/types.ts 里 total_revenue 那段注释，不要照着改成美分。
     */
    revenueUsd: numeric("revenue_usd", { precision: 14, scale: 4 })
      .notNull()
      .default("0"),
    fetchedAt: timestamp("fetched_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("cps_bill_daily_pk").on(
      t.billDate,
      t.bookId,
      t.promotionType,
      t.promotionValue,
    ),
    index("cps_bill_daily_book_idx").on(t.bookId),
  ],
);

/** 采集批次证据，旧数据不回填伪造来源。迁移见 observe-source-state.sql。 */
export const observeSources = pgTable("observe_sources", {
  source: text("source").primaryKey(),
  status: text("status").notNull(),
  attemptedAt: timestamp("attempted_at", { withTimezone: true }).notNull(),
  attemptId: text("attempt_id").notNull(),
  completedAt: timestamp("completed_at", { withTimezone: true }),
  details: jsonb("details").notNull().default({}),
});

/**
 * 选剧台（/admin/pick）的三张表。源头是 9 个分销剧场的剧单（飞书电子表格，
 * scripts/juyuantai/build.py 归一成 catalog.json）与运营自己的飞书发布记录
 * （posted.py → posted.json），由 `pnpm catalog-import` 全量替换写入。
 * 设计见 docs/plans/2026-09-11-pick-station-design.md。
 *
 * 【ReelShort 的行不在这里】——它们本来就是 dramas 表的行，页面直接查 dramas。
 * 【上线走 scripts/sql/catalog-tables.sql 手工执行，不要 pnpm db:push】。
 */
export const catalogRows = pgTable(
  "catalog_rows",
  {
    /** build.py 的 k：标题 slug + 序号，形如 kalos-the-dragon-s-contract-x，全库唯一 */
    rowKey: text("row_key").primaryKey(),
    /** kalos / shortmax / flickreels / dramabox / goodshort / starshort / touchshort / flareflow / moboreels */
    platform: text("platform").notNull(),
    /** 剧场剧单里的表名（英语剧单 / 小语种剧单 / 下架表……），给人看来源 */
    sourceTable: text("source_table").notNull().default(""),
    title: text("title").notNull(),
    titleCn: text("title_cn").notNull().default(""),
    creator: text("creator").notNull().default(""),
    /** 剧单里的语种中文名（英语 / 繁体中文……）；空串 = 剧单没给，页面显示「—」 */
    lang: text("lang").notNull().default(""),
    kind: text("kind").notNull().default(""),
    origin: text("origin").notNull().default(""),
    tags: text("tags").notNull().default(""),
    /** 剧单里的推荐 / 上新日期，YYYY-MM-DD；只有月日的由 build.py 按序列推年 */
    listedOn: text("listed_on"),
    panUrl: text("pan_url").notNull().default(""),
    panPw: text("pan_pw").notNull().default(""),
    episodes: integer("episodes"),
    payStart: integer("pay_start"),
    /** 剧场在 YouTube 剧单上标了可发（只有 KalosTV / StarShort 有这一列） */
    youtube: boolean("youtube").notNull().default(false),
    /** 同平台几行合并成这一行 */
    mergedRows: integer("merged_rows").notNull().default(1),
    /** 下架日期；与 reoffNote 互斥（下架早于重新分销的走 reoffNote） */
    offOn: text("off_on"),
    reoffNote: text("reoff_note").notNull().default(""),
    /**
     * 匹配结果三列，【导入时算好】，不在渲染时 join。
     * titleKey 过 lib/legacy-url.ts 的 legacyTitleKey()，与 dramas.title_key 同一算法。
     */
    titleKey: text("title_key").notNull().default(""),
    /** 对上的 ReelShort 正典行 id（同语种）；空数组 = 本站没有 */
    inSiteIds: text("in_site_ids")
      .array()
      .notNull()
      .default(sql`'{}'::text[]`),
    /** 只对上旧域名 sitemap 收录过的剧、ReelShort 片库里没有同名行 */
    legacyOnly: boolean("legacy_only").notNull().default(false),
    /** 同名剧只有别的语种版本在售 */
    siteOther: boolean("site_other").notNull().default(false),
    /** 有任一榜单 / 评级 / 清单 / 备注信号（冗余列，选剧 tab 的池子靠它，省一次 EXISTS） */
    hasSignal: boolean("has_signal").notNull().default(false),
    /** 最近一条证据自己的日期；null = 所有证据都日期未知（不拿剧单日期代填） */
    latestEvidenceOn: text("latest_evidence_on"),
    importedAt: timestamp("imported_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index("catalog_rows_platform_idx").on(t.platform, t.lang),
    /** 选剧 tab 默认排序：证据时间从新到旧 */
    index("catalog_rows_evidence_idx").on(t.hasSignal, t.latestEvidenceOn),
    index("catalog_rows_title_key_idx").on(t.titleKey),
  ],
);

/**
 * 一行 = 一条榜单 / 评级 / 清单 / 备注证据。
 *
 * 【主键带 ord】：实测 52 行在同一个 kind 下有多条（同一部剧在 KalosTV 日榜上
 * 两个不同名次段），用 (row_key, kind) 会静默丢掉第二条，页面照常 200。
 */
export const catalogSignals = pgTable(
  "catalog_signals",
  {
    rowKey: text("row_key").notNull(),
    /** kd / kw / sm / smd / mg / fh / sh / gh / gn / ghh / dbn */
    kind: text("kind").notNull(),
    /** 在 build.py sig 数组里的下标 */
    ord: integer("ord").notNull().default(0),
    /** 这条证据自己的日期（榜单日期 / 周起 / 入榜日期）；null = 日期未知 */
    evidenceOn: text("evidence_on"),
    rank: integer("rank"),
    grade: text("grade").notNull().default(""),
    note: text("note").notNull().default(""),
    /** 名次历史、周次序列这类只用于渲染的原始结构 */
    payload: jsonb("payload").notNull().default({}),
  },
  (t) => [
    uniqueIndex("catalog_signals_pk").on(t.rowKey, t.kind, t.ord),
    index("catalog_signals_kind_idx").on(t.kind, t.evidenceOn),
  ],
);

/** 运营飞书「选剧池 / 发布记录」归一结果，一行一部剧，帖子放 jsonb（只在证据页整表显示） */
export const catalogPosted = pgTable("catalog_posted", {
  /** 选剧池编号 SD-000001 */
  sd: text("sd").primaryKey(),
  feishuRecord: text("feishu_record").notNull().default(""),
  title: text("title").notNull(),
  titleKey: text("title_key").notNull().default(""),
  lang: text("lang").notNull().default(""),
  platform: text("platform").notNull().default(""),
  life: text("life").notNull().default(""),
  scheduled: boolean("scheduled").notNull().default(false),
  onlineOn: text("online_on"),
  why: text("why").notNull().default(""),
  note: text("note").notNull().default(""),
  archived: boolean("archived").notNull().default(false),
  /** 帖子数与最近发布日冗余出来，行上的「已发 N 条 · 最近日期」标签不用解 jsonb */
  postCount: integer("post_count").notNull().default(0),
  lastPostOn: text("last_post_on"),
  viewsTotal: integer("views_total").notNull().default(0),
  /*
   * 2026-09-11 P1 加的十列。前四个是选剧池自己的字段（来源 / 分类 / 推荐人 / 发过的账号）；
   * 后六个由 lib/pick/catalog-import 的 summarizePosts() 从 posts 算，与 posted.py 逐字同口径：
   * 「已发」只数状态为已回填 / 已公开的帖子，待公开进 sched_count。
   */
  sources: text("sources")
    .array()
    .notNull()
    .default(sql`'{}'::text[]`),
  cats: text("cats")
    .array()
    .notNull()
    .default(sql`'{}'::text[]`),
  who: text("who")
    .array()
    .notNull()
    .default(sql`'{}'::text[]`),
  accounts: text("accounts")
    .array()
    .notNull()
    .default(sql`'{}'::text[]`),
  createdOn: text("created_on"),
  updatedOn: text("updated_on"),
  firstPostOn: text("first_post_on"),
  metricAt: text("metric_at"),
  schedCount: integer("sched_count").notNull().default(0),
  viewsCount: integer("views_count").notNull().default(0),
  posts: jsonb("posts").notNull().default([]),
  /** 对上的剧场剧库行（row_key），导入时按 title_key + 剧场 / 语种约束算好 */
  rowKeys: text("row_keys")
    .array()
    .notNull()
    .default(sql`'{}'::text[]`),
  /** 对上的 ReelShort 正典行（dramas.id），同一套约束；运营表里 46/71 部挑的是 ReelShort 剧 */
  dramaIds: text("drama_ids")
    .array()
    .notNull()
    .default(sql`'{}'::text[]`),
  importedAt: timestamp("imported_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

/**
 * 运营飞书「账号台账」，一行一个发布账号（2026-09-11 P1，第 13 张表）。
 * 由 pnpm catalog-import 随发布记录一起全量替换；上线走 scripts/sql/catalog-p1.sql。
 */
export const catalogAccounts = pgTable("catalog_accounts", {
  id: text("id").primaryKey(),
  name: text("name").notNull().default(""),
  url: text("url").notNull().default(""),
  grp: text("grp").notNull().default(""),
  form: text("form").notNull().default(""),
  niche: text("niche").notNull().default(""),
  status: text("status").notNull().default(""),
  fans: integer("fans"),
  asOf: text("as_of"),
  importedAt: timestamp("imported_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});
