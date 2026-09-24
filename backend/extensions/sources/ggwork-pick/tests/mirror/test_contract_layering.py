"""Where the feed v2 contract lives: contracts.py alone defines it, feed_shape.py checks transport and hands the manifest's
content to contracts.parse_manifest (the P2-2b fix round's Finding 2), and no import cycle comes of it."""

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from ggwork_pick.mirror import client, contracts, dry_run, feed_shape

MIRROR = Path(contracts.__file__).resolve().parent
EXTENSION_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
SHARED = ("EXPORT_VERSION", "ROW_RESOURCES", "COUNTED_RESOURCES", "SERIES_RESOURCE", "MAX_LIMITS")


def _assigned_names(module_file: Path) -> set[str]:
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign | ast.AnnAssign)]
    targets = [target for node in assignments for target in (node.targets if isinstance(node, ast.Assign) else [node.target])]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _imported_modules(module_file: Path) -> set[str]:
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    return {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}


def test_the_shared_constants_are_defined_in_contracts_only():
    assert set(SHARED) <= _assigned_names(MIRROR / "contracts.py")
    for module in ("feed_shape.py", "client.py", "dry_run.py", "pan.py"):
        assert not set(SHARED) & _assigned_names(MIRROR / module), module
    # Whoever imports one gets contracts' own object; the client re-exports them for its callers.
    for module in (feed_shape, client, dry_run):
        for name in SHARED:
            if hasattr(module, name):
                assert getattr(module, name) is getattr(contracts, name), (module.__name__, name)
    assert {"COUNTED_RESOURCES", "ROW_RESOURCES", "SERIES_RESOURCE", "MAX_LIMITS"} <= set(client.__all__)


def test_contracts_imports_nothing_that_imports_it():
    # contracts -> errors only; feed_shape -> contracts; client -> feed_shape; pan -> contracts: no way back.
    assert _imported_modules(MIRROR / "contracts.py") & {"ggwork_pick.mirror", "ggwork_pick.mirror.feed_shape", "ggwork_pick.mirror.client"} == set()
    assert _imported_modules(MIRROR / "errors.py") == set()


def test_each_mirror_module_imports_first_without_a_cycle(tmp_path):
    # Each module imported first in a fresh mirror package: a cycle shows up as an ImportError on a partial module.
    script = (
        "import importlib, sys\n"
        "for name in ('contracts', 'feed_shape', 'client', 'pan', 'dry_run', 'errors'):\n"
        "    for loaded in [m for m in sys.modules if m.startswith('ggwork_pick.mirror')]:\n"
        "        del sys.modules[loaded]\n"
        "    importlib.import_module('ggwork_pick.mirror.' + name)\n"
        "print('ok')\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join([str(EXTENSION_ROOT), str(EXTENSION_API)])}
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr[-2000:]


def test_the_client_manifest_goes_through_contracts_parse_manifest(monkeypatch):
    from fake_realshort import Clock, FakeRealShort
    from mirror_harness import make_client

    seen = []
    original = contracts.parse_manifest

    def spy(manifest):
        seen.append(manifest)
        return original(manifest)

    monkeypatch.setattr(contracts, "parse_manifest", spy)
    clock = Clock()
    fake = FakeRealShort(now=clock)

    async def fetch():
        async with make_client(fake, clock) as feed:
            return await feed.manifest_when_free()

    manifest = asyncio.run(fetch())
    assert len(seen) == 1 and seen[0] == dict(manifest.row)
