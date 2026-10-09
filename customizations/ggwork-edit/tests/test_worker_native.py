import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from ggwork_edit.worker.native import NativeWorker, ProcessRunner, WorkerStopped
from ggwork_edit.worker.storage import WorkerError, WorkerStore, digest

ASSETS = Path(os.environ.get("GGWORK_NATIVE_ASSETS", "/nonexistent"))
pytestmark = pytest.mark.skipif(not (ASSETS / "ggml-tiny.en.bin").exists(), reason="opt-in real native model and synthetic media required")


def configured(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    for path in (ASSETS / "synthetic-drama").glob("*.mp4"):
        shutil.copyfile(path, media / path.name)
    store = WorkerStore(tmp_path / "state")
    model = ASSETS / "ggml-tiny.en.bin"
    store.setup(
        gateway="https://example.test",
        device_id="dev-1",
        token="test",
        output_root=tmp_path / "output",
        model=model,
        model_sha256=digest(model),
        model_language="en",
    )
    store.grant("drama", media)
    return store


@pytest.mark.asyncio
async def test_real_transcription_render_and_immutable_original_audio_output(tmp_path):
    store = configured(tmp_path)
    worker = NativeWorker(store)
    manifest = await worker.discover("drama", ".")
    assert [f["episode"] for f in manifest["files"]] == [1, 2, 3]
    verified = await worker.verify_manifest(manifest)
    before = {f["media_id"]: f["sha256"] for f in verified["files"]}
    transcript = await worker.transcribe(verified, "en", "attempt-1")
    assert "evidence" in str(transcript).lower()
    requirements = {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "duration_seconds": 5, "output_count": 1}
    plan = {
        "profile": "hook",
        "aspect_ratio": "9:16",
        "language": "en",
        "outputs": [{"output_id": "out-1", "segments": [{"media_id": verified["files"][0]["media_id"], "start": 0, "end": 5}]}],
    }
    result = await worker.render(verified, requirements, plan, "attempt-1", "out-1")
    assert result["verified"] and result["video_codec"] == "h264" and result["audio_codec"] == "aac"
    assert result["width"] * 16 == result["height"] * 9
    assert abs(result["duration_seconds"] - 5) < 0.2
    assert worker.read_artifact({**result, "offset": 0, "length": 16})[4:8] == b"ftyp"
    with pytest.raises(WorkerError, match="output_exists"):
        await worker.render(verified, requirements, plan, "attempt-1", "out-1")
    for file in verified["files"]:
        assert digest(store.source("drama", file["relative_path"])) == before[file["media_id"]]
    with store.source("drama", verified["files"][0]["relative_path"]).open("ab") as out:
        out.write(b"changed")
    with pytest.raises(WorkerError, match="source_changed"):
        await worker.transcribe(verified, "en", "attempt-2")


@pytest.mark.asyncio
async def test_process_stop_waits_for_actual_exit(tmp_path):
    stop = asyncio.Event()
    runner = ProcessRunner(stop)
    marker = tmp_path / "done"
    child = asyncio.create_task(
        runner.run([sys.executable, "-c", "import time,pathlib; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('bad')"])
    )
    await asyncio.sleep(0.15)
    stop.set()
    with pytest.raises(WorkerStopped):
        await child
    await asyncio.sleep(2.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_invalid_plans_and_ambiguous_sources_fail_before_output(tmp_path):
    store = configured(tmp_path)
    worker = NativeWorker(store)
    manifest = await worker.verify_manifest(await worker.discover("drama", "."))
    requirements = {"profile": "hook", "aspect_ratio": "1:1", "language": "en", "duration_seconds": 5, "output_count": 1}
    for segment in (
        {"media_id": "unknown", "start": 0, "end": 5},
        {"media_id": "media-1", "start": 0, "end": float("nan")},
        {"media_id": "media-1", "start": 0, "end": 100},
    ):
        with pytest.raises(WorkerError):
            await worker.render(
                manifest,
                requirements,
                {"profile": "hook", "aspect_ratio": "1:1", "language": "en", "outputs": [{"output_id": "out-1", "segments": [segment]}]},
                "invalid-attempt",
                "out-1",
            )
    assert not (tmp_path / "output" / "invalid-attempt").exists()
    with pytest.raises(WorkerError, match="model_language_unsupported"):
        await worker.transcribe(manifest, "auto", "auto-attempt")
    shutil.copyfile(store.source("drama", "episode-01.mp4"), tmp_path / "media" / "01.mp4")
    with pytest.raises(WorkerError, match="episode_order_ambiguous"):
        await worker.discover("drama", ".")


def test_browser_receipt_requires_explicit_receive_grant_and_preserves_completed_file(tmp_path):
    from ggwork_edit.worker.transfer import Receiver

    store = configured(tmp_path)
    incoming = tmp_path / "receiving"
    incoming.mkdir()
    store.grant("incoming", incoming)
    media = store.source("drama", "episode-01.mp4").read_bytes()
    manifest = {
        "version": 1,
        "grant_id": "incoming",
        "files": [{"media_id": "m1", "name": "Episode 1", "relative_path": "episode-1.mp4", "size_bytes": len(media), "episode": 1, "state": "selected"}],
    }
    command = {"task_id": "task-1", "transfer_id": "upload-1", "media_id": "m1", "source_manifest": manifest, "offset": 0, "length": len(media), "final": True}
    receiver = Receiver(store)
    with pytest.raises(WorkerError, match="grant_denied"):
        receiver.receive(command, media)
    store.grant("incoming", incoming, receive=True)
    receiver.receive(command, media)
    assert (incoming / "episode-1.mp4").read_bytes() == media
    with pytest.raises(WorkerError, match="transfer_target_exists"):
        receiver.receive(command, media)
    assert (incoming / "episode-1.mp4").read_bytes() == media


@pytest.mark.asyncio
async def test_remote_stop_heartbeat_stays_live_during_real_native_processing(tmp_path, monkeypatch):
    import json

    import httpx

    from ggwork_edit.worker import runtime

    store = configured(tmp_path)
    native = NativeWorker(store)
    manifest = await native.verify_manifest(await native.discover("drama", "."))
    attempt = {"id": "controlled-attempt", "fence": 1, "output_ids": ["out-1"]}
    store.save_journal({"task_id": "task-1", "attempt": attempt})
    task = {
        "id": "task-1",
        "status": "running",
        "attempt": attempt,
        "source_manifest": manifest,
        "requirements": {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "duration_seconds": 5, "output_count": 1},
        "plan": None,
        "outputs": [{"id": "out-1", "status": "pending"}],
    }
    transcribing = False
    heartbeats_during_asr = 0
    stopped = False

    async def gateway(request):
        nonlocal transcribing, heartbeats_during_asr, stopped
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/report"):
            if payload["kind"] == "stage" and payload["stage"] == "transcribing":
                transcribing = True
            if payload["kind"] == "heartbeat" and transcribing:
                heartbeats_during_asr += 1
                task["status"] = "stopping"
            if payload["kind"] == "stopped":
                stopped = True
                task["status"] = "stopped"
            return httpx.Response(200, json={"task": task, "stop_requested": task["status"] == "stopping", "fence": 2})
        return httpx.Response(200, json=task)

    monkeypatch.setattr(runtime, "HEARTBEAT_SECONDS", 0.01)
    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        session = runtime.WorkerSession(store, http)
        control = asyncio.create_task(session.control({"ready": True}))
        try:
            await session.execute(task)
        finally:
            session.shutdown.set()
            await control
    assert heartbeats_during_asr > 0 and stopped
    assert store.journal() == {}
    assert not list((tmp_path / "output").rglob("*.mp4"))


@pytest.mark.asyncio
async def test_stop_cancels_pending_planner_request_without_waiting_for_provider(tmp_path, monkeypatch):
    import json

    import httpx

    from ggwork_edit.worker import runtime

    store = configured(tmp_path)
    manifest = await NativeWorker(store).verify_manifest(await NativeWorker(store).discover("drama", "."))
    attempt = {"id": "planning-attempt", "fence": 1, "output_ids": ["out-1"]}
    store.save_journal({"task_id": "task-1", "attempt": attempt})
    task = {
        "id": "task-1",
        "status": "running",
        "attempt": attempt,
        "source_manifest": manifest,
        "requirements": {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "duration_seconds": 5, "output_count": 1},
        "plan": None,
        "outputs": [{"id": "out-1", "status": "pending"}],
    }
    planning = asyncio.Event()
    cancelled = False

    async def gateway(request):
        nonlocal cancelled
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/plan"):
            planning.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled = True
                raise
        if request.url.path.endswith("/report"):
            if payload["kind"] == "heartbeat" and planning.is_set():
                task["status"] = "stopping"
            if payload["kind"] == "stopped":
                task["status"] = "stopped"
            return httpx.Response(200, json={"task": task, "stop_requested": task["status"] == "stopping", "fence": 2})
        return httpx.Response(200, json=task)

    monkeypatch.setattr(runtime, "HEARTBEAT_SECONDS", 0.01)
    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        session = runtime.WorkerSession(store, http)
        control = asyncio.create_task(session.control({"ready": True}))
        try:
            await asyncio.wait_for(session.execute(task), timeout=10)
        finally:
            session.shutdown.set()
            await control
    assert cancelled and task["status"] == "stopped"


@pytest.mark.asyncio
async def test_planner_rejection_reports_failure_instead_of_replanning_forever(tmp_path):
    import json

    import httpx

    from ggwork_edit.worker.runtime import WorkerSession

    store = configured(tmp_path)
    manifest = await NativeWorker(store).verify_manifest(await NativeWorker(store).discover("drama", "."))
    attempt = {"id": "rejected-attempt", "fence": 1, "output_ids": ["out-1"]}
    store.save_journal({"task_id": "task-1", "attempt": attempt})
    task = {
        "id": "task-1",
        "status": "running",
        "attempt": attempt,
        "source_manifest": manifest,
        "requirements": {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "duration_seconds": 5, "output_count": 1},
        "plan": None,
        "outputs": [{"id": "out-1", "status": "pending"}],
    }
    failures = []

    async def gateway(request):
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/plan"):
            return httpx.Response(422, json={"detail": "Invalid model plan"})
        if request.url.path.endswith("/report"):
            if payload["kind"] == "failure":
                failures.append(payload["error"])
                task["status"] = "failed"
            return httpx.Response(200, json={"task": task, "stop_requested": False, "fence": 1})
        return httpx.Response(200, json=task)

    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        await WorkerSession(store, http).execute(task)
    assert failures == ["gateway_request_rejected"]
    assert store.journal() == {}


@pytest.mark.asyncio
async def test_transcription_never_overwrites_preexisting_symlink_targets(tmp_path):
    store = configured(tmp_path)
    worker = NativeWorker(store)
    manifest = await worker.verify_manifest(await worker.discover("drama", "."))
    outside = tmp_path / "precious-source.wav"
    outside.write_bytes(b"must remain unchanged")
    work = store.workspace("resumed-attempt")
    (work / "media-1.wav").symlink_to(outside)
    (work / "media-1-asr.json").symlink_to(outside)
    await worker.transcribe(manifest, "en", "resumed-attempt")
    assert outside.read_bytes() == b"must remain unchanged"


@pytest.mark.asyncio
async def test_real_english_model_cannot_claim_multilingual_readiness(tmp_path):
    from ggwork_edit.worker.native import doctor
    from ggwork_edit.worker.storage import private_json

    store = configured(tmp_path)
    config = store.config()
    config["model_language"] = "multilingual"
    private_json(store.home / "config.json", config)
    report = await doctor(store)
    assert not report["ready"] and "model_language_mismatch" in report["reasons"]
    worker = NativeWorker(store)
    manifest = await worker.verify_manifest(await worker.discover("drama", "."))
    with pytest.raises(WorkerError, match="model_language_mismatch"):
        await worker.transcribe(manifest, "fr", "wrong-model-attempt")


@pytest.mark.asyncio
async def test_disguised_concat_cannot_read_ungranted_media_through_directory_symlink(tmp_path):
    store = configured(tmp_path)
    root = tmp_path / "playlist-grant"
    root.mkdir()
    external = tmp_path / "ungranted"
    external.mkdir()
    shutil.copyfile(store.source("drama", "episode-01.mp4"), external / "secret.mp4")
    (root / "link").symlink_to(external, target_is_directory=True)
    (root / "episode-01.mp4").write_text("ffconcat version 1.0\nfile 'link/secret.mp4'\nduration 5.194014\n", encoding="utf-8")
    store.grant("playlist", root)
    worker = NativeWorker(store)
    manifest = await worker.discover("playlist", ".")
    with pytest.raises(WorkerError, match="native_process_failed"):
        await worker.verify_manifest(manifest)


@pytest.mark.asyncio
async def test_legacy_verified_playlist_cannot_bypass_extraction_or_render_policy(tmp_path):
    store = configured(tmp_path)
    root = tmp_path / "legacy-grant"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "media", target_is_directory=True)
    playlist = root / "episode-01.mp4"
    playlist.write_text("ffconcat version 1.0\nfile 'link/episode-01.mp4'\nduration 5.194014\n", encoding="utf-8")
    store.grant("legacy", root)
    worker = NativeWorker(store)
    manifest = await worker.discover("legacy", ".")
    manifest["files"][0].update(state="verified", sha256=digest(playlist), duration_seconds=5.194014)
    # Persisted v2 cache fixture from before the demux policy. Upgrades must never
    # reuse text obtained through a formerly admitted indirect source.
    import hashlib
    import json

    from ggwork_edit.worker.storage import private_json

    cache = store.home / "transcripts"
    cache.mkdir()
    old_key = hashlib.sha256(
        json.dumps([digest(playlist), store.config()["model_sha256"], digest(shutil.which("whisper-cli")), "en", "whisper-cpp-json-v2-16khz"]).encode()
    ).hexdigest()
    private_json(cache / (old_key + ".json"), [{"start": 0, "end": 5, "text": "Previously read outside grant"}])
    with pytest.raises(WorkerError, match="native_process_failed"):
        await worker.transcribe(manifest, "en", "legacy-asr")
    requirements = {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "duration_seconds": 5, "output_count": 1}
    plan = {
        "profile": "hook",
        "aspect_ratio": "9:16",
        "language": "en",
        "outputs": [{"output_id": "out-1", "segments": [{"media_id": "media-1", "start": 0, "end": 5}]}],
    }
    with pytest.raises(WorkerError, match="native_process_failed"):
        await worker.render(manifest, requirements, plan, "legacy-render", "out-1")
    assert not list((tmp_path / "output").rglob("*.mp4"))


def test_received_playlist_is_not_published_as_a_source(tmp_path):
    from ggwork_edit.worker.transfer import Receiver

    store = configured(tmp_path)
    root = tmp_path / "receiving-playlist"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "media", target_is_directory=True)
    store.grant("incoming", root, receive=True)
    data = b"ffconcat version 1.0\nfile 'link/episode-01.mp4'\nduration 5.194014\n"
    manifest = {
        "version": 1,
        "grant_id": "incoming",
        "files": [{"media_id": "m1", "episode": 1, "name": "Episode 1", "relative_path": "episode-1.mp4", "size_bytes": len(data), "state": "selected"}],
    }
    command = {
        "task_id": "task-1",
        "transfer_id": "playlist-upload",
        "media_id": "m1",
        "source_manifest": manifest,
        "offset": 0,
        "length": len(data),
        "final": True,
    }
    with pytest.raises(WorkerError, match="native_process_failed"):
        Receiver(store).receive(command, data)
    assert not (root / "episode-1.mp4").exists()


