// PORTED_FROM: realshort@816ca2e src/lib/pick/glossary.ts
// 本地改动：只保留 GlossaryTerm / GlossaryGroup 类型与 filterGlossary；词条（GLOSSARY）与规则表列头提示（RULE_HINTS）
// 随版本数据走（meta.rules.glossary / ruleHints，由 rules.ts 的 buildBoardRules 归一），不在前端留一份。
/**
 * 剧场规则 tab 下面的「术语与口径速查」。三组：剧场规则里的行话、ReelShort 观测口径、本页自己定的口径。
 * 每条先说是什么（d），h 是怎么用或要注意什么。
 */
export interface GlossaryTerm {
  t: string;
  /** 别名 / 上游字段名 */
  a?: string;
  /** false = 只服务页面 UI 的词条（tab、小图、取货按钮），不进问答的系统提示，省每一发的固定前缀；ruleText 仍认它的键（问答审计 P2-13） */
  ask?: false;
  d: string;
  h?: string;
}

export interface GlossaryGroup {
  g: string;
  d: string;
  items: readonly GlossaryTerm[];
}

/** 术语表的文本筛选：词条名、别名、正文、提示里任一处包含就留下（不区分大小写） */
export function filterGlossary(
  groups: readonly GlossaryGroup[],
  q: string,
): GlossaryGroup[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return [...groups];
  return groups
    .map((g) => ({
      ...g,
      items: g.items.filter((it) =>
        [it.t, it.a ?? "", it.d, it.h ?? ""]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      ),
    }))
    .filter((g) => g.items.length > 0);
}
