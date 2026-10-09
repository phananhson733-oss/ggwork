"""CLI protocol regression; HTTP and FFmpeg fakes are not live media evidence."""

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/editing-live-acceptance.py"


@pytest.mark.parametrize("failure", [None, "hash", "range", "decode", "stream"])
def test_completed_public_task_receipt_can_pass_cli_verification(tmp_path, monkeypatch, failure):
    media = b"synthetic fixture bytes" * 100
    digest = hashlib.sha256(media).hexdigest()
    login = tmp_path / "login.json"
    login.write_text(json.dumps({"email": "test@example.com", "password": "test-only"}), encoding="utf-8")
    results = tmp_path / "results"
    requests = []

    def gateway(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/auth/login/local":
            return httpx.Response(200, headers={"set-cookie": "csrf_token=test-csrf; Path=/; Secure"}, json={})
        assert request.headers["X-CSRF-Token"] == "test-csrf"
        if request.url.path == "/api/editing/tasks/task-1":
            return httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "status": "completed",
                    "stage": "completed",
                    "outputs": [{"id": "out-1", "status": "completed", "result": {"sha256": "0" * 64 if failure == "hash" else digest, "size_bytes": len(media)}}],
                },
            )
        assert request.url.path == "/api/editing/tasks/task-1/outputs/out-1/content"
        if request.headers.get("Range"):
            assert request.headers["Range"] == "bytes=0-1023"
            return httpx.Response(206, headers={"Content-Range": f"bytes 1-1024/{len(media)}" if failure == "range" else f"bytes 0-1023/{len(media)}"}, content=media[:1024])
        if failure == "stream":

            class BrokenStream(httpx.SyncByteStream):
                def __iter__(self):
                    yield media[:100]
                    raise httpx.ReadError("private external failure")

            return httpx.Response(200, stream=BrokenStream())
        return httpx.Response(200, content=media)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(gateway)))

    # The external decoder succeeds here; real decoding belongs to native/live tests.
    def decode(*args, **kwargs):
        if failure == "decode":
            raise subprocess.CalledProcessError(1, args[0])
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(subprocess, "run", decode)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--gateway", "https://localhost", "--credentials", str(login), "--device", "device-1", "--grant", "synthetic", "--request-id", "observe-1", "--task-id", "task-1", "--results", str(results)],
    )
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exited.value.code == (1 if failure else 0)
    evidence_files = list(results.glob("*/evidence.json"))
    assert len(evidence_files) == 1
    evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    assert evidence["passed"] is (failure is None)
    if failure:
        assert evidence["failure"]
        assert not list(results.glob("*/*.mp4"))
        assert not list(results.glob("*/*.partial"))
    else:
        assert evidence["outputs"][0]["sha256"] == digest
        assert next(results.glob("*/*.mp4")).read_bytes() == media
    assert ("POST", "/api/editing/tasks") not in requests
    original_evidence = evidence_files[0].read_bytes()
    original_media = {path: path.read_bytes() for path in results.glob("*/*.mp4")}
    failure = "hash"
    with pytest.raises(SystemExit) as rerun:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert rerun.value.code == 1
    assert len(list(results.glob("*/evidence.json"))) == 2
    assert evidence_files[0].read_bytes() == original_evidence
    assert all(path.read_bytes() == content for path, content in original_media.items())
    assert not list(results.glob("*/*.partial"))


def test_live_cli_rejects_non_loopback_gateway_before_reading_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--gateway",
            "https://production.example.com",
            "--credentials",
            str(tmp_path / "missing-private-login"),
            "--device",
            "d",
            "--grant",
            "synthetic",
            "--request-id",
            "r",
            "--results",
            str(tmp_path / "results"),
        ],
    )
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exited.value.code == 2
    assert not (tmp_path / "results").exists()
