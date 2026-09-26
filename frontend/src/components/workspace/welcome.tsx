"use client";

import { useSearchParams } from "next/navigation";

import { Eyebrow } from "@/components/brand/eyebrow";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

function WelcomeDescription({ children }: { children: string }) {
  return (
    <p className="text-helper max-w-[520px] text-center text-sm leading-[1.65] text-wrap break-words whitespace-pre-line">
      {children}
    </p>
  );
}

export function Welcome({
  className,
}: {
  className?: string;
  mode?: "ultra" | "pro" | "thinking" | "flash";
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const isSkillMode = searchParams.get("mode") === "skill";
  return (
    <div
      className={cn(
        "mx-auto flex w-full max-w-full flex-col items-center justify-center gap-3 px-4 text-center sm:px-8",
        className,
      )}
    >
      <Eyebrow>{t.welcome.eyebrow}</Eyebrow>
      <h1 className="text-ink-1 max-w-full text-[30px] font-bold tracking-[-0.02em]">
        {isSkillMode ? t.welcome.createYourOwnSkill : t.welcome.greeting}
      </h1>
      <WelcomeDescription>
        {isSkillMode
          ? t.welcome.createYourOwnSkillDescription
          : t.welcome.description}
      </WelcomeDescription>
    </div>
  );
}
