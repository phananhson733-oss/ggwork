import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { CandidateView } from "@/components/workspace/pick/candidate-view";
import {
  FeedbackEvidence,
  FeedbackSummary,
} from "@/components/workspace/pick/feedback-evidence";
import { FeedbackStatus } from "@/components/workspace/pick/feedback-status";
import { feedbackReplySchema } from "@/core/pick/feedback-schema";
import { pickResultSchema } from "@/core/pick/types";

import payload from "../../../core/pick/fixtures/backend-result.json";
const state = rs.hoisted(() => ({
  user: "u1" as string | null,
  get: rs.fn(),
  refresh: rs.fn(),
}));
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: state.user ? { id: state.user } : null }),
}));
rs.mock("@/core/pick/feedback-api", () => ({
  getFeedbackStatus: state.get,
  refreshFeedback: state.refresh,
}));
const idle = {
  enabled: true,
  configured: true,
  current: null,
  running: null,
  last_run: null,
  last_verified_at: null,
  lease_expired: false,
};
const coverage = {
  dramas: 1,
  posts: 2,
  measured_posts: 1,
  missing_posts: 1,
  unmatched_posts: 0,
};
const reply = () =>
  feedbackReplySchema.parse({
    status: "ok",
    contract_version: "feedback-v1",
    notice: "",
    feedback_version_id: "v1",
    scan_started_at: "2026-10-01T00:00:00Z",
    scan_completed_at: "2026-10-01T00:02:00Z",
    last_verified_at: null,
    freshness: "historical",
    source_quality: "partial",
    query_scope: {},
    items: [
      {
        key: "a",
        evidence_kind: "cohort",
        metrics: { views_total: "0", likes_total: null },
        revenue: [],
        coverage,
        evidence_refs: [],
        warnings: ["revenue_scope_unavailable"],
      },
    ],
    coverage,
    warnings: [],
    total_groups: 1,
    has_more: false,
  });
