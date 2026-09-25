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
