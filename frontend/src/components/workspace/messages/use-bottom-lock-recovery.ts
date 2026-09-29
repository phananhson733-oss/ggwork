"use client";

import { useEffect } from "react";
import {
  useStickToBottomContext,
  type StickToBottomContext,
} from "use-stick-to-bottom";

// use-stick-to-bottom parks its lock one pixel above the maximum offset, and
// fractional offsets on high-DPI screens can add another pixel.
const LATEST_MESSAGE_TOLERANCE_PX = 2;
// Runs after the library's own deferred (1 ms) scroll handling has decided.
const LOCK_RECOVERY_DELAY_MS = 16;

// Keys that scroll the focused viewport (or the page's last scroller) up.
const UPWARD_SCROLL_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);

type ScrollDirection = "up" | "down" | "none";

function distanceFromEnd(element: HTMLElement) {
  return element.scrollHeight - element.clientHeight - element.scrollTop;
}

function isScrolledToEnd(element: HTMLElement) {
  return distanceFromEnd(element) <= LATEST_MESSAGE_TOLERANCE_PX;
}

// Classifies each scroll against a baseline: the offset at the previous
// scroll event, or at the latest `sync`. Content shrinking below clamps the
// offset without moving away from the end, so only a move that also grows the
// distance to the end counts as upward.
function trackScrollDirection(element: HTMLElement) {
  let lastTop = element.scrollTop;
  let lastDistance = distanceFromEnd(element);
  const sync = () => {
    lastTop = element.scrollTop;
    lastDistance = distanceFromEnd(element);
  };
  const read = (): ScrollDirection => {
    const top = element.scrollTop;
    const distance = distanceFromEnd(element);
    const direction =
      top < lastTop && distance > lastDistance
        ? "up"
        : top > lastTop
          ? "down"
          : "none";
    lastTop = top;
    lastDistance = distance;
    return direction;
  };
  return { read, sync };
}

function isEditable(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName))
  );
}

// Reports input that asks to scroll up, before the scroll events it causes:
// an upward wheel, an upward scrolling key, or a finger dragging the content
// down.
function watchUpwardInput(viewport: HTMLElement, onUpward: () => void) {
  let touchY: number | undefined;
  const handleWheel = (event: WheelEvent) => {
    if (event.deltaY < 0) {
      onUpward();
    }
  };
  const handleKeyDown = (event: KeyboardEvent) => {
    const { target } = event;
    const scrollsViewport =
      target === document.body ||
      (target instanceof Node && viewport.contains(target));
    if (!scrollsViewport || isEditable(target)) {
      return;
    }
    if (
      UPWARD_SCROLL_KEYS.has(event.key) ||
      (event.key === " " && event.shiftKey)
    ) {
      onUpward();
    }
  };
  const handleTouch = (event: TouchEvent) => {
    const y = event.touches[0]?.clientY;
    const previousY = touchY;
    touchY = y;
    if (
      event.type === "touchmove" &&
      y !== undefined &&
      previousY !== undefined &&
      y > previousY
    ) {
      onUpward();
    }
  };

  viewport.addEventListener("wheel", handleWheel, { passive: true });
  viewport.addEventListener("touchstart", handleTouch, { passive: true });
  viewport.addEventListener("touchmove", handleTouch, { passive: true });
  document.addEventListener("keydown", handleKeyDown);
  return () => {
    viewport.removeEventListener("wheel", handleWheel);
    viewport.removeEventListener("touchstart", handleTouch);
    viewport.removeEventListener("touchmove", handleTouch);
    document.removeEventListener("keydown", handleKeyDown);
  };
}

// Mirrors use-stick-to-bottom's own selection check, which it does not export.
function hasSelectionWithin(element: HTMLElement) {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    return false;
  }
  const container = selection.getRangeAt(0).commonAncestorContainer;
  return container.contains(element) || element.contains(container);
}

function trackPointer(onRelease: () => void) {
  let down = false;
  const handleDown = () => {
    down = true;
  };
  const handleUp = () => {
    down = false;
    onRelease();
  };
  document.addEventListener("mousedown", handleDown);
  document.addEventListener("mouseup", handleUp);
  return {
    isDown: () => down,
    dispose: () => {
      document.removeEventListener("mousedown", handleDown);
      document.removeEventListener("mouseup", handleUp);
    },
  };
}

