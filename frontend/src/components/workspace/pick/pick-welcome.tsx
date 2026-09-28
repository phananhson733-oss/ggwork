"use client";

import Link from "next/link";

import { usePromptInputController } from "@/components/ai-elements/prompt-input";
import { Eyebrow } from "@/components/brand/eyebrow";

const SUGGESTIONS = [
  "找5部英语剧，排除我已经选过的",
  "看看DramaBox的英语剧",
] as const;

export function PickWelcome() {
  const { textInput } = usePromptInputController();
  return (
    // Sits directly above the welcome composer, so no bottom padding: the
    // chat page's welcome slot already leaves the 28px gap.
    <div className="mx-auto max-w-xl space-y-3 px-4 text-center">
      <Eyebrow>个人选剧工作台</Eyebrow>
      <h1 className="text-[30px] leading-tight font-bold tracking-[-0.02em]">
        今天选什么剧？
      </h1>
      <p className="text-muted-foreground text-sm leading-[1.65]">
        结合你的剧库和规则，找到有依据的候选，继续追问，再保存到个人清单。
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => textInput.setInput(text)}
            className="border-line bg-surface text-ink-2 hover:bg-hover focus-visible:border-link focus-visible:ring-brand-soft rounded-full border px-3 py-1.5 text-[12.5px] transition-colors outline-none focus-visible:ring-[3px]"
          >
            {text}
          </button>
        ))}
      </div>
      <Link
        href="/workspace/pick-data?tab=imports"
        className="text-link inline-block text-xs hover:underline"
      >
        第一次使用？先导入剧库与知识资料
      </Link>
    </div>
  );
}
