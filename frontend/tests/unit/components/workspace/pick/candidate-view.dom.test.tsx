import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  CandidateView,
  ReplayLink,
} from "@/components/workspace/pick/candidate-view";
import { REPLAY_LINK_ENABLED } from "@/core/pick/links";
import {
  UNOBSERVED_GSC,
  UNOBSERVED_TRENDS,
  forbiddenIn,
} from "@/core/pick/obs-format";
import {
  pickResultSchema,
  type PickDataAsOf,
  type PickItem,
  type PickResult,
} from "@/core/pick/types";

import obsPayload from "../../../core/pick/fixtures/backend-result-obs.json";

afterEach(cleanup);
const result: PickResult = {
  id: "r1",
  thread_id: "t1",
  run_id: "run",
  run_status: "success",
  catalog_batch_id: "b1",
  knowledge_batch_id: null,
  rule_version: "v1",
  ranking_version: "v1",
  created_at: "2026-09-21T00:00:00Z",
  conditions: { limit: 5, exclude_selected: true, language: "en" },
  items: [
    {
      item_id: "i1",
      identity: "source/1",
      title: "样例剧",
      theater: "Example",
      language: "en",
      availability: "unknown",
      reason: "符合条件",
      warnings: ["状态待核实"],
      evidence: [],
    },
  ],
};
describe("candidate interaction", () => {
  it.each([
    "pending",
    "running",
    "interrupted",
    "error",
    "timeout",
    "unknown",
  ] as const)("blocks saving a %s run", (run_status) => {
    const save = rs.fn();
    render(
      <CandidateView
        result={{ ...result, run_status }}
        selected={["i1"]}
        onToggle={rs.fn()}
        onSave={save}
        busy={false}
      />,
    );
    expect(screen.getByRole("status")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存选中（1）" }));
    expect(save).not.toHaveBeenCalled();
  });
  it("shows historical evidence without save controls in read-only mode", () => {
    render(
      <CandidateView
        result={result}
        selected={[]}
        onToggle={rs.fn()}
        onSave={rs.fn()}
        busy={false}
        readOnly
      />,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /保存选中/ })).toBeNull();
  });
  it("shows a real shortfall and does not save without a user click", () => {
    const save = rs.fn();
    render(
      <CandidateView
        result={result}
        selected={[]}
        onToggle={rs.fn()}
        onSave={save}
        busy={false}
      />,
    );
    expect(screen.getByText("找到 1 部 / 请求 5 部")).toBeTruthy();
    expect(screen.getByText("状态待核实")).toBeTruthy();
    expect(
      screen
        .getByRole("button", {
          name: "保存选中（0）",
        })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(save).not.toHaveBeenCalled();
  });
  it("uses immutable IDs for checkbox changes and save", () => {
    const toggle = rs.fn();
    const save = rs.fn();
    render(
      <CandidateView
        result={result}
        selected={["i1"]}
        onToggle={toggle}
        onSave={save}
        busy={false}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "选择样例剧" }));
    expect(toggle).toHaveBeenCalledWith("i1");
    fireEvent.click(screen.getByRole("button", { name: "保存选中（1）" }));
    expect(save).toHaveBeenCalledTimes(1);
  });
});

function rsItem(itemId: string, title: string, rowKey: string): PickItem {
  return {
    ...result.items[0]!,
    item_id: itemId,
    title,
    identity: JSON.stringify([
      "realshort-pick",
      Buffer.from(rowKey, "utf8").toString("base64url"),
      "en",
    ]),
  };
}

const SHARED: PickDataAsOf = {
  source_as_of: "2026-09-23T03:00:00.000Z",
  published_at: "2026-09-23T03:17:34Z",
  shared: true,
  mirror_version: 7,
};

const synced: PickResult = {
  ...result,
  id: "0123456789abcdef0123456789abcdef",
  items: [
    rsItem("i1", "剧一", "kalos-1"),
    rsItem("i2", "剧二", "shortmax-856049 "),
  ],
  data_as_of: SHARED,
};

function renderView(value: PickResult, readOnly = false) {
  return render(
    <CandidateView
      result={value}
      selected={[]}
      onToggle={rs.fn()}
      onSave={rs.fn()}
      busy={false}
      readOnly={readOnly}
    />,
  );
}

function checkLinks() {
  return screen.queryAllByRole("link", { name: /在选剧资料核对/ });
}

