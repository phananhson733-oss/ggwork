// PORTED_FROM: realshort@816ca2e src/components/admin/pick/rules-tab.tsx
// 本地改动：规则表、在用剧场、YouTube 标签、列头提示与术语表全部改读版本规则（rules 经 props 传入）；
// 顺序是版本的在用剧场在前、其余剧场按版本里的顺序在后，本页不认识的剧场（rules.unknownPlatforms）也列出来并标明；
// 「文档」列按版本规则的链接形态渲染：站内是不预取的 Link，外链新标签打开，其余不出链接。
import {
  YOUTUBE_UNKNOWN_LABEL,
  type PlatformRule,
} from "@/core/pick-board/platforms";
import type { BoardRules } from "@/core/pick-board/rules";

import { Glossary } from "./glossary";
import { RuleLink } from "./links";
import { TableWrap, TH } from "./rows-table";

/**
 * 剧场规则 tab：各剧场的规则表（剧场文档原话，在用的排前面）+ 术语与口径速查。
 * ReelShort 那一行的「文档」是本页的 ReelShort 榜（它没有飞书剧单）。
 */
const HEADS = [
  "剧场",
  "剧单",
  "结算",
  "报备",
  "YouTube",
  "必带 tag",
  "解禁",
  "素材",
  "信号",
] as const;
const CELL = "px-2.5 py-[7px]";

/** 版本的在用剧场在前（只留版本里有规则的），其余按版本里的顺序 */
function orderedPlatforms(rules: BoardRules): string[] {
  const keys = Object.keys(rules.platformRules);
  const inUse = rules.inUse.filter((p) => keys.includes(p));
  return [...inUse, ...keys.filter((p) => !inUse.includes(p))];
}

function HeadRow({ hints }: { hints: BoardRules["ruleHints"] }) {
  return (
    <tr>
      {HEADS.map((h) => {
        const hint = hints[h];
        return (
          <th scope="col" key={h} className={`${TH} text-left`}>
            {hint ? (
              <span
                title={hint}
                className="decoration-line-strong cursor-help underline decoration-dotted underline-offset-4"
              >
                {h}
              </span>
            ) : (
              h
            )}
          </th>
        );
      })}
    </tr>
  );
}

function statusOf(p: string, rules: BoardRules): string {
  const use = rules.inUse.includes(p) ? "在用" : "未在用";
  return rules.unknownPlatforms.includes(p) ? `${use} · 本页不认识的剧场` : use;
}

function RuleRow({
  p,
  r,
  rules,
}: {
  p: string;
  r: PlatformRule;
  rules: BoardRules;
}) {
  return (
    <tr className="border-line/60 text-ink-2 hover:bg-panel-hover border-b align-top last:border-b-0">
      <td className={`${CELL} whitespace-nowrap`}>
        <span className="text-ink-1 font-semibold">{r.name || p}</span>
        <div className="text-ink-dim text-[11px]">{statusOf(p, rules)}</div>
      </td>
      <td className={`${CELL} whitespace-nowrap`}>
        <RuleLink
          href={r.doc}
          className="text-brand hover:underline"
          fallback="—"
        >
          {r.doc.startsWith("/")
            ? `${p === "reelshort" ? "ReelShort 榜" : "打开"} ›`
            : "飞书 ↗"}
        </RuleLink>
        <div className="text-ink-dim text-[11px]">核对 {r.updated}</div>
      </td>
      <td className={CELL}>{r.back}</td>
      <td className={CELL}>{r.report}</td>
      <td className={CELL}>
        <span className={r.yt === "no" ? "text-brand" : ""}>
          {r.yt === null ? YOUTUBE_UNKNOWN_LABEL : rules.youtubeLabels[r.yt]}
        </span>
        <div className="text-helper text-[11px]">{r.ytNote}</div>
      </td>
      <td className={CELL}>{r.tag}</td>
      <td className={CELL}>{r.unban}</td>
      <td className={CELL}>{r.material}</td>
      <td className={CELL}>{r.signals || "—"}</td>
    </tr>
  );
}

export function RulesTab({ rules }: { rules: BoardRules }) {
  return (
    <>
      <p className="text-helper mb-3 text-sm leading-relaxed">
        各剧场文档原话，在用的排前面；「文档」列写着那份文档最后核对的日期。列头悬停有解释，下面的术语表可以搜。
        规则随镜像版本走，与这一版的数据同一次采集。
      </p>
      <TableWrap>
        <table className="w-full min-w-[1000px] border-collapse text-left text-[12.5px]">
          <thead>
            <HeadRow hints={rules.ruleHints} />
          </thead>
          <tbody>
            {orderedPlatforms(rules).map((p) => {
              const r = rules.platformRules[p];
              return r ? <RuleRow key={p} p={p} r={r} rules={rules} /> : null;
            })}
          </tbody>
        </table>
      </TableWrap>
      <Glossary groups={rules.glossary} />
    </>
  );
}
