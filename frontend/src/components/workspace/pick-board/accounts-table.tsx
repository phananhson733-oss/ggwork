// PORTED_FROM: realshort@816ca2e src/components/admin/pick/accounts-table.tsx
// 本地改动：账号主页只对 https:// 出链接，别的（含网盘清洗留下的「[网盘信息已移除]」、http）只显示账号名（B9）；
// 一行拆成 AccountRow（函数 <50 行）；GGWork 样式：账号链接用 link 色，折叠区圆角 12。
import type { CatalogAccount } from "@/server/pick-board";

import { ExternalLink, isSafeUrl } from "./links";

function AccountRow({ a }: { a: CatalogAccount }) {
  const name = a.name || a.id || "—";
  return (
    <tr className="border-line border-t align-top">
      <td className="py-2 pr-3 whitespace-nowrap">
        {isSafeUrl(a.url) ? (
          <ExternalLink
            href={a.url}
            rel="nofollow sponsored noopener"
            className="text-link hover:underline"
          >
            {name}
          </ExternalLink>
        ) : (
          name
        )}
      </td>
      <td className="py-2 pr-3 whitespace-nowrap">{a.grp || "—"}</td>
      <td className="py-2 pr-3 whitespace-nowrap">{a.form || "—"}</td>
      <td className="py-2 pr-3">{a.niche || "—"}</td>
      <td className="py-2 pr-3 whitespace-nowrap">{a.status || "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">
        {a.fans === null ? "—" : a.fans.toLocaleString("en-US")}
      </td>
      <td className="py-2 whitespace-nowrap tabular-nums">{a.asOf ?? "—"}</td>
    </tr>
  );
}

/** 账号台账：运营那张「账号」表的原样，七列与 artifact 相同 */
export function AccountsTable({ accounts }: { accounts: CatalogAccount[] }) {
  return (
    <details
      className="border-line bg-panel mt-6 rounded-lg border px-4 py-3"
      open
    >
      <summary className="cursor-pointer text-sm font-semibold">
        账号台账 · {accounts.length} 个
      </summary>
      {accounts.length === 0 ? (
        <p className="text-helper mt-2 text-sm">账号台账为空。</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[720px] text-xs">
            <thead>
              <tr className="text-helper text-left">
                <th scope="col" className="py-1 pr-3">
                  账号
                </th>
                <th scope="col" className="py-1 pr-3">
                  组
                </th>
                <th scope="col" className="py-1 pr-3">
                  表现形式
                </th>
                <th scope="col" className="py-1 pr-3">
                  定位
                </th>
                <th scope="col" className="py-1 pr-3">
                  状态
                </th>
                <th scope="col" className="py-1 pr-3 text-right">
                  粉丝
                </th>
                <th scope="col" className="py-1">
                  数据日期
                </th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <AccountRow key={a.id} a={a} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}
