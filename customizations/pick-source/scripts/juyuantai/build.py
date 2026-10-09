#!/usr/bin/env python3
"""把 lark-cli 拉下来的 9 个剧场剧单归一成 catalog.json。

输入：lark/ 目录下的 *.json（sheets +csv-get --output-path）与 *.ndjson（base +record-list）。
输出：catalog.json = {"built": iso, "platforms": {...}, "rows": [...]}。
每行只保留页面要用的字段，不带简介正文。
"""
import csv, glob, io, json, os, re, sys, datetime
from collections import defaultdict
from pathlib import Path
from fetch_source import read_records

HERE = os.environ.get("PICK_SOURCE_WORKDIR") or os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "lark")
# 构建日：年份推断与「上线天数」都以它为基准。默认真实今天；重放旧快照时用 JUYUANTAI_TODAY=YYYY-MM-DD 钉住
TODAY = datetime.date.fromisoformat(os.environ["JUYUANTAI_TODAY"]) if os.environ.get("JUYUANTAI_TODAY") else datetime.date.today()

# ---------- 读取 ----------

def sheet_rows(name):
    path = os.path.join(SRC, name)
    x = json.load(open(path, encoding="utf-8"))
    x = x.get("data", x)
    out = []
    for rec in csv.reader(io.StringIO(x["annotated_csv"])):
        if not rec:
            continue
        m = re.match(r"\[row=(\d+)\] ?(.*)", rec[0], re.S)
        if m:
            rec[0] = m.group(2)
        out.append([c.strip() for c in rec])
    return out


def ndjson(*names):
    return read_records(Path(SRC), names)


def cell(r, i):
    return r[i].strip() if i < len(r) and r[i] is not None else ""


def first(v):
    """base 的 select 字段是 list；取第一个。"""
    if isinstance(v, list):
        return str(v[0]).strip() if v else ""
    if v is None:
        return ""
    return str(v).strip()


def joined(v):
    if isinstance(v, list):
        return " · ".join(str(x).strip() for x in v if str(x).strip())
    return str(v or "").strip()


# 链接后面常常没有空格就接着「提取码:」，URL 一律止于第一个汉字
URL_RE = re.compile(r"https?://[^\s\)\]\"'<>，。\u4e00-\u9fff]+")
PW_RE = re.compile(r"(?:提取码|密码|pwd)[:：]?\s*([A-Za-z0-9]{4})")


def pan(text):
    """从「通过网盘分享的文件：… 链接: [url](url) 提取码: xxxx」里取出链接与提取码。"""
    if not text:
        return "", ""
    t = str(text)
    m = URL_RE.search(t)
    url = m.group(0) if m else ""
    url = url.replace("%5D", "").rstrip(".,;")
    pw = ""
    m2 = PW_RE.search(t)
    if m2:
        pw = m2.group(1)
    elif "?pwd=" in url:
        pw = url.split("?pwd=")[-1][:4]
    return url, pw


def iso_date(s, default_year=None):
    """接受 2026/9/10、2026-09-10T…、9月9日、2026.8.10。返回 yyyy-mm-dd 或 ''。"""
    if not s:
        return ""
    s = str(s).strip()
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime.date(y, mo, d).isoformat()
        except ValueError:
            return ""
    m = re.match(r"(\d{1,2})月(\d{1,2})日", s)
    if m and default_year:
        mo, d = map(int, m.groups())
        try:
            return datetime.date(default_year, mo, d).isoformat()
        except ValueError:
            return ""
    return ""


def year_for_sequence(dates_md):
    """给只有「月.日」的序列（榜单从新到旧排）推年份：月份往下变大就是跨年。"""
    out, year, prev_m = [], TODAY.year, None
    if dates_md and tuple(dates_md[0]) > (TODAY.month, TODAY.day):
        year -= 1
    for mo, d in dates_md:
        if prev_m is not None and mo > prev_m:
            year -= 1
        prev_m = mo
        try:
            out.append(datetime.date(year, mo, d).isoformat())
        except ValueError:
            out.append("")
    return out


def to_int(s):
    m = re.search(r"\d+", str(s or ""))
    return int(m.group(0)) if m else None


