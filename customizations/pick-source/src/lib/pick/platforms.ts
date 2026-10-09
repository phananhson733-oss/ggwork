/**
 * 十个剧场的规则表，从 artifact 模板（scripts/juyuantai/xuanju.tmpl.html 的 PLATFORMS）逐条搬来。
 * 每一条都是剧场自己的文档写的（doc 是飞书地址，updated 是那份文档最后核过的日期），不是我们的判断。
 * ReelShort 那一条是本站自己的 CPS 账号（2026-09-11 观测台并入选剧台后成为第十个剧场）：
 * 没有飞书剧单，doc 指向站内的 ReelShort 榜；分成比例与结算周期是 user/login 实测的商业参数。
 * 纯数据，不 import 任何 server-only 模块。
 */
import type { Platform } from "./request";

export type YoutubeRule = "ok" | "only" | "warn" | "no";

export const YOUTUBE_LABEL: Record<YoutubeRule, string> = {
  ok: "YouTube 可发",
  only: "YouTube 限剧单",
  warn: "YouTube 慎用",
  no: "禁 YouTube",
};

export interface PlatformRule {
  key: Platform;
  name: string;
  doc: string;
  /** 剧场文档最后核对的日期 */
  updated: string;
  /** 结算 */
  back: string;
  /** 报备 */
  report: string;
  yt: YoutubeRule;
  ytNote: string;
  /** 必带 tag */
  tag: string;
  /** 解禁通道 */
  unban: string;
  /** 素材从哪拿 */
  material: string;
  /** 这个剧场给了哪些榜单 / 评级信号 */
  signals: string;
}

