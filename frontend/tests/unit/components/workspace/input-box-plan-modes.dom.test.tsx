import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, waitFor } from "@testing-library/react";

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

// Evaluation batch 2 (2026-10-05): a stored Pro or Ultra sent is_plan_mode or
// subagent_enabled with every pick run, though the pick agent can use neither.
describe("composer plan modes", () => {
  it.each(["pro", "ultra", undefined])(
    "resolves a stored %s to Thinking where plan modes are off",
    async (mode) => {
      const change = rs.fn();
      renderComposer(mode, false, change);
      await waitFor(() =>
        expect(change).toHaveBeenCalledWith(
          expect.objectContaining({ mode: "thinking" }),
          { automatic: true },
        ),
      );
    },
  );
  it("keeps a stored Pro where plan modes are offered", async () => {
    const change = rs.fn();
    renderComposer("pro", undefined, change);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(change).not.toHaveBeenCalledWith(
      expect.objectContaining({ mode: "thinking" }),
      expect.anything(),
    );
  });
});