LANG_NORM = {
    "英文": "英语", "英": "英语", "西": "西班牙语", "西语": "西班牙语", "西语配音": "西班牙语",
    "葡语": "葡萄牙语", "葡": "葡萄牙语", "泰": "泰语", "日": "日语", "韩": "韩语", "法": "法语",
    "德": "德语", "印尼": "印尼语", "印尼语": "印尼语", "阿语": "阿拉伯语", "阿": "阿拉伯语",
    "越": "越南语", "越南": "越南语", "繁中": "繁体中文", "繁體": "繁体中文", "意": "意大利语",
    "意语": "意大利语", "土": "土耳其语", "俄": "俄语", "波": "波兰语", "菲": "菲律宾语",
    "繁体": "繁体中文", "中文繁体": "繁体中文", "繁": "繁体中文", "简体": "简体中文", "中文": "简体中文", "简体中文": "简体中文",
    "印度尼西亚语": "印尼语", "阿拉伯": "阿拉伯语", "马来西亚语": "马来语", "马来": "马来语", "英语配音": "英语",
    "葡语配音": "葡萄牙语", "泰语配音": "泰语", "日语配音": "日语", "韩语配音": "韩语", "法语配音": "法语", "德语配音": "德语",
    "西班牙": "西班牙语", "葡萄牙": "葡萄牙语", "土耳其": "土耳其语", "菲律宾": "菲律宾语", "印地": "印地语", "越南": "越南语",
}


LANG_LIKE = re.compile(r"语|中文|文$")


def lang_norm(s):
    s = (s or "").strip()
    if s in LANG_NORM:
        return LANG_NORM[s]
    m = re.search(r"翻(.{1,4}?)语", s)
    if m:
        return LANG_NORM.get(m.group(1) + "语", m.group(1) + "语")
    m = re.match(r"(.{1,3}?语)本土", s)
    if m:
        return LANG_NORM.get(m.group(1), m.group(1))
    if "," in s or "，" in s:
        return "多语言"
    return s  # 空就是空，不猜成英语


def is_cjk(s):
    s = str(s or "")
    return bool(s) and sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff") * 2 > len(s)


rows = []
counts = defaultdict(int)


def push(p, src, **kw):
    r = {"p": p, "src": src}
    for k, v in kw.items():
        if v not in (None, "", [], {}):
            r[k] = v
    r["t"] = (r.get("t") or "").strip()
    if not r["t"]:
        return None
    r["lang"] = lang_norm(r.get("lang"))
    if r.get("cn") and "中文" not in r["lang"] and is_cjk(r["t"]) and not is_cjk(r["cn"]):
        r["t"], r["cn"] = r["cn"], r["t"]  # 表里外语名与中文名两列写反了
    if r.get("tags"):
        r["tags"] = r["tags"][:48]
    if r.get("cn") and r["cn"] == r["t"]:
        del r["cn"]
    rows.append(r)
    counts[p] += 1
    return r


# ---------- KalosTV ----------

def kalos():
    # 剧单三张：英语 / Youtube 同结构；小语种多一列语言
    for fname, src, small in (("kalos__7ba1a7.json", "英语剧单", False), ("kalos__9WGFv2.json", "小语种剧单", True), ("kalos__weGTx6.json", "Youtube剧单", False)):
        rs = sheet_rows(fname)
        hi = next(i for i, r in enumerate(rs) if cell(r, 0) == "推荐日期")
        for r in rs[hi + 1:]:
            if not cell(r, 1):
                continue
            if small:
                lang = cell(r, 3)
                if not (lang in LANG_NORM or LANG_LIKE.search(lang)):  # 串列：这一格是剧类型或版权，不是语言
                    lang = ""
                push("kalos", src, t=cell(r, 1), cr=cell(r, 2), lang=lang, origin=cell(r, 4), cn=cell(r, 5),
                     date=iso_date(cell(r, 0)), pan=pan(cell(r, 7))[0], pw=pan(cell(r, 7))[1], ep=to_int(cell(r, 8)))
            else:
                push("kalos", src, t=cell(r, 1), cr=cell(r, 2), lang="英语", kind=cell(r, 3), origin=cell(r, 4), cn=cell(r, 5),
                     tags=cell(r, 6).replace("、", " · "), date=iso_date(cell(r, 0)), pan=pan(cell(r, 8))[0], pw=pan(cell(r, 8))[1],
                     ep=to_int(cell(r, 9)), pay=to_int(cell(r, 10)), yt=(src == "Youtube剧单"))
    # 日榜：按块
    daily = defaultdict(list)  # title -> [(date, rank, note, kind, tags)]
    rs = sheet_rows("kalos__OrwcFd.json")
    blocks, cur = [], None
    for r in rs:
        head = cell(r, 0)
        m = re.search(r"KalosTV\s*(\d{1,2})\.(\d{1,2})", head)
        if m:
            cur = {"md": (int(m.group(1)), int(m.group(2))), "items": []}
            blocks.append(cur)
            continue
        if cur is None or head == "排名":
            continue
        title_cell = cell(r, 2)
        if not title_cell:
            continue
        rank_raw = head
        # 上游日榜的名次列前三名不是数字，是奖杯和奖牌符号（原样匹配，这里用命名转义而不是字面 emoji）
        rank = {"\N{TROPHY}": 1, "\N{SECOND PLACE MEDAL}": 2, "\N{THIRD PLACE MEDAL}": 3}.get(rank_raw) or to_int(rank_raw)
        if not rank:
            continue
        lines = [x.strip() for x in title_cell.split("\n") if x.strip()]
        title = lines[0]
        note = " ".join(lines[1:])
        cur["items"].append((rank, title, note, cell(r, 3), cell(r, 4)))
    dates = year_for_sequence([b["md"] for b in blocks])
    for b, d in zip(blocks, dates):
        b["date"] = d
        for rank, title, note, kind, tags in b["items"]:
            daily[title].append((d, rank, note, kind, tags))
    # 周榜
    weekly = defaultdict(list)
    rs = sheet_rows("kalos__GhBujK.json")
    # 周表头两种写法：2026.1.5-2026.1.11 与 12.22-12.28（82 个周块里 54 个是后者）。
    # 只认前者会把后一种当成剧名、并把它下面的 1,420 条记录记到上一个能解析的周
    wblocks, week = [], None
    for r in rs:
        h = cell(r, 0)
        # 第三种：2026.4.14-2026.4.20番茄周榜单，区间后面带字（8 块）；
        # 第四种：（11.4-11.10），全角括号包着（表尾 12 块，2024-08 到 2024-11）。
        # 漏认第四种会把那 12 周共 435 条记录全记到上一个能解析的周（11.11-11.17）
        hh = h.strip().strip("（）()")
        m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*(\D*)$", hh)
        m2 = None if m else re.match(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})\s*(\D*)$", hh)
        if m or m2:
            g = [int(x) for x in (m or m2).groups()[:-1]]
            suffix = (m or m2).groups()[-1].strip()
            mo, d, emo, ed, y = (g[1], g[2], g[4], g[5], g[0]) if m else (g[0], g[1], g[2], g[3], None)
            week = {"label": f"{mo}.{d}–{emo}.{ed}" + (" 番茄" if "番茄" in suffix else ""), "md": (mo, d), "y": y, "rows": []}
            wblocks.append(week)
            continue
        if h in ("周热门榜单", "榜单", "") or week is None:
            continue
        week["rows"].append((h, cell(r, 1)))
    inferred = year_for_sequence([w["md"] for w in wblocks])  # 周块从新到旧排
    for w, d in zip(wblocks, inferred):
        start = datetime.date(w["y"], *w["md"]).isoformat() if w["y"] else d
        for h, c1 in w["rows"]:
            weekly[h].append((start, w["label"], c1))
    return daily, weekly