export const PLATFORM_RULES: Record<Platform, PlatformRule> = {
  reelshort: {
    key: "reelshort",
    name: "ReelShort",
    /* 站内地址：规则 tab 里用 next/link 渲染，不当外链 */
    doc: "/admin/pick?tab=rank&rk=rs_rr",
    updated: "2026-09-11",
    back: "月结 · 分成 50%",
    report: "自有 CPS 账号，无需报备",
    yt: "ok",
    ytNote: "导流走本站播放页付费墙，出站一律经 /api/go",
    tag: "—",
    unban: "—",
    material: "站内免费集可播；付费集在 App，不提供下载",
    signals: "30 天销售指标 · 推广人数 · 每日快照增量 · 预估分成 · GSC 搜索 · 7 天出站",
  },
  kalos: {
    key: "kalos",
    name: "KalosTV",
    doc: "https://ncnbcsohm3bm.feishu.cn/wiki/B4rdwUyJriPGpmkKzWIcOul9nUd",
    updated: "2026-09-10",
    back: "未标注",
    report: "番茄版权需报备（白名单周期）；青榕版权后台直接加白",
    yt: "only",
    ytNote: "只能投 YouTube 剧单内的剧",
    tag: "#KalosTV",
    unban: "YouTube / Facebook 解封通道表单",
    material: "剧单内网盘",
    signals: "每日高转化 TOP10 · 周热门榜",
  },
  shortmax: {
    key: "shortmax",
    name: "ShortMax",
    doc: "https://ehyg6a9wjd.feishu.cn/base/FIlebFMQta8mQEsQoV3cxWaunlg",
    updated: "2026-09-10",
    back: "订单及时 · 广告次日",
    report: "需账号报备",
    yt: "ok",
    ytNote: "油管不超过全集时长 50%，二创不少于 10%，禁纯原片",
    tag: "#ShortMax",
    unban: "每天 16:00 统一解禁，次日 11:00 前查结果",
    material: "网盘（免费剧集）",
    signals: "投放榜单评级 SS / S / A / B · 每日推荐",
  },
  flickreels: {
    key: "flickreels",
    name: "FlickReels",
    doc: "https://my.feishu.cn/base/ZpUub25STaYxdisfenkc1iVpnVb",
    updated: "2026-09-10",
    back: "及时",
    report: "需账号报备（YouTube / TK）",
    yt: "ok",
    ytNote: "禁多剧拼接；时长在免费集总时长内，不足 40 分钟可放宽到 40 分钟",
    tag: "剧场 tag + 推广链接 / 口令",
    unban: "飞书表单",
    material: "百度网盘",
    signals: "爆款剧单 · 下架剧单",
  },
  starshort: {
    key: "starshort",
    name: "StarShort",
    doc: "https://xtlmcmusfp.feishu.cn/wiki/N8Hvwk91LitpJPkwgHUcVLWanyV",
    updated: "2026-09-09",
    back: "实时",
    report: "需账号报备",
    yt: "only",
    ytNote: "走 YouTube 剧单：只有 YouTube 剧单表里的剧能发",
    tag: "剧场 tag",
    unban: "24 小时内解禁",
    material: "只能从剧单 + 网盘获取",
    signals: "高充值剧单",
  },
  goodshort: {
    key: "goodshort",
    name: "GoodShort",
    doc: "https://my.feishu.cn/wiki/Imonw1RA1iVHlgkC5RHcA2p9nug",
    updated: "2026-09-09",
    back: "次日",
    report: "Facebook 百粉账号可专属报白",
    yt: "ok",
    ytNote: "油管不超过 30 分钟；发布前去掉原片 BGM",
    tag: "#GoodShort",
    unban: "钉钉反馈表",
    material: "剧单内（多数无网盘）",
    signals: "爆款 / 重点推荐 · 历史高充值",
  },
  dramabox: {
    key: "dramabox",
    name: "DramaBox",
    doc: "https://mv0mabk0o8r.feishu.cn/base/F1Jabe1VsaELajsIg8YcgCNXnfb",
    updated: "2026-08-31",
    back: "次日",
    report: "需账号报备（YouTube 粉丝不少于 100）",
    yt: "ok",
    ytNote: "需报备后发布",
    tag: "—",
    unban: "钉钉表单，当天 16 点前统一提报",
    material: "可二创素材 zip",
    signals: "运营备注（爆款 / 高优排期）",
  },
  moboreels: {
    key: "moboreels",
    name: "MoboReels",
    doc: "https://cvbadml3pe5.feishu.cn/sheets/MRdRsef0jhRLTXtG6vHcVEuInch",
    updated: "2026-08-21",
    back: "订单实时 · 广告次日",
    report: "需账号报备",
    yt: "no",
    ytNote: "不能在 YouTube 推广",
    tag: "剧场 tag + 视频加 logo",
    unban: "Facebook 二次报白表单",
    material: "网盘（约一成有）",
    signals: "评级（表里只有 11 行有）",
  },
  flareflow: {
    key: "flareflow",
    name: "flareflow",
    doc: "https://natqt914jbm.feishu.cn/wiki/DOxnwHjh7ifX2WkMs7FcVQhPnDb",
    updated: "2026-08-28",
    back: "未标注",
    report: "—",
    yt: "no",
    ytNote: "禁止 YouTube 发布",
    tag: "#flareflow",
    unban: "不允许 YouTube",
    material: "百度网盘",
    signals: "",
  },
  touchshort: {
    key: "touchshort",
    name: "TouchShort",
    doc: "https://icn4xkpgklir.feishu.cn/wiki/Q2bXwp6UziJURPkn7glczrK8nYd",
    updated: "2025-12-24",
    back: "实时",
    report: "无需报备",
    yt: "warn",
    ytNote: "不推荐 YouTube 大批量发布",
    tag: "#touchshort",
    unban: "飞书表单，处理较慢",
    material: "分销素材（50% 集数）网盘，多语言",
    signals: "",
  },
};

/** 这一行在 YouTube 上能不能发：限剧单的剧场还要看这一行自己在不在 YouTube 剧单上 */
export function youtubeStatus(platform: Platform, rowOnList: boolean): { label: string; blocked: boolean } {
  const rule = PLATFORM_RULES[platform];
  if (rule.yt === "only")
    return rowOnList
      ? { label: "YouTube 限剧单 · 在剧单", blocked: false }
      : { label: "YouTube 限剧单 · 不在剧单", blocked: true };
  return { label: YOUTUBE_LABEL[rule.yt], blocked: rule.yt === "no" };
}

/** 运营的飞书「选剧池 / 发布记录」表，发布记录 tab 的来源链接（用户身份只读，见 scripts/juyuantai/posted.py） */
export const POSTED_POOL_URL =
  "https://gengrowth.feishu.cn/base/OtnsbnRnwaLmnVsJByscTkFMntd?table=tbl4efRfwhJRqryA&view=vewqPXGMlA";
