import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "alice" } }),
}));
rs.mock("@/core/pick/api", () => ({
  getPickResult: rs.fn(),
  getPickResultNotes: rs.fn(),
  savePickSelection: rs.fn(),
}));

import { CandidatePanel } from "@/components/workspace/pick/candidate-panel";
import { PickProvider } from "@/components/workspace/pick/pick-context";
import { PickToolCard } from "@/components/workspace/pick/pick-tool-card";
import {
  getPickResult,
  getPickResultNotes,
  savePickSelection,
} from "@/core/pick/api";
import {
  pickResultSchema,
  type PickItem,
  type PickResult,
} from "@/core/pick/types";

import projectedPayload from "../../../core/pick/fixtures/backend-model-projection.json";
import obsPayload from "../../../core/pick/fixtures/backend-result-obs.json";

afterEach(() => {
  cleanup();
  rs.mocked(getPickResult).mockReset();
  rs.mocked(getPickResultNotes).mockReset();
  rs.mocked(savePickSelection).mockReset();
});
describe("chat save confirmation", () => {
  it("saves only after one explicit confirmation and preserves the command on retry", async () => {
    rs.mocked(getPickResult).mockResolvedValue({
      id: "r1",
      thread_id: "t1",
      run_id: "run1",
      run_status: "success",
      catalog_batch_id: "b1",
      knowledge_batch_id: null,
      rule_version: "v1",
      ranking_version: "v1",
      conditions: { limit: 1, exclude_selected: true },
      created_at: "2026-09-21T00:00:00Z",
      items: [
        {
          item_id: "i1",
          identity: "source/1",
          title: "待保存的剧",
          theater: "Example",
          language: "en",
          availability: "active",
          reason: "匹配",
          warnings: [],
          evidence: [],
        },
      ],
    });
    rs.mocked(savePickSelection).mockRejectedValueOnce(new Error("offline"));
    rs.mocked(savePickSelection).mockResolvedValueOnce({
      request_id: "receipt",
      saved: [
        { id: "s1", identity: "source/1", status: "created", version: 1 },
      ],
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <PickToolCard
          threadId="t1"
          result={{
            result_id: "r1",
            item_ids: ["i1"],
            requires_confirmation: true,
            note: "下周准备剪辑",
          }}
        />
      </QueryClientProvider>,
    );
    await screen.findByText("待保存的剧");
    expect(screen.getByText(/下周准备剪辑/)).toBeTruthy();
    expect(savePickSelection).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认保存（1）" }));
    await screen.findByText("offline");
    fireEvent.click(screen.getByRole("button", { name: "确认保存（1）" }));
    await waitFor(() => expect(savePickSelection).toHaveBeenCalledTimes(2));
    expect(rs.mocked(savePickSelection).mock.calls[0]?.[0]).toEqual(
      rs.mocked(savePickSelection).mock.calls[1]?.[0],
    );
    expect(rs.mocked(savePickSelection).mock.calls[0]?.[0]).toMatchObject({
      result_id: "r1",
      item_ids: ["i1"],
      note: "下周准备剪辑",
    });
  });
  it("does not present a failed tool response as permanently running", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <PickToolCard result="Tool failed" threadId="t1" />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("alert").textContent).toContain("未完成");
  });
});

describe("a query that produced no candidates", () => {
  const alertText = (result: unknown) => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PickToolCard result={result} threadId="t1" />
      </QueryClientProvider>,
    );
    return screen.getByRole("alert").textContent;
  };

  // 2026-10-10: a feedback refresh failing with schema_changed blocked the query, and the card said only
  // "选剧查询未完成，请检查资料或重试。" The answer check drops the model's relay of the notice, so the
  // card is the one place the reason can reach the user.
  it.each(["schema_changed", "auth_required", "refresh_failed", "unavailable"])(
    "says why when the feedback refresh ended as %s",
    (status) => {
      const notice =
        "飞书反馈字段发生变化，需要核验字段映射；未使用旧数据冒充最新反馈。";
      const text = alertText({
        status,
        notice,
        items: [],
        feedback_refresh_id: "fr_0123456789abcdef0123456789abcdef",
      });
      expect(text).toBe(`未生成候选：${notice}`);
    },
  );

  it("asks to retry later while feedback is still refreshing, without the model-only resume id", () => {
    const text = alertText({
      status: "refresh_pending",
      notice:
        "运营反馈正在刷新，尚未生成候选。刷新完成后可用 feedback_refresh_id 继续同一次请求，不要反复创建新刷新。",
      feedback_refresh_id: "fr_0123456789abcdef0123456789abcdef",
    });
    expect(text).toBe("运营反馈正在刷新，本次还没有生成候选。请稍后重新提问。");
  });

  it("keeps relaying a business refusal verbatim", () => {
    const notice = "剧库里没有这个剧场：reelshort。可选：ReelShort";
    expect(alertText({ status: "rejected", notice })).toBe(notice);
  });

  it("falls back to the generic line when a refusal carries no notice", () => {
    expect(alertText({ status: "schema_changed" })).toBe(
      "选剧查询未完成，请检查资料或重试。",
    );
  });
});

