import { afterEach, describe, expect, it } from "@rstest/core";
import { act, cleanup, render } from "@testing-library/react";
import { createRef } from "react";
import { StickToBottom, type StickToBottomContext } from "use-stick-to-bottom";

import { useBottomLockRecovery } from "@/components/workspace/messages/use-bottom-lock-recovery";

const SCROLL_HEIGHT = 2_000;
const CLIENT_HEIGHT = 500;
const MAX_SCROLL_TOP = SCROLL_HEIGHT - CLIENT_HEIGHT;

function RecoveryProbe() {
  useBottomLockRecovery();
  return null;
}

function renderConversation() {
  const contextRef = createRef<StickToBottomContext>();
  render(
    <StickToBottom contextRef={contextRef} initial="instant" resize="instant">
      <StickToBottom.Content>
        <p>Latest answer</p>
        <RecoveryProbe />
      </StickToBottom.Content>
    </StickToBottom>,
  );
  const context = contextRef.current;
  const scroller = context?.scrollRef.current;
  if (!context || !scroller) {
    throw new Error("StickToBottom did not mount its scroll container");
  }
  // happy-dom computes no default `overflow: visible`, so the library never
  // applies its own `overflow: auto`, which its wheel escape looks for.
  scroller.style.overflow = "auto";
  // happy-dom has no layout, so give the viewport a scrollable geometry.
  const geometry = { scrollHeight: SCROLL_HEIGHT };
  Object.defineProperty(scroller, "scrollHeight", {
    configurable: true,
    get: () => geometry.scrollHeight,
  });
  Object.defineProperty(scroller, "clientHeight", {
    configurable: true,
    get: () => CLIENT_HEIGHT,
  });
  return { context, geometry, scroller };
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function keyDown(init: KeyboardEventInit) {
  return new KeyboardEvent("keydown", { ...init, bubbles: true });
}

// happy-dom's TouchEvent needs Touch objects; the hook only reads clientY.
function touchEvent(type: "touchstart" | "touchmove", clientY: number) {
  const event = new Event(type, { bubbles: true });
  Object.defineProperty(event, "touches", { value: [{ clientY }] });
  return event;
}

async function scrollTo(scroller: HTMLElement, top: number) {
  await act(async () => {
    scroller.scrollTop = top;
    scroller.dispatchEvent(new Event("scroll"));
    await wait(5);
  });
}

async function leaveLiveTail(
  context: StickToBottomContext,
  scroller: HTMLElement,
) {
  await act(async () => {
    context.stopScroll();
  });
  await scrollTo(scroller, 0);
  expect(context.state.isAtBottom).toBe(false);
}

afterEach(cleanup);

describe("useBottomLockRecovery", () => {
  it("re-engages the lock when a return to the bottom lands during a resize", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    // A virtualized row was just re-measured: use-stick-to-bottom ignores
    // every scroll event until this settles, including the reader's jump back.
    context.state.resizeDifference = -56;
    await scrollTo(scroller, MAX_SCROLL_TOP);
    context.state.resizeDifference = 0;
    expect(context.state.isAtBottom).toBe(false);

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(true);
  });

  it("keeps a pending recovery through a repeated scroll event that did not move", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    context.state.resizeDifference = -56;
    await scrollTo(scroller, MAX_SCROLL_TOP);
    // The browser still dispatches its own scroll event after a script both
    // set the offset and dispatched one; nothing moved in between.
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll"));
      await wait(5);
    });
    context.state.resizeDifference = 0;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(true);
  });

  it("keeps the lock released while the reader stays above the bottom", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    context.state.resizeDifference = -56;
    await scrollTo(scroller, MAX_SCROLL_TOP - 40);
    context.state.resizeDifference = 0;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
  });

  it("still re-locks when streamed content grows before the recovery runs", async () => {
    const { context, geometry, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    context.state.resizeDifference = -56;
    await scrollTo(scroller, MAX_SCROLL_TOP);
    context.state.resizeDifference = 0;
    // Tokens keep arriving below the reader; growth emits no scroll event.
    geometry.scrollHeight = SCROLL_HEIGHT + 120;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(true);
  });

  it("does not re-lock once the reader has moved up again", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    context.state.resizeDifference = -56;
    await scrollTo(scroller, MAX_SCROLL_TOP);
    context.state.resizeDifference = 0;
    // A keyboard or scrollbar escape whose scroll event has not been
    // dispatched yet: only the offset has moved.
    scroller.scrollTop = MAX_SCROLL_TOP - 200;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
  });

  it("never undoes an escape that follows a scroll at the bottom", async () => {
    const { context, scroller } = renderConversation();
    expect(context.state.isAtBottom).toBe(true);

    // A scroll while the lock holds (e.g. its own follow animation), then the
    // reader escapes before any further scroll event arrives.
    await scrollTo(scroller, MAX_SCROLL_TOP);
    await act(async () => {
      context.stopScroll();
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
  });

  it("cancels a pending recovery when the reader wheels up", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    // The library drops the return to the bottom, so a recovery is pending.
    context.state.resizeDifference = -56;
    await act(async () => {
      scroller.scrollTop = MAX_SCROLL_TOP;
      scroller.dispatchEvent(new Event("scroll"));
      // use-stick-to-bottom escapes on an upward wheel before the browser
      // emits the scroll events for it.
      scroller.dispatchEvent(new WheelEvent("wheel", { deltaY: -100 }));
      await wait(5);
    });
    context.state.resizeDifference = 0;
    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
  });

  it("does not pull a reader who nudges up within the tolerance back down", async () => {
    const { context, geometry, scroller } = renderConversation();
    // The lock parks one pixel above the maximum offset.
    await scrollTo(scroller, MAX_SCROLL_TOP - 1);
    expect(context.state.isAtBottom).toBe(true);

    // A one-pixel trackpad nudge: use-stick-to-bottom releases the lock on
    // the upward wheel, and the scroll it causes still ends within the
    // tolerance of the end.
    await act(async () => {
      scroller.dispatchEvent(
        new WheelEvent("wheel", { deltaY: -1, bubbles: true }),
      );
    });
    expect(context.state.isAtBottom).toBe(false);
    await scrollTo(scroller, MAX_SCROLL_TOP - 2);
    // Tokens keep streaming in below the reader before any recovery runs.
    geometry.scrollHeight = SCROLL_HEIGHT + 120;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
    expect(scroller.scrollTop).toBe(MAX_SCROLL_TOP - 2);
  });

  it("does not read a nudge up coalesced with the library's follow as a move down", async () => {
    const { context, geometry, scroller } = renderConversation();
    // The last dispatched scroll event saw the parked position.
    await scrollTo(scroller, MAX_SCROLL_TOP - 1);
    expect(context.state.isAtBottom).toBe(true);

    // Streamed content grows and the library follows it by writing the
    // offset; the browser has not dispatched a scroll event for that yet.
    geometry.scrollHeight = SCROLL_HEIGHT + 100;
    const followedTop = MAX_SCROLL_TOP + 100 - 1;
    await act(async () => {
      context.state.scrollTop = followedTop;
    });

    // The reader nudges up one pixel. The browser coalesces the follow and
    // the nudge into a single scroll event, still within the tolerance and
    // below the offset the last event saw.
    await act(async () => {
      scroller.dispatchEvent(
        new WheelEvent("wheel", { deltaY: -1, bubbles: true }),
      );
    });
    await scrollTo(scroller, followedTop - 1);
    // Content keeps growing before any recovery would run.
    geometry.scrollHeight = SCROLL_HEIGHT + 220;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
    expect(scroller.scrollTop).toBe(followedTop - 1);
  });

  it("does not re-lock on an upward scroll that stays within the tolerance", async () => {
    const { context, geometry, scroller } = renderConversation();
    await scrollTo(scroller, MAX_SCROLL_TOP - 1);
    await act(async () => {
      context.stopScroll();
    });

    // A scrollbar or keyboard nudge: no wheel event, only an upward scroll.
    await scrollTo(scroller, MAX_SCROLL_TOP - 2);
    geometry.scrollHeight = SCROLL_HEIGHT + 120;

    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
    expect(scroller.scrollTop).toBe(MAX_SCROLL_TOP - 2);
  });

  it("re-locks once the reader scrolls back down after an upward nudge", async () => {
    const { context, geometry, scroller } = renderConversation();
    await scrollTo(scroller, MAX_SCROLL_TOP - 1);
    await act(async () => {
      scroller.dispatchEvent(
        new WheelEvent("wheel", { deltaY: -1, bubbles: true }),
      );
    });
    await scrollTo(scroller, MAX_SCROLL_TOP - 2);
    geometry.scrollHeight = SCROLL_HEIGHT + 120;

    // Back down to the new end while a row is re-measured, which the library
    // ignores on its own.
    context.state.resizeDifference = -56;
    await scrollTo(scroller, MAX_SCROLL_TOP + 120);
    context.state.resizeDifference = 0;
    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(true);
  });

  it.each([
    ["an Up arrow", () => keyDown({ key: "ArrowUp" })],
    ["Page Up", () => keyDown({ key: "PageUp" })],
    ["Shift+Space", () => keyDown({ key: " ", shiftKey: true })],
  ])(
    "cancels a pending recovery when the reader presses %s",
    async (_name, createEvent) => {
      const { context, scroller } = renderConversation();
      await leaveLiveTail(context, scroller);

      context.state.resizeDifference = -56;
      await act(async () => {
        scroller.scrollTop = MAX_SCROLL_TOP;
        scroller.dispatchEvent(new Event("scroll"));
        // The key's own scroll event has not been dispatched yet.
        scroller.dispatchEvent(createEvent());
        await wait(5);
      });
      context.state.resizeDifference = 0;
      await act(async () => {
        await wait(50);
      });

      expect(context.state.isAtBottom).toBe(false);
    },
  );

  it("cancels a pending recovery when a touch drags the content down", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    context.state.resizeDifference = -56;
    await act(async () => {
      scroller.scrollTop = MAX_SCROLL_TOP;
      scroller.dispatchEvent(new Event("scroll"));
      // A finger moving down scrolls the content up.
      scroller.dispatchEvent(touchEvent("touchstart", 100));
      scroller.dispatchEvent(touchEvent("touchmove", 130));
      await wait(5);
    });
    context.state.resizeDifference = 0;
    await act(async () => {
      await wait(50);
    });

    expect(context.state.isAtBottom).toBe(false);
  });

  it("waits for a selection drag to end before re-engaging the lock", async () => {
    const { context, scroller } = renderConversation();
    await leaveLiveTail(context, scroller);

    const answer = scroller.querySelector("p");
    const selection = window.getSelection();
    if (!answer || !selection) {
      throw new Error("expected selectable message text");
    }
    const range = document.createRange();
    range.selectNodeContents(answer);
    selection.removeAllRanges();
    selection.addRange(range);

    try {
      document.dispatchEvent(new MouseEvent("mousedown"));
      await scrollTo(scroller, MAX_SCROLL_TOP);
      await act(async () => {
        await wait(50);
      });
      // Like use-stick-to-bottom, never pull the viewport mid-selection.
      expect(context.state.isAtBottom).toBe(false);

      await act(async () => {
        document.dispatchEvent(new MouseEvent("mouseup"));
        await wait(5);
      });
      expect(context.state.isAtBottom).toBe(true);
    } finally {
      document.dispatchEvent(new MouseEvent("mouseup"));
      selection.removeAllRanges();
    }
  });
});
