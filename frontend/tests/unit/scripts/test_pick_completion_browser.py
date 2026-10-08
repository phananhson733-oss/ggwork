"""Offline isolation checks for the real-browser QA entrypoint."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
