/** Rehydrate retained observations without advancing their source dates. */
export type SavedRank = {
  kind: string;
  platform: string;
  title: string;
  lang: string;
  evidence_on: string | null;
  rank: number | null;
  payload: Record<string, unknown>;
};
const THEATERS: Record<string, string> = {
  dramabox: "DramaBox",
  flickreels: "FlickReels",
  shortmax: "ShortMax",
  goodshort: "GoodShort",
  moboreels: "MoboReels",
  starshort: "StarShort",
  touchshort: "TouchShort",
  kalos: "KalosTV",
  flareflow: "FlareFlow",
};
export function rankHistory(records: SavedRank[]) {
  const days = new Map<
    string,
    {
      date: string;
      conv: Record<string, unknown>[];
      rev: Record<string, unknown>[];
      provenance: string;
    }
  >();
  const seen = new Set<string>();
  for (const r of records) {
    const list = r.kind === "qc" ? "conv" : r.kind === "qr" ? "rev" : null;
    if (!list || !THEATERS[r.platform]) continue;
    const history = Array.isArray(r.payload.h)
      ? r.payload.h
      : [[r.evidence_on, r.rank]];
    for (const pair of history) {
      if (!Array.isArray(pair))
        throw new Error("Invalid retained ranking history");
      const [day, rank] = pair;
      if (
        typeof day !== "string" ||
        !/^\d{4}-\d{2}-\d{2}$/.test(day) ||
        !Number.isInteger(rank) ||
        rank < 1 ||
        rank > 25
      )
        throw new Error("Invalid retained ranking observation");
      const key = JSON.stringify([
        list,
        day,
        r.platform,
        r.title,
        r.payload.qy,
      ]);
      if (seen.has(key)) continue;
      seen.add(key);
      const entry = days.get(day) ?? {
        date: day,
        conv: [],
        rev: [],
        provenance:
          "Restored retained source observations; not a new collection",
      };
      entry[list].push({
        id: r.payload.qy,
        playletId: r.payload.pid ?? "",
        title: r.title,
        theater: THEATERS[r.platform],
        language: r.lang,
        rank,
      });
      days.set(day, entry);
    }
  }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date));
}