# ---------- ShortMax ----------

def shortmax():
    def common(rec, src, **extra):
        url, pw = pan(rec.get("网盘链接（免费剧集）"))
        return push("shortmax", src, t=rec.get("短剧名称"), cn=rec.get("短剧别名"), id=str(rec.get("短剧ID") or ""),
                    lang=first(rec.get("语言")), kind=" · ".join(x for x in (first(rec.get("作品类型")), first(rec.get("内容类型")), first(rec.get("字幕配音类型"))) if x),
                    g=first(rec.get("男女频")), tags=" · ".join(x for x in (rec.get("一级分类"), rec.get("二级分类")) if x),
                    date=iso_date(rec.get("分销商上线时间")), date2=iso_date(rec.get("端内上线时间")), pan=url, pw=pw, **extra)
    for rec in ndjson("shortmax__rank.ndjson"):
        r = common(rec, "投放榜单剧")
        if r:
            g = first(rec.get("评级"))
            if g:
                r.setdefault("sig", []).append({"s": "sm", "g": g})
    for rec in ndjson("shortmax__daily.ndjson"):
        r = common(rec, "每日推荐必看")
        if r:
            r.setdefault("sig", []).append({"s": "smd"})
    for rec in ndjson("shortmax__ai.ndjson"):
        common(rec, "AI力荐新剧")


# ---------- FlickReels ----------

