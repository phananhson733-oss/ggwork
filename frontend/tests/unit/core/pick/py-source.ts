/**
 * Reading constants out of the backend's Python source in the frontend's
 * tests (plan TR-16, TR-24): the wording twins compare against the source
 * itself, so the two cannot drift apart. Only the simple shapes the wording
 * modules use: a string constant, a tuple of strings, and a MappingProxyType
 * of strings, f-strings over module constants and **spreads.
 */
export function pyString(source: string, name: string): string {
  const found = new RegExp(`^${name} = "([^"]*)"$`, "m").exec(source);
  if (found?.[1] === undefined) throw new Error(`no ${name}`);
  return found[1];
}

export function pyTuple(source: string, name: string): string[] {
  const found = new RegExp(`^${name} = \\(([^)]*)\\)`, "m").exec(source);
  if (!found?.[1]) throw new Error(`no ${name}`);
  return [...found[1].matchAll(/"([^"]*)"/g)].map((m) => m[1] ?? "");
}

export type Known = Readonly<{
  /** module string constants an f-string names */
  strings?: Readonly<Record<string, string>>;
  /** mappings a `**NAME` spread names */
  mappings?: Readonly<Record<string, Readonly<Record<string, string>>>>;
}>;

/** A `NAME = MappingProxyType({...})` of string literals, f-strings over module constants and `**OTHER` spreads. */
export function pyMapping(
  source: string,
  name: string,
  known: Known = {},
): Record<string, string> {
  const head = `${name} = MappingProxyType(`;
  const start = source.indexOf(head);
  if (start < 0) throw new Error(`no ${name}`);
  // The texts use full-width brackets, so the first ASCII ")" closes the call.
  const body = source.slice(start, source.indexOf(")", start + head.length));
  const entries = [
    ...body.matchAll(/\*\*(\w+)|"(\w+)":\s*(f?)"([^"]*)"/g),
  ].flatMap((m): [string, string][] => {
    if (m[1]) return Object.entries(known.mappings?.[m[1]] ?? {});
    const text = (m[4] ?? "").replace(/\{(\w+)\}/g, (_, constant: string) =>
      m[3] ? (known.strings?.[constant] ?? "?") : `{${constant}}`,
    );
    return [[m[2] ?? "", text]];
  });
  return Object.fromEntries(entries);
}
