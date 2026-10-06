/** Independent audit view only; never imported by product code or sent to a model. */
import type { Document } from "./readiness-protocol";

function object(value: unknown): value is Document {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function check(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
export function auditModelPayload(value: unknown): Document {
  check(object(value), "Model payload must be an object");
  const result = structuredClone(value);
  if (!Object.hasOwn(result, "evidence_encoding")) return result;
  if (result.evidence_encoding === "inline-v1") {
    check(
      Object.keys(result).every((key) =>
        ["id", "result_id", "evidence_encoding", "inline_payload"].includes(
          key,
        ),
      ),
      "Invalid inline envelope",
    );
    const inner = result.inline_payload;
    check(object(inner), "Missing inline payload");
    for (const key of ["id", "result_id"])
      check(
        Object.hasOwn(result, key) === Object.hasOwn(inner, key) &&
          JSON.stringify(result[key]) === JSON.stringify(inner[key]),
        "Inline locator mismatch",
      );
    return inner; // Literal legacy payload, including marker-like content: no recursion.
  }
  check(
    result.evidence_encoding === "facts-ref-v1",
    "Unknown evidence encoding",
  );
  const table = result.evidence_facts;
  check(
    object(table) && Object.keys(table).length > 0,
    "Missing evidence dictionary",
  );
  const allowed = [
    "kind",
    "label",
    "observed_at",
    "rank",
    "value",
    "grade",
    "note",
  ];
  check(
    Object.values(table).every(
      (facts) =>
        object(facts) &&
        Object.keys(facts).every((key) => allowed.includes(key)),
    ),
    "Invalid dictionary entry",
  );
  delete result.evidence_encoding;
  delete result.evidence_facts;
  const items = Object.hasOwn(result, "items") ? result.items : [result.item];
  check(Array.isArray(items) && items.every(object), "Missing encoded items");
  for (const item of items) {
    check(Array.isArray(item.evidence), "Missing encoded evidence");
    item.evidence = item.evidence.map((entry: unknown) => {
      check(object(entry), "Invalid evidence");
      if (!Object.hasOwn(entry, "facts_ref")) return entry;
      const reference = entry.facts_ref;
      check(
        typeof reference === "string" && Object.hasOwn(table, reference),
        "Invalid evidence reference",
      );
      const inline = { ...entry };
      delete inline.facts_ref;
      const facts = table[reference];
      check(
        !Object.keys(inline).some((key) => Object.hasOwn(facts, key)),
        "Dictionary/inline conflict",
      );
      const merged = { ...structuredClone(facts), ...inline };
      check(
        !String(merged.kind ?? "").startsWith("obs_"),
        "Observation evidence cannot use dictionary",
      );
      return merged;
    });
  }
  return result;
}
