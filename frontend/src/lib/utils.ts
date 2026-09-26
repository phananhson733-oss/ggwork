import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// The GGWork theme adds shadow-popover/shadow-composer and the bg-cta
// gradient (styles/ggwork-theme.css). Without this, tailwind-merge reads the
// shadows as shadow colors and bg-cta as a background color, so overrides
// like `shadow-none` or `bg-none` would not win.
const twMerge = extendTailwindMerge({
  extend: {
    theme: { shadow: ["popover", "composer"] },
    classGroups: { "bg-image": [{ bg: ["cta"] }] },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Shared class for external links (underline by default). */
export const externalLinkClass =
  "text-link underline underline-offset-2 hover:no-underline";
/** Link style without underline by default (e.g. for streaming/loading). */
export const externalLinkClassNoUnderline = "text-link hover:underline";
