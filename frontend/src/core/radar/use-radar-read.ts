"use client";

import { useQuery } from "@tanstack/react-query";
import type { z } from "zod";

/** Same-origin authenticated GET only. Query key and AbortSignal bind each response to its selection. */
export function useRadarRead<T>(path: string | null, schema: z.ZodType<T>) {
  return useQuery({
    queryKey: ["historical-radar", path],
    enabled: path !== null,
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/pick/radar/${path}`, {
        signal,
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "登录已过期，请重新登录后刷新。"
            : "历史资料暂时读不了，请重试；不会改用采集批次或补造数据。",
        );
      const parsed = schema.safeParse(await response.json());
      if (!parsed.success)
        throw new Error("历史资料格式校验失败，请稍后重试。");
      return parsed.data;
    },
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    gcTime: 0,
  });
}
