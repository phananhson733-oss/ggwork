-- Query-only indexes. No source rows or classification rules change.
CREATE INDEX dramas_group_locale_cover_idx
  ON pick_source.dramas (group_key, locale) INCLUDE (id);
CREATE INDEX observations_verified_day_idx
  ON pick_source.drama_observations (observed_on, drama_id)
  WHERE metrics_valid IS TRUE;
-- This predicate matches notBot(); changing its patterns needs a new migration.
CREATE INDEX clicks_human_created_drama_idx
  ON pick_source.outbound_clicks (created_at, drama_id)
  WHERE user_agent IS NULL OR lower(user_agent) NOT LIKE ALL (
    ARRAY[
      '%meta-webindexer%',
      '%meta-externalagent%',
      '%meta-externalfetcher%',
      '%googlebot%',
      '%bingbot%',
      '%applebot%',
      '%yandexbot%',
      '%duckduckbot%',
      '%baiduspider%',
      '%petalbot%',
      '%sogou%',
      '%gptbot%',
      '%oai-searchbot%',
      '%claudebot%',
      '%claude-searchbot%',
      '%ccbot%',
      '%perplexitybot%',
      '%bytespider%',
      '%amazonbot%',
      '%amzn-searchbot%',
      '%semrushbot%',
      '%ahrefsbot%',
      '%mj12bot%',
      '%dotbot%',
      '%crawler%',
      '%spider/%',
      '%python-requests%',
      '%curl/%',
      '%wget/%',
      '%headlesschrome%',
      '%lightpanda%'
    ]::text[]
  );
