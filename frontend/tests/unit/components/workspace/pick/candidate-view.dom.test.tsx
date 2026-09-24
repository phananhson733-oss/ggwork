import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  CandidateView,
  ReplayLink,
} from "@/components/workspace/pick/candidate-view";
import { REPLAY_LINK_ENABLED } from "@/core/pick/links";
import type { PickDataAsOf, PickItem, PickResult } from "@/core/pick/types";

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

  it("does not offer replay until the replay view ships (critique A4)", () => {
    expect(REPLAY_LINK_ENABLED).toBe(false);
    renderView(synced);
    expect(screen.queryByRole("link", { name: /回放/ })).toBeNull();
    expect(
      screen
        .getAllByRole("link")
        .some((link) => link.getAttribute("href")?.includes("result=")),
    ).toBe(false);
  });
});
