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

function isScrolledToEnd(element: HTMLElement) {
  return (
    element.scrollHeight - element.clientHeight - element.scrollTop <=
    LATEST_MESSAGE_TOLERANCE_PX
  );
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
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cancelRecovery = () => {
    clearTimeout(timer);
    timer = undefined;
    recovery.cancel();
  };
  const handleScroll = () => {
    cancelRecovery();
    // A scroll while the lock still holds never schedules a recovery, so this
    // can only help a reader back onto the lock, never undo their escape.
    if (controls.state.isAtBottom || !isScrolledToEnd(viewport)) {
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
  const handleWheel = (event: WheelEvent) => {
    // The library escapes on an upward wheel before its scroll events arrive.
    if (event.deltaY < 0) {
      cancelRecovery();
    }
  };

  viewport.addEventListener("scroll", handleScroll, { passive: true });
  viewport.addEventListener("wheel", handleWheel, { passive: true });
  return () => {
    viewport.removeEventListener("scroll", handleScroll);
    viewport.removeEventListener("wheel", handleWheel);
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
 * Only a scroll that ends at the very bottom while the lock is released
 * re-engages it, so a reader who stopped even slightly above keeps their
 * position. While a selection is being dragged the decision waits for the
 * pointer to be released.
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