def flickreels():
    off, off_rec = {}, {}
    for rec in ndjson("flickreels__off.ndjson"):
        t = (rec.get("短剧名称") or "").strip()
        off[t] = iso_date(rec.get("下架日期"))
        off_rec.setdefault(t, rec)
    hot = {}
    for rec in ndjson("flickreels__hot.ndjson"):
        hot[(rec.get("短剧名称") or "").strip()] = rec
    seen = set()
    for rec in ndjson("flickreels__main.ndjson", "flickreels__main2.ndjson", "flickreels__main3.ndjson"):
        url, pw = pan(rec.get("百度网盘链接"))
        t = (rec.get("短剧名称") or "").strip()
        d0, o = iso_date(rec.get("开始分销日期")), off.get(t)
        reoff = f"曾下架 {o}，{d0} 重新分销" if o and d0 and o < d0 else ""
        r = push("flickreels", "达人分销剧单", t=t, cn=rec.get("中文剧名"), lang=first(rec.get("语种")), kind=first(rec.get("剧集类型")),
                 excl=first(rec.get("版权信息")), tags=joined(rec.get("题材类型")), date=d0, pan=url, pw=pw,
                 off=None if reoff else o, reoff=reoff)
        if r and t in hot:
            h = hot[t]
            r["sig"] = [{"s": "fh", "d": iso_date(h.get("推荐日期"))}]
        seen.add(t)
    for t, h in hot.items():
        if t in seen:
            continue
        url, pw = pan(h.get("素材链接"))
        push("flickreels", "FlickReels爆款剧单", t=t, cn=h.get("中文名"), kind=first(h.get("短剧类型")), excl=first(h.get("版权信息")),
             tags=joined(h.get("题材类型")), date=iso_date(h.get("推荐日期")), pan=url, pw=pw, sig=[{"s": "fh", "d": iso_date(h.get("推荐日期"))}])
        seen.add(t)
    # 下架表 664 条 / 435 个标题里只有 12 个还在主剧单上；其余只出现在下架表，照样建行（无资源，带下架日期），
    # 这样全部剧库里搜得到、「含已下架」才真的是全量。下架表的列名是错位的：语种列装的是版权，剧集类型列装的是语言
    for t, rec in off_rec.items():
        if not t or t in seen:
            continue
        # 两种布局各占一半：484 条串列（剧集类型=语言、语种=版权），180 条正常；哪格像语言取哪格
        a, c = first(rec.get("剧集类型")) or "", first(rec.get("语种")) or ""
        lang = a if LANG_LIKE.search(a) else (c if LANG_LIKE.search(c) else "")
        excl = next((x for x in (a, c) if x in ("独家", "非独家", "FB有风险", "FB、TK受限制")), "")
        push("flickreels", "下架表", t=t, cn=rec.get("中文剧名"), lang=lang, excl=excl, tags=joined(rec.get("题材类型")), off=off[t])


# ---------- DramaBox ----------

NOTE_KEYS = re.compile(r"爆款|重点|高优|收入|充值|破\d|万|热门|优先")


def dramabox():
    for rec in ndjson("dramabox__ai.ndjson"):
        url, _ = pan(rec.get("可二创素材"))
        note = (rec.get("备注") or "").strip()
        r = push("dramabox", "ai剧专区", t=rec.get("短剧名称"), cn=rec.get("中文名称"), id=str(rec.get("短剧ID") or ""), lang=first(rec.get("作品语言")),
                 g=first(rec.get("受众")), kind="AI仿真人剧", date=iso_date(rec.get("分销时间")), zip=url)
        if r and note and NOTE_KEYS.search(note):
            r["sig"] = [{"s": "dbn", "t": note[:60]}]
    # 英语剧单 / 小语种剧单的列内容在同一张表里就是错位的（2026-09-10 实测：英语剧单 1,259 行 zip 在「简介」、
    # 受众在「素材链接」，另 310 行 zip 在「素材链接」；小语种第二页 818 行 zip 在「受众类型」、受众在「版权信息」），
    # 按表头取值必然串列——曾把 818 行的资源包 URL 当成频道印在页面上。所以 zip / 受众 / 独家 一律按内容从所有字段里认，
    # 「版权信息」这一列从没装过版权（全空或装受众），不取。
    for files, src in ((("dramabox__en.ndjson",), "英语剧单"), (("dramabox__other.ndjson", "dramabox__other2.ndjson"), "小语种剧单")):
        for rec in ndjson(*files):
            f = dramabox_fields(rec)
            r = push("dramabox", src, t=rec.get("短剧名称"), cn=f["cn"], id=str(rec.get("短剧ID") or ""),
                     lang=first(rec.get("语种")) or "英语", kind=first(rec.get("剧集类型")), g=f["g"], excl=f["excl"],
                     date=iso_date(rec.get("更新日期")), zip=f["zip"])
            if r and f["note"]:
                r["sig"] = [{"s": "dbn", "t": f["note"][:60]}]


ZIP_RE = re.compile(r"https?://[^\s\)\]]+\.zip")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
AUDIENCE_RE = re.compile(r"男频|女频")
DRAMABOX_SKIP = {"record_id", "父记录", "短剧ID", "短剧名称", "更新日期", "语种", "剧集类型"}


def dramabox_fields(rec):
    """按内容而不是表头认字段：zip 资源包、受众、独家 / 非独家、原剧名、运营备注。"""
    texts = []
    for k, v in rec.items():
        if k in DRAMABOX_SKIP:
            continue
        t = MD_LINK_RE.sub(r"\1", joined(v)).strip()
        if t:
            texts.append((k, t))
    zip_url = next((ZIP_RE.search(t).group(0) for _, t in texts if ZIP_RE.search(t)), "")
    short = [(k, t) for k, t in texts if len(t) <= 40 and not ZIP_RE.search(t)]
    g = next((t for _, t in short if AUDIENCE_RE.search(t)), "")
    excl = next((t for _, t in short if t in ("独家", "非独家")), "")
    cn = first(rec.get("原剧名"))
    if cn and (ZIP_RE.search(cn) or AUDIENCE_RE.search(cn) or cn in ("独家", "非独家")):
        cn = ""
    note = next((t for k, t in short if k in ("备注", "简介") and NOTE_KEYS.search(t)), "")
    return {"zip": zip_url, "g": g, "excl": excl, "cn": cn, "note": note}


