// PORTED_FROM: realshort@816ca2e src/components/admin/pick/sources.tsx
// 本地改动：「现在」是版本的 as_of（now 改名 asOf），来源状态取自这个版本的 meta.sources；QUEYU_INDEX 改从
// @/core/pick-board/queyu 引入；「入选」一段不再写死在用的五个剧场（在用剧场随版本规则走）；
// 几段说明与来源行拆成小组件（函数 <50 行）。
import type { ReactNode } from "react";

import { formatObservedAt } from "@/core/pick-board/metrics";
import { QUEYU_INDEX } from "@/core/pick-board/queyu";
import {
  OBSERVE_SOURCES,
  SOURCE_LABELS,
  sourceRowStatus,
  type ObserveSource,
  type SourceState,
} from "@/core/pick-board/source-types";
import type { PickFreshness } from "@/server/pick-board";

import { ExternalLink } from "./links";

/**
 * 页底「数据来源与口径」（artifact 的 details.src 四段 + 原观测台概览面板里的采集来源状态，
 * 2026-09-23 起加第五条「剧单导入」）。来源状态取这个镜像版本导出时的 meta.sources。
 * 行按 OBSERVE_SOURCES 逐个渲染（标签表 SOURCE_LABELS 是 Record<ObserveSource, string>，漏一个 tsc 就报）；
 * sources 里没有某个 key 时那一行照常出，时间列是「—」。
 */
type SourceStateMap = Partial<Record<ObserveSource, SourceState>>;

function Para({ title, children }: { title: string; children: ReactNode }) {
  return (
    <p>
      <b className="text-ink-2 font-medium">{title}</b>：{children}
    </p>
  );
}

function SourceParas({ fresh }: { fresh: PickFreshness }) {
  const built = fresh.importedAt
    ? `${fresh.importedAt.toISOString().slice(0, 16).replace("T", " ")} UTC`
    : "还没有导入过";
  return (
    <>
      <Para title="来源">
        KalosTV、ShortMax、FlickReels、StarShort、GoodShort、DramaBox、MoboReels、flareflow、TouchShort
        九个剧场各自维护的飞书剧单，用登录的用户身份原样读取，快照 {built}
        。剧单之间没有统一 ID，同一剧场里同一部剧（同
        ID，或同名同语言）合并成一行。ReelShort 是本站自己的 CPS
        账号，不在鹊娱剧库里，行来自本站片库（正典行{" "}
        {fresh.rsCanonical.toLocaleString("en-US")} 部，指标最近采集{" "}
        {formatObservedAt(fresh.rsSyncedAt)}），指标与榜单是同一条查询。
      </Para>
      <Para title="入选">
        有任一剧场信号（日榜、周热门、评级、各剧场的推荐清单、运营备注）或满足
        ReelShort 候选条件（7
        天有出站、有预估订单、有搜索匹配）的行。在用的剧场排在剧场 chips
        前面，「收起其他剧场」只看在用的。其余的行只是在剧单上，没有任何榜单信号（flareflow、MoboReels、TouchShort
        三个剧场的剧单整体如此），打开「含仅剧单收录的行」才进入选剧表，按剧单日期排。剧场信号全是剧场自己的声明，没有播放量或收入数字；ReelShort
        的数值是本站采集的原值。默认按证据时间从新到旧排，只是浏览顺序，不代表投放价值。
      </Para>
      <PickupParas />
    </>
  );
}

/** 取货、发布记录、同名匹配三段：与版本数据无关的固定说明 */
function PickupParas() {
  return (
    <>
      <Para title="取货">
        鹊娱剧库（
        <ExternalLink
          href={QUEYU_INDEX}
          rel="nofollow sponsored noopener"
          className="text-brand hover:underline"
        >
          cps-distribution.zwnet.cn
        </ExternalLink>
        ）九个剧场都在，它没有单剧直达地址，所以按钮做的是把剧名复制到剪贴板再打开剧库（地址带{" "}
        <code>?title=剧名</code>），装了配套的 Tampermonkey
        脚本会自动搜索并打开「短剧详情」抽屉。ReelShort
        行没有可下载素材，站内免费集可播，付费集在
        App；取货列给的是证据页与公开页。
      </Para>
      <Para title="发布记录">
        运营自己的飞书多维表格「选剧池 / 发布记录 /
        账号台账」，用登录的用户身份只读拉取，快照时间见「发布记录」tab。按剧名归一对到剧库行上（剧场行连中文名一起比，ReelShort
        行按同一套归一对 book_id），同名不等于同剧；播放等指标是表里 lookup
        出来的值，待公开的帖子没有指标就空着，不填 0。
      </Para>
      <Para title="本站在售 / 旧站收录">
        都是剧名归一后的同名匹配，会误配，页面上一律标「同名未核」。旧站收录只说明旧域名的
        sitemap 有过这部剧，不是有人在搜的证据。
      </Para>
    </>
  );
}

function SourceStates({
  sources,
  asOf,
}: {
  sources: SourceStateMap;
  asOf: Date;
}) {
  return (
    <div>
      <b className="text-ink-2 font-medium">采集来源状态</b>
      <ul className="mt-1 grid gap-x-6 gap-y-1 text-[13px] md:grid-cols-2">
        {OBSERVE_SOURCES.map((k) => {
          const s = sources[k];
          return (
            <li key={k} className="flex flex-wrap gap-x-2">
              <span className="text-ink-2">{SOURCE_LABELS[k]}</span>
              <span>{sourceRowStatus(k, s, asOf)}</span>
              <span className="text-ink-dim">
                {s?.completedAt ? formatObservedAt(s.completedAt) : "—"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function Sources({
  fresh,
  sources,
  asOf,
}: {
  fresh: PickFreshness;
  sources: SourceStateMap;
  /** 版本的 as_of：来源是否过期、剧单导入是否僵死都按它判，不看墙上时钟 */
  asOf: Date;
}) {
  return (
    <details className="border-line bg-panel mt-6 rounded-[8px] border px-3 py-3 text-[14px]">
      <summary className="cursor-pointer font-medium">
        数据来源与口径 · 9 个分销剧场剧单 + ReelShort 本站 CPS 片库 +
        运营发布记录 · 鹊娱剧库取货
      </summary>
      <div className="text-helper mt-3 flex flex-col gap-3 leading-[1.65]">
        <SourceParas fresh={fresh} />
        <SourceStates sources={sources} asOf={asOf} />
      </div>
    </details>
  );
}
