// PORTED_FROM: realshort@816ca2e src/components/admin/pick/row-detail.tsx
// 本地改动：剧场规则与 YouTube 判定改读版本规则（rules 经 props 传入；版本里没有这个剧场时各项写「规则未知」）；
// 「素材」一格只说有没有网盘（链接与提取码不进镜像），有的话给 RealShort 证据页的外链；剧场文档链接按版本规则的
// 三种形态渲染（站内 Link / 外链 / 不出链接）；公开页是 ReelShort 站的绝对地址，新标签打开（★38）；
// Link 加 prefetch={false}；同名行截断时说明；整页拆成几个小组件（函数 <50 行）。
// GGWork 配色：YouTube 一格用与表格相同的语义 pill（YoutubePill），链接用 link 色。
import Link from "next/link";
import type { ReactNode } from "react";

import { EditingEntry } from "@/components/workspace/editing/editing-entry";
import { youtubeStatus, type PlatformRule } from "@/core/pick-board/platforms";
import {
  PLATFORM_LABELS,
  isDailyRank,
  reelshortRowKey,
  type PickRequest,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import { dramaPath, realshortRowUrl } from "@/core/pick-board/site";
import type { PickRow, RowDetail } from "@/server/pick-board";

import { FactTags, SignalPills, YoutubePill } from "./cells";
import { ExternalLink, RuleLink } from "./links";
import { PostedRecordCard } from "./posted-record";
import { QueyuButton } from "./queyu-button";
import { TAB_LABELS, pickHref, rowHref } from "./toolbar";

/**
 * 证据页：限制与资源 → 直接证据（逐条，附来源表）→ 我们的发布记录 → 同名核对小表（一律标「未核」）。
 * 结构与 artifact 的整页证据页相同；对上的 ReelShort 剧给一条链接到它自己的证据页（reelshort-detail.tsx）。
 */
const UNKNOWN_RULE = "规则未知";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-line bg-panel rounded-lg border p-4">
      <h3 className="mb-2 font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="bg-raised rounded-lg p-3">
      <div className="text-helper text-xs">{label}</div>
      <div className="mt-1 text-sm leading-[1.65]">{value}</div>
    </div>
  );
}

function payloadDetail(kind: string, payload: Record<string, unknown>): string {
  if (isDailyRank(kind)) {
    const h = payload.h as [string, number, string?][] | undefined;
    if (h?.length)
      return h.map(([d, r, n]) => `${d} #${r}${n ? ` ${n}` : ""}`).join("；");
  }
  if (kind === "kw") {
    const h = payload.h as [string, string][] | undefined;
    if (h?.length) return h.map(([d, w]) => `${w}（周起 ${d}）`).join("；");
  }
  return "";
}

/** 返回链接按来源 tab 回去（/qa 2026-09-11 ISSUE-001） */
function backHref(req: PickRequest): string {
  return pickHref(req, { tab: req.from, rowKey: "", page: req.page });
}

/** 标题下一行：剧场与来源表、语种、集数、剧单日期、row_key */
function RowFacts({ row }: { row: PickRow }) {
  return (
    <div className="text-helper mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
      <span>
        {PLATFORM_LABELS[row.platform]} · {row.sourceTable}
        {row.mergedRows > 1 ? ` · 由 ${row.mergedRows} 行合并` : ""}
      </span>
      <span>{row.lang || "语种未标"}</span>
      {row.episodes !== null ? (
        <span>
          {row.episodes} 集
          {row.payStart !== null ? ` · 第 ${row.payStart} 集起付费` : ""}
        </span>
      ) : null}
      <span>剧单日期 {row.listedOn ?? "未知"}</span>
      <code className="text-xs">{row.rowKey}</code>
    </div>
  );
}

function DetailHead({
  row,
  req,
  rules,
}: {
  row: PickRow;
  req: PickRequest;
  rules: BoardRules;
}) {
  return (
    <section className="border-line bg-panel rounded-lg border px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">
            {row.title}
            {row.titleCn ? (
              <span className="text-helper ml-2 text-sm font-normal">
                {row.titleCn}
              </span>
            ) : null}
          </h2>
          <RowFacts row={row} />
          <div className="mt-2">
            <SignalPills row={row} rules={rules} />
          </div>
          <FactTags row={row} rules={rules} />
        </div>
        <div className="flex flex-col items-end gap-2">
          <EditingEntry title={row.title} />
          <QueyuButton title={row.title} off={Boolean(row.offOn)} />
          <Link
            prefetch={false}
            href={backHref(req)}
            className="text-helper hover:text-ink-1 text-[12px]"
          >
            ← 返回{TAB_LABELS[req.from]}
          </Link>
        </div>
      </div>
    </section>
  );
}