# ---------- GoodShort ----------

def goodshort():
    rs = sheet_rows("goodshort__471SFU.json")
    for r in rs[1:]:
        if not cell(r, 2):
            continue
        reason = cell(r, 0)  # 四种：新剧推荐 / 站内高充值 / KOC高转化 / 爆款推荐，原文进信号，页面按原文显示
        s = "gh" if "爆款" in reason else "gn"
        push("goodshort", "重点推荐", t=cell(r, 4), cn=cell(r, 3), id=cell(r, 2), g=cell(r, 5), lang=cell(r, 6), kind=cell(r, 7),
             tags=cell(r, 8).replace(",", " · "), date=iso_date(cell(r, 1)), sig=[{"s": s, "d": iso_date(cell(r, 1)), "t": reason[:24]}])
    # 英语剧单
    for r in sheet_rows("goodshort__icA5Xf.json")[1:]:
        if not cell(r, 1):
            continue
        url, pw = pan(cell(r, 11))
        push("goodshort", "英语剧单", t=cell(r, 3), cn=cell(r, 2), id=cell(r, 1), g=cell(r, 4), lang="英语", kind=cell(r, 5),
             tags=cell(r, 6).replace(",", " · "), date=iso_date(cell(r, 0)), free=to_int(cell(r, 9)), pan=url, pw=pw)
    for r in sheet_rows("goodshort__RkkUgO.json")[1:]:
        if not cell(r, 1):
            continue
        url, pw = pan(cell(r, 11))
        push("goodshort", "小语种剧单", t=cell(r, 3), cn=cell(r, 2), id=cell(r, 1), g=cell(r, 4), lang=cell(r, 5), kind=cell(r, 6),
             tags=cell(r, 7).replace(",", " · "), date=iso_date(cell(r, 0)), free=to_int(cell(r, 10)), pan=url, pw=pw)
    # 历史高充值（隐藏表）
    for r in sheet_rows("goodshort__JIOH6H.json")[1:]:
        if not cell(r, 1):
            continue
        url, pw = pan(cell(r, 10))
        push("goodshort", "历史高充值英语", t=cell(r, 3), cn=cell(r, 2), id=cell(r, 1), g=cell(r, 4), lang="英语", kind=cell(r, 5),
             tags=cell(r, 8).replace("、", " · "), date=iso_date(cell(r, 0)), free=to_int(cell(r, 9)), pan=url, pw=pw,
             sig=[{"s": "ghh", "d": iso_date(cell(r, 0))}])
    for r in sheet_rows("goodshort__BqgaO4.json")[1:]:
        if not cell(r, 1):
            continue
        url, pw = pan(cell(r, 11))
        push("goodshort", "历史高充值小语种", t=cell(r, 3), cn=cell(r, 2), id=cell(r, 1), g=cell(r, 4), lang=cell(r, 5), kind=cell(r, 6),
             tags=cell(r, 9).replace("、", " · "), date=iso_date(cell(r, 0)), free=to_int(cell(r, 10)), pan=url, pw=pw,
             sig=[{"s": "ghh", "d": iso_date(cell(r, 0))}])


# ---------- StarShort ----------

