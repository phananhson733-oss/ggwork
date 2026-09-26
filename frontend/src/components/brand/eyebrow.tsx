import { cn } from "@/lib/utils";

/**
 * The GGWork eyebrow: a mono, wide-tracked label in a brand-soft pill with a
 * link-colored dot, as on gengrowth.ai ("SEO AND TECHNICAL WEBSITE AGENTS").
 */
export function Eyebrow({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "border-brand-line bg-brand-soft text-brand-ink inline-flex items-center gap-2 rounded-md border px-2.5 py-1 font-mono text-[11px] font-medium tracking-[0.14em]",
        className,
      )}
    >
      <span aria-hidden className="bg-link size-1.5 shrink-0 rounded-full" />
      {children}
    </span>
  );
}