@pytest.mark.asyncio
async def test_disguised_network_playlist_makes_no_http_request(tmp_path):
    store = configured(tmp_path)
    hits = []

    async def endpoint(reader, writer):
        hits.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(endpoint, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        root = tmp_path / "http-playlist"
        root.mkdir()
        (root / "episode-01.mp4").write_text(
            f"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:5.194014,\nhttp://127.0.0.1:{port}/secret.mp4\n#EXT-X-ENDLIST\n", encoding="utf-8"
        )
        store.grant("http-playlist", root)
        worker = NativeWorker(store)
        with pytest.raises(WorkerError, match="native_process_failed"):
            await worker.verify_manifest(await worker.discover("http-playlist", "."))
        assert hits == []
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["mov", "mkv", "webm"])
async def test_self_contained_mov_matroska_webm_remain_supported(tmp_path, extension):
    store = configured(tmp_path)
    root = tmp_path / "container"
    root.mkdir()
    target = root / ("episode-01." + extension)
    args = ["ffmpeg", "-v", "error", "-i", str(store.source("drama", "episode-01.mp4"))]
    args += ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus"] if extension == "webm" else ["-c", "copy"]
    await ProcessRunner().run([*args, str(target)])
    store.grant("container", root)
    worker = NativeWorker(store)
    verified = await worker.verify_manifest(await worker.discover("container", "."))
    requirements = {"profile": "hook", "aspect_ratio": "1:1", "language": "en", "duration_seconds": 5, "output_count": 1}
    plan = {
        "profile": "hook",
        "aspect_ratio": "1:1",
        "language": "en",
        "outputs": [{"output_id": "out-1", "segments": [{"media_id": "media-1", "start": 0, "end": 5}]}],
    }
    assert (await worker.render(verified, requirements, plan, "container-render", "out-1"))["verified"]


