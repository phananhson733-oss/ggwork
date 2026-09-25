"""The weekly pan check's pattern and runbook text, docs/pick-workbench/supabase.md section 6; no database needed.

One pattern serves pan-check.sql, pan-redact.sql and the runbook's PAN= line, and it keeps every branch the runbook had
before. Running the scripts is test_pan_runbook_sql.py; the container's disk is test_pan_runbook_disk.py.
"""

import hashlib
import re
import sys

from pan_runbook import (
    CHECK,
    DELETES,
    JSON_COLUMNS,
    KEPT,
    LOCATIONS,
    REDACT,
    REDACTED_NONE,
    RUNBOOK,
    UPDATES,
    cleared_at,
    pattern_of,
    runbook_pan,
    runbook_steps,
)

# The runbook's broad pattern before this change; the scripts must keep matching all of it.
OLD_PATTERN = r"pan\.baidu|pan\.quark|aliyundrive|alipan|115\.com|123pan|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|提取码|提取碼|访问码|訪問碼|pwd="
# One sample host per branch of RealShort #67's share-link list (its fixture's patterns.url); the check has to find them all.
REALSHORT_HOSTS = [
    "pan.baidu.com",
    "yun.baidu.com",
    "pan.quark.cn",
    "aliyundrive.com",
    "alipan.com",
    "115.com",
    "115cdn.com",
    "123pan.com",
    "123pan.cn",
    "123684.com",
    "123865.com",
    "123912.com",
    "lanzou.com",
    "lanzoui.com",
    "drive.uc.cn",
    "cloud.189.cn",
    "pan.xunlei.com",
    "caiyun.139.com",
    "yun.139.com",
    "weiyun.com",
    "jianguoyun.com",
    "mypikpak.com",
    "pan.wo.cn",
    "ctfile.com",
    "ilanzou.com",
    "feijipan.com",
    "lanzn.com",
    "wenshushu.cn",
    "cowtransfer.com",
    "yunpan.360.cn",
    "fast.uc.cn",
    "anxia.com",
    "123952.com",
    "400gb.com",
    "pipipan.com",
    "545c.com",
    "90pan.com",
    "089u.com",
    "474b.com",
    "t00y.com",
    "306t.com",
    "47ks.com",
    "4765.com",
    "77tj.com",
    "feijix.com",
    "fjpan.com",
    "wss.cc",
    "c-t.work",
    "yunpan.cn",
    "yunpan.com",
    "pan.360.cn",
    "quqi.com",
    "musetransfer.com",
    "tmp.link",
    "airportal.cn",
    "airportal.link",
    "easychuan.cn",
    "filez.com",
    "box.lenovo.com",
    "vdisk.weibo.com",
    "v.disk.weibo.com",
    "vdisk.cn",
    "kuaipan.cn",
    "dbank.com",
    "dbank.vmall.com",
    "pan.sohu.net",
    "fhrl.wostore.cn",
]
# Every non-ASCII character Unicode counts as whitespace, from Python's own tables. The pattern writes each as a branch of its
# own: a binary checkpoint column and a C-locale grep match them only as bytes, where [[:space:]] never sees them.
WIDE_SPACES = [chr(c) for c in range(0x80, sys.maxunicode + 1) if chr(c).isspace()]
# The pattern's other non-ASCII characters: the keywords and the full-width colon.
KEYWORD_CHARS = "提取码碼访问訪問密："


