import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
import { OwnedPostReview } from "@/components/workspace/pick/post-review";
import { fetch as fetcher } from "@/core/api/fetcher";

import fixture from "../../../core/pick/fixtures/completion-v1.json";
afterEach(() => {
  cleanup();
  rs.mocked(fetcher).mockReset();
});
it("shows measured zero and absent metrics separately with incomplete observation window", async () => {
  const post = {
    ...fixture.review.items[0]!,
    views: 0,
    likes: null,
    comments: null,
    link: null,
    observation_days: 3,
    requested_observation_days: 7,
    window_complete: false,
  };
  rs.mocked(fetcher).mockResolvedValue(
    Response.json({ ...fixture.review, items: [post] }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OwnedPostReview ownerId="owner-a" />
    </QueryClientProvider>,
  );
  await screen.findByText("播放：0");
  expect(screen.getByText("点赞：未提供/未更新")).toBeTruthy();
  expect(screen.getByText("已观察 3 天 / 目标 7 天（窗口不足）")).toBeTruthy();
  expect(screen.getByText("未关联计划")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "人工关联计划行" }));
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "确认双方证据并关联",
    }).disabled,
  ).toBe(true);
});
it("starts a distinct current query from the post while preserving historical feedback", async () => {
  rs.mocked(fetcher).mockImplementation(async (url) =>
    Response.json(
      typeof url === "string" && url.endsWith("/query")
        ? fixture.response
        : fixture.review,
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <OwnedPostReview ownerId="owner-a" />
    </QueryClientProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "继续选剧（发起新查询）" }),
  );
  await screen.findByRole("region", { name: "复盘后的新查询" });
  await screen.findByText(/完整匹配/);
  const call = rs
    .mocked(fetcher)
    .mock.calls.find(
      ([url]) => typeof url === "string" && url.endsWith("/query"),
    );
  expect(JSON.parse(call?.[1]?.body as string)).toMatchObject({
    domain: "candidates",
    scope: "candidate_pool",
    pin: null,
    exclude_selected: true,
  });
  expect(screen.getByText(/原反馈版本.*保留/)).toBeTruthy();
  client.clear();
});
