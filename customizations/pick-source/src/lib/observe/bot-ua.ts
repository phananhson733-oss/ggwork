/**
 * 出站点击里剔除爬虫的 UA 模式。【纯数据，可单测】。
 *
 * 【这是启发式，不是真相】。CLAUDE.md 记着：`outbound_clicks` 全表 2,340 行里
 * 1,773 行（75.8%）是爬虫，几乎全部来自 Meta 的两个 UA。正解是在 `/api/go`
 * 落库时就打 `is_bot` 标记（比事后正则可靠），但那要加一列、要单独做，
 * 不能夹带在观测台里。在那之前观测台用这张表过滤，并在页面上说明它是启发式。
 *
 * 【宁可漏判也不要误判】：把真人当爬虫剔掉，会让一部真的有人点的剧看起来没人点，
 * 而观测台存在的意义正是发现那种剧。所以只列已经实测见过的爬虫标识，
 * 不用 `%bot%` 这种会连 `Cubot`（一个手机品牌）都吃掉的宽匹配。
 */
export const BOT_UA_PATTERNS = [
  // 实测占比最大的两个，CLAUDE.md 有记录
  "%meta-webindexer%",
  "%meta-externalagent%",
  "%meta-externalfetcher%",
  // 常规搜索引擎
  "%googlebot%",
  "%bingbot%",
  "%applebot%",
  "%yandexbot%",
  "%duckduckbot%",
  "%baiduspider%",
  "%petalbot%",
  "%sogou%",
  // AI 抓取
  "%gptbot%",
  "%oai-searchbot%",
  "%claudebot%",
  "%claude-searchbot%",
  "%ccbot%",
  "%perplexitybot%",
  "%bytespider%",
  "%amazonbot%",
  "%amzn-searchbot%",
  // 通用工具与监控
  "%semrushbot%",
  "%ahrefsbot%",
  "%mj12bot%",
  "%dotbot%",
  "%crawler%",
  "%spider/%",
  "%python-requests%",
  "%curl/%",
  "%wget/%",
  "%headlesschrome%",
  // 2026-09-20 挂住宅代理打了 225 万请求，也打了 /api/go，见 crawler-policy.ts
  "%lightpanda%",
] as const;
