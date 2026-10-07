import type { TrendsCandidates } from "@/core/pick/trends-candidates-schema";
import {
  isShortTerm,
  SHORT_TERM_HINT,
  trendsExploreUrl,
} from "@/core/pick/trends-table";
import { basisText } from "@/core/pick/trends-table-wording";

import { ExternalLink } from "../links";

import {
  HEADING,
  LINK,
  MUTED,
  SECTION,
  TABLE,
  TD,
  TH,
} from "./trends-table-parts";
import { TableSources } from "./trends-table-sources";

export function TrendsCandidatesView({
  candidates,
}: {
  candidates: TrendsCandidates;
}) {
  return (
    <section className={SECTION} data-trends-preview="true">
      <h2 className={HEADING}>待采集剧集（{candidates.selected} 部）</h2>
      <p>
        尚未形成采集批次。以下来自现有剧库和榜单，走势与指标尚未采集；刷新页面不会启动采集。
      </p>
      <p className={MUTED}>
        按当前来源选取，最多 {candidates.target}{" "}
        部；正式名单以采集开始时的计划为准。
      </p>
      <TableSources sources={candidates.sources} />
      {candidates.unusable_titles > 0 ? (
        <p className={MUTED}>
          {candidates.unusable_titles} 条候选没有有效查询词，已排除。
        </p>
      ) : null}
      {candidates.selected === 0 ? (
        <p>目前没有可用于趋势查询的榜单或收入候选。</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className={TABLE}>
            <thead>
              <tr>
                {[
                  "剧",
                  "入选依据",
                  "查询词",
                  "走势 / 指标",
                  "状态",
                  "提示",
                  "链接",
                ].map((label) => (
                  <th key={label} scope="col" className={TH}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {candidates.rows.map((row) => (
                <tr key={row.identity} data-trends-candidate={row.order}>
                  <td className={TD}>
                    <div className="text-ink-1 font-semibold">{row.title}</div>
                    <div className={MUTED}>
                      {[row.platform, row.language].filter(Boolean).join(" · ")}
                    </div>
                  </td>
                  <td className={TD}>
                    {row.basis.map((basis, index) => (
                      <div key={index}>{basisText(basis)}</div>
                    ))}
                  </td>
                  <td className={TD}>
                    {row.term === row.title ? "同剧名" : row.term}
                  </td>
                  <td className={TD}>—</td>
                  <td className={TD}>尚未采集</td>
                  <td className={TD}>
                    {isShortTerm(row.term) ? SHORT_TERM_HINT : "—"}
                  </td>
                  <td className={TD}>
                    <ExternalLink
                      href={trendsExploreUrl(row.term, row.geo, row.time_range)}
                      className={LINK}
                    >
                      Google Trends
                    </ExternalLink>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
