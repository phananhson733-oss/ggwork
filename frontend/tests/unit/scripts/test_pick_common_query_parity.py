"""Offline public harness-config boundary checks; never opens a database/socket."""

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/pick-common-query-parity.py"
spec = importlib.util.spec_from_file_location("pick_common_parity", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ClusterConfiguration(unittest.TestCase):
    def test_libpq_overrides_and_nonlocal_targets_are_rejected(self):
        for target in (
            "postgresql://synthetic@127.0.0.1:5432/postgres?host=outside.example.invalid",
            "postgresql://synthetic@127.0.0.1/postgres?hostaddr=203.0.113.1",
            "postgresql://synthetic@127.0.0.1/postgres?service=production",
            "postgresql://synthetic@127.0.0.1/postgres?options=anything",
            "postgresql://synthetic@127.0.0.1/postgres#fragment",
            "mysql://synthetic@127.0.0.1/postgres",
            "postgresql://synthetic@outside.example.invalid/postgres",
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                module.validate_cluster_settings(
                    {"purpose": "throwaway tests only", "test_pg_url": target}
                )

    def test_only_explicit_local_throwaway_configuration_is_accepted(self):
        target = "postgresql://synthetic@127.0.0.1:5432/postgres"
        self.assertEqual(
            module.validate_cluster_settings(
                {"purpose": "throwaway tests only", "test_pg_url": target}
            ),
            target,
        )
        with self.assertRaises(ValueError):
            module.validate_cluster_settings(
                {"purpose": "production", "test_pg_url": target}
            )


if __name__ == "__main__":
    unittest.main()
