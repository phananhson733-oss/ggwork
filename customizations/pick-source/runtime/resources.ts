import { getPool } from "../src/db";
export function safeResourceUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const u = new URL(value);
    return u.protocol === "https:" && !u.username && !u.password
      ? u.href
      : null;
  } catch {
    return null;
  }
}
/** Raw resource URLs stay out of the shared mirror and require the owner's Gateway route. */
export async function resource(rowKey: string) {
  if (rowKey.length > 512 || /[\u0000-\u001f]/.test(rowKey)) return null;
  if (/^reelshort-[a-f0-9]{24}$/.test(rowKey)) {
    const r = (
      await getPool().query(
        "SELECT book_promotion_link,app_promotion_link FROM pick_source.dramas WHERE id=$1",
        [rowKey.slice(10)],
      )
    ).rows[0];
    return r
      ? {
          url:
            safeResourceUrl(r.book_promotion_link) ||
            safeResourceUrl(r.app_promotion_link),
          code: null,
          label: "ReelShort 官方链接",
        }
      : null;
  }
  const r = (
    await getPool().query(
      "SELECT pan_url,pan_pw FROM pick_source.catalog_rows WHERE row_key=$1",
      [rowKey],
    )
  ).rows[0];
  return r
    ? {
        url: safeResourceUrl(r.pan_url),
        code: r.pan_pw || null,
        label: "剧场素材链接",
      }
    : null;
}
