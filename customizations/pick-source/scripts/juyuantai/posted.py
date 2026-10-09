#!/usr/bin/env python3
"""运营自己的飞书多维表格（选剧池 / 发布记录 / 账号台账）-> posted.json

输入是 fetch.sh 拉到 lark/ 的三份 ndjson（用户身份只读），输出给 render.py 挂到剧库行上。
只做归一，不做任何判断：字段原样保留，日期截到日，指标空着就是空着（待公开的帖子没有播放量，不填 0）。
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta

HERE = os.environ.get("PICK_SOURCE_WORKDIR") or os.path.dirname(os.path.abspath(__file__))
LARK = os.path.join(HERE, "lark")
BASE_TOKEN = "OtnsbnRnwaLmnVsJByscTkFMntd"
BASE_URL = f"https://gengrowth.feishu.cn/base/{BASE_TOKEN}"
TABLES = {"pool": "tbl4efRfwhJRqryA", "posts": "tbl5Kzrhuz9B7LTE", "accounts": "tblHeWrgRPNshRdE"}
POOL_VIEW = "vewqPXGMlA"


def title_key(t):
    """剧名归一：NFKC、小写、去掉各种撇号、其余非字母数字折成空格。
    render.py 对剧库行用同一个函数，两边算出不同键就对不上，所以只在这里定义一份。"""
    t = unicodedata.normalize("NFKC", str(t or "")).lower()
    t = re.sub(r"[‘’'`´]", "", t)
    t = re.sub(r"[^\w]+|_", " ", t)
    return " ".join(t.split())


def load(name):
    path = os.path.join(LARK, f"gengrowth__{name}.ndjson")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def scalar(v):
    """lark-cli 的 ndjson 里 select / lookup 是数组，link 是 [{id}]，文本是字符串。取第一个可读值。"""
    if v is None:
        return None
    if isinstance(v, list):
        return scalar(v[0]) if v else None
    if isinstance(v, dict):
        return v.get("text") or v.get("name") or v.get("id")
    return v


def strings(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [s for s in (scalar(x) for x in v) if s not in (None, "")]
    s = scalar(v)
    return [s] if s not in (None, "") else []


def link_ids(v):
    return [x.get("id") for x in (v or []) if isinstance(x, dict) and x.get("id")]


def day(v):
    s = scalar(v)
    return s[:10] if isinstance(s, str) and len(s) >= 10 else None


def num(v):
    s = scalar(v)
    if s in (None, ""):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


URL_RE = re.compile(r"https?://[^\s()\]]+")


def url(v):
    s = scalar(v)
    if not isinstance(s, str):
        return None
    m = URL_RE.search(s)
    return m.group(0) if m else None


def text(v):
    s = scalar(v)
    return s.strip() if isinstance(s, str) and s.strip() else None


def build():
    pool = load("pool")
    posts = load("posts")
    accounts = load("accounts")

    acct_by_rec = {}
    acct_out = []
    for a in accounts:
        row = {
            "id": text(a.get("账号ID")),
            "name": text(a.get("账号名")) or text(a.get("账号ID")),
            "url": url(a.get("主页链接")),
            "group": text(a.get("所属组")),
            "form": text(a.get("表现形式")),
            "niche": text(a.get("定位垂类")),
            "status": text(a.get("状态")),
            "fans": num(a.get("粉丝数")),
            "asOf": day(a.get("数据日期")),
        }
        acct_by_rec[a["record_id"]] = row
        acct_out.append(row)

    by_rec = {}
    dramas = []
    for p in pool:
        title = text(p.get("剧名")) or ""
        d = {
            "sd": text(p.get("剧ID")),
            "rec": p["record_id"],
            "t": title,
            "tk": title_key(title),
            "lang": text(p.get("语言")),
            "plat": text(p.get("平台")),
            "src": strings(p.get("来源")),
            "cats": strings(p.get("剧分类")),
            "online": day(p.get("上线日期")),
            "life": text(p.get("生命周期")),
            "sched": text(p.get("是否已排期")) == "是",
            "who": strings(p.get("推荐人")),
            "why": text(p.get("推荐理由")),
            "note": text(p.get("备注")),
            "archived": text(p.get("归档状态")) == "archived",
            "created": day(p.get("创建时间")),
            "updated": day(p.get("最后修改时间")),
            "posts": [],
        }
        by_rec[p["record_id"]] = d
        dramas.append(d)

    orphan = 0
    for r in posts:
        rec = (link_ids(r.get("剧")) or [None])[0]
        d = by_rec.get(rec)
        if d is None:
            orphan += 1
            continue
        acct_rec = (link_ids(r.get("账号")) or [None])[0]
        acct = acct_by_rec.get(acct_rec, {})
        d["posts"].append({
            "d": day(r.get("日期")),
            "acct": text(r.get("账号名")) or acct.get("name"),
            "st": text(r.get("发布状态")),
            "views": num(r.get("播放量")),
            "likes": num(r.get("点赞")),
            "favs": num(r.get("收藏")),
            "cmts": num(r.get("评论")),
            "shares": num(r.get("转发")),
            "md": day(r.get("指标日期")),
            "url": url(r.get("视频链接")),
            "note": text(r.get("备注")),
            "how": text(r.get("匹配方式")),
            "pid": text(r.get("发布ID")),
        })

    published = {"已回填", "已公开"}
    for d in dramas:
        d["posts"].sort(key=lambda x: (x["d"] or "", x["pid"] or ""), reverse=True)
        pub = [x for x in d["posts"] if x["st"] in published]
        d["n"] = len(pub)
        d["nSched"] = len(d["posts"]) - len(pub)
        views = [x["views"] for x in pub if x["views"] is not None]
        d["views"] = sum(views) if views else None
        d["viewsN"] = len(views)
        d["last"] = max((x["d"] for x in pub if x["d"]), default=None)
        d["first"] = min((x["d"] for x in pub if x["d"]), default=None)
        d["accts"] = sorted({x["acct"] for x in pub if x["acct"]})
        d["metricAt"] = max((x["md"] for x in pub if x["md"]), default=None)

    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M+08:00")
    out = {
        "built": now,
        "base": BASE_URL,
        "poolUrl": f"{BASE_URL}?table={TABLES['pool']}&view={POOL_VIEW}",
        "tables": TABLES,
        "dramas": dramas,
        "accounts": acct_out,
        "nPosts": len(posts),
        "orphanPosts": orphan,
    }
    with open(os.path.join(HERE, "posted.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    n_pub = sum(1 for d in dramas if d["n"])
    print(f"dramas={len(dramas)} posted={n_pub} posts={len(posts)} orphan={orphan} accounts={len(acct_out)}")


if __name__ == "__main__":
    try:
        build()
    except FileNotFoundError as e:
        print(f"missing input: {e.filename}. run: bash fetch.sh", file=sys.stderr)
        sys.exit(1)