def starshort():
    rs = sheet_rows("starshort__5f20e7.json")
    hi = next(i for i, r in enumerate(rs) if cell(r, 0) == "推荐日期")
    data = [r for r in rs[hi + 1:] if cell(r, 3)]
    mds = []
    for r in data:
        m = re.match(r"(\d{1,2})月(\d{1,2})日", cell(r, 0))
        mds.append((int(m.group(1)), int(m.group(2))) if m else None)
    # 缺日期的行留空（82 行，全在表尾）；沿用上一行等于代填剧单日期，页面承诺过不代填
    filled = mds
    dates = year_for_sequence([md for md in filled if md]) if filled else []
    di = iter(dates)
    for r, md in zip(data, filled):
        d = next(di) if md else ""
        url, pw = pan(cell(r, 9))
        push("starshort", "新剧上新", t=cell(r, 3), cn=cell(r, 2), plat=cell(r, 1), lang=cell(r, 4), kind=cell(r, 5), g=cell(r, 6),
             tags=cell(r, 7).replace("、", " · "), date=d, pan=url, pw=pw, ep=to_int(cell(r, 10)), pay=to_int(cell(r, 11)))
    rs = sheet_rows("starshort__c1Fgzk.json")
    hi = next(i for i, r in enumerate(rs) if cell(r, 0) == "推荐日期")
    data = [r for r in rs[hi + 1:] if cell(r, 2)]
    mds = []
    for r in data:
        m = re.match(r"(\d{1,2})月(\d{1,2})日", cell(r, 0))
        mds.append((int(m.group(1)), int(m.group(2))) if m else None)
    filled, last = [], None
    for md in mds:
        if md:
            last = md
        filled.append(last)
    dates = year_for_sequence([md for md in filled if md])
    di = iter(dates)
    for r, md in zip(data, filled):
        d = next(di) if md else ""
        url, pw = pan(cell(r, 8))
        push("starshort", "YouTube剧单", t=cell(r, 2), cn=cell(r, 1), lang=cell(r, 3), kind=cell(r, 4), g=cell(r, 5),
             tags=cell(r, 6).replace("、", " · "), date=d, pan=url, pw=pw, ep=to_int(cell(r, 9)), pay=to_int(cell(r, 10)), yt=True)
    rs = sheet_rows("starshort__VOxpIN.json")
    hi = next(i for i, r in enumerate(rs) if cell(r, 0) == "原剧名")
    for r in rs[hi + 1:]:
        if not cell(r, 1):
            continue
        url, pw = pan(cell(r, 7))
        push("starshort", "高充值剧单", t=cell(r, 1), cn=cell(r, 0), lang=cell(r, 2), kind=cell(r, 3), g=cell(r, 4),
             tags=cell(r, 5).replace("、", " · "), pan=url, pw=pw, ep=to_int(cell(r, 8)), pay=to_int(cell(r, 9)), sig=[{"s": "sh"}])


# ---------- TouchShort ----------

def touchshort():
    rs = sheet_rows("touchshort__baaa17.json")
    head = rs[0]
    # 从表头找每种语言的 名字列 / 链接列
    lang_cols = []
    for i, h in enumerate(head):
        m = re.match(r"分销素材50%剧集网盘链接（(.+)）", h)
        if m:
            lang_cols.append((m.group(1), i))
    for r in rs[1:]:
        if not cell(r, 3) and not cell(r, 4):
            continue
        url, pw = pan(cell(r, 11))
        langs = []
        alt = {}
        for lname, li in lang_cols:
            u, p = pan(cell(r, li))
            if u:
                langs.append(lname)
                alt[lname] = u + (("#" + p) if p else "")
        push("touchshort", "Sheet1", t=cell(r, 3) or cell(r, 4), cn=cell(r, 4), g=cell(r, 5), era=cell(r, 6), tags=cell(r, 7).replace("、", " · "),
             ep=to_int(cell(r, 8)), paytxt=cell(r, 9)[:24], kind=cell(r, 12) or cell(r, 0), date=iso_date(cell(r, 1)), date2=iso_date(cell(r, 2)),
             pan=url, pw=pw, langs=langs, alt=alt, lang=langs[0] if len(langs) == 1 else ("多语言" if langs else ""))


# ---------- flareflow ----------

def flareflow():
    for fname, src in (("flareflow__d49b4e.json", "英语剧单"), ("flareflow__z6DfWg.json", "小语种剧单")):
        for r in sheet_rows(fname)[1:]:
            if not cell(r, 2):
                continue
            url, pw = pan(cell(r, 5))
            push("flareflow", src, t=cell(r, 2), cn=cell(r, 3) if cell(r, 3) != cell(r, 2) else "", id=cell(r, 1), lang=cell(r, 0), kind=cell(r, 4), pan=url, pw=pw)


# ---------- MoboReels ----------

def moboreels():
    for r in ([] if os.environ.get("PICK_SOURCE_RETAIN_MOBOREELS") == "1" else sheet_rows("moboreels__omjCKZ.json")[1:]):
        if not cell(r, 2):
            continue
        url, pw = pan(cell(r, 11))
        grade = cell(r, 12).strip().upper()
        push("moboreels", "全语种", t=cell(r, 2), cn=cell(r, 3), lang=cell(r, 1), g=cell(r, 6), tags=cell(r, 7).replace(",", " · ")[:80],
             kind=cell(r, 8), ep=to_int(cell(r, 9)), pay=to_int(cell(r, 10)), pan=url, pw=pw, grade=grade, date=iso_date(cell(r, 0)),
             sig=[{"s": "mg", "g": grade}] if grade in ("SSS", "SS", "S", "A", "B", "C", "D") else None)


# ---------- 组装 ----------

daily, weekly = kalos()
shortmax(); flickreels(); dramabox(); goodshort(); starshort(); touchshort(); flareflow(); moboreels()

# KalosTV：Youtube剧单 是英语剧单的子集，同名只留一行并标 yt
_en = {}
for r in rows:
    if r["p"] == "kalos" and r["src"] == "英语剧单":
        _en.setdefault(r["t"].strip().lower(), r)
