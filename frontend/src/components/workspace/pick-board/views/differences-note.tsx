// 工作台新建：页脚「与 RealShort 选剧台的差异」，固定文字（方案 plan:1165-1169，合成稿 P3-5 页脚、★U27）。
import { REALSHORT_ORIGIN } from "@/core/pick-board/site";

import { ExternalLink } from "../links";

const ITEMS = [
  "网盘只显示有没有：链接与提取码不同步到本页，要看到 RealShort 证据页。",
  "没有分成金额；「按分成排」用 RealShort 导出的名次（bill_rank），本页不显示金额。",
  "订单对账按日期、剧与推广类型合并成一行，「行数」仍是合并前的原始账单行数；按日期、订单数排，最多列 200 行合并后的行，没有上线日期两列。",
  "数据截至镜像版本的采集时间（as_of），不是实时数据；RealShort 选剧台是实时查询。",
] as const;

export function DifferencesNote() {
  return (
    <section className="border-line bg-panel mt-6 rounded-xl border px-4 py-3 text-[13px]">
      <h2 className="text-ink-2 font-semibold">与 RealShort 选剧台的差异</h2>
      <ul className="text-helper mt-2 list-disc space-y-1 pl-5 leading-relaxed">
        {ITEMS.map((text) => (
          <li key={text}>{text}</li>
        ))}
        <li>
          剧的公开页、证据页原文都外链到{" "}
          <ExternalLink
            href={REALSHORT_ORIGIN}
            className="text-brand hover:underline"
          >
            dramashortstv.com
          </ExternalLink>
          ，新标签打开。
        </li>
      </ul>
    </section>
  );
}
