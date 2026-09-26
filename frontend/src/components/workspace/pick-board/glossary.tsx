// PORTED_FROM: realshort@816ca2e src/components/admin/pick/glossary.tsx
// 本地改动：词条不在前端留一份，由页面把版本的 rules.glossary 经 props 传进来；词条原文不改（U9），
// 上方加一段固定说明，讲清本页和 RealShort 旧页的差别（★U9）；整段是一个带标题的 section，测试据此认出术语表子树。
// GGWork 样式：筛选框聚焦用 link 边框加 brand-soft 光圈；词条卡按 300px 自动排列，圆角 12，行高 1.65。
"use client";

import { useState } from "react";

import { filterGlossary, type GlossaryGroup } from "@/core/pick-board/glossary";

/** 术语表描述的是 RealShort 旧页；本页不显示的东西在这里说一次 */
export const GLOSSARY_NOTE =
  "以下词条来自 RealShort 选剧台；本页不显示网盘链接、提取码和分成金额，「分成对账」在本页叫「订单对账」。";

/**
 * 术语与口径速查（artifact 的 #gloss）：一个筛选框 + 版本里的三组词条。
 * 这里只做客户端文本筛选，不查库、不发请求。
 */
export function Glossary({ groups }: { groups: readonly GlossaryGroup[] }) {
  const [q, setQ] = useState("");
  const shown = filterGlossary(groups, q);
  return (
    <section className="mt-6" aria-labelledby="pick-glossary-title">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pick-glossary-title" className="text-[15px] font-semibold">
          术语与口径速查
        </h2>
        <label className="text-helper flex items-center gap-2 text-[12px]">
          <span className="sr-only">筛选术语</span>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="找一个词，比如 报备 / 出站 / 评级"
            className="border-line bg-panel text-ink-1 placeholder:text-ink-dim focus-visible:border-link focus-visible:ring-brand-soft w-64 max-w-full rounded-md border px-3 py-1.5 text-[13px] focus-visible:ring-[3px] focus-visible:outline-none"
          />
        </label>
      </div>
      <p className="text-helper mb-3 text-[12px]">{GLOSSARY_NOTE}</p>
      {shown.length === 0 ? (
        <div className="border-line bg-panel text-helper rounded-lg border px-4 py-8 text-center text-[13px]">
          没有匹配的词。
        </div>
      ) : (
        shown.map((g) => <GlossaryGroupBlock key={g.g} group={g} />)
      )}
    </section>
  );
}

function GlossaryGroupBlock({ group }: { group: GlossaryGroup }) {
  return (
    <div className="mb-4">
      <h3 className="mb-0.5 text-[13px] font-semibold">{group.g}</h3>
      <p className="text-helper mb-2 text-[12px]">{group.d}</p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-2">
        {group.items.map((it) => (
          <div
            key={it.t}
            className="border-line bg-panel rounded-lg border px-3.5 py-3 text-[12.5px] leading-[1.65]"
          >
            <div className="font-semibold">
              {it.t}
              {it.a ? (
                <span className="text-ink-dim ml-1.5 font-mono text-[11px] font-normal">
                  {it.a}
                </span>
              ) : null}
            </div>
            <p className="text-ink-2 mt-1">{it.d}</p>
            {it.h ? <p className="text-helper mt-1">{it.h}</p> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