_drop = set()
for i, r in enumerate(rows):
    if r["p"] == "kalos" and r["src"] == "Youtube剧单":
        e = _en.get(r["t"].strip().lower())
        if e is not None:
            e["yt"] = True
            _drop.add(i)
rows[:] = [r for i, r in enumerate(rows) if i not in _drop]
counts["kalos"] -= len(_drop)

# KalosTV 榜单信号挂到剧单行上；剧单里没有的榜单剧另起一行
DECO_RE = re.compile(r"^[\U0001F195\U0001F525\u2B50\u2705\U0001F4A5\s]+")  # 🆕 🔥 ⭐ ✅ 💥


def strip_deco(t):
    return DECO_RE.sub("", (t or "").strip())


def norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[’‘]", "'", strip_deco(t).lower()))


# 按平台分桶的剧名索引：KalosTV 的日榜 / 周榜、鹊娱的两张跨剧场榜都靠它把信号挂到剧单行上
by_title = defaultdict(lambda: defaultdict(list))
for r in rows:
    by_title[r["p"]][norm(r["t"])].append(r)


def attach(p, title, sig, src_missing, single=False, **fallback):
    """把一条榜单信号挂到 p 平台里同名的剧单行上；剧单里没有就用榜里给的字段另起一行（src_missing 标明来源）。
    single=True 时只挂一行：同名多行先按语言（fallback 里的 lang）筛，再取有 id / 日期最新的那行——
    榜单 tab 里一个名次对应一行，挂到三个语种版本上就是三行 #1（鹊娱榜 2026-09-12 实测踩到）。"""
    key = norm(title)
    hits = by_title[p].get(key)
    if not hits:
        # 常见差异：剧单里带冒号全角、结尾标点
        for k, v in by_title[p].items():
            if k.replace("：", ":") == key.replace("：", ":"):
                hits = v
                break
    if hits and single:
        lang = fallback.get("lang")
        same_lang = [h for h in hits if lang and h.get("lang") == lang]
        pool = same_lang or hits
        hits = [max(pool, key=lambda h: (1 if h.get("id") else 0, h.get("date") or ""))]
    if hits:
        for h in hits:
            h.setdefault("sig", []).append(sig)
    else:
        r = push(p, src_missing, t=strip_deco(title), sig=[sig], **fallback)
        if r is not None:
            by_title[p][key].append(r)  # 同一部剧在两张榜上都有时，第二张挂到第一张起的那一行


def attach_kalos(title, sig, kind="", tags=""):
    attach("kalos", title, sig, "榜单（剧单未收录）", lang="英语", kind=kind, tags=tags.replace("、", " · "))  # 日榜是英语榜


for title, hist in daily.items():
    hist.sort(reverse=True)
    latest = hist[0]
    best = min(h[1] for h in hist)
    attach_kalos(title, {"s": "kd", "r": latest[1], "d": latest[0], "best": best, "days": len(hist), "first": hist[-1][0], "note": latest[2],
                         "h": [[h[0], h[1]] + ([h[2]] if h[2] else []) for h in hist]}, latest[3], latest[4])
for title, hist in weekly.items():
    hist.sort(reverse=True)
    attach_kalos(title, {"s": "kw", "w": hist[0][1], "weeks": len(hist), "d": hist[0][0], "h": [[h[0], h[1]] for h in hist]}, hist[0][2])


# ---------- 鹊娱两张跨剧场榜（queyu.ts 每天采的 queyu/rank-<日期>.json，2026-09-12 起） ----------
# 与 KalosTV 日榜同形：sig.h 是 [日期, 名次] 的历史，r / d 是最新一次，best / days / first 从历史算。
# 榜里的剧按剧场挂到对应平台的剧单行上；剧单里没有的（DramaBox 的剧单常缺）用榜里给的字段另起一行。
QUEYU_THEATER = {"DramaBox": "dramabox", "FlickReels": "flickreels", "ShortMax": "shortmax", "GoodShort": "goodshort", "MoboReels": "moboreels",
                 "StarShort": "starshort", "TouchShort": "touchshort", "KalosTV": "kalos", "FlareFlow": "flareflow"}
QUEYU_LISTS = (("conv", "qc"), ("rev", "qr"))
queyu_hist = defaultdict(list)  # (list, platform, norm title) -> [(date, rank, row)]
queyu_dates = []
for fname in sorted(glob.glob(os.path.join(HERE, "queyu", "rank-*.json"))):
    snap = json.load(open(fname, encoding="utf-8"))
    d = snap.get("date") or ""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        print("queyu: skip", os.path.basename(fname), "bad date", d)
        continue
    queyu_dates.append(d)
    for lst, kind in QUEYU_LISTS:
        for row in snap.get(lst, []):
            p = QUEYU_THEATER.get(row.get("theater") or "")
            if not p or not row.get("title") or not row.get("rank"):
                continue
            queyu_hist[(kind, p, norm(row["title"]))].append((d, int(row["rank"]), row))
