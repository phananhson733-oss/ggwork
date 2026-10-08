"""Private local state and explicitly granted filesystem boundaries."""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class WorkerError(RuntimeError):
    """Safe, non-secret error code suitable for reporting."""


def digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def private_json(path, value):
    path = no_symlink(path)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise WorkerError("invalid_identifier")
    return value


def no_symlink(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise WorkerError("symlink_denied")
    return path


def confined(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkerError("grant_denied")
    path = no_symlink(root / relative)
    if not path.is_relative_to(root):
        raise WorkerError("grant_denied")
    return path


class WorkerStore:
    def __init__(self, home):
        self.home = no_symlink(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.lock_fd = None
        self._read_proofs = {}

    def config(self):
        path = no_symlink(self.home / "config.json")
        if path.stat().st_mode & 0o077:
            raise WorkerError("config_permissions_unsafe")
        return json.loads(path.read_text(encoding="utf-8"))

    def setup(self, *, gateway, device_id, token, output_root, model, model_sha256, model_language):
        url = urlsplit(gateway)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise WorkerError("gateway_https_origin_required")
        if not token or not re.fullmatch(r"[a-f0-9]{64}", model_sha256) or model_language not in ("en", "multilingual"):
            raise WorkerError("invalid_setup")
        if (self.home / "config.json").exists():
            raise WorkerError("already_configured_use_new_home")
        output = no_symlink(output_root)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(output.iterdir()) or output == self.home or self.home.is_relative_to(output):
            raise WorkerError("dedicated_empty_output_required")
        private_json(
            self.home / "config.json",
            {
                "gateway": gateway.rstrip("/"),
                "device_id": identifier(device_id),
                "token": token,
                "output_root": str(output),
                "model": str(no_symlink(model)),
                "model_sha256": model_sha256,
                "model_language": model_language,
                "grants": {},
            },
        )

    def grant(self, grant_id, root, *, receive=False):
        config = self.config()
        root = no_symlink(root)
        output = Path(config["output_root"])
        if not root.is_dir() or any(root.is_relative_to(p) or p.is_relative_to(root) for p in (output, self.home)):
            raise WorkerError("source_output_overlap")
        key = identifier(grant_id)
        if key in config["grants"] and config["grants"][key] != str(root):
            raise WorkerError("grant_immutable_use_new_id")
        config["grants"][key] = str(root)
        if receive:
            config["receiving_grants"] = sorted(set(config.get("receiving_grants", [])) | {key})
        private_json(self.home / "config.json", config)

    def source(self, grant_id, relative):
        root = self.config()["grants"].get(grant_id)
        if root is None:
            raise WorkerError("grant_denied")
        path = confined(no_symlink(root), relative)
        if not path.is_file():
            raise WorkerError("file_missing")
        return path

    def directory(self, grant_id, relative):
        root = self.config()["grants"].get(grant_id)
        if root is None:
            raise WorkerError("grant_denied")
        path = confined(no_symlink(root), relative)
        if not path.is_dir():
            raise WorkerError("directory_missing")
        return path

    def workspace(self, attempt_id):
        path = confined(no_symlink(self.config()["output_root"]), identifier(attempt_id))
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def journal(self):
        path = no_symlink(self.home / "journal.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save_journal(self, value):
        private_json(self.home / "journal.json", value)

    @contextlib.contextmanager
    def lock(self):
        path = self.home / "worker.lock"
        fd = os.open(no_symlink(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkerError("worker_already_running") from error
            self.lock_fd = fd
            yield
        finally:
            if self.lock_fd == fd:
                self.lock_fd = None
            os.close(fd)
