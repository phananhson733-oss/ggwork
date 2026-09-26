import { GitHubLogoIcon } from "@radix-ui/react-icons";
import Link from "next/link";

import { GGWorkWordmark } from "@/components/brand/ggwork-logo";
import { Button } from "@/components/ui/button";
import { DEFAULT_LOCALE, type Locale } from "@/core/i18n/locale";
import { getI18n } from "@/core/i18n/server";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { MobileNav } from "./mobile-nav";
import { StarCounter } from "./star-counter";

export type HeaderProps = {
  className?: string;
  homeURL?: string;
  locale?: Locale;
};

export async function Header({ className, homeURL, locale }: HeaderProps) {
  const { locale: resolvedLocale, t } = await getI18n(locale ?? DEFAULT_LOCALE);
  const lang = resolvedLocale.substring(0, 2);
  return (
    <header
      className={cn(
        "container-md fixed top-0 right-0 left-0 z-20 mx-auto flex h-16 items-center justify-between gap-3 px-4 backdrop-blur-xs",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-6">
        <Link href={homeURL ?? "/"} className="whitespace-nowrap">
          <GGWorkWordmark markSize={24} textClassName="text-lg" />
        </Link>
      </div>
      <nav className="ml-auto hidden items-center gap-5 text-sm font-medium sm:flex md:mr-8 md:gap-8">
        <Link
          href={`/${lang}/docs`}
          className="text-secondary-foreground hover:text-foreground transition-colors"
        >
          {t.home.docs}
        </Link>
        <Link
          href="/blog/posts"
          className="text-secondary-foreground hover:text-foreground transition-colors"
        >
          {t.home.blog}
        </Link>
      </nav>
      <div className="relative">
        <Button variant="outline" size="sm" asChild className="group">
          <a
            href="https://github.com/bytedance/deer-flow"
            target="_blank"
            rel="noopener noreferrer"
          >
            <GitHubLogoIcon className="size-4" />
            <span className="hidden sm:inline">Star on GitHub</span>
            {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && <StarCounter />}
          </a>
        </Button>
      </div>
      <MobileNav
        links={[
          { href: `/${lang}/docs`, label: t.home.docs },
          { href: "/blog/posts", label: t.home.blog },
        ]}
      />
      <hr className="bg-line absolute top-16 right-0 left-0 z-10 m-0 h-px w-full border-none" />
    </header>
  );
}
