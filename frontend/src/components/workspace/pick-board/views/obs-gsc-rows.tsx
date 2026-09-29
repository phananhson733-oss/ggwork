// 工作台新建（TR-24）：一行 GSC 判定（身份 × 国家或全站 × 窗口）的展示，search tab 与详情页共用（设计 5.8、2.2）。
// 正式标签与描述性标签分开列，都带采集端写下的命中条件与原始计数；没观测到的计数写「未观测到」，从不写成 0（前提 1）。
// 24 小时行按集合的 formal_24h_window 标「完整」或「暂定」；7 天窗口是最近 7 个完整 PT 日。准入只说「两份下界一致（准入）」，
// 不说完整、已核实或独立（前提 2）。有可粘贴行时给纯文本，一次选中整行复制。
import { GSC_STATE_TEXT } from "@/core/pick/obs-format";
import type {
  ObsGscSummary,
  ObsLabelHit,
  ObsState,
} from "@/core/pick/obs-rows";
import {
  ADMISSION_AGREED,
  GSC_FLAG_TEXT,
  labelCountsText,
  pasteRowText,
  textOf,
  WINDOW_COMPLETE,
  WINDOW_PROVISIONAL,
  WINDOW_TEXT,
} from "@/core/pick/obs-wording";
import type { PickRequest } from "@/core/pick-board/request";

import { IdentityLink, MUTED } from "./obs-parts";

const CARD = "border-line border-b py-2.5 last:border-b-0";
const PASTE =
  "border-line bg-raised mt-1 rounded border px-2 py-1 font-mono text-[12px] break-all whitespace-pre-wrap select-all";

export function scopeText(scope: string): string {
  return scope === "ALL" ? "全站合计" : scope;
}

/** 24-hour rows are complete only when the set had a formal 24-hour window; the 7-day window is 7 complete PT days. */
function windowMark(row: ObsState, summary: ObsGscSummary | null): string {
  if (row.window_kind !== "24h") return WINDOW_COMPLETE;
  return summary?.formal_24h_window ? WINDOW_COMPLETE : WINDOW_PROVISIONAL;
}

function admissionText(row: ObsState): string {
  return row.admission === "formal"
    ? `正式（${ADMISSION_AGREED}）`
    : "描述性（只作描述）";
}

function HitLine({ hit }: { hit: ObsLabelHit }) {
  return (
    <li data-obs-label={hit.label} data-obs-formal={String(hit.formal)}>
      {textOf(GSC_STATE_TEXT, hit.label)}
      <span className={MUTED}>
        {" "}
        — 条件：{hit.condition}；原始计数：{labelCountsText(hit.counts)}
      </span>
    </li>
  );
}

function Hits({ labels }: { labels: readonly ObsLabelHit[] }) {
  if (labels.length === 0) return <p className={MUTED}>没有命中任何标签。</p>;
  const groups = [
    ["正式标签", labels.filter((hit) => hit.formal)],
    ["描述性标签", labels.filter((hit) => !hit.formal)],
  ] as const;
  return (
    <>
      {groups.map(([name, hits]) =>
        hits.length === 0 ? null : (
          <div key={name}>
            <span className={MUTED}>{name}：</span>
            <ul className="ml-4 list-disc">
              {hits.map((hit) => (
                <HitLine key={`${hit.label}|${hit.formal}`} hit={hit} />
              ))}
            </ul>
          </div>
        ),
      )}
    </>
  );
}

function QualityNote({ row }: { row: ObsState }) {
  const note = row.quality_note;
  if (!note) return null;
  const tested =
    note.tested && note.p_value !== null && note.bh_adjusted !== null
      ? `p = ${note.p_value}，BH 校正后 ${note.bh_adjusted}（q = ${note.bh_q}）`
      : "没有做检验";
  return (
    <p className={MUTED}>
      质量注记（只作注记，不是门槛）：{note.note}；{tested}
    </p>
  );
}

function PasteRow({ row }: { row: ObsState }) {
  if (!row.paste_row) return null;
  return (
    <div data-obs-paste-row="true">
      <p className={MUTED}>
        可粘贴行（编辑精选表的七列，制表符分隔；选中整行复制）：
      </p>
      <pre className={PASTE}>{pasteRowText(row.paste_row)}</pre>
    </div>
  );
}

/** One GSC judgment row; `req` given, the title links to the identity's detail. */
export function GscRowCard({
  row,
  summary,
  req,
}: {
  row: ObsState;
  summary: ObsGscSummary | null;
  req?: PickRequest;
}) {
  const title = req ? (
    <IdentityLink req={req} identity={row.identity}>
      {row.title}
    </IdentityLink>
  ) : (
    row.title
  );
  return (
    <li className={CARD} data-obs-state={row.row_id}>
      <div>
        <span className="text-ink-1 font-semibold">{title}</span>
        <span className={MUTED}>
          {" "}
          {row.theater} · {row.language}
        </span>
      </div>
      <div>
        {scopeText(row.scope)} · {textOf(WINDOW_TEXT, row.window_kind)}窗口（
        {windowMark(row, summary)}）· {admissionText(row)} ·{" "}
        {row.state === "present"
          ? "没有过正式门槛的标签"
          : `主标签：${textOf(GSC_STATE_TEXT, row.state)}`}
      </div>
      <Hits labels={row.labels} />
      {row.flags.length > 0 ? (
        <p className={MUTED}>
          标记：
          {row.flags.map((flag) => textOf(GSC_FLAG_TEXT, flag)).join("、")}
        </p>
      ) : null}
      <QualityNote row={row} />
      <PasteRow row={row} />
    </li>
  );
}
