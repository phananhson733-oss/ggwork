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
from unittest.mock import patch, Mock, MagicMock

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
    def spawn_owned(self, root, *, exit_parent):
        ready = root / "child.pid"
        marker = root / "child.tick"
        child = (
            "import os,signal,time,sys; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); "
            + "\nwhile True: Path(sys.argv[2]).write_text(str(time.monotonic())); time.sleep(.02)"
        )
        parent = (
            "import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
            + "\nwhile not Path(sys.argv[2]).exists(): time.sleep(.01)\n"
            + ("" if exit_parent else "time.sleep(30)")
        )
        real_popen = subprocess.Popen
        holder = {}

        def spawn(_command, **kwargs):
            self.assertTrue(
                kwargs.get("start_new_session"),
                "Every QA supervisor must own its process group",
            )
            process = real_popen(
                [sys.executable, "-c", parent, child, str(ready), str(marker)], **kwargs
            )
            holder["process"] = process
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            return process

        return spawn, holder, ready, marker

    def assert_child_stopped(self, ready):
        child_pid = int(ready.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["ps", "-p", str(child_pid), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertIn(result.returncode, (0, 1))
            self.assertEqual(result.stderr, "")
            # A killed adopted child can briefly remain a zombie. It cannot write
            # or execute; killpg(...,0) is not a reliable liveness proof on macOS.
            state = result.stdout.strip()
            if not state or state.startswith("Z"):
                return
            time.sleep(0.02)
        self.fail("Owned child remained executable after cleanup")

    def emergency_cleanup(self, holder):
        process = holder.get("process")
        if process is None:
            return
        if holder.get("cleaned"):
            process.wait(timeout=5)
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)

    @unittest.skipUnless(os.name == "posix", "Local QA process-group contract is POSIX")
    def test_gateway_owns_group_and_cleans_term_ignoring_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            spawn, holder, ready, _marker = self.spawn_owned(root, exit_parent=False)
            probe = MagicMock()
            probe.__enter__.return_value.get.return_value.status_code = 200
            try:
                with (
                    patch.object(module.subprocess, "Popen", side_effect=spawn),
                    patch.object(
                        module.parity, "direct_http_client", return_value=probe
                    ),
                ):
                    process = module.launch_gateway(
                        {"DEER_FLOW_PROJECT_ROOT": str(root)}, 12345, root
                    )
                module.stop_group(process)
                self.assert_child_stopped(ready)
                holder["cleaned"] = True
            finally:
                self.emergency_cleanup(holder)

    @unittest.skipUnless(os.name == "posix", "Local QA process-group contract is POSIX")
    def test_browser_cleans_descendant_after_normal_exit_timeout_and_interrupt(self):
        for outcome in ("normal", "timeout", "interrupt"):
            with (
                self.subTest(outcome=outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                spawn, holder, ready, _marker = self.spawn_owned(
                    root, exit_parent=outcome == "normal"
                )

                def wrapped(command, **kwargs):
                    process = spawn(command, **kwargs)
                    actual_wait = process.wait
                    first = True

                    def wait(timeout=None):
                        nonlocal first
                        if first and outcome != "normal":
                            first = False
                            if outcome == "timeout":
                                raise subprocess.TimeoutExpired("synthetic", timeout)
                            raise KeyboardInterrupt
                        return actual_wait(timeout)

                    process.wait = wait
                    return process

                try:
                    with patch.object(module.subprocess, "Popen", side_effect=wrapped):
                        if outcome == "normal":
                            self.assertEqual(
                                module.run_browser(module.clean_environment(), root), 0
                            )
                        else:
                            error = (
                                subprocess.TimeoutExpired
                                if outcome == "timeout"
                                else KeyboardInterrupt
                            )
                            with self.assertRaises(error):
                                module.run_browser(module.clean_environment(), root)
                    self.assert_child_stopped(ready)
                    holder["cleaned"] = True
                finally:
                    self.emergency_cleanup(holder)


if __name__ == "__main__":
    unittest.main()