def test_the_scripts_and_the_runbook_use_one_pattern_that_keeps_the_old_one():
    pattern = pattern_of(CHECK.read_text(encoding="utf-8"))
    assert pattern_of(REDACT.read_text(encoding="utf-8")) == pattern
    assert runbook_pan() == f"PAN='{pattern}'"
    assert set(OLD_PATTERN.split("|")) <= set(pattern.split("|"))
    # Every share host RealShort scrubs is found by one of the host branches (grep -i and ~* ignore ASCII case). Only branches made of
    # letters, digits, -, \. and [a-z] count: splitting on | also leaves bits of the 密码 group such as a lone ":".
    hosts = [re.compile(alt, re.IGNORECASE) for alt in pattern.split("|") if re.fullmatch(r"(?:[A-Za-z0-9-]|\\\.|\[a-z\])+", alt)]
    for host in REALSHORT_HOSTS:
        assert any(p.search(f"share.{host.upper()}/s/1AbC") for p in hosts), host
    # Every backslash escape takes one or more backslashes, whatever the number of JSON layers.
    backslash = chr(92)
    assert backslash * 2 + "u" not in pattern.replace(backslash * 2 + "+u", "") and backslash * 2 + "[" not in pattern
    # Both gaps after 密码: ASCII whitespace, each non-ASCII whitespace character as its own branch, then the escapes.
    gap = "([[:space:]]|" + "|".join(WIDE_SPACES) + f"|{backslash * 2}+[bfnrtv]|{backslash * 2}+u[0-9a-fA-F]{{4}})*"
    assert pattern.count(gap) == 2
    assert {c for c in pattern if not c.isascii()} - set(KEYWORD_CHARS) == set(WIDE_SPACES)
    # The old inline queries are gone: the runbook holds the pattern once, and every grep uses $PAN.
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert runbook.count(pattern) == 1 and OLD_PATTERN not in runbook
    # The runbook's paste self-check is the hash of this very pattern.
    assert f"`{hashlib.sha256(pattern.encode()).hexdigest()}`" in runbook


def test_every_check_looks_at_the_database_and_the_disk():
    # Imports write the raw file before the rows: the database can be clean while the disk is not.
    steps = runbook_steps()
    assert sorted(steps) == list(range(1, 10))
    assert "-f docs/pick-workbench/supabase/pan-check.sql" in steps[1] and "PAN='" in steps[1]
    # pan_scan covers the raw feed files and the host's externalized tool outputs; its threads go in with -v disk_threads.
    assert 'pan_scan; echo "pan_scan 退出码 $?"' in steps[1] and "-v disk_threads=" in steps[1]
    assert "第 1 步" in steps[8] and "库和磁盘都查" in steps[8] and "第 1 步" in steps[9]
    # A leftover anywhere but the kept locations means redact and restart again, not only for candidate sets.
    assert "重做第 4、5 步" in steps[8] and "选择快照" in steps[8]
    assert "暂停使用" in steps[4]


def test_the_runbook_and_the_script_headers_count_what_the_scripts_print():
    # The operator compares the check's rows against these numbers and writes each UPDATE line of the redaction into
    # section 12; test_pan_runbook_sql.py holds the scripts' real output to the same lists.
    runbook, check, redact = (path.read_text(encoding="utf-8") for path in (RUNBOOK, CHECK, REDACT))
    section = runbook[runbook.index("**网盘片段核查") : runbook.index("## 7.")]
    locations, cleared = str(len(LOCATIONS)), str(len(REDACTED_NONE))
    # The redaction prints one UPDATE per rewritten location, then one DELETE for the GSC queries (D18).
    assert re.findall(r"(\d+) 行 `UPDATE n`", section) == [str(UPDATES)]
    assert re.findall(r"(\d+) 行 `DELETE n`", section) == [str(DELETES)]
    assert int(cleared) == UPDATES + DELETES
    assert set(re.findall(r"(\d+) 个位置", section + check)) == {locations}
    assert set(re.findall(r"(\d+) 个 0", section)) == {locations}
    assert set(re.findall(r"前 (\d+) 个", section + check)) == {cleared}
    assert set(re.findall(r"最后 (\d+) 个", section + check)) == {str(len(KEPT))}
    assert set(re.findall(r"(\d+) 个 JSON 列", section + redact)) == {str(len(JSON_COLUMNS))}
    # A database still before 0005, or before 0007, prints fewer lines; the runbook gives both numbers.
    assert sorted(set(re.findall(r"只有 (\d+) 行", section)), key=int) == [str(len(cleared_at("0004"))), str(len(cleared_at("0006")))]
