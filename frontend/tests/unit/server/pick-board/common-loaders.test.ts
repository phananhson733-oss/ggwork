import { expect, it } from "@rstest/core";

import { parsePickRequest } from "@/core/pick-board/request";
import { boardQuery } from "@/server/pick-board/common-loaders";
const pin = {
  catalog_batch_id: "catalog-historical",
  knowledge_batch_id: "knowledge-historical",
  mirror_version: 7,
  rule_version: "mirror-rules-v7",
  feedback_version_id: null,
};
it("preserves historical catalog scope, unknown-language filter, literal search and pagination", () => {
  const request = boardQuery(
    parsePickRequest({
      tab: "all",
      v: "7",
      lang: "__unknown__",
      q: "中文%来源id",
      page: "3",
      size: "50",
      sort: "title",
      posted: "yes",
      off: "1",
    }),
    pin,
  );
  expect(request).toMatchObject({
    domain: "catalog",
    scope: "full_catalog",
    pin,
    language: "",
    query: "中文%来源id",
    offset: 100,
    limit: 50,
    order: "title",
    posted_filter: "yes",
    with_off: true,
    exclude_selected: false,
  });
});
it("preserves separate candidate pool versus full catalog and legacy fixed top50 growth", () => {
  expect(boardQuery(parsePickRequest({ tab: "pick" }), pin)).toMatchObject({
    domain: "candidates",
    scope: "candidate_pool",
    confirmed_eligible_only: false,
  });
  expect(boardQuery(parsePickRequest({ tab: "pick", w: "1" }), pin).scope).toBe(
    "full_catalog",
  );
  expect(
    boardQuery(
      parsePickRequest({
        tab: "rank",
        rk: "rs_growth",
        page: "8",
        size: "20",
        rs: "d1",
      }),
      pin,
    ),
  ).toMatchObject({
    domain: "rankings",
    scope: "full_catalog",
    rank: "rs_growth",
    offset: 0,
    limit: 50,
  });
});
it("does not turn ignored catalog URL controls into new posted or ranking filters", () => {
  for (const tab of ["posted", "rank"]) {
    expect(
      boardQuery(
        parsePickRequest({
          tab,
          lang: "英语",
          platform: "shortmax",
          basis: "kd",
          yt: "1",
          off: "1",
          inuse: "1",
        }),
        pin,
      ),
    ).toMatchObject({
      language: null,
      theater: null,
      signal_kind: null,
      youtube_ok: false,
      with_off: false,
      in_use_only: false,
    });
  }
});
it("translates invalid legacy calendar bookmarks into explicit latest lookup rather than throwing an unhandled schema error", () => {
  expect(
    boardQuery(
      parsePickRequest({ tab: "rank", rk: "kd", day: "2026-02-31" }),
      pin,
    ).period,
  ).toEqual({ kind: "latest", value: null });
  const weekly = boardQuery(
    parsePickRequest({ tab: "rank", rk: "kw", week: "2026-02-31" }),
    pin,
  );
  expect(weekly.period).toEqual({ kind: "latest", value: null });
  expect(weekly.legacy_week_label).toBe("2026-02-31");
});
it("sends only supported rules controls and ignores inherited theater rank search controls", () => {
  expect(
    boardQuery(
      parsePickRequest({
        tab: "rules",
        q: "prior search",
        grade: "S",
        rs: "d1",
      }),
      pin,
    ),
  ).toMatchObject({
    domain: "rules",
    order: "evidence_date",
    query: null,
    grade: null,
    rs_sort: "rr",
    confirmed_eligible_only: true,
  });
  expect(
    boardQuery(
      parsePickRequest({
        tab: "rank",
        rk: "kd",
        q: "prior search",
        rs: "d1",
        rl: "en",
      }),
      pin,
    ),
  ).toMatchObject({ query: null, rs_sort: "rr", rs_locale: null });
});
