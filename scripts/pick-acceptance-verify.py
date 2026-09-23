"""Independent re-implementation of the pick acceptance rules; deliberately does not import ggwork_pick.

Usage: python scripts/pick-acceptance-verify.py FEED_JSON RESULTS_JSON
FEED_JSON is a full RealShort /api/pick-feed pull ({"rows": [...]}); RESULTS_JSON lists the
cloud results as {q, mt, c, rows[, parent]} with rows as decoded RealShort row keys. Both files
hold business data and stay outside Git.
"""
import base64, json, sys
feed = json.load(open(sys.argv[1]))["rows"]
results = {r["q"]: r for r in json.load(open(sys.argv[2]))}
# Q5 answer as shown in the chat on 2026-09-23 (count tool, batch captured 03:40:27Z).
count_answer = {"total": 2241, "by_theater": {"ReelShort": 839, "KalosTV": 550, "ShortMax": 522, "DramaBox": 145, "GoodShort": 113, "FlickReels": 58, "MoboReels": 10, "StarShort": 4}}
key = lambda r: base64.urlsafe_b64decode(r["source_id"] + "=" * (-len(r["source_id"]) % 4)).decode()
ident = lambda r: json.dumps([r["source"], r["source_id"], r["language"]], ensure_ascii=False, separators=(",", ":"))
def latest(r): return max((s["observed_at"] for s in r["signals"] if s["observed_at"]), default="")
def expected(c, exclude=()):
    rows = [r for r in feed if r["availability"] != "delisted" and key(r) not in exclude]
    if c.get("language"): rows = [r for r in rows if r["language"] == c["language"]]
    if c.get("theater"): rows = [r for r in rows if r["theater"].lower() == c["theater"].lower()]
    if c.get("query"): rows = [r for r in rows if c["query"].lower() in r["title"].lower()]
    if c.get("channel"): rows = [r for r in rows if r["channel_rules"].get(c["channel"]) == "allowed" and r["availability"] == "active"]
    if c.get("exclude_posted"): rows = [r for r in rows if r["posted"]["post_count"] == 0]
    if c.get("posted_account"): rows = [r for r in rows if c["posted_account"].lower() not in [a.lower() for a in r["posted"]["accounts"]]]
    k = c.get("signal_kind")
    if k: rows = [r for r in rows if any(s["kind"] == k for s in r["signals"])]
    rows.sort(key=ident)
    if c.get("sort") == "rank":
        sig = lambda r: max((s for s in r["signals"] if s["kind"] == k), key=lambda s: (s["observed_at"] or "", s["rank"] is not None, -(s["rank"] or 0)))
        board = max(s["observed_at"] for r in feed for s in r["signals"] if s["kind"] == k and s["observed_at"])
        rows = [r for r in rows if sig(r)["observed_at"] == board]
        rows.sort(key=lambda r: (sig(r)["rank"] is None, sig(r)["rank"] or 0))
    else:
        rows.sort(key=latest, reverse=True)
    return rows
report = []
for q, r in results.items():
    exclude = set(results[r["parent"]]["rows"]) if r.get("parent") else set()
    rows = expected(r["c"], exclude)
    got = r["rows"]; want = [key(x) for x in rows[: r["c"]["limit"]]]
    # every returned row must satisfy the conditions in the current source (drift can only reorder/replace, not violate)
    valid = {key(x) for x in rows}
    report.append({"q": q, "order_match": got == want, "all_rows_satisfy": all(g in valid for g in got), "mt_cloud": r["mt"], "mt_now": len(rows),
                   "diff": [w for w in want if w not in got][:3]})
en = [r for r in feed if r["language"] == "en"]
th = {}
for r in en: th[r["theater"]] = th.get(r["theater"], 0) + 1
report.append({"q": "Q5", "total_cloud": count_answer["total"], "total_now": len(en), "by_theater_now": dict(sorted(th.items(), key=lambda kv: -kv[1])), "by_theater_cloud": count_answer["by_theater"]})
print(json.dumps(report, ensure_ascii=False, indent=1))