function syncedResult(mirror_version: number | null): PickResult {
  const identity = (key: string) =>
    JSON.stringify([
      "realshort-pick",
      Buffer.from(key, "utf8").toString("base64url"),
      "en",
    ]);
  const item = (
    item_id: string,
    title: string,
    identity: string,
  ): PickItem => ({
    item_id,
    identity,
    title,
    theater: "KalosTV",
    language: "en",
    availability: "unknown",
    reason: "匹配",
    warnings: [],
    evidence: [],
  });
  return {
    id: "r1",
    thread_id: "t1",
    run_id: "run1",
    run_status: "success",
    catalog_batch_id: "b1",
    knowledge_batch_id: null,
    rule_version: "v1",
    ranking_version: "v1",
    conditions: { limit: 3, exclude_selected: true },
    created_at: "2026-09-21T00:00:00Z",
    data_as_of: {
      source_as_of: "2026-09-23T03:00:00.000Z",
      published_at: "2026-09-23T03:17:34Z",
      shared: true,
      mirror_version,
    },
    items: [
      item("i1", "剧一", identity("kalos-1")),
      item("i2", "剧二", identity("shortmax-856049 ")),
      item("i3", "剧三", "source/3"),
    ],
  };
}

function renderConfirmation(itemIds: string[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PickToolCard
        threadId="t1"
        result={{
          result_id: "r1",
          item_ids: itemIds,
          requires_confirmation: true,
        }}
      />
    </QueryClientProvider>,
  );
}

describe("row check links on the confirmation card (P4-1)", () => {
  it("links each requested title that decodes, at the pinned version", async () => {
    rs.mocked(getPickResult).mockResolvedValue(syncedResult(7));
    renderConfirmation(["i2", "i3"]);
    await screen.findByText("剧二");
    expect(screen.getByText("剧三")).toBeTruthy();
    expect(screen.queryByText("剧一")).toBeNull();
    expect(screen.getByText("剧三").parentElement?.textContent).toBe(
      "剧二 在选剧资料核对（新标签页）、剧三",
    );
    const links = screen.getAllByRole("link", { name: /在选剧资料核对/ });
    expect(links.map((link) => link.getAttribute("aria-label"))).toEqual([
      "在选剧资料核对：剧二（新标签页）",
    ]);
    const href = new URL(
      links[0]?.getAttribute("href") ?? "",
      "https://workbench.test",
    );
    expect(href.searchParams.get("row")).toBe("shortmax-856049 ");
    expect(href.searchParams.get("v")).toBe("7");
  });

  it("shows no link without a mirror version", async () => {
    rs.mocked(getPickResult).mockResolvedValue(syncedResult(null));
    renderConfirmation(["i1", "i2"]);
    await screen.findByText("剧一");
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("剧一").parentElement?.textContent).toBe(
      "剧一、剧二",
    );
  });

  it("keeps the query card a summary without per-title links", async () => {
    rs.mocked(getPickResult).mockResolvedValue(syncedResult(7));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <PickToolCard threadId="t1" result={{ result_id: "r1" }} />
      </QueryClientProvider>,
    );
    await screen.findByText("找到 3 部，点击查看依据和保存。");
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
});

