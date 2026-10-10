"""SYNTHETIC source and scripted candidate fixture for local browser acceptance.

Never deploy/import this module in production. Run with isolated DEER_FLOW_HOME,
config, database and regular QA account. FEEDBACK_E2E_HOME/state.json controls
source mode (ok/partial/auth/error/pending), views and mapping_mode
(confirmed/manual_unverified/pending_master). Source replaces ONLY the
provider: actual sync, SQL, auth, routes, runtime tools, and frontend stay intact.
The fixture endpoints require normal authenticated sessions. Candidate runs are
explicitly scripted, not evidence that a live model selected/called these tools.
"""

import asyncio
import json
import os
from pathlib import Path

from deerflow_extension_api.auth import resolve_principal
from fastapi import HTTPException, Request

from feedback.fakes import operating_rows, snapshot_from_rows
from ggwork_pick.feedback.contracts import SourcePageV2
from ggwork_pick.feedback.source import FeedbackSourceError
from ggwork_pick.feedback.sync import FeedbackSyncService
from ggwork_pick.service import PickService

D = Path(os.environ["FEEDBACK_E2E_HOME"])


class SyntheticSource:
    def __init__(self, *args):
        self.state = json.loads((D / "state.json").read_text())
        rows = operating_rows()
        mapping_mode = self.state.get("mapping_mode", "confirmed")
        rows["dramas"][0].update(
            {
                "选剧台剧集ID": '["synthetic","catalog-a","en"]',
                "选剧台对应状态": "待确认" if mapping_mode == "pending_master" else "已确认",
            }
        )
        rows["external_ids"] = [
            {
                "record_id": "synthetic-mapping-a",
                "关联剧集": [{"id": "drama-a"}],
                "来源系统": "RSBoost",
                "来源剧场": "ReelShort",
                "外部ID类型": "剧目ID",
                "外部ID": "001Synthetic",
                "适用范围": "账号:synthetic-cps-account",
                "确认状态": "已确认",
                "核对依据": "Synthetic fixture assertion; not real financial evidence",
            }
        ]
        # feedback-v3 has no automatic CPS lane: revenue reaches a drama only through a manual row that
        # links it and states the single-drama grain. A direct link alone must not attribute the amount.
        rows["cps_manual"][0].update(
            {
                "关联剧集": [{"id": "drama-a"}],
                "剧场": "ReelShort",
                "数据粒度": "待核验" if mapping_mode == "manual_unverified" else "单剧",
            }
        )
        rows["observations"][1]["播放量"] = self.state.get("views", 150)
        if self.state["mode"] == "partial":
            rows["observations"][1]["采集状态"] = ["部分缺失"]
        self.tables = {t.table_id: t for t in snapshot_from_rows(rows, transform_version="feedback-v3").tables}

    async def fields(self, table):
        if self.state["mode"] == "auth":
            raise FeedbackSourceError("auth_required")
        if self.state["mode"] == "error":
            raise FeedbackSourceError("incomplete")
        if self.state["mode"] == "pending":
            await asyncio.sleep(0.1)
        return self.tables[table.table_id].fields

    async def page(self, table, fields, offset):
        return SourcePageV2(table_id=table.table_id, records=self.tables[table.table_id].records, has_more=False)


original = PickService.initialize
service = None


async def initialize(self, *args, **kwargs):
    global service
    await original(self, *args, **kwargs)
    service = self


PickService.initialize = initialize
from app.gateway.app import app  # noqa: E402 -- patch fixture source before app construction


@app.post("/api/pick/e2e/enable")
async def enable(request: Request):
    principal = resolve_principal(request)
    if principal is None:
        raise HTTPException(401)
    service.feedback = FeedbackSyncService(service.session_factory, owner_id=principal.user_id, enabled=True, source_factory=SyntheticSource)
    return {"synthetic": True, "owner_id": principal.user_id}


@app.post("/api/pick/e2e/candidate")
async def candidate(request: Request):
    import uuid
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.tools import query_candidates_tool

    principal = resolve_principal(request)
    if principal is None:
        raise HTTPException(401)
    owner = principal.user_id
    repo = PickRepository(service.session_factory, owner)
    if await repo.current_batch("catalog") is None:
        await Importer(repo, service.data_dir).catalog(
            json.dumps(
                [
                    {
                        "source": "synthetic",
                        "source_id": "catalog-a",
                        "title": "Synthetic Wolf",
                        "language": "en",
                        "theater": "ReelShort",
                        "posted": {"matched": True, "records": ["SD-A"], "post_count": 3},
                    }
                ]
            ).encode(),
            "json",
        )
    run = str(uuid.uuid4())
    thread = str(uuid.uuid4())
    store = ExtensionData(run)
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo(run, run, thread, "lead"))
    runtime = SimpleNamespace(context={"user_id": owner, EXTENSION_TASK_STORE_KEY: store}, tool_call_id=str(uuid.uuid4()))
    result = json.loads(await query_candidates_tool.coroutine(filters={"exclude_selected": False}, runtime=runtime))
    if "id" in result:
        await app.state.run_store.put(
            run_id=run, thread_id=thread, assistant_id="lead", status="success", user_id=owner, metadata={"synthetic_scripted_tool": True}, kwargs={}
        )
    return result
