"""Synthetic-only feedback source builders. Never capture production data into fixtures."""

from ggwork_pick.feedback.contracts import TABLES, FeedbackSnapshot


def snapshot_from_rows(rows):
    tables = []
    for table in TABLES:
        records = rows.get(table.key, [])
        names = sorted({name for record in records for name in record if name != "record_id"})
        ids = {name: f"fldSynthetic{index}" for index, name in enumerate(names)}
        tables.append(
            {
                "table_id": table.table_id,
                "fields": [{"field_id": ids[name], "name": name, "semantic_name": name, "field_type": "text"} for name in names],
                "records": [
                    {"record_id": record["record_id"], "values": {ids[name]: value for name, value in record.items() if name != "record_id"}}
                    for record in records
                ],
                "complete": True,
                "pages": 1,
            }
        )
    return FeedbackSnapshot.model_validate(
        {"scan_started_at": "2026-10-07T12:00:00Z", "scan_completed_at": "2026-10-07T12:00:08Z", "consistency": "bounded_scan", "tables": tables}
    )


def operating_rows():
    return {
        "dramas": [
            {"record_id": "drama-a", "剧ID": "SD-A", "剧名": "Synthetic Wolf", "语言": ["英语"], "平台": ["ReelShort"], "剧分类": ["狼人", "复仇"]},
            {"record_id": "drama-b", "剧ID": "SD-B", "剧名": "Synthetic Wolf", "语言": ["西班牙语"], "平台": ["ReelShort"], "剧分类": ["狼人"]},
        ],
        "posts": [
            {
                "record_id": f"release-{i}",
                "发布ID": f"SR-{i}",
                "剧": [{"id": "drama-a"}],
                "Post ID": f"post-{i}",
                "视频链接": f"https://www.tiktok.com/@synthetic/video/post-{i}",
                "实际发布时间": "2026-10-01T12:00:00+08:00",
                "账号": [{"id": "account-a"}],
            }
            for i in range(3)
        ],
        "observations": [
            {"record_id": "ob-old", "Post ID": "post-0", "关联发布记录": [{"id": "release-0"}], "快照日期": "2026-10-02T00:00:00+08:00", "播放量": 100},
            {"record_id": "ob-new", "Post ID": "post-0", "关联发布记录": [{"id": "release-0"}], "快照日期": "2026-10-03T00:00:00+08:00", "播放量": 150},
            {"record_id": "ob-missing", "Post ID": "post-1", "关联发布记录": [{"id": "release-1"}], "快照日期": "2026-10-03T00:00:00+08:00", "播放量": None},
            {"record_id": "ob-zero", "Post ID": "post-2", "关联发布记录": [{"id": "release-2"}], "快照日期": "2026-10-03T00:00:00+08:00", "播放量": 0},
        ],
        "cps_auto": [
            {"record_id": "revenue-account", "剧名": "Synthetic Wolf", "数据粒度": ["账号级"], "币种": ["USD"], "分成收益": "12.34", "日期": "2026-10-03"}
        ],
        "cps_manual": [{"record_id": "revenue-manual", "出单剧名（仅统计出单）": "Synthetic Wolf", "币种": ["USD"], "分成收益": "12.34", "日期": "2026-10-03"}],
        "revenue_totals": [{"record_id": "revenue-summary", "分成收益": "24.68", "币种": ["USD"]}],
    }