@pytest.mark.asyncio
@pytest.mark.parametrize("submit_allowed", [False, True])
async def test_upgrade_reconciles_legacy_planning_journal_without_resubmitting(tmp_path, monkeypatch, submit_allowed):
    import json

    import httpx

    from ggwork_edit.worker import runtime

    store = configured(tmp_path)
    manifest = await NativeWorker(store).verify_manifest(await NativeWorker(store).discover("drama", "."))
    attempt = {"id": "legacy-planning-attempt", "fence": 1, "output_ids": ["out-1"]}
    store.save_journal({"task_id": "task-1", "attempt": attempt, **({"planner_submit_allowed": True} if submit_allowed else {})})
    task = {
        "id": "task-1",
        "status": "running",
        "stage": "planning",
        "attempt": attempt,
        "source_manifest": manifest,
        "requirements": {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "duration_seconds": 5, "output_count": 1},
        "plan": None,
        "outputs": [{"id": "out-1", "status": "pending"}],
    }
    model_calls, failures = [], []

    async def gateway(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/plan"):
            model_calls.append(body["attempt_id"])
            return httpx.Response(502, json={"detail": {"code": "planner_unavailable", "retry": "explicit"}})
        if request.url.path.endswith("/report"):
            if body["kind"] == "failure":
                failures.append(body["error"])
                task["status"] = "failed"
            return httpx.Response(200, json={"task": task, "stop_requested": False, "fence": 1})
        return httpx.Response(200, json=task)

    monkeypatch.setattr(runtime, "POLL_SECONDS", 0.01)
    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        await runtime.WorkerSession(store, http).execute(task)
    assert model_calls == (["legacy-planning-attempt"] if submit_allowed else [])
    assert failures == (["planner_unavailable"] if submit_allowed else ["planner_outcome_unknown"])
