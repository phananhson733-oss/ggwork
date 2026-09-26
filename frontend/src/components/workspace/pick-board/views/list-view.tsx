// PORTED_FROM: realshort@816ca2e src/app/admin/(protected)/pick/page.tsx
// 本地改动：ListView / EmptyState 拆成同步的 ListBody（取数在页面）；规则经 ctx.rules 传给 Filters 与 RowsTable；
// 「表里还没有剧单」改成指向「同步与导入」；放宽条件的九个链接改成一张表（函数 <50 行），文案与顺序不变。
import Link from "next/link";

import {
  Empty,
  Filters,
  OutOfRange,
  Pager,
  pickHref,
} from "@/components/workspace/pick-board/toolbar";
import type { PickRequest } from "@/core/pick-board/request";
import type { PickFreshness } from "@/server/pick-board";

import { RowsTable } from "../rows-table";

import { IMPORTS_HREF } from "./banner-rules";
import type { BoardContext, ListData } from "./board-data";

const PICK_NOTE =
  "入选池 = 有任一剧场榜单 / 评级 / 清单 / 备注信号的剧单行，加 ReelShort 里满足候选条件（7 天有出站 / 有预估订单 / 有搜索匹配）的正典行；默认排除剧单下架表里的行。剧场信号是剧场自己的声明，不是需求证据；ReelShort 的数值是本站采集的原值。日期是那条证据自己的日期，没有就写日期未知。";
const ALL_NOTE =
  "九个剧场剧单的全部行加 ReelShort 全部正典行，用来搜索与补查。「同名未核」= 剧名归一后对上 ReelShort 片库，只是同名匹配，不是同剧确认。";
const LINK = "text-link hover:underline";

type Relax = Readonly<{
  key: string;
  when: (req: PickRequest) => boolean;
  patch: Partial<PickRequest>;
  text: string;
}>;

/** 当前条件下没有行时给的放宽出口，顺序与 RealShort 相同 */
const RELAX: readonly Relax[] = [
  {
    key: "off",
    when: (r) => !r.withOff,
    patch: { withOff: true },
    text: "含已下架",
  },
  {
    key: "wide",
    when: (r) => r.tab === "pick" && !r.wide,
    patch: { wide: true },
    text: "含仅剧单收录的行",
  },
  {
    key: "sig",
    when: (r) => r.tab === "all" && r.signalOnly,
    patch: { signalOnly: false },
    text: "含仅剧单收录的行",
  },
  {
    key: "inuse",
    when: (r) => r.inUseOnly,
    patch: { inUseOnly: false },
    text: "展开其他剧场",
  },
  {
    key: "yt",
    when: (r) => r.youtubeOk,
    patch: { youtubeOk: false },
    text: "去掉 YouTube 条件",
  },
  {
    key: "dated",
    when: (r) => r.datedOnly,
    patch: { datedOnly: false },
    text: "含日期未知的证据",
  },
  {
    key: "basis",
    when: (r) => r.basis !== "",
    patch: { basis: "" },
    text: "去掉依据条件",
  },
  {
    key: "posted",
    when: (r) => r.posted !== "",
    patch: { posted: "" },
    text: "去掉发布记录条件",
  },
  { key: "q", when: (r) => r.q !== "", patch: { q: "" }, text: "清掉搜索词" },
];

function EmptyState({
  req,
  fresh,
}: {
  req: PickRequest;
  fresh: PickFreshness;
}) {
  if (fresh.rows === 0 && fresh.rsCanonical === 0)
    return (
      <Empty>
        镜像里还没有剧单，
        <Link prefetch={false} href={IMPORTS_HREF} className={LINK}>
          看「同步与导入」
        </Link>
      </Empty>
    );
  const relax = RELAX.filter((r) => r.when(req));
  return (
    <Empty>
      当前条件下没有行。
      {relax.length ? (
        <span className="ml-2 inline-flex flex-wrap gap-3">
          {relax.map((r) => (
            <Link
              prefetch={false}
              key={r.key}
              href={pickHref(req, r.patch)}
              className={LINK}
            >
              {r.text}
            </Link>
          ))}
        </span>
      ) : null}
    </Empty>
  );
}

export function ListBody({
  data,
  req,
  ctx,
}: {
  data: ListData;
  req: PickRequest;
  ctx: BoardContext;
}) {
  const { rows, total, hasMore } = data.page;
  return (
    <>
      <Filters req={req} facets={data.facets} rules={ctx.rules} />
      <p className="text-helper mb-3 text-sm leading-[1.65]">
        {req.tab === "pick" ? PICK_NOTE : ALL_NOTE}
      </p>
      {rows.length === 0 ? (
        total > 0 ? (
          <OutOfRange req={req} total={total} />
        ) : (
          <EmptyState req={req} fresh={ctx.freshness} />
        )
      ) : (
        <>
          <RowsTable rows={rows} req={req} rules={ctx.rules} />
          <Pager
            req={req}
            hasMore={hasMore}
            count={rows.length}
            total={total}
          />
        </>
      )}
    </>
  );
}
