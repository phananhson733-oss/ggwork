"use client";

import { Button } from "@/components/ui/button";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

/**
 * The pick data board's error boundary: errors the page does not turn into a
 * notice (the mirror's typed errors are notices, not errors). It shows a fixed
 * sentence and the digest the server logged the error under, never the
 * message: a database error's message can quote SQL or values.
 */
export default function PickDataError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <div
          role="alert"
          className="mx-auto w-full max-w-xl space-y-3 p-8 text-center"
        >
          <h1 className="text-xl font-bold tracking-[-0.01em]">
            选剧资料暂时打不开
          </h1>
          <p className="text-muted-foreground text-sm">
            可以重试一次；还是不行，把下面的错误编号发给管理员。
          </p>
          {error.digest ? (
            <p className="text-muted-foreground text-xs">
              错误编号：<code>{error.digest}</code>
            </p>
          ) : null}
          <Button variant="outline" onClick={reset}>
            重试
          </Button>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
