import { expect, it } from "@rstest/core";

import { firstCommitBarrier } from "../../e2e-pick/support/first-commit";

it("an existing alert cannot release the barrier before committed receipt and response abort", async () => {
  const events: string[] = ["existing-alert"];
  let releaseReceipt!: () => void;
  let releaseAbort!: () => void;
  const receiptReady = new Promise<void>((resolve) => {
    releaseReceipt = resolve;
  });
  const abortDone = new Promise<void>((resolve) => {
    releaseAbort = resolve;
  });
  const barrier = firstCommitBarrier<{ request_id: string }>();
  const capture = barrier.capture(async () => {
    await receiptReady;
    events.push("receipt-captured");
    await abortDone;
    events.push("response-aborted");
    return { request_id: "same-request" };
  });
  const read = barrier.finished.then((receipt) => {
    events.push("read-selections");
    return receipt;
  });
  await Promise.resolve();
  expect(events).toEqual(["existing-alert"]);
  releaseReceipt();
  await Promise.resolve();
  expect(events).not.toContain("read-selections");
  releaseAbort();
  await capture;
  expect(await read).toEqual({ request_id: "same-request" });
  expect(events).toEqual([
    "existing-alert",
    "receipt-captured",
    "response-aborted",
    "read-selections",
  ]);
});

it("fails with a sanitized message without copying credential-bearing transport errors", async () => {
  const barrier = firstCommitBarrier();
  await barrier.capture(async () => {
    throw new Error("route.fetch Cookie: private-session");
  });
  await expect(barrier.finished).rejects.toThrow(
    "First committed save response was not captured",
  );
});
