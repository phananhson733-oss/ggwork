"""Offline public harness-config boundary checks; never opens a database/socket."""

import importlib.util
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
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

    def test_ambient_libpq_overrides_are_removed_and_restored_even_after_failure(self):
        overrides = {
            "PGHOSTADDR": "203.0.113.1",
            "PGSERVICE": "production",
            "PGPORT": "9999",
        }
        with patch.dict(os.environ, overrides):
            with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                with module.isolated_pg_environment():
                    self.assertFalse(any(key.startswith("PG") for key in os.environ))
                    os.environ["PGAPPNAME"] = "temporary-test-setting"
                    raise RuntimeError("fixture failed")
            for key, value in overrides.items():
                self.assertEqual(os.environ[key], value)
            self.assertNotEqual(os.environ.get("PGAPPNAME"), "temporary-test-setting")

    def test_frontend_child_cannot_inherit_proxies_or_node_autoload(self):
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://outside.example.invalid",
                "NODE_USE_ENV_PROXY": "1",
                "NODE_OPTIONS": "--require=untrusted",
            },
        ):
            child = module.frontend_environment(
                Path("fixture.private.json"), "http://127.0.0.1:1234"
            )
            self.assertNotIn("HTTP_PROXY", child)
            self.assertNotIn("NODE_USE_ENV_PROXY", child)
            self.assertNotIn("NODE_OPTIONS", child)
            self.assertEqual(
                child["DEER_FLOW_INTERNAL_GATEWAY_BASE_URL"], "http://127.0.0.1:1234"
            )

    def test_gateway_client_bypasses_an_ambient_loopback_proxy(self):
        def handler(text):
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(text.encode())

                def log_message(self, *_args):
                    pass

            return Handler

        origin = ThreadingHTTPServer(("127.0.0.1", 0), handler("direct-origin"))
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler("proxy-was-used"))
        workers = [
            Thread(target=server.serve_forever, daemon=True)
            for server in (origin, proxy)
        ]
        for worker in workers:
            worker.start()
        try:
            proxy_url = f"http://127.0.0.1:{proxy.server_port}"
            with patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": proxy_url,
                    "ALL_PROXY": proxy_url,
                    "NO_PROXY": "",
                    "http_proxy": proxy_url,
                    "all_proxy": proxy_url,
                    "no_proxy": "",
                },
            ):
                with module.direct_http_client(
                    f"http://127.0.0.1:{origin.server_port}"
                ) as client:
                    self.assertEqual(client.get("/health").text, "direct-origin")
        finally:
            for server in (origin, proxy):
                server.shutdown()
                server.server_close()
            for worker in workers:
                worker.join(timeout=2)

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
