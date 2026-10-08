#!/usr/bin/env bash
# 用 lark-cli 用户身份把 9 个剧场的剧单和运营自己的发布记录原样拉到 ./lark/（只读，不写任何飞书文档）。
# 前置：lark-cli auth login 扫码登录用户身份（外部租户的文档 bot 身份读不到）。
# 用法：bash fetch.sh  → 然后 python3 build.py → match.ts → reelshort.ts → python3 posted.py → python3 render.py
set -euo pipefail
umask 077
cd "$(dirname "$0")"
mkdir -p lark
sheet() { # 平台 spreadsheet_token sheet_id
  python3 fetch_source.py sheet "$2" "$3" "lark/$1__$3.json"
  echo "sheet $1/$3"
}
base() { # 平台 base_token 表名 输出名；按 next_offset 读到 has_more=false
  python3 fetch_source.py base "$2" "$3" "lark/$1__$4.ndjson"
  echo "base $1/$4"
}
# KalosTV：英语剧单 / 小语种剧单 / Youtube剧单 / 每日排行榜 / 周热门榜单
for s in 7ba1a7 9WGFv2 weGTx6 OrwcFd GhBujK; do sheet kalos RRBAszuhOhNM8StMqRVcGknSnyf $s; done
# GoodShort：重点推荐 / 英语剧单 / 历史高充值英语 / 历史高充值小语种 / 小语种剧单
for s in 471SFU icA5Xf JIOH6H BqgaO4 RkkUgO; do sheet goodshort HnxRsUam8hCH7ltOsTSceXplnic $s; done
# StarShort：新剧上新 / YouTube剧单 / 高充值剧单
for s in 5f20e7 c1Fgzk VOxpIN; do sheet starshort Kg0osz7hAhRw3ltzSzrcw1SMnkh $s; done
sheet touchshort HYvBsZBbWhKgTxt5OxDcGg6Ynwg baaa17
for s in d49b4e z6DfWg; do sheet flareflow Q4NmsixaThGYEZtBUR1cbmnFnAh $s; done
if [[ "${PICK_SOURCE_RETAIN_MOBOREELS:-0}" != "1" ]]; then
  sheet moboreels MRdRsef0jhRLTXtG6vHcVEuInch omjCKZ
fi
# ShortMax / FlickReels / DramaBox 是多维表格，每页 2000 行，超过要按 offset 分页。
# 第三个参数是上游多维表格里的【表名原文】，其中两张的名字自带符号，必须逐字一致才拉得到。
base shortmax FIlebFMQta8mQEsQoV3cxWaunlg '投放榜单剧🔥' rank
base shortmax FIlebFMQta8mQEsQoV3cxWaunlg '每日推荐必看' daily
base shortmax FIlebFMQta8mQEsQoV3cxWaunlg 'AI力荐新剧🎬' ai
base flickreels ZpUub25STaYxdisfenkc1iVpnVb '达人分销剧单' main
base flickreels ZpUub25STaYxdisfenkc1iVpnVb 'FlickReels爆款剧单' hot
base flickreels ZpUub25STaYxdisfenkc1iVpnVb 'FlickReels下架剧单' off
base dramabox F1Jabe1VsaELajsIg8YcgCNXnfb 'ai剧专区' ai
base dramabox F1Jabe1VsaELajsIg8YcgCNXnfb '英语剧单' en
base dramabox F1Jabe1VsaELajsIg8YcgCNXnfb '小语种剧单' other
# 运营自己的多维表格（gengrowth 租户）：选剧池 / 发布记录 / 账号台账，这里用的是 table id 不是表名。
# 只读；采集数据与每日播放趋势两张表不拉（帖子指标已经 lookup 进发布记录）。之后 python3 posted.py
base gengrowth OtnsbnRnwaLmnVsJByscTkFMntd tbl4efRfwhJRqryA pool
base gengrowth OtnsbnRnwaLmnVsJByscTkFMntd tbl5Kzrhuz9B7LTE posts
base gengrowth OtnsbnRnwaLmnVsJByscTkFMntd tblHeWrgRPNshRdE accounts
echo "done -> lark/"
