import "server-only";

import {
  trendsCandidatesSchema,
  type TrendsCandidates,
} from "@/core/pick/trends-candidates-schema";

import { gatewayGet } from "./gateway";

export type TrendsCandidatesLoad =
  | Readonly<{ kind: "ok"; candidates: TrendsCandidates }>
  | Readonly<{ kind: "unauthenticated" }>
  | Readonly<{ kind: "unavailable" }>;

export async function loadTrendsCandidates(): Promise<TrendsCandidatesLoad> {
  const answer = await gatewayGet(
    "/api/pick/obs/trends-candidates",
    trendsCandidatesSchema,
  );
  if (answer.ok) return { kind: "ok", candidates: answer.data };
  return answer.status === 401
    ? { kind: "unauthenticated" }
    : { kind: "unavailable" };
}
