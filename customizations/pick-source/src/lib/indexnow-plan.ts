/**
 * IndexNow 的纯逻辑部分。**不带 server-only**，所以 tests/indexnow.test.ts
 * 能直接 import 来单测，而不用退化成源码文本比对——
 * 这是照 lib/sitemap-plan.ts 的先例拆的。
 *
 * 带副作用的那半（读环境变量、发请求）在 lib/indexnow.ts。
 */

/** 协议规定单次请求最多 10,000 条 URL。 */
export const MAX_URLS_PER_REQUEST = 10_000;

/**
 * 单轮同步提交的总量上限。
 *
 * 铺库阶段 `detailBudget` 可能上千，一部剧展开「1 条剧目页 + 最多 20 条免费集」，
 * 不夹的话一轮能产出上万条。IndexNow 没有公开的硬性日配额，但它明确要求
 * 只提交**真正变更**的 URL，短时间灌大批量会被降权处理。
 * 稳态下每轮只有几十条新剧，这个夹子只在铺库时起作用。
 */
export const MAX_URLS_PER_RUN = 10_000;

/**
 * key 必须是 8-128 位的字母数字/连字符。
 *
 * 【这条校验防的是 path traversal，不是格式洁癖】：
 * scripts/write-indexnow-key.mjs 拿这个值直接当**文件名**写进 `public/`，
 * key 里含 `/` 或 `..` 就会把文件写出 public 之外。
 * 那个脚本是 .mjs、import 不了这里，所以它有一份**同样的**副本，
 * tests/indexnow.test.ts 钉住两边逐字一致。
 */
export const KEY_PATTERN = /^[a-zA-Z0-9-]{8,128}$/;

/** 把 URL 列表切成不超过协议上限的批次。 */
export function chunkUrls(
  urls: readonly string[],
  size: number = MAX_URLS_PER_REQUEST,
): string[][] {
  const chunks: string[][] = [];
  for (let i = 0; i < urls.length; i += size) {
    chunks.push(urls.slice(i, i + size));
  }
  return chunks;
}

/**
 * 去重 + 夹到单轮上限。
 *
 * 【顺序即优先级】：调用方按「剧目页在前、播放页在后」拼列表，
 * 于是超限截断时先保住的是剧目页——它是那部剧的正典落地页，
 * 播放页少提交几条还能靠 sitemap 被发现。
 * 所以这里的去重**必须保序**（Set 的插入序天然如此），不要改成排序或 filter+indexOf。
 */
export function planSubmission(
  urls: readonly string[],
  limit: number = MAX_URLS_PER_RUN,
): string[] {
  const deduped = [...new Set(urls)];
  if (deduped.length <= limit) return deduped;
  console.warn(
    `[indexnow] ${deduped.length} 条 URL 超过单轮上限 ${limit}，截断（剧目页优先）`,
  );
  return deduped.slice(0, limit);
}

/** 一条正典 URL 行，以及解析到它的那些 raw id（按待提交顺序）。 */
export interface DrainCandidate {
  /** 上游给同一部剧发多个 book_id，它们会解析到同一条正典行。 */
  ids: readonly string[];
  /** `freeEpisodeCount()` 的结果。**可能非常大**，见 planDrain 的注释。 */
  freeEpisodes: number;
}

/** 装进本轮的某个候选，以及实际要展开几集。 */
export interface DrainSlice {
  index: number;
  episodes: number;
}

export interface DrainPlan {
  slices: DrainSlice[];
  /** 可以写进 indexnow_submissions 的 raw id。 */
  covered: string[];
  /** 预算不够，留到下一轮的 raw id。 */
  deferred: string[];
  /** 集数被预算切掉的 raw id，只用来打日志。 */
  truncatedIds: string[];
}

/**
 * 按预算挑出本轮提交哪些剧、各展开几集。**纯函数，不构造 URL**。
 *
 * 【截断以剧为单位】covered 的 id 会被写进 indexnow_submissions，此后除非内容再变
 * 就永不重交。按 URL 条数截断的话，被切掉的播放页会跟着那部剧一起变成「已提交」，
 * 再也没有人补——那正是这次要修的病本身。
 *
 * 【队首单独超预算时截断提交】`freeEpisodeCount()` 没有上界而 `chapter_count` 是
 * integer，「整部装不下就留到下一轮」对这种剧没有进展保证：它每轮都排在队首、
 * 每轮都装不下，后面的剧一部都出不去。实测全库免费集最多 20 集，所以这条是防御性的。
 *
 * 【先算条数再展开】先展开后判断预算的写法，遇到 `pay_start=0` /
 * `chapter_count=2147483647` 会尝试构造 21 亿条字符串，try/catch 救不回 OOM。
 */
export function planDrain(
  candidates: readonly DrainCandidate[],
  budget: number,
): DrainPlan {
  const plan: DrainPlan = {
    slices: [],
    covered: [],
    deferred: [],
    truncatedIds: [],
  };
  let remaining = budget;

  candidates.forEach((candidate, index) => {
    // 1 条剧目页 + 免费集。相加前不展开任何数组。
    const needed = 1 + candidate.freeEpisodes;
    if (needed <= remaining) {
      plan.slices.push({ index, episodes: candidate.freeEpisodes });
      remaining -= needed;
      plan.covered.push(...candidate.ids);
      return;
    }
    // 队首（还什么都没装下）拿的是全额预算，它装不下就是真的装不下。
    if (plan.slices.length === 0 && remaining >= 1) {
      const episodes = remaining - 1;
      plan.slices.push({ index, episodes });
      remaining = 0;
      plan.covered.push(...candidate.ids);
      plan.truncatedIds.push(...candidate.ids);
      return;
    }
    // 后面若还有装得下的小剧，照常提交——它们下一轮就不用再排队了。
    plan.deferred.push(...candidate.ids);
  });

  return plan;
}

/**
 * 只有 200 与 202 算提交成功。协议原文（indexnow.org/documentation）：
 * 200 OK「URL submitted successfully」，202 Accepted「URL received.
 * IndexNow key validation pending.」，403「key not valid」。
 *
 * 【202 照常算成功、照常写记录】：端点持续 202 时不写的话，每轮都会重灌同一批 URL。
 * 代价是 key 校验最终失败、而这部剧内容再不变时不会重试，补救是一条
 * `DELETE FROM indexnow_submissions WHERE http_status = 202`。
 */
export function isAccepted(status: number): boolean {
  return status === 200 || status === 202;
}

export type IndexNowSyncOutcome = "new" | "changed" | "unchanged";

export interface OrderedIndexNowSyncIds {
  newlyPublished: string[];
  contentChanged: string[];
}

/**
 * 按 selectStaleDramas 的选择顺序整理成功的详情同步结果。
 *
 * outcome Map 的插入顺序是并发完成顺序，不能拿来排 IndexNow 优先级；缺席表示
 * runPool 已吞掉的失败，unchanged 则没有需要通知搜索引擎的新 URL。
 */
export function orderIndexNowSyncIds(
  stale: readonly { id: string }[],
  outcomes: ReadonlyMap<string, IndexNowSyncOutcome>,
): OrderedIndexNowSyncIds {
  const newlyPublished: string[] = [];
  const contentChanged: string[] = [];
  for (const { id } of stale) {
    const outcome = outcomes.get(id);
    if (outcome === "new") newlyPublished.push(id);
    else if (outcome === "changed") contentChanged.push(id);
  }
  return { newlyPublished, contentChanged };
}
