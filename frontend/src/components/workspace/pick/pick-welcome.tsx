"use client";

import Link from "next/link";

import { usePromptInputController } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";

export function PickWelcome() {
  const { textInput } = usePromptInputController();
  return (
    <div className="mx-auto max-w-xl space-y-4 px-4 py-6 text-center">
      <p className="text-muted-foreground text-xs tracking-widest">
        个人选剧工作台
      </p>
      <h1 className="text-2xl font-semibold">今天选什么剧？</h1>
      <p className="text-muted-foreground text-sm leading-6">
        结合你的剧库和规则，找到有依据的候选，继续追问，再保存到个人清单。
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {["找5部英语剧，排除我已经选过的", "看看DramaBox的英语剧"].map(
          (text) => (
            <Button
              key={text}
              type="button"
              size="sm"
              variant="outline"
              onClick={() => textInput.setInput(text)}
            >
              {text}
            </Button>
          ),
        )}
      </div>
      <Link
        href="/workspace/pick-data"
        className="text-muted-foreground inline-block text-xs underline"
      >
        第一次使用？先导入剧库与知识资料
      </Link>
    </div>
  );
}
