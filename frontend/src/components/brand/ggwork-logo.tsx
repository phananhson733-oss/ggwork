import { APP_LOGO_SRC, APP_NAME } from "@/core/brand";
import { cn } from "@/lib/utils";

/** The round GenGrowth G mark. Decorative next to the wordmark. */
export function GGWorkMark({
  size = 22,
  className,
  alt = "",
}: {
  size?: number;
  className?: string;
  alt?: string;
}) {
  return (
    <img
      src={APP_LOGO_SRC}
      alt={alt}
      width={size}
      height={size}
      draggable={false}
      className={cn("shrink-0 rounded-full select-none", className)}
    />
  );
}

/**
 * Logo plus the "GGWork" wordmark: DM Sans 700, -0.01em, ink-1. The default
 * size is the sidebar header (22px mark, 15px text).
 */
export function GGWorkWordmark({
  markSize = 22,
  className,
  textClassName,
}: {
  markSize?: number;
  className?: string;
  textClassName?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <GGWorkMark size={markSize} />
      <span
        className={cn(
          "text-ink-1 text-[15px] font-bold tracking-[-0.01em] whitespace-nowrap",
          textClassName,
        )}
      >
        {APP_NAME}
      </span>
    </span>
  );
}