describe("row check links (P4-1)", () => {
  it("links every decodable card to its row at the pinned version", () => {
    renderView(synced);
    const links = checkLinks();
    expect(links.map((link) => link.getAttribute("aria-label"))).toEqual([
      "在选剧资料核对：剧一",
      "在选剧资料核对：剧二",
    ]);
    const [first, second] = links.map(
      (link) =>
        new URL(link.getAttribute("href") ?? "", "https://workbench.test"),
    );
    expect(first?.pathname).toBe("/workspace/pick-data");
    expect(first?.searchParams.get("tab")).toBe("row");
    expect(first?.searchParams.get("row")).toBe("kalos-1");
    expect(first?.searchParams.get("v")).toBe("7");
    expect(second?.searchParams.get("row")).toBe("shortmax-856049 ");
    for (const link of links) {
      expect(link.textContent).toBe("在选剧资料核对");
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
    }
  });

  it("keeps the links in read-only history", () => {
    renderView(synced, true);
    expect(checkLinks()).toHaveLength(2);
  });

  it.each([
    ["mirror_version is null", { ...SHARED, mirror_version: null }],
    ["mirror_version is missing", { ...SHARED, mirror_version: undefined }],
    ["the batch is personal", { ...SHARED, shared: false }],
  ])("shows no link when %s", (_label, data_as_of) => {
    renderView({ ...synced, data_as_of });
    expect(checkLinks()).toHaveLength(0);
  });

  it("shows no link for an old result without data_as_of", () => {
    renderView({ ...synced, data_as_of: undefined });
    expect(checkLinks()).toHaveLength(0);
  });

  it("skips only the cards whose identity is not a RealShort row key", () => {
    renderView({
      ...synced,
      items: [
        synced.items[0]!,
        { ...synced.items[1]!, identity: "source/1" },
        {
          ...synced.items[1]!,
          item_id: "i3",
          title: "个人导入的剧",
          identity: JSON.stringify(["sheet-upload", "Yy0x", "en"]),
        },
      ],
    });
    expect(checkLinks().map((link) => link.getAttribute("aria-label"))).toEqual(
      ["在选剧资料核对：剧一"],
    );
  });

  it("renders the replay link as its own line in a new tab, for P4-2", () => {
    const href = "/workspace/pick-data?result=0123456789abcdef0123456789abcdef";
    const { container } = render(<ReplayLink href={href} />);
    const link = screen.getByRole("link", { name: "回放这份候选" });
    expect(link.getAttribute("href")).toBe(href);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.parentElement?.tagName).toBe("P");
    expect(link.parentElement?.parentElement).toBe(container);
  });

  it("offers replay with the replay view (P4-2, critique A4): pinned to the result's version", () => {
    expect(REPLAY_LINK_ENABLED).toBe(true);
    renderView(synced);
    const link = screen.getByRole("link", { name: "回放这份候选" });
    expect(link.getAttribute("href")).toBe(
      `/workspace/pick-data?result=${synced.id}&v=7`,
    );
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("replays an unpaired shared result on the current version, never a personal one (U28)", () => {
    renderView({ ...synced, data_as_of: { ...SHARED, mirror_version: null } });
    expect(
      screen.getByRole("link", { name: "回放这份候选" }).getAttribute("href"),
    ).toBe(`/workspace/pick-data?result=${synced.id}`);
    cleanup();
    renderView({ ...synced, data_as_of: { ...SHARED, shared: false } });
    expect(screen.queryByRole("link", { name: /回放/ })).toBeNull();
  });
});

// Plan TR-16 (premise 1): an observation the backend did not see reads
// "未观测到" on the card, never zero and never "数值未知".
describe("observation evidence on the card (TR-16)", () => {
  const obsResult = pickResultSchema.parse(obsPayload);
  const obsItem = obsResult.items[0]!;

  function evidenceText(container: HTMLElement) {
    return [...container.querySelectorAll("li")]
      .map((li) => li.textContent ?? "")
      .join("\n");
  }

  it("renders 未观测到 when an obs value is null", () => {
    // Only the observation entries: the fixture's qc signal has no value either, and says 数值未知 as before.
    const unseen = obsItem.evidence
      .filter((e) => e.kind === "obs_gsc" || e.kind === "obs_trends")
      .map((e) => ({
        ...e,
        value: null,
        note: e.kind === "obs_gsc" ? "gsc-rules-v1" : "trend-rules-v1",
      }));
    const { container } = renderView({
      ...obsResult,
      items: [{ ...obsItem, evidence: unseen }],
    });
    const text = evidenceText(container);
    expect(text).toContain(UNOBSERVED_GSC);
    expect(text).toContain(UNOBSERVED_TRENDS);
    expect(text).not.toContain("数值未知");
    expect(forbiddenIn(text)).toEqual([]);
  });

  it("renders the obs fixture's card: its evidence, conditions and order", () => {
    const { container } = renderView(obsResult);
    expect(screen.getByText(/按观测状态排序/)).toBeTruthy();
    const text = evidenceText(container);
    expect(text).toContain("US · Google Trends 上升（已确认）");
    expect(text).toContain("USA · 曝光飙升（24 小时）");
    expect(text).toContain("发现词「reelshort the alpha's bride」");
    expect(forbiddenIn(text)).toEqual([]);
  });
});

// Evaluation batch 2 (2026-10-05): the card shows what the model was told
// beside the result, and each item's tags, listing date and channel rules.
describe("result notes on the card", () => {
  const view = (props: Partial<Parameters<typeof CandidateView>[0]>) =>
    render(
      <CandidateView
        result={result}
        selected={[]}
        onToggle={rs.fn()}
        onSave={rs.fn()}
        busy={false}
        {...props}
      />,
    );
  it("shows each item's facts, the stale warnings and the hot scope", () => {
    view({
      notes: {
        kind: "notes",
        notes: {
          item_facts: {
            i1: {
              tags: ["复仇"],
              listed_at: "2026-09-01",
              channel_rules: { youtube: "allowed" },
            },
          },
          data_notices: ["kd 最新一期 2026-09-27，距今已超过 2 天"],
          hot_scope: { counted: ["kd"], not_counted: ["clk"] },
        },
      },
    });
    expect(
      screen.getByText("标签 复仇 · 上架 2026-09-01 · youtube 规则允许"),
    ).toBeTruthy();
    expect(screen.getByText(/kd 最新一期 2026-09-27/)).toBeTruthy();
    expect(screen.getByText("热门依据算了 kd；不算 clk")).toBeTruthy();
  });
  it("explains an empty result condition by condition", () => {
    view({
      result: { ...result, items: [], matched_total: 0 },
      notes: {
        kind: "notes",
        notes: {
          item_facts: {},
          zero_diagnosis: {
            catalog_rows: 3,
            delisted_rows: 0,
            without_each: [
              { condition: "tags", value: ["复仇"], matched_total: 2 },
            ],
          },
        },
      },
    });
    const diagnosis = screen.getByTestId("pick-zero-diagnosis");
    expect(diagnosis.textContent).toContain("去掉「标签 复仇」：2 部");
    expect(diagnosis.textContent).not.toContain("可以放宽条件后重新查询");
  });
  it("keeps the plain card while notes load, are missing or failed", () => {
    view({ result: { ...result, items: [] } });
    expect(screen.getByText(/可以放宽条件后重新查询/)).toBeTruthy();
    expect(screen.queryByTestId("pick-notes")).toBeNull();
    cleanup();
    view({ notes: { kind: "none" } });
    expect(screen.queryByTestId("pick-notes")).toBeNull();
    cleanup();
    view({ notes: { kind: "gone", message: "批次已清理" } });
    expect(screen.getByTestId("pick-notes").textContent).toBe("批次已清理");
    cleanup();
    view({ notes: { kind: "error" } });
    expect(screen.getByTestId("pick-notes").textContent).toContain(
      "依据说明暂不可用",
    );
    expect(screen.getByText("样例剧", { exact: false })).toBeTruthy();
  });
});

describe("readiness evidence and uncertainty", () => {
  const evidence = (kind: string, date: string | null, rank?: number) => ({
    citation_id: kind,
    kind,
    label: `${kind} 榜`,
    source_ref: `source/${kind}`,
    observed_at: date,
    value: null,
    rank,
    grade: "A",
    note: "合成说明",
  });
  it("shows selected board evidence before details, preserving all evidence and item order", () => {
    const { container } = renderView({
      ...result,
      conditions: { ...result.conditions, signal_kind: "kd", sort: "rank" },
      items: [
        {
          ...result.items[0]!,
          evidence: [
            evidence("qc", "2026-10-05", 1),
            evidence("kd", "2026-09-30", 8),
          ],
        },
      ],
    });
    const primary = screen.getByTestId("pick-primary-evidence");
    expect(primary.textContent).toContain("kd 榜 · 第8名 · 评级 A · 合成说明");
    expect(primary.textContent).toContain("2026-09-30");
    expect(primary.closest("details")).toBeNull();
    expect(container.querySelector("details")?.open).toBe(false);
    expect(container.querySelectorAll("details li")).toHaveLength(2);
  });
  it("keeps allowed separate from unknown availability, unmatched posted and unknown evidence dates", () => {
    render(
      <CandidateView
        result={{
          ...result,
          conditions: {
            ...result.conditions,
            channel: "youtube",
            confirmed_eligible_only: false,
          },
          items: [
            {
              ...result.items[0]!,
              warnings: [
                "上下架状态待核实",
                "上下架状态待核实",
                "部分依据日期未知",
              ],
              evidence: [evidence("qc", null)],
              posted: {
                matched: false,
                records: [],
                post_count: 0,
                sched_count: 0,
                accounts: [],
                last_post_on: null,
              },
            },
          ],
        }}
        selected={["i1"]}
        onToggle={rs.fn()}
        onSave={rs.fn()}
        busy={false}
        notes={{
          kind: "notes",
          notes: {
            item_facts: {
              i1: {
                tags: [],
                listed_at: null,
                channel_rules: { youtube: "allowed" },
              },
            },
            notices_reference_at: null,
          },
        }}
      />,
    );
    expect(screen.getByText(/youtube 规则允许/)).toBeTruthy();
    expect(screen.getAllByText("上下架状态待核实")).toHaveLength(1);
    expect(screen.getByText("发布记录：未对上（不代表从未发布）")).toBeTruthy();
    expect(screen.getByTestId("pick-primary-evidence").textContent).toContain(
      "日期未知",
    );
    expect(screen.getByText(/查询时点未知/)).toBeTruthy();
    expect(screen.queryByText(/确认可发/)).toBeNull();
  });
  it.each([undefined, null, "2026-09-30T12:14:11.000000+00:00"])(
    "labels notes reference %s independently of browser timezone",
    (reference) => {
      render(
        <CandidateView
          result={result}
          selected={[]}
          onToggle={rs.fn()}
          onSave={rs.fn()}
          busy={false}
          notes={{
            kind: "notes",
            notes: {
              item_facts: {},
              data_notices: ["该批次资料过期"],
              ...(reference !== undefined
                ? { notices_reference_at: reference }
                : {}),
            },
          }}
        />,
      );
      const text = screen.getByTestId("pick-notes").textContent;
      expect(text).toContain(
        reference === undefined
          ? "数据时效说明"
          : reference === null
            ? "查询时点未知"
            : "按候选生成时点核对的数据时效",
      );
      if (reference) {
        expect(text).toContain("20:14:11");
        expect(text).toContain("北京时间");
      }
      expect(screen.getAllByText("该批次资料过期")).toHaveLength(1);
    },
  );
});

describe("notes failures keep candidate operations independent", () => {
  it.each([
    undefined,
    { kind: "none" } as const,
    { kind: "error" } as const,
    { kind: "gone", message: "旧依据已清理" } as const,
  ])("keeps select, evidence and save operable for %s", (notes) => {
    const toggle = rs.fn();
    const save = rs.fn();
    const { container } = render(
      <CandidateView
        result={result}
        selected={["i1"]}
        onToggle={toggle}
        onSave={save}
        busy={false}
        notes={notes}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "选择样例剧" }));
    fireEvent.click(screen.getByRole("button", { name: "保存选中（1）" }));
    expect(toggle).toHaveBeenCalledWith("i1");
    expect(save).toHaveBeenCalledTimes(1);
    const details = container.querySelector("details")!;
    expect(details.querySelector("summary")?.textContent).toContain("查看依据");
    expect(details.open).toBe(false);
  });
  it.each([5, 10, 20])(
    "keeps %s candidate identities/order and all full evidence",
    (count) => {
      const items = Array.from({ length: count }, (_, index) => ({
        ...result.items[0]!,
        item_id: `i${index}`,
        title: `Synthetic ${index} 中文长片名`,
        evidence: [
          {
            kind: "qc",
            label: "评级",
            grade: "S",
            note: "需要核实的合成备注",
            value: 0,
            rank: null,
            citation_id: `c${index}`,
            observed_at: null,
            source_ref: "synthetic/grade",
          },
        ],
      }));
      const { container } = renderView({ ...result, items });
      expect(
        screen
          .getAllByRole("checkbox")
          .map((box) => box.getAttribute("aria-label")),
      ).toEqual(items.map((item) => `选择${item.title}`));
      expect(
        screen
          .getAllByTestId("pick-primary-evidence")
          .every((node) =>
            node.textContent?.includes("评级 S · 需要核实的合成备注 · 0"),
          ),
      ).toBe(true);
      expect(container.querySelectorAll("details li")).toHaveLength(count);
    },
  );
});
