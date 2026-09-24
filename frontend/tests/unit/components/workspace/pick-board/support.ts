/**
 * DOM readers shared by the pick-board component tests: what a person can
 * read on the page (text, hover titles, link targets), and the pan-domain
 * pattern the mirror's scrubber uses (gp/mirror/pan_rules.json).
 */
import { readFileSync } from "node:fs";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../../../../../..");
const PAN_RULES = path.join(
  REPO_ROOT,
  "customizations/pick-workbench/ggwork_pick/mirror/pan_rules.json",
);

interface PanRules {
  patterns: { url: { source: string } };
}

/** Any pan-hosting domain the export scrubber knows (case-insensitive). */
export function panDomainPattern(): RegExp {
  const rules = JSON.parse(readFileSync(PAN_RULES, "utf8")) as PanRules;
  return new RegExp(rules.patterns.url.source, "i");
}

/** The glossary subtree: version text about the old page, not asserted on. */
export const GLOSSARY_SELECTOR =
  'section[aria-labelledby="pick-glossary-title"]';

/** A copy of root without the glossary subtree. */
export function withoutGlossary(root: Element): Element {
  const copy = root.cloneNode(true) as Element;
  copy.querySelectorAll(GLOSSARY_SELECTOR).forEach((node) => node.remove());
  return copy;
}

export function attributeValues(root: Element, name: string): string[] {
  return Array.from(root.querySelectorAll(`[${name}]`)).map(
    (node) => node.getAttribute(name) ?? "",
  );
}

/** Text, hover titles, aria labels and link targets, joined. */
export function readableText(root: Element): string {
  return [
    root.textContent ?? "",
    ...attributeValues(root, "title"),
    ...attributeValues(root, "aria-label"),
    ...attributeValues(root, "href"),
  ].join("\n");
}

/** Every href on the page, in document order. */
export function hrefs(root: Element): string[] {
  return attributeValues(root, "href");
}

/** An internal link of this board, pinned to version 7. */
export const PINNED_BOARD_LINK = /^\/workspace\/pick-data\?(?:.*&)?v=7(?:&|$)/;
