// PORTED_FROM: realshort@816ca2e src/components/admin/pick/rows-table.tsx
// 本地改动：版本规则（rules）传给信号格与资源格；「信号 · 依据」说明里的「分成」pill 改叫「订单」（★U26），
// 「备用资源 · 限制」说明写明网盘只说有没有。
import type { ReactNode } from "react";

import type { PickRequest } from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { PickRow } from "@/server/pick-board";

import {
  DateCell,
  EpisodesCell,
  KindCell,
  LangCell,
  PickupCell,
  ResourceCell,
  SignalCell,
  TitleCell,
} from "./cells";

/**
 * 八列，与 artifact 的行布局逐列相同：剧 / 信号·依据 / 语言 / 类型·频道 / 集数·起付费 / 日期 / 取货 /
 * 备用资源·限制。用户 2026-09-10 明确要求一行看全，不把信息藏进证据页。
 * 表宽贴容器（宽内容在 tablewrap 里横向滚，页面本身不横滚）；样式照 artifact 的 .tablewrap / th / td / tr.row。
 */
const HEADS: { text: string; hint?: string; right?: boolean }[] = [
  { text: "剧" },
  {
    text: "信号 · 依据",
    hint:
      "剧场给的名次 / 分级 / 清单 / 备注，鹊娱汇聚台给的是跨剧场的两张 Top25（7 日转化率 / 7 日总收入，全体分销商的成交），ReelShort 给的是本站采集的指标（30d 指标 / 推广 / 7 天净变化 / 订单 / 搜索 / 出站，只印有数的）。" +
      "pill 的顺序：ReelShort 指标在前，剧场信号按采集顺序，最后是旧站收录 / 同名。" +
      "下面一行是最近一条证据自己的日期（KalosTV 日榜 = 榜单日期、周热门 = 周起、鹊娱两张 Top25 = 榜单日期、FlickReels = 入榜日期、GoodShort = 推荐日期；" +
      "ReelShort = 最近出站 / 最近账单 / GSC 采集三者最近；评级 / StarShort / DramaBox 备注没有日期就写日期未知，不拿剧单日期代填）和事实标签。" +
      "「证据时间」排序用的就是这个日期，完整规则在上面「排序」那排 chip 的 hover 里。",
  },
  { text: "语言" },
  {
    text: "类型 · 频道",
    hint:
      "剧单里的制作类型（译制剧 / 本土剧 / 配音剧 / AI 真人剧 / 动态漫……）；「频道」只有 KalosTV 剧单给（国内短剧IP / 国内网文IP / 海外网文IP / 原创……）。" +
      "ReelShort 上游没有这两个字段，一律「—」；剧单没填的也是「—」，不拿标签代填——上游标签在剧名下面那行。",
  },
  {
    text: "集数 · 起付费",
    hint: "总集数 · 第几集起付费；没有起付费集的只显示集数",
    right: true,
  },
  { text: "日期", hint: "剧单里的推荐 / 上新日期；ReelShort 行是上线日期" },
  { text: "取货" },
  {
    text: "备用资源 · 限制",
    hint: "剧单附没附网盘（链接不同步到本页，到 RealShort 证据页看），以及这个剧场的 YouTube 条件与下架记录",
  },
];

export const TH =
  "border-b border-line bg-raised px-3 py-2 text-[11px] font-semibold tracking-[.06em] text-helper uppercase whitespace-nowrap";
export const TR =
  "border-b border-line/60 last:border-b-0 hover:bg-panel-hover";

export function TableWrap({ children }: { children: ReactNode }) {
  return (
    <div className="border-line bg-panel overflow-x-auto rounded-[10px] border">
      {children}
    </div>
  );
}

/** 表头一格；有口径说明的挂在 hover 上，虚线下划线提示能悬停 */
export function HeadCell({
  text,
  hint,
  right,
}: {
  text: string;
  hint?: string;
  right?: boolean;
}) {
  return (
    <th scope="col" className={`${TH} ${right ? "text-right" : "text-left"}`}>
      {hint ? (
        <span
          title={hint}
          className="decoration-line-strong cursor-help underline decoration-dotted underline-offset-4"
        >
          {text}
        </span>
      ) : (
        text
      )}
    </th>
  );
}

export function RowsTable({
  rows,
  req,
  rules,
}: {
  rows: PickRow[];
  req: PickRequest;
  rules: BoardRules;
}) {
  return (
    <TableWrap>
      <table className="w-full min-w-[1100px] border-collapse text-left text-[13px]">
        <thead>
          <tr>
            {HEADS.map((h) => (
              <HeadCell key={h.text} {...h} />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.rowKey}
              className={`${TR} ${r.offOn ? "opacity-60" : ""}`}
            >
              <TitleCell row={r} req={req} />
              <SignalCell row={r} rules={rules} />
              <LangCell row={r} />
              <KindCell row={r} />
              <EpisodesCell row={r} />
              <DateCell row={r} />
              <PickupCell row={r} req={req} />
              <ResourceCell row={r} rules={rules} />
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
