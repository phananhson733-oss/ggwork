// PORTED_FROM: realshort@816ca2e src/components/admin/pick/rank-filters.tsx
// 本地改动：榜名、剧场榜依据与 rs 排序的标签改读版本规则（rules.rsRankLabels / basisLabels / sortLabels；
// 订单对账的标签由 buildBoardRules 固定成「ReelShort 订单对账」）；「更早…」表单的 action 改成 /workspace/pick-data
// 并带隐藏的 v；ReelShort 与剧场榜两排 chips、rs 榜的排序 / 语种 / 上线筛选、「更早…」的隐藏字段拆成小组件（函数 <50 行）。
import { LOCALE_CODES, localeFromCode } from "@/core/pick-board/lang";
import { BUCKETS, bucketLabel } from "@/core/pick-board/metrics";
import {
  GRADES,
  GROWTH_SORTS,
  PAGE_SIZE,
  RANK_DEFAULT,
  RS_RANKS,
  RS_SORTS,
  THEATER_BASES,
  isRsRank,
  weekText,
  type PickRequest,
  type RsRank,
  type RsSort,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { RankMeta } from "@/server/pick-board";

import { BOARD_PATH, Chips, pickHref } from "./toolbar";

/**
 * 榜单 tab 的子筛选：先选哪张榜（artifact 的 subtabs 分「ReelShort」「剧场榜」两段），再按那张榜自己的维度筛
 * （ReelShort：排序 / 语种 / 上线分桶；日榜按天、周榜按周、评级榜按档）。
 * 全部走 URL 参数；日期与周超出 chips 的部分用一个 GET 表单的 select 兜住——
 * 服务端组件没有 onChange，所以旁边配一个「查看」按钮。
 */
const DAY_CHIPS = 14;
const WEEK_CHIPS = 8;

/** rs 榜的 chips：涨幅榜 / 候选清单攒够快照前是 0 也要露出来，那个空态本身就是信息（与 artifact 同） */
const ALWAYS: readonly RsRank[] = ["rs_growth", "rs_cand"];

/** 换一张榜时把上一张榜自己的维度清掉 */
const RESET: Partial<PickRequest> = {
  day: "",
  week: "",
  grade: "",
  rsSort: "rr",
  rsLocale: "",
  rsBucket: null,
};

/** 这张 rs 榜能按什么排：涨幅榜用涨幅排序，大盘榜与候选清单用通用排序，其余榜不给排序 */
function sortsFor(kind: RsRank): readonly RsSort[] {
  if (kind === "rs_growth") return GROWTH_SORTS;
  return kind === "rs_rr" || kind === "rs_cand" ? RS_SORTS : [];
}

function LocaleBucketChips({ req }: { req: PickRequest }) {
  return (
    <>
      <Chips
        label="语种"
        current={req.rsLocale}
        items={[
          { value: "", text: "全部" },
          ...LOCALE_CODES.map((l) => ({
            value: l,
            text: `${localeFromCode(l)?.cnName ?? l} ${l}`,
          })),
        ]}
        build={(v) => pickHref(req, { rsLocale: v })}
      />
      <Chips
        label="上线"
        current={req.rsBucket ?? ""}
        items={[
          { value: "", text: "全部" },
          ...BUCKETS.map((b) => ({ value: b, text: bucketLabel(b) })),
        ]}
        build={(v) =>
          pickHref(req, { rsBucket: (v || null) as PickRequest["rsBucket"] })
        }
      />
    </>
  );
}

function RsFilters({
  req,
  kind,
  rules,
}: {
  req: PickRequest;
  kind: RsRank;
  rules: BoardRules;
}) {
  const sorts = sortsFor(kind);
  const sortCurrent =
    kind === "rs_growth" && !GROWTH_SORTS.includes(req.rsSort)
      ? "d7"
      : req.rsSort;
  return (
    <>
      {sorts.length ? (
        <Chips
          label="排序"
          current={sortCurrent}
          items={sorts.map((s) => ({ value: s, text: rules.sortLabels[s] }))}
          build={(v) => pickHref(req, { rsSort: v as PickRequest["rsSort"] })}
        />
      ) : null}
      <LocaleBucketChips req={req} />
    </>
  );
}

function RankChips({
  req,
  meta,
  rules,
}: {
  req: PickRequest;
  meta: RankMeta;
  rules: BoardRules;
}) {
  const kind = req.rank;
  const build = (v: string) =>
    pickHref(req, { rank: v as PickRequest["rank"], ...RESET });
  return (
    <>
      <Chips
        label="ReelShort"
        current={kind}
        items={RS_RANKS.filter(
          (k) => (meta.counts[k] ?? 0) > 0 || ALWAYS.includes(k) || k === kind,
        ).map((k) => ({
          value: k,
          text: rules.rsRankLabels[k].replace(/^ReelShort /, ""),
          count: meta.counts[k] ?? 0,
        }))}
        build={build}
      />
      <Chips
        label="剧场榜"
        current={kind}
        items={THEATER_BASES.filter(
          (b) => (meta.counts[b] ?? 0) > 0 || b === kind,
        ).map((b) => ({
          value: b,
          text: rules.basisLabels[b],
          count: meta.counts[b] ?? 0,
        }))}
        build={build}
      />
    </>
  );
}

function PeriodFilters({ req, meta }: { req: PickRequest; meta: RankMeta }) {
  const kind = req.rank;
  if (kind === "kd")
    return (
      <DateChips
        label="日期"
        name="day"
        values={meta.days}
        current={meta.day}
        shown={DAY_CHIPS}
        req={req}
        text={(d) => d.slice(5)}
        build={(d) => pickHref(req, { day: d })}
      />
    );
  if (kind === "kw")
    return (
      <DateChips
        label="周"
        name="week"
        values={meta.weeks.map((w) => w.start)}
        current={meta.week}
        shown={WEEK_CHIPS}
        req={req}
        text={weekText(meta.weeks)}
        optionText={(s) => `${weekText(meta.weeks)(s)}（周起 ${s}）`}
        build={(s) => pickHref(req, { week: s })}
      />
    );
  if (kind === "sm" || kind === "mg")
    return (
      <Chips
        label="评级"
        current={req.grade}
        items={[
          { value: "", text: "全部" },
          ...GRADES.filter(
            (g) => (meta.grades[g] ?? 0) > 0 || g === req.grade,
          ).map((g) => ({ value: g, text: g, count: meta.grades[g] ?? 0 })),
        ]}
        build={(v) => pickHref(req, { grade: v as PickRequest["grade"] })}
      />
    );
  return null;
}

export function RankFilters({
  req,
  meta,
  rules,
}: {
  req: PickRequest;
  meta: RankMeta;
  rules: BoardRules;
}) {
  const kind = req.rank;
  return (
    <div className="mb-3.5 flex flex-col gap-2.5">
      <RankChips req={req} meta={meta} rules={rules} />
      {isRsRank(kind) && kind !== "rs_ledger" ? (
        <RsFilters req={req} kind={kind} rules={rules} />
      ) : null}
      <PeriodFilters req={req} meta={meta} />
    </div>
  );
}

/** 「更早…」表单带上的隐藏字段：tab、非默认的榜与每页行数、版本 v */
function EarlierHidden({ req }: { req: PickRequest }) {
  return (
    <>
      <input type="hidden" name="tab" value="rank" />
      {req.rank !== RANK_DEFAULT ? (
        <input type="hidden" name="rk" value={req.rank} />
      ) : null}
      {req.size !== PAGE_SIZE ? (
        <input type="hidden" name="size" value={req.size} />
      ) : null}
      {req.v !== null ? <input type="hidden" name="v" value={req.v} /> : null}
    </>
  );
}

/** 「更早…」：超出 chips 的日期 / 周用一个 GET 表单兜住，带着榜、每页行数与版本 v */
function EarlierForm({
  label,
  name,
  rest,
  current,
  req,
  optionText,
}: {
  label: string;
  name: "day" | "week";
  rest: readonly string[];
  current: string;
  req: PickRequest;
  optionText?: (v: string) => string;
}) {
  return (
    <form action={BOARD_PATH} method="get" className="flex items-center gap-1">
      <EarlierHidden req={req} />
      <label htmlFor={`pick-${name}-more`} className="sr-only">
        更早的{label}
      </label>
      {/* required：没选就点「查看」由浏览器拦下（「请选择一项」），不再提交空的 day= / week= 静默回落到最近一天 / 周
          （/qa 2026-09-11 ISSUE-006）。占位项 value="" 是 HTML 的 placeholder label option，required 才认它是「没选」 */}
      <select
        id={`pick-${name}-more`}
        name={name}
        defaultValue={rest.includes(current) ? current : ""}
        required
        className="border-line bg-panel rounded-full border px-2 py-1 text-[12px]"
      >
        <option value="">更早…</option>
        {rest.map((v) => (
          <option key={v} value={v}>
            {optionText ? optionText(v) : v}
          </option>
        ))}
      </select>
      <button
        type="submit"
        className="border-line hover:border-brand/50 rounded-full border px-2.5 py-1 text-[12px]"
      >
        查看
      </button>
    </form>
  );
}

function DateChips({
  label,
  name,
  values,
  current,
  shown,
  req,
  text,
  optionText,
  build,
}: {
  label: string;
  name: "day" | "week";
  values: readonly string[];
  current: string;
  shown: number;
  req: PickRequest;
  text: (v: string) => string;
  /** 「更早…」下拉里的文字；不给就印原值（日期） */
  optionText?: (v: string) => string;
  build: (v: string) => string;
}) {
  const head = values.slice(0, shown);
  const rest = values.slice(shown);
  /* 选中的在「更早」里时把它顶到 chips 末尾，否则页面上看不出当前选的是哪天 */
  const chips = current && !head.includes(current) ? [...head, current] : head;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Chips
        label={label}
        current={current}
        items={chips.map((v) => ({ value: v, text: text(v) }))}
        build={build}
      />
      {rest.length ? (
        <EarlierForm
          label={label}
          name={name}
          rest={rest}
          current={current}
          req={req}
          optionText={optionText}
        />
      ) : null}
    </div>
  );
}
