"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { BrowserViewProvider } from "@/components/workspace/browser-view";
import { PickProvider } from "@/components/workspace/pick/pick-context";
import { SubtasksProvider } from "@/core/tasks/context";

export function ChatProviders({ children }: { children: React.ReactNode }) {
  return (
    <SubtasksProvider>
      <ArtifactsProvider>
        <BrowserViewProvider>
          <PickProvider>
            <PromptInputProvider>{children}</PromptInputProvider>
          </PickProvider>
        </BrowserViewProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
