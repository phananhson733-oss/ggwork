import { notFound, redirect } from "next/navigation";

import { resourceSchema } from "@/core/pick/resources";
import { gatewayGet } from "@/server/pick-board/gateway";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "取货资料 - GGWork",
  robots: { index: false, follow: false },
};
export default async function ResourcePage({
  searchParams,
}: {
  searchParams: Promise<{ row?: string }>;
}) {
  const { row } = await searchParams;
  if (typeof row !== "string" || !row || row.length > 512) notFound();
  const result = await gatewayGet(
    `/api/pick/resources?row=${encodeURIComponent(row)}`,
    resourceSchema,
  );
  if (!result.ok && result.status === 401)
    redirect(
      `/login?next=${encodeURIComponent(`/workspace/pick-resources?row=${encodeURIComponent(row)}`)}`,
    );
  return (
    <main className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">当前取货资料</h1>
      <p className="text-muted-foreground text-sm">
        按资料所有者权限读取。历史选剧依据仍保留在原版本中。
      </p>
      {!result.ok ? (
        <p role="alert">
          {result.status === 403
            ? "只有资料所有者可以查看取货链接。"
            : "暂时无法读取取货资料，请稍后刷新或查看剧场原始文档。"}
        </p>
      ) : result.data.url ? (
        <div className="space-y-2">
          <a
            className="text-link hover:underline"
            href={result.data.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {result.data.label} ↗
          </a>
          {result.data.code && <p>提取码：{result.data.code}</p>}
        </div>
      ) : (
        <p>当前资料未提供取货链接，请查看剧场原始文档。</p>
      )}
    </main>
  );
}
