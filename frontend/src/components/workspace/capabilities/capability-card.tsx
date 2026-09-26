"use client";

import {
  ArrowUpRightIcon,
  PuzzleIcon,
  SparklesIcon,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

export function CapabilityIcon({
  skill = false,
  icon: CustomIcon,
}: {
  name: string;
  skill?: boolean;
  icon?: LucideIcon;
}) {
  const Icon = CustomIcon ?? (skill ? SparklesIcon : PuzzleIcon);
  return (
    <div className="bg-raised text-ink-2 flex size-12 shrink-0 items-center justify-center rounded-2xl">
      <Icon className="size-6" strokeWidth={1.6} />
    </div>
  );
}

export function CapabilityCard({
  name,
  description,
  label,
  icon,
  status,
  children,
  onDetails,
  detailsLabel,
}: {
  name: string;
  description: string;
  label: string;
  icon: ReactNode;
  status?: ReactNode;
  children: ReactNode;
  onDetails?: () => void;
  detailsLabel?: string;
}) {
  return (
    <article className="bg-card group hover:border-line-strong flex min-w-0 flex-col rounded-lg border p-5 transition-colors">
      <div className="mb-5 flex items-start justify-between gap-3">
        {icon}
        <span className="text-helper bg-raised rounded-sm px-2 py-0.5 text-[11px] font-medium">
          {label}
        </span>
      </div>
      <h3 className="min-w-0 text-base font-semibold tracking-tight">
        {onDetails ? (
          <button
            className="hover:text-muted-foreground flex max-w-full items-center gap-1.5 text-left"
            onClick={onDetails}
            aria-label={detailsLabel}
          >
            <span className="truncate">{name}</span>
            <ArrowUpRightIcon className="size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
          </button>
        ) : (
          <span className="block truncate">{name}</span>
        )}
      </h3>
      <p className="text-muted-foreground mt-2 line-clamp-2 min-h-10 text-[13px] leading-5">
        {description}
      </p>
      <div className="mt-6 flex min-h-8 items-center justify-between gap-2 border-t pt-4">
        <div className="text-muted-foreground flex min-w-0 items-center gap-1.5 text-xs">
          {status}
        </div>
        <div className="flex shrink-0 items-center gap-1">{children}</div>
      </div>
    </article>
  );
}
