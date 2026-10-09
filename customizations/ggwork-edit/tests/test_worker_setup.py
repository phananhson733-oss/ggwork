import subprocess
import sys

import pytest

from ggwork_edit.worker.storage import WorkerError, WorkerStore


def test_setup_authorizes_only_explicit_roots_and_keeps_credentials_private(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "episode-1.mp4").write_bytes(b"source")
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    (media / "escape.mp4").symlink_to(outside)
    store = WorkerStore(tmp_path / "state")
    store.setup(
        gateway="https://example.test",
        device_id="device-1",
        token="secret-token",
        output_root=tmp_path / "outputs",
        model=outside,
        model_sha256="a" * 64,
        model_language="en",
    )
    store.grant("drama", media)
    assert store.source("drama", "episode-1.mp4").read_bytes() == b"source"
    for path in ("../private.mp4", "escape.mp4", str(outside)):
        with pytest.raises(WorkerError):
            store.source("drama", path)
    assert (store.home / "config.json").stat().st_mode & 0o777 == 0o600
    assert store.home.stat().st_mode & 0o777 == 0o700
    with pytest.raises(WorkerError):
        store.grant("bad", tmp_path)
    with store.lock():
        with pytest.raises(WorkerError, match="already_running"):
            with store.lock():
                pass
    store.save_journal({"claim_request_id": "stable-claim"})
    assert WorkerStore(store.home).journal()["claim_request_id"] == "stable-claim"


def test_cli_rejects_token_argument_without_importing_gateway_packages(tmp_path):
    result = subprocess.run([sys.executable, "-m", "ggwork_edit.worker", "setup", "--token", "never-allowed"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "never-allowed" not in result.stdout
    probe = subprocess.run(
        [sys.executable, "-c", "import ggwork_edit.worker.cli,sys; assert not any(n in sys.modules for n in ('fastapi','sqlalchemy','deerflow'))"],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
