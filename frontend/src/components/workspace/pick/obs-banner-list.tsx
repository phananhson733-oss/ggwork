// The observation radar's banners (plan TR-25, TR-24; design 3.7; D10), shown by the imports tab (the gateway's banners from
// /sync's obs key) and by the data page's trends and search tabs (the banners obs-banner-rules.ts computes at request time).
// Presentational only: no hook, no clock, so a server view and a client panel both render it. Red banners are alerts and
// come first; warn and info are status lines. Each names its channel, because the two channels fail independently.
import {
  OBS_CHANNEL_LABELS,
  levelRank,
  obsBannerText,
  type BannerLevel,
  type ObsChannel,
} from "@/core/pick/obs-status";

export type ObsBannerItem = Readonly<{
  channel: ObsChannel;
  code: string;
  level: BannerLevel;
}>;

const TONE: Readonly<Record<BannerLevel, string>> = {
  red: "border-danger-line bg-danger-surface text-danger-ink",
  warn: "border-warning-line bg-warning-surface text-warning-ink",
  info: "border-line bg-info-surface text-info-ink",
};

/** Red first, then warn, then info; within a level the channel order, then the order given. */
export function orderObsBanners(
  banners: readonly ObsBannerItem[],
): ObsBannerItem[] {
  const channelRank = (channel: ObsChannel) => (channel === "trends" ? 0 : 1);
  return banners
    .map((banner, index) => ({ banner, index }))
    .sort(
      (a, b) =>
        levelRank(a.banner.level) - levelRank(b.banner.level) ||
        channelRank(a.banner.channel) - channelRank(b.banner.channel) ||
        a.index - b.index,
    )
    .map(({ banner }) => banner);
}

export function ObsBannerList({
  banners,
}: {
  banners: readonly ObsBannerItem[];
}) {
  if (banners.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      {orderObsBanners(banners).map((banner) => (
        <p
          key={`${banner.channel}:${banner.code}`}
          role={banner.level === "red" ? "alert" : "status"}
          data-obs-banner={banner.level}
          className={`rounded-lg border px-4 py-2.5 text-[13px] leading-[1.65] ${TONE[banner.level]}`}
        >
          <span className="font-semibold">
            {OBS_CHANNEL_LABELS[banner.channel]}
          </span>
          ：{obsBannerText(banner.code)}
        </p>
      ))}
    </div>
  );
}
