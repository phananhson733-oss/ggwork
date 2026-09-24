import "server-only";

import { z } from "zod";

import {
  pickConditionsSchema,
  pickDataAsOfSchema,
  type PickConditions,
} from "@/core/pick/types";

import { gatewayGet, type GatewayResult } from "./gateway";

/**
 * 回放一份智能体候选（P4-2，方案 2.5 第 4 条）。
 *
 * 名单与顺序由 gateway 的 GET /api/pick/replay 给：它在结果自己的批次上按智能体语义重跑
 * （selection.replay_view），本页不翻译条件、不自己筛；行再由页面按 row_key 从镜像取（queries.ts 的
 * loadRowsByKeys）。/replay 不回条件，「近似筛选」要的条件另调 GET /api/pick/results/{id} 取（U15），
 * 两次并发。这里只读 conditions 一个字段：结果的其余部分不归本页，也不拿严格的 pickResultSchema 整份校验。
 *
 * 回答按状态码分类（401 / 404 / 409 / 410），响应体不进结果也不进日志，页面按分类给固定文案。
 */

/** selection.REPLAY_LIMIT：名单最多列这么多，total 与 truncated 说一共有几部 */
export const REPLAY_LIMIT = 2000;

/** 结果 id 是 uuid4().hex；页面的 cleanResult 丢掉别的，这里再守一次，免得拼出别的路径 */
const RESULT_ID = /^[0-9a-f]{32}$/;
const IDENTITY_MAX = 512;
const MIRROR_VERSION_MAX = 999_999;

const identity = z.string().min(1).max(IDENTITY_MAX);

const replaySchema = z.object({
  result_id: z.string().min(1).max(64),
  catalog_batch_id: z.string().min(1).max(IDENTITY_MAX),
  knowledge_batch_id: z.string().max(IDENTITY_MAX).nullable(),
  /** 结果钉住的镜像版本；降级发布的结果、以及 SQLite 上一律为 null；早于 P2-8a 的 gateway 没有这个键 */
  mirror_version: z
    .number()
    .int()
    .positive()
    .max(MIRROR_VERSION_MAX)
    .nullable()
    .optional(),
  limit: z.number().int().min(1).max(20),
  total: z.number().int().nonnegative(),
  identities: z.array(identity).max(REPLAY_LIMIT),
  truncated: z.boolean(),
  shown: z.array(identity).max(20),
  excluded_reproducible: z.boolean(),
  ranking_reproducible: z.boolean(),
  unmappable: z.array(z.string().min(1).max(64)).max(20),
  /** 结果冻结的 data_as_of；形状不对只丢掉批次时间，不让整份回放失败 */
  data_as_of: pickDataAsOfSchema.nullable().optional().catch(null),
});

const conditionsSchema = z.object({ conditions: pickConditionsSchema });

type RawReplay = z.infer<typeof replaySchema>;

/** 回放接口的回答，页面与视图要的形状 */
export type ReplayAnswer = Readonly<{
  resultId: string;
  mirrorVersion: number | null;
  /** 卡片展示了几部：名单前 limit 个高亮 */
  limit: number;
  total: number;
  /** 有序 identity 名单，最多 REPLAY_LIMIT 个 */
  identities: readonly string[];
  truncated: boolean;
  /** 结果早于 excluded_json：排除已选与换一批无法复现 */
  excludedReproducible: boolean;
  /** 规则与排序版本都还是当时的 */
  rankingReproducible: boolean;
  /** 资料页没有对应筛选的条件名（selection.unmappable_conditions，固定顺序） */
  unmappable: readonly string[];
  /** 名单用的批次采集于何时（结果冻结的 data_as_of.source_as_of） */
  sourceAsOf: string | null;
}>;

export type ReplayLoad =
  | Readonly<{
      kind: "ok";
      answer: ReplayAnswer;
      conditions: PickConditions | null;
    }>
  | Readonly<{ kind: "conflict" | "gone"; conditions: PickConditions | null }>
  | Readonly<{ kind: "notFound" }>
  | Readonly<{ kind: "unauthenticated" }>
  | Readonly<{ kind: "unavailable" }>;

function answerOf(raw: RawReplay): ReplayAnswer {
  return Object.freeze({
    resultId: raw.result_id,
    mirrorVersion: raw.mirror_version ?? null,
    limit: raw.limit,
    total: raw.total,
    identities: Object.freeze([...raw.identities]),
    truncated: raw.truncated,
    excludedReproducible: raw.excluded_reproducible,
    rankingReproducible: raw.ranking_reproducible,
    unmappable: Object.freeze([...raw.unmappable]),
    sourceAsOf: raw.data_as_of?.source_as_of ?? null,
  });
}

function loadOf(
  replay: GatewayResult<RawReplay>,
  conditions: PickConditions | null,
): ReplayLoad {
  if (replay.ok) {
    return { kind: "ok", answer: answerOf(replay.data), conditions };
  }
  switch (replay.status) {
    case 401:
      return { kind: "unauthenticated" };
    case 404:
      return { kind: "notFound" };
    case 409:
      return { kind: "conflict", conditions };
    case 410:
      return { kind: "gone", conditions };
    default:
      return { kind: "unavailable" };
  }
}

/** 一份候选的回放：名单与条件并发取；条件取不到时照样回放，只是没有近似筛选 */
export async function loadReplay(resultId: string): Promise<ReplayLoad> {
  if (!RESULT_ID.test(resultId)) return { kind: "notFound" };
  const [replay, result] = await Promise.all([
    gatewayGet(`/api/pick/replay?result_id=${resultId}`, replaySchema),
    gatewayGet(`/api/pick/results/${resultId}`, conditionsSchema),
  ]);
  return loadOf(replay, result.ok ? result.data.conditions : null);
}
