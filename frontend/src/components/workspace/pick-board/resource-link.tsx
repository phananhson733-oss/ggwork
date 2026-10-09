import Link from "next/link";

/** The protected resource page reads current private data only after explicit navigation. */
export function ResourceLink({ rowKey }: { rowKey: string }) {
  return (
    <Link
      prefetch={false}
      className="text-link hover:underline"
      href={`/workspace/pick-resources?row=${encodeURIComponent(rowKey)}`}
      target="_blank"
      rel="noopener noreferrer"
    >
      当前取货资料 ↗
    </Link>
  );
}
