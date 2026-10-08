"""Conservative facts from common-query receipts, never arbitrary mirror prose."""

import hashlib
import json
import re

from ggwork_pick.answer_evidence import _RANK_LABELS, EvidenceAtom, EvidenceRead, _date, _label, _number, _url


def _reference(call_id, subject):
    digest = hashlib.sha256(subject.encode()).hexdigest()[:20]
    return f"tool:{call_id}:row:{digest}"


def _posted_scope(request):
    parts = []
    for key, label in (("account", "账号"), ("channel", "渠道")):
        value = request.get(key)
        if value:
            if not _label(value):
                return None
            parts.append(f"{label}“{value}”")
    start, end = request.get("published_from"), request.get("published_to")
    if start or end:
        if start and not _date(start) or end and not _date(end):
            return None
        parts.append(f"发布日期{start or '起始未知'}至{end or '截止未知'}")
    return "且".join(parts) + "范围内" if parts else "本次发布记录范围内"


def capture_common(evidence, call_id, payload):
    success = payload.get("status") in (None, "ok") and "counts" in payload
    request, pin = payload.get("request", {}), payload.get("pin", {})
    ref = f"tool:{call_id}"
    evidence.reads.append(
        EvidenceRead(
            "pick_query_data",
            call_id,
            "success" if success else "unavailable",
            pin.get("catalog_batch_id"),
            None,
            (),
            json.dumps(request, ensure_ascii=False, sort_keys=True),
            json.dumps({key: payload.get(key) for key in ("source_as_of", "mirror_synced_at", "actual_period")}, ensure_ascii=False, sort_keys=True),
        )
    )
    drama_count = request.get("domain") in {"catalog", "candidates", "rankings"} and request.get("rank") != "rs_ledger"
    total = payload.get("counts", {}).get("matched") if success and drama_count else None
    evidence.atoms.append(
        EvidenceAtom(
            "本次查询符合条件总数为",
            str(total) if type(total) is int and total >= 0 else None,
            "部",
            ref,
            field_name="matched_total",
            unit="部",
            display_prefix="本次查询共",
        )
    )
    if not success:
        return

    def atom(prefix, value, field, *, subject=None, reference=ref, suffix="", **kwargs):
        evidence.atoms.append(EvidenceAtom(prefix, value, suffix, reference, subject, field, **kwargs))

    period = payload.get("actual_period") or {}
    atom("本次榜单期次为", _date(period.get("value")), "actual_period")
    atom("本次资料来源时点为", _date(payload.get("source_as_of")), "source_as_of")
    atom("本次镜像版本为", _number(pin.get("mirror_version")), "mirror_version")
    rule = pin.get("rule_version")
    atom("本次规则版本为", rule if isinstance(rule, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", rule) else None, "rule_version")
    scope = {"candidate_pool": "候选池", "full_catalog": "完整剧库"}.get(request.get("scope"))
    atom("本次查询范围为", scope, "scope")
    posted_scope = _posted_scope(request)
    for row in payload.get("rows", []):
        drama, subject = row["drama"], row["identity"]
        evidence.titles.add(drama["title"])
        if not _label(drama["title"]):
            continue
        title, row_ref = f"《{drama['title']}》", _reference(call_id, subject)
        for key, label in (("source", "来源"), ("source_id", "来源编号")):
            value = drama.get(key)
            safe = value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) else None
            atom(f"{title}的{label}为", safe, key, subject=subject, reference=row_ref)
        language = drama.get("language", "")
        atom(
            f"{title}的语种为",
            language if re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language) else None,
            "language",
            subject=subject,
            reference=row_ref,
        )
        theater = drama.get("theater")
        atom(f"{title}的剧场为“", theater if _label(theater) else None, "theater", subject=subject, reference=row_ref, suffix="”")
        atom(f"{title}的上架日期为", _date(drama.get("listed_at")), "listed_at", subject=subject, reference=row_ref)
        for channel, permission in drama.get("channel_rules", {}).items():
            if channel in {"youtube", "tiktok", "facebook"}:
                atom(
                    f"{title}的{channel}规则为",
                    {"allowed": "允许", "denied": "禁止"}.get(permission),
                    f"channel_rules.{channel}",
                    subject=subject,
                    reference=row_ref,
                )
        if posted_scope:
            state = row.get("posted_status")
            value = {"posted": "已发布", "not_posted": "未发布"}.get(state) if state == "posted" or row.get("posted_scope_complete") is True else None
            atom(f"{title}在{posted_scope}的发布状态为", value, "posted_status", subject=subject, reference=row_ref)
        for index, signal in enumerate(drama.get("signals", [])):
            kind = signal["kind"]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", kind):
                continue
            observed = _date(signal.get("observed_at"))
            signal_ref = f"{row_ref}:signal:{index}"
            grade = signal.get("grade")
            for key, label, value in (
                ("rank", "名次", _number(signal.get("rank"))),
                ("observed_at", "观测日期", observed),
                ("value", "", _number(signal.get("value"))),
                ("grade", "等级", grade if grade in {"SSS", "SS", "S", "A", "B", "C", "D"} else None),
            ):
                display = {}
                if key == "rank" and kind in _RANK_LABELS and observed:
                    display = {"display_prefix": f"{title}在{observed}的{_RANK_LABELS[kind]}第", "display_suffix": "名"}
                atom(
                    f"{title}的{kind}{label}为",
                    value,
                    f"{kind}.{key}",
                    subject=subject,
                    reference=signal_ref,
                    observed_at=observed,
                    source_ref=signal.get("source_ref"),
                    **display,
                )
            if _url(signal.get("source_ref")):
                atom(f"来源[{signal_ref}]为", signal["source_ref"], "source_ref", subject=subject, reference=signal_ref)
    board = payload.get("board") or {}
    for platform, rule in (board.get("rules") or {}).get("platformRules", {}).items():
        name = rule.get("name")
        if not _label(name):
            continue
        permission = {"ok": "允许", "no": "禁止", "only": "仅限YouTube剧单"}.get(rule.get("yt"))
        atom(f"剧场“{name}”的youtube规则为", permission, "platform.youtube", subject=platform, reference=_reference(call_id, "platform:" + platform))
    for posted in board.get("posted", []):
        key = posted["sd"]
        if not _label(key):
            continue
        record_ref = _reference(call_id, "posted:" + key)
        # Explicitly aggregate record totals, never claim an account/window count.
        for field, label in (("post_count", "已发布条数"), ("sched_count", "待公开条数")):
            atom(
                f"发布记录“{key}”的{label}为",
                str(posted[field]) if type(posted.get(field)) is int and posted[field] >= 0 else None,
                field,
                subject=key,
                reference=record_ref,
            )
        atom(f"发布记录“{key}”的最近发布日期为", _date(posted.get("last_post_on")), "last_post_on", subject=key, reference=record_ref)
