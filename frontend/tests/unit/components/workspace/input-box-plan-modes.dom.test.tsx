import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { InputBox } from "@/components/workspace/input-box";
import { ThreadContext } from "@/components/workspace/messages/context";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { DEFAULT_LOCALE } from "@/core/i18n";
import { I18nProvider } from "@/core/i18n/context";

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn(), replace: rs.fn(), refresh: rs.fn() }),
  usePathname: () => "/workspace",
  useSearchParams: () => new URLSearchParams(),
}));
rs.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [{ name: "azure-pick", supports_thinking: true }],
    tokenUsageEnabled: false,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: rs.fn(),
  }),
}));

afterEach(() => {
  rs.restoreAllMocks();
  cleanup();
});

function renderComposer(
  mode: string | undefined,
  planModes: boolean | undefined,
  onContextChange: (...args: unknown[]) => void,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <I18nProvider initialLocale={DEFAULT_LOCALE}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider
          initialUser={{
            id: "user-1",
            email: "user@example.test",
            system_role: "user",
            needs_setup: false,
            oauth_provider: null,
          }}
        >
          <ThreadContext.Provider
            value={{ thread: { messages: [] } as never, isMock: true }}
          >
            <PromptInputProvider>
              <InputBox
                threadId="thread-1"
                status="ready"
                context={{ model_name: "azure-pick", mode } as never}
                onContextChange={onContextChange as never}
                planModes={planModes}
              />
            </PromptInputProvider>
          </ThreadContext.Provider>
        </AuthProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

// Evaluation batch 2 (2026-10-05): the pick agent can use neither plan mode
// nor subagents, so its composer shows a stored Pro or Ultra as the Thinking
// its runs go out with (chat-page sends planlessMode). gpt-6-astra review: it
// must not write that back, since other agents' chats read the same choice.
describe("composer plan modes", () => {
  it.each(["pro", "ultra"])(
    "shows a stored %s as Thinking where plan modes are off, without rewriting it",
    async (mode) => {
      const change = rs.fn();
      const { container } = renderComposer(mode, false, change);
      await new Promise((resolve) => setTimeout(resolve, 0));
      const text = container.textContent ?? "";
      expect(/思考|Reasoning/.test(text)).toBe(true);
      expect(/Pro|Ultra/.test(text)).toBe(false);
      expect(change).not.toHaveBeenCalledWith(
        expect.objectContaining({ mode: "thinking" }),
        expect.anything(),
      );
    },
  );
  it("shows a stored Pro where plan modes are offered", async () => {
    const change = rs.fn();
    const { container } = renderComposer("pro", undefined, change);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container.textContent).toContain("Pro");
    expect(change).not.toHaveBeenCalledWith(
      expect.objectContaining({ mode: "thinking" }),
      expect.anything(),
    );
  });
});
