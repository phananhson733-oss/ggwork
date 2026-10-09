import "server-only";
import {
  commonQuerySchema,
  queryResponseSchema,
  type QueryResponse,
} from "@/core/pick/completion-types";

import { gatewayQuery, type GatewayResult } from "./gateway";

/** Authenticated query boundary used by common-loaders; direct SQL modules remain the differential oracle. */
export type CommonBoardReader = (
  input: unknown,
  signal?: AbortSignal,
) => Promise<GatewayResult<QueryResponse>>;
export const queryPickBoard: CommonBoardReader = async (input, signal) => {
  const query = commonQuerySchema.safeParse(input);
  if (!query.success) return { ok: false, status: 422 };
  return gatewayQuery(
    "/api/pick/query",
    queryResponseSchema,
    query.data,
    signal,
    query.data.budget_ms,
  );
};
