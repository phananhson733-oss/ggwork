"""The shipped editing runtime must contain the reviewed business sources."""

import hashlib
import subprocess
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def test_business_extension_snapshots_match_tracked_sources():
    for source, distribution in (("ggwork-edit", "ggwork-edit"), ("pick-workbench", "ggwork-pick")):
        directory = ROOT / "customizations" / source
        files = subprocess.check_output(["git", "ls-files", "-z", "--", str(directory)], cwd=ROOT).decode().split("\0")
        assert files[:-1], f"No tracked sources for {source}"
        for filename in files[:-1]:
            original = ROOT / filename
            snapshot = ROOT / "backend/extensions/sources" / distribution / original.relative_to(directory)
            assert snapshot.is_file(), f"Missing managed snapshot: {snapshot.relative_to(ROOT)}"
            assert hashlib.sha256(original.read_bytes()).digest() == hashlib.sha256(snapshot.read_bytes()).digest(), f"Stale managed snapshot: {snapshot.relative_to(ROOT)}; regenerate with deerflow extensions upgrade"


def test_pick_image_includes_all_admitted_editing_skills():
    dockerfile = (ROOT / "docker/Dockerfile.pick-gateway").read_text(encoding="utf-8")
    for name in ("pick-drama", "clip-highlight", "clip-hook"):
        assert f"COPY skills/public/{name} ./skills/public/{name}" in dockerfile


def test_gateway_extra_declares_framework_dependencies_while_native_stays_light():
    project = tomllib.loads((ROOT / "customizations/ggwork-edit/pyproject.toml").read_text(encoding="utf-8"))["project"]
    native = {Requirement(value).name for value in project["dependencies"]}
    gateway = {Requirement(value).name for value in project["optional-dependencies"]["gateway"]}
    assert native == {"deerflow-extension-api", "httpx", "pydantic"}
    assert {"fastapi", "sqlalchemy", "alembic", "langchain", "langchain-core"} <= gateway
