export interface ReviewedDramaGroupOverride {
  readonly memberIds: readonly string[];
  readonly canonicalGroupKey: string;
  readonly reviewedAt: string;
  readonly evidence: string;
}

export interface DramaGroupDetail {
  readonly id: string;
  readonly relation_books: readonly { readonly id: string }[];
}

export const REVIEWED_DRAMA_GROUP_OVERRIDES = [
  {
    memberIds: ["6a952f97339c7f37f005f685"],
    canonicalGroupKey: "6a469b12d3f5c65f7f095b8a",
    reviewedAt: "2026-09-04",
    evidence: "Product-owner confirmed duplicate; CPS relation_books is empty",
  },
] as const satisfies readonly ReviewedDramaGroupOverride[];

export function resolveReviewedDramaGroupKey(
  memberIds: readonly string[],
): string {
  return resolveReviewedDramaGroupKeyWithRegistry(
    memberIds,
    REVIEWED_DRAMA_GROUP_OVERRIDES,
  );
}

export function resolveReviewedDramaGroupKeyFromDetail(
  detail: DramaGroupDetail,
): string {
  return resolveReviewedDramaGroupKey([
    detail.id,
    ...detail.relation_books.map((row) => row.id),
  ]);
}

function normalizedUniqueIds(memberIds: readonly string[]): string[] {
  return [...new Set(memberIds)].sort();
}

/** @internal Exported only so the conflict guard can be tested without fake production data. */
export function resolveReviewedDramaGroupKeyWithRegistry(
  memberIds: readonly string[],
  registry: readonly ReviewedDramaGroupOverride[],
): string {
  if (memberIds.length === 0) {
    throw new Error("Expected at least one drama member ID");
  }

  const memberOwners = new Map<string, { target: string }>();
  const reviewedEntries = registry.map((entry) => {
    const normalizedMemberIds = normalizedUniqueIds(entry.memberIds);
    if (normalizedMemberIds.length === 0) {
      throw new Error(
        "Reviewed drama group override must contain at least one member ID",
      );
    }
    if (!entry.canonicalGroupKey.trim()) {
      throw new Error(
        "Reviewed drama group override must contain a canonical group key",
      );
    }

    for (const memberId of normalizedMemberIds) {
      const existing = memberOwners.get(memberId);
      if (existing) {
        if (existing.target !== entry.canonicalGroupKey) {
          throw new Error(
            `Conflicting reviewed drama group targets for member ID ${memberId}`,
          );
        }
        throw new Error(
          `Member ID ${memberId} appears in multiple reviewed drama group overrides`,
        );
      }
      memberOwners.set(memberId, { target: entry.canonicalGroupKey });
    }

    return { entry, normalizedMemberIds };
  });

  const normalizedMemberIds = normalizedUniqueIds(memberIds);
  const ordinaryGroupKey = normalizedMemberIds[0];
  const reviewedEntry = reviewedEntries.find(
    (candidate) =>
      candidate.normalizedMemberIds.length === normalizedMemberIds.length &&
      candidate.normalizedMemberIds.every(
        (memberId, index) => memberId === normalizedMemberIds[index],
      ),
  );

  return reviewedEntry?.entry.canonicalGroupKey ?? ordinaryGroupKey;
}