beforeEach(() => {
  state.user = "u1";
  state.get.mockReset();
  state.refresh.mockReset();
  state.get.mockResolvedValue(idle);
});
afterEach(() => {
  cleanup();
  rs.useRealTimers();
});
describe("feedback display", () => {
  it("shows frozen time, partial coverage, 0 versus unknown and excluded finance", () => {
    const data = reply();
    render(
      <>
        <FeedbackSummary feedback={data} />
        <FeedbackEvidence item={data.items[0]} />
      </>,
    );
    expect(screen.getByText("历史候选依据")).toBeTruthy();
    expect(
      screen.getByText("读取飞书时间：2026/10/1 08:02:00 北京时间"),
    ).toBeTruthy();
    expect(screen.getByText("累计播放：0")).toBeTruthy();
    expect(screen.getByText("点赞：未知")).toBeTruthy();
    expect(screen.getByText(/当前筛选无法准确归因收益/)).toBeTruthy();
    expect(screen.getByText(/同类经验/)).toBeTruthy();
  });
  it("keeps revenue lanes/currencies/bases separate and null not zero", () => {
    const data = reply();
    const item = data.items[0]!;
    item.revenue = [
      {
        source_lane: "cps_auto",
        grain: "drama",
        currency: "USD",
        metric: "commission",
        amount: "0.00",
        amount_basis: "settled",
        records: 1,
        missing_records: 0,
      },
      {
        source_lane: "cps_manual",
        grain: "drama",
        currency: "EUR",
        metric: "order_amount",
        amount: null,
        amount_basis: null,
        records: 1,
        missing_records: 1,
      },
    ];
    item.evidence_refs = [
      {
        table_id: "tbl/a&evil=x",
        record_id: "rec#x",
        field_ids: [],
        metric_as_of: null,
        grain: "drama",
        attribution: "confirmed",
        source_lane: "cps_auto",
      },
    ];
    render(<FeedbackEvidence item={item} />);
    expect(screen.getByText(/分成收益：0.00 USD/)).toBeTruthy();
    expect(screen.getByText(/用户订单金额：未知 EUR/)).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe(
      "https://gengrowth.feishu.cn/base/OtnsbnRnwaLmnVsJByscTkFMntd?table=tbl%2Fa%26evil%3Dx&record=rec%23x",
    );
  });
  it("does not poll idle, disabled or unconfigured sources", async () => {
    state.get.mockResolvedValue({ ...idle, enabled: false });
    render(<FeedbackStatus />);
    await screen.findByText("反馈同步未启用");
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "刷新飞书反馈" })
        .disabled,
    ).toBe(true);
    expect(state.get).toHaveBeenCalledTimes(1);
  });
  it("keeps auth link after failed refresh and performs no blind refresh retry", async () => {
    state.refresh.mockResolvedValue({
      status: "auth_required",
      run_id: null,
      error_code: "auth_required",
    });
    render(<FeedbackStatus />);
    fireEvent.click(
      await screen.findByRole("button", { name: "刷新飞书反馈" }),
    );
    await screen.findByRole("link", { name: "连接飞书" });
    expect(state.refresh).toHaveBeenCalledTimes(1);
  });
  it("clears a previous auth failure when a newer run starts and succeeds", async () => {
    const failed = {
      id: "failed-1",
      status: "failed",
      trigger: "manual",
      started_at: "2026-10-01T00:00:00Z",
      finished_at: "2026-10-01T00:00:10Z",
      version_id: null,
      error_code: "auth_required",
    };
    const running = {
      ...failed,
      id: "reconnected-2",
      status: "running",
      trigger: "cron",
      started_at: "2026-10-01T00:01:00Z",
      finished_at: null,
      error_code: null,
    };
    state.get
      .mockResolvedValueOnce({ ...idle, last_run: failed })
      .mockResolvedValueOnce({ ...idle, last_run: running, running })
      .mockResolvedValue({
        ...idle,
        last_run: {
          ...running,
          status: "success",
          finished_at: "2026-10-01T00:02:00Z",
        },
      });
    // A racing manual request may fail auth just before another client reconnects.
    state.refresh.mockResolvedValue({
      status: "auth_required",
      run_id: null,
      error_code: "auth_required",
    });
    render(<FeedbackStatus />);
    await screen.findByRole("link", { name: "连接飞书" });
    fireEvent.click(screen.getByRole("button", { name: "刷新飞书反馈" }));
    await screen.findByText("反馈刷新中");
    expect(screen.queryByRole("link", { name: "连接飞书" })).toBeNull();
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(3), {
      timeout: 3000,
    });
    expect(screen.queryByRole("link", { name: "连接飞书" })).toBeNull();
    expect(screen.queryByText("需要连接飞书")).toBeNull();
  });
  it("clears old private data and aborts read on user change", async () => {
    let first: AbortSignal | undefined;
    state.get.mockImplementationOnce((signal: AbortSignal) => {
      first = signal;
      return new Promise(() => {
        /* Keep this read in flight until the user switches. */
      });
    });
    const rendered = render(<FeedbackStatus />);
    state.user = "u2";
    rendered.rerender(<FeedbackStatus />);
    await screen.findByText("尚无反馈版本");
    expect(first?.aborted).toBe(true);
    expect(state.get).toHaveBeenCalledTimes(2);
  });
  it("shows access errors without retaining a previous private snapshot", async () => {
    state.get.mockRejectedValue(new Error("仅反馈来源所有者可查看或刷新"));
    render(<FeedbackStatus />);
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "刷新飞书反馈" })).toBeNull();
  });
  it("polls a running job then stops after completion and cancels on unmount", async () => {
    const run = {
      id: "run1",
      status: "running",
      trigger: "manual",
      started_at: "2026-10-01T00:00:00Z",
      finished_at: null,
      version_id: null,
      error_code: null,
    };
    state.get
      .mockResolvedValueOnce({ ...idle, running: run })
      .mockResolvedValue(idle);
    const rendered = render(<FeedbackStatus />);
    await screen.findByText("反馈刷新中");
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2), {
      timeout: 3000,
    });
    const signal = state.get.mock.calls[1]?.[0] as AbortSignal;
    rendered.unmount();
    expect(signal.aborted).toBe(true);
  });
});

it("matches feedback by identity without renumbering or reordering candidates", () => {
  const result = pickResultSchema.parse(payload);
  const original = result.items[0]!;
  result.items = [
    { ...original, item_id: "first", identity: "identity-a", title: "First" },
    { ...original, item_id: "second", identity: "identity-b", title: "Second" },
  ];
  const data = reply();
  const item = data.items[0]!;
  data.items = [
    { ...item, key: "identity-b", metrics: { views_total: "222" } },
    { ...item, key: "identity-a", metrics: { views_total: "111" } },
  ];
  const view = render(
    <CandidateView
      result={result}
      selected={[]}
      onToggle={rs.fn()}
      onSave={rs.fn()}
      busy={false}
      notes={{ kind: "notes", notes: { item_facts: {}, feedback: data } }}
    />,
  );
  const cards = view.container.querySelectorAll("article");
  expect(cards[0]?.textContent).toContain("1. First");
  expect(cards[0]?.textContent).toContain("累计播放：111");
  expect(cards[1]?.textContent).toContain("2. Second");
  expect(cards[1]?.textContent).toContain("累计播放：222");
  expect(screen.getAllByLabelText("运营反馈版本")).toHaveLength(1);
});

