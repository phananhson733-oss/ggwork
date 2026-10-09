"use client";
import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import { editingLabel } from "@/core/editing/presentation";

export const editingContentClass =
  "min-w-0 text-base break-words [&_button]:min-h-11 [&_button]:min-w-11 [&_button]:h-auto [&_button]:whitespace-normal [&_button]:text-base! [&_input:not([type=checkbox])]:min-h-11 [&_input]:text-base! [&_a]:inline-flex [&_a]:min-h-11 [&_a]:items-center [&_a]:py-2 [&_summary]:min-h-11 [&_summary]:text-base [&_:is(a,button,input,select,textarea,summary):focus-visible]:outline-2 [&_:is(a,button,input,select,textarea,summary):focus-visible]:outline-offset-2 [&_:is(a,button,input,select,textarea,summary):focus-visible]:outline-link [&_a:visited]:text-violet-ink motion-reduce:[&_*]:transition-none";

export function EditingStatus({ status }: { status: string }) {
  const color =
    status === "completed" || status === "verified"
      ? "bg-success-surface text-success-ink"
      : status === "failed"
        ? "bg-danger-surface text-danger-ink"
        : ["waiting", "stopping", "partial", "awaiting_plan"].includes(status)
          ? "bg-warning-surface text-warning-ink"
          : "bg-info-surface text-info-ink";
  return (
    <span className={`inline-flex rounded-md px-2 py-1 font-medium ${color}`}>
      {editingLabel(status)}
    </span>
  );
}

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
      className={`editing-workspace mx-auto w-full max-w-6xl overflow-y-auto p-4 md:p-6 ${editingContentClass}`}
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
    <div
      role="alert"
      className="border-danger-border bg-danger-surface text-danger-ink rounded-lg border p-4"
    >
      {children}
    </div>
  );
}