/** 素材：剧场怎么给素材（版本规则）+ 这一行剧单附没附网盘 */
function Material({
  row,
  rule,
}: {
  row: PickRow;
  rule: PlatformRule | undefined;
}) {
  return (
    <>
      {rule ? rule.material : UNKNOWN_RULE}
      <br />
      {row.hasPan ? (
        <span className="text-helper text-xs">
          有网盘（网盘信息不同步到本页，到 RealShort 证据页查看）{" "}
          <ExternalLink
            href={realshortRowUrl(row.rowKey)}
            className="text-link hover:underline"
          >
            RealShort 证据页 ↗
          </ExternalLink>
        </span>
      ) : (
        <span className="text-helper text-xs">这一行剧单没附网盘</span>
      )}
    </>
  );
}

function RuleFacts({ row, rules }: { row: PickRow; rules: BoardRules }) {
  const rule = rules.platformRules[row.platform];
  const yt = youtubeStatus(rules, row.platform, row.youtube);
  const text = (pick: (r: PlatformRule) => string) =>
    rule ? pick(rule) : UNKNOWN_RULE;
  return (
    <Section title="限制与资源">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Fact
          label="YouTube"
          value={
            <>
              <YoutubePill kind={yt.kind} blocked={yt.blocked}>
                {yt.label}
              </YoutubePill>
              <div className="text-helper mt-1 text-xs">
                {rule?.ytNote ?? ""}
              </div>
            </>
          }
        />
        <Fact label="报备" value={text((r) => r.report)} />
        <Fact label="必带 tag" value={text((r) => r.tag)} />
        <Fact label="解禁" value={text((r) => r.unban)} />
        <Fact label="素材" value={<Material row={row} rule={rule} />} />
        <Fact label="结算" value={text((r) => r.back)} />
        <Fact label="下架记录" value={<OffRecord row={row} />} />
        <Fact label="剧场文档" value={<RuleDoc rule={rule} />} />
      </div>
    </Section>
  );
}

function OffRecord({ row }: { row: PickRow }) {
  if (row.offOn) return <>剧单下架表：{row.offOn}</>;
  if (row.reoffNote) return <>{row.reoffNote}</>;
  return (
    <span className="text-helper">
      未记录下架（不等于已确认可取，到鹊娱核对）
    </span>
  );
}

function RuleDoc({ rule }: { rule: PlatformRule | undefined }) {
  if (!rule) return <span className="text-helper">{UNKNOWN_RULE}</span>;
  return (
    <>
      <RuleLink
        href={rule.doc}
        className="text-link hover:underline"
        fallback={<span className="text-helper">版本里没有可用的链接</span>}
      >
        {rule.doc.startsWith("/") ? "打开 ›" : "飞书 ↗"}
      </RuleLink>
      <span className="text-helper ml-2 text-xs">最后核对 {rule.updated}</span>
    </>
  );
}