const run = {
  id: "new-run",
  status: "running",
  trigger: "manual",
  started_at: "2026-10-01T18:01:02.123456+00:00",
  finished_at: null,
  version_id: null,
  error_code: null,
};
const oldRun = { ...run, id: "old-run", status: "success" };
async function startPendingRefresh() {
  state.refresh.mockResolvedValue({
    status: "refresh_pending",
    run_id: null,
    error_code: null,
  });
  await act(async () => {
    render(<FeedbackStatus />);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "刷新飞书反馈" }));
  });
}
describe("pending feedback admission", () => {
  beforeEach(() => {
    rs.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  });
  it.each([null, oldRun])(
    "keeps polling an unchanged pre-claim snapshot: %s",
    async (lastRun) => {
      state.get
        .mockResolvedValueOnce({ ...idle, last_run: lastRun })
        .mockResolvedValueOnce(idle)
        .mockResolvedValueOnce({ ...idle, last_run: lastRun })
        .mockResolvedValueOnce({ ...idle, running: run, last_run: run })
        .mockResolvedValue({
          ...idle,
          last_run: {
            ...run,
            status: "success",
            finished_at: "2026-10-01T18:02:03Z",
          },
        });
      await startPendingRefresh();
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "反馈刷新中…" })
          .disabled,
      ).toBe(true);
      await act(() => rs.advanceTimersByTimeAsync(2000));
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "反馈刷新中…" })
          .disabled,
      ).toBe(true);
      await act(() => rs.advanceTimersByTimeAsync(2000));
      expect(state.get).toHaveBeenCalledTimes(4);
      await act(() => rs.advanceTimersByTimeAsync(2000));
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "刷新飞书反馈" })
          .disabled,
      ).toBe(false);
      expect(screen.queryByText("反馈刷新中")).toBeNull();
      await act(() => rs.advanceTimersByTimeAsync(60_000));
      expect(state.get).toHaveBeenCalledTimes(5);
    },
  );
  it("keeps observing a committed last_run until its running lease is visible", async () => {
    state.get
      .mockResolvedValueOnce(idle)
      .mockResolvedValueOnce({ ...idle, last_run: run })
      .mockResolvedValueOnce({ ...idle, running: run, last_run: run })
      .mockResolvedValue({ ...idle, last_run: { ...run, status: "success" } });
    await startPendingRefresh();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "反馈刷新中…" })
        .disabled,
    ).toBe(true);
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(state.get).toHaveBeenCalledTimes(3);
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "反馈刷新中…" })
        .disabled,
    ).toBe(true);
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "刷新飞书反馈" })
        .disabled,
    ).toBe(false);
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(4);
    expect(screen.queryByText(/尚未确认刷新任务已开始/)).toBeNull();
  });
  it("bounds observation when last_run remains running without a visible lease", async () => {
    state.get
      .mockResolvedValueOnce(idle)
      .mockResolvedValue({ ...idle, last_run: run });
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(state.get).toHaveBeenCalledTimes(3);
    await act(() => rs.advanceTimersByTimeAsync(58_000));
    expect(screen.getByText(/尚未确认刷新任务已开始/)).toBeTruthy();
    const count = state.get.mock.calls.length;
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(count);
  });
  it("stops admission observation when the new run has explicitly expired", async () => {
    state.get
      .mockResolvedValueOnce(idle)
      .mockResolvedValue({ ...idle, last_run: run, lease_expired: true });
    await startPendingRefresh();
    expect(screen.getByText("同步任务已过期，需要重新刷新")).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "刷新飞书反馈" })
        .disabled,
    ).toBe(false);
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/尚未确认刷新任务已开始/)).toBeNull();
  });
  it("observes a failure completed before the running snapshot was visible", async () => {
    state.get
      .mockResolvedValueOnce(idle)
      .mockResolvedValueOnce(idle)
      .mockResolvedValue({
        ...idle,
        last_run: { ...run, status: "failed", error_code: "capacity" },
      });
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(screen.getByText(/本次读取超过容量限制/)).toBeTruthy();
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(3);
  });
  it("bounds unconfirmed observation without claiming success, failure or cancelling the worker", async () => {
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(
      screen.getByText(/尚未确认刷新任务已开始.*后台可能仍在处理/),
    ).toBeTruthy();
    expect(screen.queryByText(/最近反馈刷新失败/)).toBeNull();
    const count = state.get.mock.calls.length;
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(count);
    expect(state.refresh).toHaveBeenCalledTimes(1);
    expect((state.refresh.mock.calls[0]?.[0] as AbortSignal).aborted).toBe(
      false,
    );
    expect(screen.getByRole("button", { name: "重新读取状态" })).toBeTruthy();
  });
  it.each(["running", "success"])(
    "clears an unconfirmed timeout when a manual read observes %s",
    async (status) => {
      await startPendingRefresh();
      await act(() => rs.advanceTimersByTimeAsync(60_000));
      expect(screen.getByText(/尚未确认刷新任务已开始/)).toBeTruthy();
      const nextRun = { ...run, status };
      state.get.mockResolvedValue({
        ...idle,
        last_run: nextRun,
        running: status === "running" ? nextRun : null,
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
      });
      expect(screen.queryByText(/尚未确认刷新任务已开始/)).toBeNull();
      expect(state.refresh).toHaveBeenCalledTimes(1);
      const count = state.get.mock.calls.length;
      await act(() => rs.advanceTimersByTimeAsync(2000));
      expect(state.get).toHaveBeenCalledTimes(
        count + (status === "running" ? 1 : 0),
      );
    },
  );
  it("resumes bounded observation after a manual recheck sees a split claim frame", async () => {
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    state.get
      .mockResolvedValueOnce({ ...idle, last_run: run })
      .mockResolvedValueOnce({ ...idle, last_run: run, running: run })
      .mockResolvedValue({ ...idle, last_run: { ...run, status: "success" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
    });
    expect(screen.queryByText(/尚未确认刷新任务已开始/)).toBeNull();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "反馈刷新中…" })
        .disabled,
    ).toBe(true);
    const count = state.get.mock.calls.length;
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(state.get).toHaveBeenCalledTimes(count + 1);
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "刷新飞书反馈" })
        .disabled,
    ).toBe(false);
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(count + 2);
    expect(state.refresh).toHaveBeenCalledTimes(1);
    expect((state.refresh.mock.calls[0]?.[0] as AbortSignal).aborted).toBe(
      false,
    );
    expect(screen.queryByText(/尚未确认刷新任务已开始/)).toBeNull();
  });
  it("does not renew the manual observation deadline on automatic split-frame reads", async () => {
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    state.get.mockResolvedValue({ ...idle, last_run: run });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
    });
    expect(screen.queryByText(/尚未确认刷新任务已开始/)).toBeNull();
    await act(() => rs.advanceTimersByTimeAsync(58_000));
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "反馈刷新中…" })
        .disabled,
    ).toBe(true);
    await act(() => rs.advanceTimersByTimeAsync(2000));
    expect(screen.getByText(/尚未确认刷新任务已开始/)).toBeTruthy();
    const count = state.get.mock.calls.length;
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(count);
    expect(state.refresh).toHaveBeenCalledTimes(1);
    expect((state.refresh.mock.calls[0]?.[0] as AbortSignal).aborted).toBe(
      false,
    );
  });
  it("keeps an empty manual status recheck unconfirmed without restarting observation", async () => {
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
    });
    expect(screen.getByText(/尚未确认刷新任务已开始/)).toBeTruthy();
    const count = state.get.mock.calls.length;
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(state.get).toHaveBeenCalledTimes(count);
  });
  it.each(["running", "empty", "error"])(
    "ignores an obsolete %s read after a manual terminal confirmation",
    async (oldResult) => {
      let resolveOld!: (value: unknown) => void;
      let rejectOld!: (reason: Error) => void;
      state.get
        .mockResolvedValueOnce(idle)
        .mockImplementationOnce(
          () =>
            new Promise((resolve, reject) => {
              resolveOld = resolve;
              rejectOld = reject;
            }),
        )
        .mockResolvedValue({
          ...idle,
          last_run: {
            ...run,
            status: "success",
            finished_at: "2026-10-01T18:02:03Z",
          },
        });
      await startPendingRefresh();
      await act(() => rs.advanceTimersByTimeAsync(60_000));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "重新读取状态" }));
      });
      const completedText =
        "最近尝试：2026/10/2 02:01:02 北京时间 · 完成：2026/10/2 02:02:03 北京时间";
      expect(screen.getByText(completedText)).toBeTruthy();
      await act(async () => {
        if (oldResult === "error") rejectOld(new Error("过期请求失败"));
        else
          resolveOld(
            oldResult === "empty"
              ? idle
              : { ...idle, running: run, last_run: run },
          );
      });
      expect(screen.getByText(completedText)).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "刷新飞书反馈" })
          .disabled,
      ).toBe(false);
      await act(() => rs.advanceTimersByTimeAsync(60_000));
      expect(state.get).toHaveBeenCalledTimes(3);
      expect(state.refresh).toHaveBeenCalledTimes(1);
      expect((state.refresh.mock.calls[0]?.[0] as AbortSignal).aborted).toBe(
        false,
      );
    },
  );
  it("bounds observation even when a status read is still in flight", async () => {
    state.get.mockResolvedValueOnce(idle).mockImplementationOnce(
      () =>
        new Promise(() => {
          // Keep the status read unresolved beyond the admission observation window.
        }),
    );
    await startPendingRefresh();
    await act(() => rs.advanceTimersByTimeAsync(60_000));
    expect(screen.getByText(/尚未确认刷新任务已开始/)).toBeTruthy();
    expect(state.get).toHaveBeenCalledTimes(2);
    expect((state.refresh.mock.calls[0]?.[0] as AbortSignal).aborted).toBe(
      false,
    );
  });
  it.each(["unmount", "user change"])(
    "cleans up pending observation on %s",
    async (action) => {
      state.refresh.mockResolvedValue({
        status: "refresh_pending",
        run_id: null,
        error_code: null,
      });
      let view!: ReturnType<typeof render>;
      await act(async () => {
        view = render(<FeedbackStatus />);
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "刷新飞书反馈" }));
      });
      const signal = state.refresh.mock.calls[0]?.[0] as AbortSignal;
      await act(async () => {
        if (action === "unmount") view.unmount();
        else {
          state.user = "u2";
          view.rerender(<FeedbackStatus />);
        }
      });
      const count = state.get.mock.calls.length;
      await act(() => rs.advanceTimersByTimeAsync(120_000));
      expect(signal.aborted).toBe(true);
      expect(state.get).toHaveBeenCalledTimes(count);
      expect(screen.queryByText(/尚未确认刷新任务/)).toBeNull();
    },
  );
});
it.each([
  ["auth_required", "需要连接飞书"],
  ["schema_changed", "飞书字段发生变化"],
  ["source_changed", "飞书来源在读取期间发生变化"],
  ["incomplete", "源表读取不完整"],
  ["capacity", "本次读取超过容量限制"],
  ["lease_expired", "同步任务已过期"],
  ["cancelled", "同步任务已中断"],
  ["unavailable", "反馈来源暂不可用"],
  ["refresh_failed", "反馈刷新失败"],
])(
  "explains persisted %s without a manual refresh",
  async (errorCode, text) => {
    state.get.mockResolvedValue({
      ...idle,
      last_run: { ...run, status: "failed", error_code: errorCode },
    });
    render(<FeedbackStatus />);
    await screen.findByText(new RegExp(text + ".*(请|需要)"));
    expect(screen.queryByText(errorCode)).toBeNull();
    expect(state.refresh).not.toHaveBeenCalled();
  },
);
it("shows order counts as counts and preserves monetary precision", () => {
  const item = reply().items[0]!;
  const revenue = {
    source_lane: "cps_auto" as const,
    grain: "drama" as const,
    currency: "USD",
    amount_basis: "settled",
    records: 1,
    missing_records: 0,
  };
  item.revenue = [
    { ...revenue, metric: "orders", amount: "12" },
    { ...revenue, metric: "orders", amount: null, missing_records: 1 },
    { ...revenue, metric: "commission", amount: "123456789012345678.123456" },
  ];
  render(<FeedbackEvidence item={item} />);
  expect(screen.getByText(/订单数：12 单/)).toBeTruthy();
  expect(screen.getByText(/订单数：未知/)).toBeTruthy();
  expect(screen.queryByText(/12 USD/)).toBeNull();
  expect(screen.getByText(/123456789012345678.123456 USD/)).toBeTruthy();
});
it("localizes uncertain publication identity in summary and evidence", () => {
  const data = reply();
  data.warnings = ["unknown_publication_identity"];
  data.items[0]!.warnings = ["unknown_publication_identity"];
  render(
    <>
      <FeedbackSummary feedback={data} />
      <FeedbackEvidence item={data.items[0]} />
    </>,
  );
  expect(
    screen.getAllByText("部分源记录的帖子身份不确定，已排除在去重统计之外"),
  ).toHaveLength(2);
});
it("formats microsecond UTC timestamps in Beijing time and preserves source calendar dates", async () => {
  const timestamp = "2026-10-01T18:01:02.123456+00:00";
  const data = reply();
  data.scan_completed_at = timestamp;
  data.last_verified_at = timestamp;
  const item = data.items[0]!;
  item.metrics.metric_as_of_min = "2026-10-01";
  item.metrics.metric_as_of_max = timestamp;
  item.evidence_refs = ["2026-10-01", timestamp].map((date, i) => ({
    table_id: "table",
    record_id: `rec${i}`,
    field_ids: [],
    metric_as_of: date,
    grain: "post",
    attribution: "confirmed",
    source_lane: "post",
  }));
  state.get.mockResolvedValue({
    ...idle,
    current: {
      id: "v1",
      scan_started_at: timestamp,
      scan_completed_at: timestamp,
      source_quality: "complete",
      tables: [],
    },
    last_verified_at: timestamp,
    last_run: { ...run, status: "success", finished_at: timestamp },
  });
  render(
    <>
      <FeedbackStatus />
      <FeedbackSummary feedback={data} />
      <FeedbackEvidence item={item} />
    </>,
  );
  await screen.findByText("已有反馈版本");
  expect(
    screen.getAllByText("读取飞书时间：2026/10/2 02:01:02 北京时间"),
  ).toHaveLength(2);
  expect(
    screen.getAllByText("最近核验：2026/10/2 02:01:02 北京时间"),
  ).toHaveLength(2);
  expect(
    screen.getByText(
      "最近尝试：2026/10/2 02:01:02 北京时间 · 完成：2026/10/2 02:01:02 北京时间",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText("指标截至：2026-10-01 至 2026/10/2 02:01:02 北京时间"),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "post · rec0 · 指标日期 2026-10-01" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", {
      name: "post · rec1 · 指标日期 2026/10/2 02:01:02 北京时间",
    }),
  ).toBeTruthy();
});

