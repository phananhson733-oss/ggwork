"""Offline isolation checks for the real-browser QA entrypoint."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/pick-completion-browser.py"
spec = importlib.util.spec_from_file_location("completion_browser", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CompletionIsolation(unittest.TestCase):
    def test_nonlocal_or_libpq_overridden_cluster_fails_before_output_creation(self):
        for url in (
            "postgresql://u@external.example/postgres",
            "postgresql://u@127.0.0.1/postgres?host=external.example",
        ):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                config = base / "cluster.json"
                config.write_text(
                    json.dumps({"purpose": "throwaway tests only", "test_pg_url": url}),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    module.run(config, base / "not-created")
                self.assertFalse((base / "not-created").exists())

    def test_child_environment_drops_proxies_providers_autoload_and_database_overrides(
        self,
    ):
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://outside.invalid",
                "NODE_OPTIONS": "--require=outside",
                "PYTHONPATH": "/outside",
                "PICK_DATABASE_URL": "outside",
                "PGHOST": "outside",
                "AZURE_OPENAI_API_KEY": "synthetic-never-forwarded",
            },
        ):
            child = module.clean_environment()
            self.assertLessEqual(set(child), {"PATH", "HOME", "LANG", "TMPDIR"})
            for key in (
                "HTTP_PROXY",
                "NODE_OPTIONS",
                "PYTHONPATH",
                "PICK_DATABASE_URL",
                "PGHOST",
                "AZURE_OPENAI_API_KEY",
            ):
                self.assertNotIn(key, child)


class InstalledImportGuard(unittest.TestCase):
    def test_source_mask_is_rejected_before_any_fixture_database_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "cluster.json"
            config.write_text(
                json.dumps(
                    {
                        "purpose": "throwaway tests only",
                        "test_pg_url": "postgresql://synthetic@127.0.0.1:5432/postgres",
                    }
                ),
                encoding="utf-8",
            )
            source_package = SimpleNamespace(
                __file__="/not-site-packages/customizations/pick-workbench/ggwork_pick/__init__.py"
            )
            fixture = SimpleNamespace(up=Mock())
            previous = list(sys.path)
            try:
                with (
                    patch.dict(
                        sys.modules,
                        {"ggwork_pick": source_package, "board_fixture": fixture},
                    ),
                    self.assertRaisesRegex(RuntimeError, "declared QA mode"),
                ):
                    module.run(config, root / "output", mode="installed")
                fixture.up.assert_not_called()
            finally:
                sys.path[:] = previous


class OwnedTlsTeardown(unittest.TestCase):
    def test_foreign_data_or_marker_is_rejected_before_pg_ctl(self):
        script = SCRIPT.with_name("pick-completion-tls.py")
        tls_spec = importlib.util.spec_from_file_location("completion_tls", script)
        tls = importlib.util.module_from_spec(tls_spec)
        tls_spec.loader.exec_module(tls)
        for marker, data in (("wrong", "data"), ("owned", "../foreign")):
            with (
                self.subTest(marker=marker, data=data),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "owned-fixture.marker").write_text("owned", encoding="utf-8")
                descriptor = root / "test-env.json"
                descriptor.write_text(
                    json.dumps(
                        {
                            "fixture": "pick-completion-owned-tls-v1",
                            "marker": marker,
                            "data": str(root / data),
                            "bin": "/does-not-exist",
                        }
                    ),
                    encoding="utf-8",
                )
                with (
                    patch.object(tls, "command") as command,
                    self.assertRaises(ValueError),
                ):
                    tls.stop(descriptor)
                command.assert_not_called()


class SupervisedRunner(unittest.TestCase):
    @unittest.skipUnless(
        os.name == "posix", "The local QA process-group contract is POSIX"
    )
    def test_group_cleanup_reaches_owned_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            child = "import signal,time,sys; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[1]).write_text('ready'); time.sleep(30)"
            code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(30)"
            process = subprocess.Popen(
                [sys.executable, "-c", code, child, str(ready)], start_new_session=True
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                module.stop_group(process)
                self.assertIsNotNone(process.poll())
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    self.fail("Owned descendant process group survived teardown")
            finally:
                module.stop_group(process)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
