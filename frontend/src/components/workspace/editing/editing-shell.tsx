"use client";
import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";

import { useAuth } from "@/core/auth/AuthProvider";

export function EditingShell({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const { user } = useAuth();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [title]);
  return (
    <main
      className="editing-workspace mx-auto w-full max-w-6xl overflow-y-auto p-4 text-base md:p-6 [&_button]:min-h-11 [&_button]:text-base! [&_input]:text-base!"
      style={{
        fontFamily:
          '"Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif',
      }}
    >
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {title}
        </h1>
        <Link
          href="/workspace/editing"
          className="text-link min-h-11 py-2 underline"
        >
          剪辑任务
        </Link>
      </header>
      {user ? (
        <div key={user.id}>{children}</div>
      ) : (
        <p role="alert">请重新登录后查看剪辑任务。登录恢复后可继续当前请求。</p>
      )}
    </main>
  );
}
export function EditingNotice({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="border-danger-border bg-danger-surface text-danger-ink rounded-lg border p-4"
    >
      {children}
    </p>
  );
}
