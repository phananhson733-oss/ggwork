"""Fixed synthetic content cases at the real shared planning HTTP boundary."""

import json
from pathlib import Path

import pytest
from test_contract import HEARTBEAT, OWNER, worker_identity
from test_planner import Model

CORPUS = json.loads((Path(__file__).parent / "fixtures/dialogue-quality-v1.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
async def test_annotated_dialogue_case_through_shared_planning_api(api, case):
    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, service, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Synthetic quality Mac"})).json()["device"]["id"]
    worker = worker_identity(app, device)
    base = f"/api/editing/worker/devices/{device}"
    await client.post(base + "/heartbeat", headers=worker, json=HEARTBEAT)
    manifest = {
        "version": 1,
        "grant_id": "grant-1",
        "files": [
            {
                "media_id": source["media_id"],
                "name": source["file"],
                "relative_path": source["file"],
                "episode": source["episode"],
                "size_bytes": source["bytes"],
            }
            for source in CORPUS["sources"]
        ],
    }
    outputs = case.get("outputs", [{"output_id": "out-1", "segments": case.get("cuts", [])}])
    requirements = {
        "profile": case.get("profile", "hook"),
        "instructions": "Preserve complete dialogue units in the requested narrative order.",
        "output_count": len(outputs),
        "duration_seconds": case["duration"],
        "aspect_ratio": case.get("aspect_ratio", "16:9"),
        "language": "en",
    }
    task = (
        await client.post(
            "/api/editing/tasks",
            headers=OWNER,
            json={
                "request_id": case["id"],
                "title": "Synthetic envelope exchange",
                "requirements": requirements,
                "device_id": device,
                "source_manifest": manifest,
            },
        )
    ).json()
    for source, original in zip(manifest["files"], CORPUS["sources"], strict=True):
        source.update(state="verified", sha256=original["sha256"], duration_seconds=original["duration_seconds"])
    assert (await client.post(base + f"/tasks/{task['id']}/manifest", headers=worker, json={"source_manifest": manifest})).status_code == 200
    attempt = (await client.post(base + "/claim", headers=worker, json={"request_id": "quality-claim"})).json()["attempt"]
    plan = {
        "profile": requirements["profile"],
        "aspect_ratio": requirements["aspect_ratio"],
        "language": case.get("plan_language", "en"),
        "outputs": outputs,
    }
    model = Model(plan)
    app.include_router(build_planner_router(service, TextPlanner(model)))
    response = await client.post(
        base + f"/tasks/{task['id']}/plan",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "transcripts": [
                {"media_id": source["media_id"], "segments": source["segments"]}
                for source in CORPUS["sources"]
                if source["media_id"] != case.get("omit_transcript")
            ],
        },
    )
    assert response.status_code == case["expected_http"], response.text
    current = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert current["plan"] == (plan if case["expected_http"] == 200 else None)
    if case["expected_http"] == 200:
        sent = json.loads(model.messages[0][1].content)
        assert {source["media_id"] for source in sent["transcripts"]} == {"m-1", "m-2", "m-3"}
        assert sent["dialogue_units"]["m-2"] == [{"start": 0.0, "end": 5.44}]
    if "omit_transcript" in case:
        assert model.messages == []
