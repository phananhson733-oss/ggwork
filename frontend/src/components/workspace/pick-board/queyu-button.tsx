// PORTED_FROM: realshort@816ca2e src/components/admin/pick/queyu-button.tsx
// 本地改动：queyuHref 改从 @/core/pick-board/queyu 引入（那个文件不 import 任何模块，request.ts 与 metrics.ts
// 不会被这个 client 组件带进客户端包），其余原样；GGWork 样式：圆角走 8 的刻度，下架态悬停加深边框。
"use client";

import { queyuHref } from "@/core/pick-board/queyu";

/**
 * 「复制剧名并打开鹊娱」。鹊娱剧库没有单剧直达地址（Vue 单页，详情是页内抽屉、`?title=` 被忽略），
 * 所以取货 = 剧名进剪贴板 + 新标签打开剧库；装了 queyu-open-drawer.user.js 的会自动搜并开抽屉。
 * 只做搜索与打开详情，不碰鹊娱的创建任务 / 链接 / 锚点。
 *
 * 剪贴板失败（http 页面、权限被拒）不阻断打开：剧名在链接的 ?title= 里还带着一份。
 */
export function QueyuButton({ title, off }: { title: string; off: boolean }) {
  const href = queyuHref(title);
  return (
    <a
      href={href}
      target="_blank"
      rel="nofollow sponsored noopener"
      onClick={() => {
        void navigator.clipboard?.writeText(title).catch(() => undefined);
      }}
      className={`inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-[12px] whitespace-nowrap ${
        off
          ? "border-line text-ink-dim hover:border-line-strong"
          : "border-brand bg-brand text-on-brand hover:border-brand-hover hover:bg-brand-hover font-semibold"
      }`}
      title={
        off
          ? "剧单里有下架记录，先到鹊娱核对"
          : "复制剧名到剪贴板，并在新标签打开鹊娱剧库"
      }
    >
      {off ? "打开鹊娱核对下架" : "复制剧名并打开鹊娱"}
    </a>
  );
}
