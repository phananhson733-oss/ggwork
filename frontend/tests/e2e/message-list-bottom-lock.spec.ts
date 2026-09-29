import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

// Enough groups to switch the message list into its virtualized mode.
const TURNS = 40;

test.describe("Message list bottom lock", () => {
  test("follows new content after the reader returns to the latest message during a resize", async ({
    page,
  }) => {
    const messages = Array.from({ length: TURNS }, (_, turn) => [
      {
        type: "human",
        id: `lock-human-${turn}`,
        content: `Bottom lock question ${turn}`,
      },
      {
        type: "ai",
        id: `lock-ai-${turn}`,
        content: `Bottom lock answer ${turn}`,
      },
    ]).flat();
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Bottom lock",
          updated_at: "2025-06-03T12:00:00Z",
          messages,
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(
      page.getByText(`Bottom lock answer ${TURNS - 1}`, { exact: true }),
    ).toBeVisible({ timeout: 15_000 });

    const conversation = page.getByRole("log");
    const scroller = conversation.locator(":scope > div").first();

    // Leave the live tail the way a reader does.
    await scroller.dispatchEvent("wheel", { deltaY: -1_000 });
    await scroller.evaluate((element) => {
      element.scrollTop = 0;
      element.dispatchEvent(new Event("scroll"));
    });
    await expect(
      conversation.getByText("Bottom lock question 0", { exact: true }),
    ).toBeVisible();

    // Jump back to the latest message in the same frame as a content resize,
    // as happens when virtualized rows are re-measured while the reader
    // scrolls. use-stick-to-bottom ignores scroll events until the resize
    // settles, so on its own it never re-engages the lock here.
    await scroller.evaluate(
      (element) =>
        new Promise<void>((resolve) => {
          const content = element.firstElementChild as HTMLElement;
          const bottomSpacer = content.lastElementChild as HTMLElement;
          let sawInitialSize = false;
          const observer = new ResizeObserver(() => {
            if (!sawInitialSize) {
              sawInitialSize = true;
              requestAnimationFrame(() => {
                bottomSpacer.style.height = "0px";
              });
              return;
            }
            observer.disconnect();
            element.scrollTop = element.scrollHeight;
            element.dispatchEvent(new Event("scroll"));
            resolve();
          });
          observer.observe(content);
        }),
    );

    const distanceFromBottom = () =>
      scroller.evaluate(
        (element) =>
          element.scrollHeight - element.clientHeight - element.scrollTop,
      );
    await expect.poll(distanceFromBottom).toBeLessThanOrEqual(2);
    // Let the pending resize settle before new content arrives.
    await page.waitForTimeout(100);

    // New content below the reader (a streamed token, a new message) must be
    // followed now that they are back on the latest message.
    await scroller.evaluate((element) => {
      const content = element.firstElementChild as HTMLElement;
      const bottomSpacer = content.lastElementChild as HTMLElement;
      bottomSpacer.style.height = "600px";
    });
    await expect.poll(distanceFromBottom).toBeLessThanOrEqual(2);
  });
});