queyu_attached = queyu_missing = 0
for (kind, p, _key), hist in queyu_hist.items():
    hist.sort(key=lambda x: x[0], reverse=True)
    latest = hist[0]
    row = latest[2]
    sig = {"s": kind, "r": latest[1], "d": latest[0], "best": min(h[1] for h in hist), "days": len(hist), "first": hist[-1][0],
           "h": [[h[0], h[1]] for h in hist], "qy": row.get("id"), "pid": row.get("playletId") or ""}
    before = len(rows)
    attach(p, row["title"], sig, "鹊娱榜单（剧单未收录）", single=True, lang=lang_norm(row.get("language")), kind=row.get("productionType") or "",
           tags=(row.get("labels") or "").replace(",", " · "), date=(row.get("publishTime") or "")[:10] if re.match(r"^\d{4}-", row.get("publishTime") or "") else "")
    if len(rows) > before:
        queyu_missing += 1
    else:
        queyu_attached += 1

# 同一剧场里同一部剧只留一行：信号表（重点推荐 / ai剧专区 / 榜单）与剧单表重复、
# 剧单自己也会重复登记（重新上架、多个来源表）。合并规则：日期最新的行为底，缺的字段从其余行补，信号与来源表求并集。
def merge_dups():
    groups = defaultdict(list)
    for r in rows:
        # KalosTV / FlickReels 同一个外语名下常是不同的剧（中文名不同），键里带上中文名
        cn_key = norm(r.get("cn") or "") if r["p"] in ("kalos", "flickreels") else ""
        key = ("id", r["p"], r["id"]) if r.get("id") else ("t", r["p"], norm(r["t"]), r.get("lang", ""), cn_key)
        groups[key].append(r)
    merged = []
    for grp in groups.values():
        if len(grp) == 1:
            merged.append(grp[0])
            continue
        grp.sort(key=lambda x: (x.get("date") or "", 1 if x.get("pan") or x.get("zip") else 0), reverse=True)
        base = dict(grp[0])
        sigs = list(base.get("sig", []))
        seen_sig = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in sigs}
        srcs = [base["src"]]
        for o in grp[1:]:
            for k, v in o.items():
                if k in ("sig", "src"):
                    continue
                if k == "yt":
                    base["yt"] = base.get("yt", False) or v
                elif k not in base or base[k] in (None, "", [], {}):
                    base[k] = v
            for x in o.get("sig", []):
                j = json.dumps(x, sort_keys=True, ensure_ascii=False)
                if j not in seen_sig:
                    seen_sig.add(j)
                    sigs.append(x)
            if o["src"] not in srcs:
                srcs.append(o["src"])
        if sigs:
            base["sig"] = sigs
        base["src"] = " · ".join(srcs)
        base["n"] = len(grp)
        merged.append(base)
    dropped = len(rows) - len(merged)
    rows[:] = merged
    counts.clear()
    for r in rows:
        counts[r["p"]] += 1
    print("merged duplicate rows:", dropped)


merge_dups()

# key：平台 + ID 或标题 slug（进 db 路径，只允许 [a-z0-9-]）
def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "x"

seen_keys = defaultdict(int)
for r in rows:
    base = r["p"] + "-" + (r["id"] if r.get("id") else slug(r["t"]) + ("-" + slug(r.get("lang", ""))[:6] if r.get("lang") else ""))
    seen_keys[base] += 1
    r["k"] = base if seen_keys[base] == 1 else f"{base}-{seen_keys[base]}"

built = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="minutes")
out = {"built": built, "kalosDailyRange": [min(b for b in [h[0] for hh in daily.values() for h in hh] if b), max(h[0] for hh in daily.values() for h in hh)],
       "rows": rows}
json.dump(out, open(os.path.join(HERE, "catalog.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

# ---------- 报告 ----------
print("rows per platform:", dict(counts))
sigc = defaultdict(int)
for r in rows:
    for s in r.get("sig", []):
        sigc[s["s"]] += 1
print("signals:", dict(sigc))
print("with pan link:", sum(1 for r in rows if r.get("pan")), " with zip:", sum(1 for r in rows if r.get("zip")))
print("kalos daily range:", out["kalosDailyRange"], "titles:", len(daily), "weekly titles:", len(weekly))
print("queyu ranks:", len(queyu_dates), "days", (queyu_dates[0] + ".." + queyu_dates[-1]) if queyu_dates else "(none)",
      "attached:", queyu_attached, "new rows:", queyu_missing)
print("size bytes:", os.path.getsize(os.path.join(HERE, "catalog.json")))
langs = defaultdict(int)
for r in rows:
    langs[r["lang"]] += 1
print("langs:", sorted(langs.items(), key=lambda x: -x[1])[:25])
