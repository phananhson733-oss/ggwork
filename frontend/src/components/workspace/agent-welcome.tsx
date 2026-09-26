"use client";

import { BotIcon } from "lucide-react";

import { type Agent } from "@/core/agents";
import { cn } from "@/lib/utils";

export function AgentWelcome({
  className,
  agent,
  agentName,
}: {
  className?: string;
  agent: Agent | null | undefined;
  agentName: string;
}) {
  const displayName = agent?.display_name?.length
    ? agent.display_name
    : (agent?.name ?? agentName);
  const description = agent?.description;

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center gap-2 px-8 py-4 text-center",
        className,
      )}
    >
      <div className="bg-brand-soft flex h-12 w-12 items-center justify-center rounded-full">
        <BotIcon className="text-brand-ink h-6 w-6" />
      </div>
      <h1 className="text-ink-1 text-[30px] leading-tight font-bold tracking-[-0.02em]">
        {displayName}
      </h1>
      {description && (
        <p className="text-helper max-w-[520px] text-sm leading-[1.65]">
          {description}
        </p>
      )}
    </div>
  );
}
