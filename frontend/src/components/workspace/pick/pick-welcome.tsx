"use client";

import Link from "next/link";

import { Eyebrow } from "@/components/brand/eyebrow";

// The pick quick actions live in the composer's suggestion row
// (inputBox.suggestions), so this header carries none of its own.
export function PickWelcome() {
  return (
    // Anchored above the welcome composer and grows upward, so no bottom
    // padding: the composer header already leaves the 28px gap.
    <div className="mx-auto max-w-xl space-y-3 px-4 text-center">
      <Eyebrow>个人选剧工作台</Eyebrow>
      <h1 className="text-[30px] leading-tight font-bold tracking-[-0.02em]">
        今天选什么剧？
      </h1>
      <p className="text-muted-foreground text-sm leading-[1.65]">
        结合你的剧库和规则，找到有依据的候选，继续追问，再保存到个人清单。
      </p>
      <Link
        href="/workspace/pick-data?tab=imports"
        className="text-link inline-block text-xs hover:underline"
      >
        第一次使用？先导入剧库与知识资料
      </Link>
    </div>
  );
}
