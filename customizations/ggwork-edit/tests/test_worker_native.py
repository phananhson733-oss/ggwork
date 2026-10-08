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
