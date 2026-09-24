// 工作台新建：把 bannersFor 算出的横幅渲染成提示条。状态说明 role="status"，失败或数据可能不全 role="alert"。
import Link from "next/link";

import type { Banner } from "./banner-rules";

const STYLE: Record<Banner["role"], string> = {
  status: "border-line bg-info-surface text-info-ink",
  alert: "border-warning-line bg-warning-surface text-warning-ink",
};

export function Banners({ banners }: { banners: readonly Banner[] }) {
  if (banners.length === 0) return null;
  return (
    <div className="mb-4 flex flex-col gap-2">
      {banners.map((b) => (
        <p
          key={b.key}
          role={b.role}
          className={`rounded-[10px] border px-4 py-2.5 text-[13px] leading-relaxed ${STYLE[b.role]}`}
        >
          {b.text}
          {b.link ? (
            <Link
              prefetch={false}
              href={b.link.href}
              className="text-brand ml-2 hover:underline"
            >
              {b.link.text}
            </Link>
          ) : null}
        </p>
      ))}
    </div>
  );
}
