/** Plan-only history guard. No draft data is written to browser history/storage. */
export type PlanHistoryAttempt = { href: string; proceed: () => void };
export type PlanHistoryGuard = (attempt: PlanHistoryAttempt) => void;
const marker = "__ggwpPlanHistoryIndex";

type NavigationPositions = EventTarget & {
  currentEntry: { index: number } | null;
};

/** Restore a tracked traversal before Next receives popstate, then request a decision. */
export function watchPlanHistory(
  getGuard: () => PlanHistoryGuard | null,
): () => void {
  const navigation = (window as Window & { navigation?: NavigationPositions })
    .navigation;
  const nativeIndex = () => {
    const value = navigation?.currentEntry?.index;
    return typeof value === "number" && value >= 0 ? value : null;
  };
  const stateIndex = (state: unknown) => {
    const value =
      state && typeof state === "object"
        ? (state as Record<string, unknown>)[marker]
        : null;
    return typeof value === "number" && Number.isSafeInteger(value)
      ? value
      : null;
  };
  let position = nativeIndex() ?? stateIndex(history.state) ?? 0;
  let restoring: { from: number; target: number; href: string } | null = null;
  let allowed: number | null = null;
  let active = true;
  // Retain exact router wrappers for restoration; calls below explicitly bind this.
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const originalPush = history.pushState;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const originalReplace = history.replaceState;
  const stamped = (state: unknown, index: number) =>
    state === null || (typeof state === "object" && !Array.isArray(state))
      ? { ...state, [marker]: index }
      : state;
  const push: History["pushState"] = function (
    this: History,
    data,
    unused,
    url,
  ) {
    if (!active) return originalPush.call(this, data, unused, url);
    originalPush.call(this, stamped(data, position + 1), unused, url);
    position += 1;
  };
  const replace: History["replaceState"] = function (
    this: History,
    data,
    unused,
    url,
  ) {
    if (!active) return originalReplace.call(this, data, unused, url);
    originalReplace.call(this, stamped(data, position), unused, url);
  };
  const entryChanged = (event: Event) => {
    if (
      (event as Event & { navigationType: string | null }).navigationType !==
      "traverse"
    )
      position = nativeIndex() ?? position;
  };
  if (navigation && nativeIndex() !== null)
    navigation.addEventListener("currententrychange", entryChanged);
  else {
    originalReplace.call(history, stamped(history.state, position), "");
    history.pushState = push;
    history.replaceState = replace;
  }
  const pop = (event: PopStateEvent) => {
    const target = nativeIndex() ?? stateIndex(event.state);
    // Older browsers cannot identify pre-existing untracked entries. Do not
    // guess a direction or rewrite/truncate those entries; native unload and
    // the workspace's in-memory recovery remain available.
    if (target === null) return;
    if (restoring) {
      event.stopImmediatePropagation();
      if (target !== restoring.from) {
        history.go(restoring.from - target);
        return;
      }
      const requested = restoring;
      restoring = null;
      const attempt = {
        href: requested.href,
        proceed: () => {
          allowed = requested.target;
          history.go(requested.target - position);
        },
      };
      const guard = getGuard();
      if (guard) guard(attempt);
      else attempt.proceed();
      return;
    }
    if (allowed === target) {
      allowed = null;
      position = target;
      return;
    }
    if (getGuard() && target !== position) {
      event.stopImmediatePropagation();
      restoring = { from: position, target, href: window.location.href };
      history.go(position - target);
      return;
    }
    position = target;
  };
  window.addEventListener("popstate", pop, true);
  return () => {
    // Next may wrap our methods after mount. A retired wrapper must become a
    // transparent delegate, never stamp an old index after StrictMode/remount.
    active = false;
    getGuard = () => null;
    window.removeEventListener("popstate", pop, true);
    navigation?.removeEventListener("currententrychange", entryChanged);
    if (history.pushState === push) history.pushState = originalPush;
    if (history.replaceState === replace)
      history.replaceState = originalReplace;
  };
}