// Plan TR-16, rollback matrix F1 x new card: a card whose result carries
// observation conditions, evidence and observations works like any other.
describe("a card with observations (TR-16)", () => {
  const obsResult = pickResultSchema.parse(obsPayload);
  const [item] = obsResult.items;

  it("summarises the query card and confirms a save of an obs candidate", async () => {
    rs.mocked(getPickResult).mockResolvedValue({
      ...obsResult,
      thread_id: "t1",
    });
    rs.mocked(savePickSelection).mockResolvedValue({
      request_id: "receipt",
      saved: [
        { id: "s1", identity: item!.identity, status: "created", version: 1 },
      ],
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <PickToolCard
          threadId="t1"
          result={{
            result_id: obsResult.id,
            item_ids: [item!.item_id],
            requires_confirmation: true,
          }}
        />
      </QueryClientProvider>,
    );
    await screen.findByText(item!.title);
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认保存（1）" }));
    await screen.findByText("保存完成：新存入 1 部");
    expect(rs.mocked(savePickSelection).mock.calls[0]?.[0]).toMatchObject({
      result_id: obsResult.id,
      item_ids: [item!.item_id],
    });
  });
});

// Generated by model_payload and SelectionService.prepare at the fixture's
// recorded backend SHA. The slim JSON is deliberately not a PickResult.
describe("model projection to full candidate and explicit save", () => {
  it.each([
    ["projected_query", "legacy"],
    ["projected_detail", "legacy"],
    ["projected_query", "facts-ref-v1"],
    ["projected_detail", "facts-ref-v1"],
    ["projected_query", "inline-v1"],
    ["projected_detail", "inline-v1"],
  ] as const)(
    "hydrates %s/%s via its authoritative ID and confirms exact IDs/note",
    async (shape, encoding) => {
      const full = pickResultSchema.parse(projectedPayload.full_http_result);
      const prepared = projectedPayload.prepared_selection;
      const original = JSON.parse(projectedPayload[shape]);
      let payload = original;
      if (encoding === "inline-v1") {
        payload = {
          ...(shape === "projected_query"
            ? { id: original.id }
            : { result_id: original.result_id }),
          evidence_encoding: "inline-v1",
          inline_payload: original,
        };
      } else if (encoding === "facts-ref-v1") {
        const items = original.items ?? [original.item];
        const kind = items[0].evidence[0].kind;
        for (const item of items) {
          item.evidence = item.evidence.map(
            (evidence: Record<string, unknown>) => {
              if (evidence.kind !== kind) return evidence;
              const { kind: sharedKind, ...distinct } = evidence;
              expect(sharedKind).toBe(kind);
              return { ...distinct, facts_ref: "0" };
            },
          );
        }
        payload = {
          ...original,
          evidence_encoding: "facts-ref-v1",
          evidence_facts: { "0": { kind } },
        };
      }
      const raw = JSON.stringify(payload);
      expect(raw).not.toContain('"source_ref"');
      expect(raw).not.toContain('"detail_url"');
      expect(pickResultSchema.safeParse(JSON.parse(raw)).success).toBe(false);
      rs.mocked(getPickResult).mockImplementation(async (id) => {
        expect(id).toBe(full.id);
        return full;
      });
      rs.mocked(getPickResultNotes).mockResolvedValue({ kind: "none" });
      rs.mocked(savePickSelection).mockResolvedValue({
        request_id: "synthetic-receipt",
        saved: [
          {
            id: "synthetic-saved",
            identity: full.items[1]!.identity,
            status: "created",
            version: 1,
          },
        ],
      });
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const tree = (payload: unknown) => (
        <QueryClientProvider client={client}>
          <PickProvider>
            <PickToolCard threadId={full.thread_id} result={payload} />
            <CandidatePanel />
          </PickProvider>
        </QueryClientProvider>
      );
      const view = render(tree(raw));
      await screen.findByText("找到 2 部，点击查看依据和保存。");
      expect(getPickResult).toHaveBeenCalledWith(
        full.id,
        expect.any(AbortSignal),
      );
      expect(savePickSelection).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "查看候选" }));
      const cards = await screen.findAllByRole("article");
      expect(cards).toHaveLength(full.items.length);
      for (const [index, card] of cards.entries()) {
        const item = full.items[index]!;
        expect(
          within(card).getByRole("checkbox", { name: `选择${item.title}` }),
        ).toBeTruthy();
        expect(
          within(card).getByTestId("pick-primary-evidence").textContent,
        ).toContain("评级 A · 合成原始证据说明 · 0");
        const details = card.querySelector("details")!;
        fireEvent.click(details.querySelector("summary")!);
        expect(details.textContent).toContain(item.evidence[0]!.source_ref);
        expect(details.textContent).toContain("日期未知");
      }
      expect(savePickSelection).not.toHaveBeenCalled();
      // The real prepare output carries immutable IDs and the user's note;
      // rendering it still must not write until explicit confirmation.
      view.rerender(tree(JSON.stringify(prepared)));
      expect(await screen.findByText(`备注：${prepared.note}`)).toBeTruthy();
      expect(savePickSelection).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "确认保存（1）" }));
      await screen.findByText("保存完成：新存入 1 部");
      expect(savePickSelection).toHaveBeenCalledTimes(1);
      expect(rs.mocked(savePickSelection).mock.calls[0]?.[0]).toEqual({
        request_id: expect.any(String),
        result_id: full.id,
        item_ids: prepared.item_ids,
        note: prepared.note,
      });
      expect(
        screen.getByRole("button", { name: "已保存" }).hasAttribute("disabled"),
      ).toBe(true);
      expect(
        rs.mocked(getPickResult).mock.calls.every(([id]) => id === full.id),
      ).toBe(true);
      client.clear();
    },
  );
});