function SignalsTable({ row, rules }: { row: PickRow; rules: BoardRules }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-helper text-left text-xs">
          <th scope="col" className="py-1 pr-3">
            依据
          </th>
          <th scope="col" className="py-1 pr-3">
            日期
          </th>
          <th scope="col" className="py-1 pr-3">
            内容
          </th>
          <th scope="col" className="py-1">
            历史
          </th>
        </tr>
      </thead>
      <tbody>
        {row.signals.map((s) => (
          <tr
            key={`${s.kind}-${s.ord}`}
            className="border-line border-t align-top"
          >
            <td className="py-2 pr-3 whitespace-nowrap">
              {rules.basisLabels[s.kind]}
            </td>
            <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
              {s.evidenceOn ? (
                `${rules.basisDateLabels[s.kind] || "日期"} ${s.evidenceOn}`
              ) : (
                <span className="text-ink-dim">日期未知</span>
              )}
            </td>
            <td className="py-2 pr-3">
              {s.rank !== null ? `名次 #${s.rank}` : ""}
              {s.grade ? `评级 ${s.grade}` : ""}
              {s.note ? <span className="ml-1">{s.note}</span> : null}
            </td>
            <td className="text-helper py-2 text-xs">
              {payloadDetail(s.kind, s.payload) || "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SiteDramas({ detail, req }: { detail: RowDetail; req: PickRequest }) {
  if (detail.siteDramas.length === 0) return null;
  return (
    <div>
      <div className="text-helper mb-1 text-xs">
        ReelShort 片库里的同名正典行（本站在售）
      </div>
      <ul className="flex flex-col gap-1">
        {detail.siteDramas.map((d) => (
          <li key={d.id} className="flex flex-wrap gap-x-3">
            <span>{d.title}</span>
            <span className="text-helper">{d.locale}</span>
            <span className="text-helper">
              {d.chapterCount} 集
              {d.payStart > 0 ? ` · 第 ${d.payStart} 集起付费` : ""}
            </span>
            <ExternalLink
              href={dramaPath(d.locale, d.slug)}
              className="text-link hover:underline"
            >
              公开页
            </ExternalLink>
            <Link
              prefetch={false}
              href={rowHref(req, reelshortRowKey(d.id))}
              className="text-link hover:underline"
            >
              证据页
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SameTitleRows({
  detail,
  req,
}: {
  detail: RowDetail;
  req: PickRequest;
}) {
  if (detail.sameTitle.length === 0) return null;
  return (
    <div>
      <div className="text-helper mb-1 text-xs">其它剧场剧单里的同名行</div>
      <ul className="flex flex-col gap-1">
        {detail.sameTitle.map((s) => (
          <li key={s.rowKey} className="flex flex-wrap gap-x-3">
            <Link
              prefetch={false}
              href={rowHref(req, s.rowKey)}
              className="hover:underline"
            >
              {s.title}
            </Link>
            {s.titleCn ? (
              <span className="text-helper">{s.titleCn}</span>
            ) : null}
            <span className="text-helper">
              {PLATFORM_LABELS[s.platform]} · {s.lang || "语种未标"}
            </span>
          </li>
        ))}
      </ul>
      {detail.sameTitleTruncated ? (
        <p className="text-helper mt-1 text-xs">
          只列了前 {detail.sameTitle.length} 行，可能还有。
        </p>
      ) : null}
    </div>
  );
}

function SameTitleCheck({
  detail,
  req,
}: {
  detail: RowDetail;
  req: PickRequest;
}) {
  const { row, siteDramas, sameTitle } = detail;
  const none =
    siteDramas.length === 0 &&
    sameTitle.length === 0 &&
    !row.siteOther &&
    !row.legacyOnly;
  return (
    <Section title="同名核对（一律未核：同名不等于同剧）">
      {none ? (
        <p className="text-helper text-sm">
          ReelShort 片库、旧域名收录与其它剧场剧单里都没有同名行。
        </p>
      ) : (
        <div className="flex flex-col gap-3 text-sm">
          <SiteDramas detail={detail} req={req} />
          {row.siteOther ? (
            <p className="text-helper">
              ReelShort 片库里有同名剧，但只有别的语种版本在售。
            </p>
          ) : null}
          {row.legacyOnly ? (
            <p className="text-helper">
              旧域名 sitemap 收录过同名剧，ReelShort
              片库里没有同名行；这只说明旧站当年挂过，不是需求证据。
            </p>
          ) : null}
          <SameTitleRows detail={detail} req={req} />
        </div>
      )}
    </Section>
  );
}

export function RowDetailView({
  detail,
  req,
  rules,
}: {
  detail: RowDetail;
  req: PickRequest;
  rules: BoardRules;
}) {
  const { row, postedRecords } = detail;
  return (
    <div className="flex flex-col gap-4">
      <DetailHead row={row} req={req} rules={rules} />
      <RuleFacts row={row} rules={rules} />
      <Section title={`直接证据 · ${row.signals.length} 条`}>
        {row.signals.length === 0 ? (
          <p className="text-helper text-sm">
            这一行没有榜单 / 评级 / 清单 / 备注信号，只是在剧单上（剧单日期{" "}
            {row.listedOn ?? "未知"}）。
          </p>
        ) : (
          <SignalsTable row={row} rules={rules} />
        )}
      </Section>
      {postedRecords.length ? (
        <Section title={`我们的发布记录 · ${postedRecords.length} 条`}>
          <div className="flex flex-col gap-3">
            {postedRecords.map((p) => (
              <PostedRecordCard key={p.sd} p={p} req={req} />
            ))}
          </div>
        </Section>
      ) : null}
      <SameTitleCheck detail={detail} req={req} />
    </div>
  );
}