type LockControls = Pick<StickToBottomContext, "scrollToBottom" | "state">;

// Re-engages the lock unless the reader is dragging a selection; like the
// library, it then waits and decides again once the pointer is released.
function createLockRecovery(
  viewport: HTMLElement,
  { scrollToBottom, state }: LockControls,
) {
  let deferredUntilPointerUp = false;
  const recover = () => {
    if (state.isAtBottom) {
      return;
    }
    if (pointer.isDown() && hasSelectionWithin(viewport)) {
      deferredUntilPointerUp = true;
      return;
    }
    void scrollToBottom({ animation: "instant" });
  };
  const pointer = trackPointer(() => {
    // Content may have grown during the drag; only a reader still on the
    // latest message is locked again.
    if (deferredUntilPointerUp && isScrolledToEnd(viewport)) {
      recover();
    }
    deferredUntilPointerUp = false;
  });
  return {
    recover,
    cancel: () => {
      deferredUntilPointerUp = false;
    },
    dispose: pointer.dispose,
  };
}

function watchBottomLockRecovery(
  viewport: HTMLElement,
  controls: LockControls,
) {
  const recovery = createLockRecovery(viewport, controls);
  const direction = trackScrollDirection(viewport);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cancelRecovery = () => {
    clearTimeout(timer);
    timer = undefined;
    recovery.cancel();
  };
  const handleScroll = () => {
    const moved = direction.read();
    if (moved === "none") {
      // Nothing moved (the browser's own event after a script scrolled and
      // dispatched one, or a clamp from content shrinking): keep whatever
      // decision the last real move made.
      return;
    }
    cancelRecovery();
    // Only a downward scroll that lands on the end helps a reader back onto
    // the lock. An upward one, even a one-pixel nudge that stays within the
    // tolerance, is the reader leaving; and a scroll while the lock still
    // holds (the library's own follow) must never undo their escape.
    if (
      moved === "up" ||
      controls.state.isAtBottom ||
      !isScrolledToEnd(viewport)
    ) {
      return;
    }
    // Sampled now, because streamed content may grow below before the timer
    // fires. Growth leaves the offset alone, while a keyboard, scrollbar or
    // touch escape lowers it before its own scroll event is dispatched.
    const landedScrollTop = viewport.scrollTop;
    timer = setTimeout(() => {
      timer = undefined;
      if (viewport.scrollTop >= landedScrollTop - LATEST_MESSAGE_TOLERANCE_PX) {
        recovery.recover();
      }
    }, LOCK_RECOVERY_DELAY_MS);
  };
  // The library escapes on an upward wheel before its scroll events arrive;
  // any upward input likewise cancels a recovery that has not run yet. It
  // also re-bases the direction on the offset the input starts from: the
  // library's follow of streamed content may have written the offset since
  // the last scroll event, and the browser can coalesce that write and the
  // reader's nudge up into one event that would otherwise read as a move down.
  const disposeUpwardInput = watchUpwardInput(viewport, () => {
    direction.sync();
    cancelRecovery();
  });

  viewport.addEventListener("scroll", handleScroll, { passive: true });
  return () => {
    viewport.removeEventListener("scroll", handleScroll);
    disposeUpwardInput();
    cancelRecovery();
    recovery.dispose();
  };
}

/**
 * Re-engages the stick-to-bottom lock when a reader scrolls back onto the
 * latest message.
 *
 * use-stick-to-bottom drops every scroll event that arrives while a content
 * resize is settling (`state.resizeDifference !== 0`), including a reader's
 * jump back to the bottom. Virtualized rows are measured as they enter the
 * viewport, so that jump regularly coincides with a resize. The lock then
 * stays released while the list looks settled, and new messages or streamed
 * tokens grow below the viewport instead of being followed.
 *
 * Only a downward scroll that ends at the very bottom while the lock is
 * released re-engages it, so a reader who stopped even slightly above, or
 * nudged up by a pixel from the parked position, keeps their position. Upward
 * input cancels a recovery that has not run yet. While a selection is being
 * dragged the decision waits for the pointer to be released.
 */
export function useBottomLockRecovery() {
  const { scrollRef, scrollToBottom, state } = useStickToBottomContext();

  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport) {
      return;
    }
    return watchBottomLockRecovery(viewport, { scrollToBottom, state });
  }, [scrollRef, scrollToBottom, state]);
}
