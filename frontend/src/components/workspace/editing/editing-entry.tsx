"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { editingCreateHref } from "@/core/editing/presentation";

export function EditingEntry({ title }: { title: string }) {
  const pathname = usePathname();
  const search = useSearchParams();
  const returnTo = `${pathname}${search.size ? `?${search.toString()}` : ""}`;
  return (
    <Link
      href={editingCreateHref(title, returnTo)}
      prefetch={false}
      className="text-link inline-flex min-h-11 items-center rounded-md px-2 text-base underline focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      剪辑
    </Link>
  );
}
