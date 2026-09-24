/**
 * The ReelShort evidence page's own additions (review of P3-4): the curve
 * note about trimmed points, the fallback when the version has no reelshort
 * rule, and order rows that stay apart when sibling book_ids share a day
 * and a promotion type.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";

import { ReelshortDetailView } from "@/components/workspace/pick-board/reelshort-detail";

import {
  AS_OF,
  billRow,
  boardRules,
  reelshortDetail,
  request,
} from "./fixtures";

rs.mock("next/link", () => ({
  default: ({
    href,
    prefetch,
    children,
    ...rest
  }: {
    href: string;
    prefetch?: boolean;
    children: ReactNode;
  }) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(cleanup);

const rules = boardRules();
const TRIMMED = "的曲线点已清理";

function detailText(
  props: Partial<Parameters<typeof ReelshortDetailView>[0]> = {},
): string {
  const { container } = render(
    <ReelshortDetailView
      detail={reelshortDetail()}
      req={request()}
      requestedId="demo0001"
      asOf={AS_OF}
      rules={rules}
      {...props}
    />,
  );
  const text = container.textContent ?? "";
  cleanup();
  return text;
}

describe("the trimmed-curve note", () => {
  /* AS_OF is 2026-09-24: the 90 drawn days run from 2026-06-27 to 2026-09-24 */
  it("shows when the trim cuts into the drawn days", () => {
    expect(detailText({ seriesTrimmedBefore: "2026-06-28" })).toContain(
      `早于 2026-06-28 ${TRIMMED}`,
    );
  });

  it("is absent when the trim is at or before the first drawn day, or unknown", () => {
    expect(detailText({ seriesTrimmedBefore: "2026-06-27" })).not.toContain(
      TRIMMED,
    );
    expect(detailText({ seriesTrimmedBefore: "2026-01-01" })).not.toContain(
      TRIMMED,
    );
    expect(detailText({ seriesTrimmedBefore: null })).not.toContain(TRIMMED);
    expect(detailText()).not.toContain(TRIMMED);
  });

  it("ignores a value that is not a day", () => {
    expect(detailText({ seriesTrimmedBefore: "not-a-day" })).not.toContain(
      TRIMMED,
    );
  });
});

describe("the reelshort rule", () => {
  it("says the rule is unknown when the version has none", () => {
    const missing = boardRules((raw) => {
      raw.platformRules = Object.fromEntries(
        Object.entries(raw.platformRules).filter(
          ([key]) => key !== "reelshort",
        ),
      ) as typeof raw.platformRules;
    });
    expect(detailText({ rules: missing })).toContain("规则未知");
    expect(detailText()).not.toContain("规则未知");
  });
});

describe("order rows of sibling book_ids", () => {
  it("name the book_id when it is not the canonical one", () => {
    const { container } = render(
      <ReelshortDetailView
        detail={reelshortDetail({
          bill: [
            billRow({ bookId: "demo0001", orderCnt: 5 }),
            billRow({ bookId: "demo0002", orderCnt: 2 }),
          ],
        })}
        req={request()}
        requestedId="demo0001"
        asOf={AS_OF}
        rules={rules}
      />,
    );
    const section = Array.from(container.querySelectorAll("section")).find(
      (s) => s.querySelector("h3")?.textContent?.startsWith("订单明细"),
    );
    const [own, sibling] = Array.from(
      section?.querySelectorAll("tbody tr") ?? [],
    );
    expect(own?.textContent).not.toContain("demo0001");
    expect(sibling?.textContent).toContain("demo0002");
  });
});
