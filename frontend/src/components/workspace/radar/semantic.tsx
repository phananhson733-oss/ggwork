import {
  Clock3,
  type LucideIcon,
  Minus,
  TrendingUp,
  TriangleAlert,
  Info,
  CircleX,
} from "lucide-react";
import type { ReactNode } from "react";

import { growth } from "@/core/radar/presentation";
import type { RadarRow } from "@/core/radar/schema";
import { changeTone, type TrendTone } from "@/core/radar/semantic-tones";
import { cn } from "@/lib/utils";

export const toneText: Record<TrendTone, string> = {
  neutral: "text-ink-2",
  success: "text-success-ink",
  info: "text-info-ink",
  warning: "text-warning-ink",
  danger: "text-danger-ink",
};
const toneSurface: Record<TrendTone, string> = {
  neutral: "bg-raised border-line-strong",
  success: "bg-success-surface border-success-ink/30",
  info: "bg-info-surface border-info-ink/30",
  warning: "bg-warning-surface border-warning-line",
  danger: "bg-danger-surface border-danger-line",
};
const toneIcon: Record<TrendTone, LucideIcon> = {
  neutral: Minus,
  success: TrendingUp,
  info: Info,
  warning: TriangleAlert,
  danger: CircleX,
};

export function TrendBadge({
  tone,
  children,
  icon,
}: {
  tone: TrendTone;
  children: ReactNode;
  icon?: LucideIcon;
}) {
  const Icon = icon ?? toneIcon[tone];
  return (
    <span
      data-trend-tone={tone}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-semibold whitespace-normal",
        toneText[tone],
        toneSurface[tone],
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {children}
    </span>
  );
}

export function HistoricalWindowBadge() {
  return (
    <TrendBadge tone="warning" icon={Clock3}>
      较早历史窗口
    </TrendBadge>
  );
}

export function RadarGrowth({ pilot }: { pilot: RadarRow["pilot"] }) {
  const tone =
    pilot.growth_state === "from_zero"
      ? "info"
      : changeTone(pilot.growth_state === "percent" ? pilot.growth_pct : null);
  return (
    <span className={cn("font-semibold tabular-nums", toneText[tone])}>
      {growth(pilot)}
    </span>
  );
}