it("retains a just-returned authorization failure through an old status snapshot", async () => {
  state.get.mockResolvedValue({ ...idle, last_run: oldRun });
  state.refresh.mockResolvedValue({
    status: "auth_required",
    run_id: null,
    error_code: "auth_required",
  });
  render(<FeedbackStatus />);
  fireEvent.click(await screen.findByRole("button", { name: "刷新飞书反馈" }));
  await screen.findByRole("link", { name: "连接飞书" });
  expect(screen.getByText("需要连接飞书")).toBeTruthy();
  expect(state.get).toHaveBeenCalledTimes(2);
});

it("renders mapping diagnostics as safe Chinese text at the supplied scope", () => {
  const data = reply();
  data.warnings = [
    "master_identity_invalid",
    "master_identity_unconfirmed",
    "master_identity_conflict",
    "master_identity_incompatible",
    "external_mapping_scope_unknown",
    "external_mapping_unconfirmed",
    "external_mapping_inactive",
    "external_mapping_conflict",
    "external_mapping_target_invalid",
    "revenue_direct_link_invalid",
    "revenue_grain_unconfirmed",
  ];
  data.items[0]!.warnings = ["external_mapping_conflict"];
  const view = render(
    <>
      <FeedbackSummary feedback={data} />
      <FeedbackEvidence item={data.items[0]} />
    </>,
  );
  expect(
    screen.getAllByText(
      "部分外部 ID 映射重复、范围重叠或关联冲突，相关收益未归因",
    ),
  ).toHaveLength(2);
  expect(
    screen.getAllByText("部分外部 ID 的适用范围不明，相关收益未归因"),
  ).toHaveLength(1);
  expect(
    screen.getByText("部分收益记录未明确为单剧粒度，未分配到剧集"),
  ).toBeTruthy();
  for (const code of data.warnings)
    expect(view.container.textContent).not.toContain(code);
  expect(view.container.querySelectorAll("a, script")).toHaveLength(0);
});
