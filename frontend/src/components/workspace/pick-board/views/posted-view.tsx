// PORTED_FROM: realshort@816ca2e src/app/admin/(protected)/pick/page.tsx
// 本地改动：PostedView / PostedRecordView 拆成同步的 PostedBody / PostedRecordBody（取数在页面）；
// 「还没有导入过发布记录」不再指向 RealShort 的脚本；选剧池链接取版本规则 rules.postedPoolUrl；所有 Link 不预取。
import Link from "next/link";

import {
  Empty,
  OutOfRange,
  Pager,
  pickHref,
} from "@/components/workspace/pick-board/toolbar";
import type { PickRequest } from "@/core/pick-board/request";

import { AccountsTable } from "../accounts-table";
import { PostedRecordCard } from "../posted-record";
import { PostedFilters, PostedNote, PostedTable } from "../posted-table";

import type { BoardContext, PostedData, PostedRecordData } from "./board-data";

function PostedEmpty({ req, none }: { req: PickRequest; none: boolean }) {
  return (
    <Empty>
      {none
        ? "这个版本里还没有发布记录（RealShort 还没导入过选剧池）。"
        : "当前条件下没有记录。"}
      {req.q ? (
        <Link
          prefetch={false}
          href={pickHref(req, { q: "" })}
          className="text-link ml-2 hover:underline"
        >
          清掉搜索词
        </Link>
      ) : null}
    </Empty>
  );
}

export function PostedBody({
  data,
  req,
  ctx,
}: {
  data: PostedData;
  req: PickRequest;
  ctx: BoardContext;
}) {
  const { list, stats, accounts } = data;
  return (
    <>
      <PostedFilters req={req} counts={list.counts} />
      <PostedNote stats={stats} shown={list.total} rules={ctx.rules} />
      {list.rows.length === 0 && list.total > 0 ? (
        <OutOfRange req={req} total={list.total} />
      ) : list.rows.length === 0 ? (
        <PostedEmpty req={req} none={stats.total === 0} />
      ) : (
        <>
          <PostedTable rows={list.rows} links={list.links} req={req} />
          <Pager
            req={req}
            hasMore={list.hasMore}
            count={list.rows.length}
            total={list.total}
          />
        </>
      )}
      <AccountsTable accounts={accounts} />
    </>
  );
}

export function PostedRecordBody({
  data,
  req,
}: {
  data: PostedRecordData;
  req: PickRequest;
}) {
  const back = (
    <Link
      prefetch={false}
      href={pickHref(req, { sd: "", page: req.page })}
      className="text-helper hover:text-ink-1 text-[12px]"
    >
      ← 返回发布记录
    </Link>
  );
  if (!data.found)
    return (
      <Empty>
        找不到这条记录（{req.sd}）。剧ID
        来自飞书选剧池，上一次导入之后它可能已被删掉或改号。
        <div className="mt-2">{back}</div>
      </Empty>
    );
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">{data.found.record.title}</h2>
        {back}
      </div>
      <PostedRecordCard
        p={data.found.record}
        req={req}
        links={data.found.links}
      />
    </div>
  );
}
