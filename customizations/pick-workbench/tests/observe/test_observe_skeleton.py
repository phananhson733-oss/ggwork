"""TR-01: the observe package starts light, and the gateway entry point still installs the same way (plan D1, D7).

The two cron services run `python -m ggwork_pick.observe.<channel>` from the gateway image. Importing the package
must not drag in the gateway runtime, its app config, fastapi or alembic: each probe runs in a fresh interpreter,
because this process has long since imported all of them through the other tests.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
ROOT = Path(__file__).resolve().parents[4]
EXTENSION_API = ROOT / "backend/packages/extension-api"
HARNESS = ROOT / "backend/packages/harness"
HEAVY = ("deerflow.runtime", "deerflow.config.app_config", "fastapi", "alembic", "langgraph", "dotenv")
LIGHT_ENTRIES = (
    "ggwork_pick.observe.versions",
    "ggwork_pick.observe.clock",
    "ggwork_pick.observe.errors",
    "ggwork_pick.observe.admin.__main__",
    "ggwork_pick.observe.admin.args",
)


def _probe(module: str) -> dict:
    """Import `module` in a fresh isolated interpreter (-I: no cwd, no PYTHONPATH) that sees this checkout first."""
    code = (
        "import importlib, json, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE), str(EXTENSION_API), str(HARNESS)])}\n"
        f"module = importlib.import_module({module!r})\n"
        "print(json.dumps({'modules': sorted(sys.modules), 'file': module.__file__}))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _heavy_loaded(modules: list[str]) -> list[str]:
    return sorted(name for name in modules if any(name == heavy or name.startswith(heavy + ".") for heavy in HEAVY))


@pytest.mark.parametrize("module", LIGHT_ENTRIES)
def test_observe_import_is_light(module):
    loaded = _probe(module)
    assert Path(loaded["file"]).is_relative_to(SOURCE / "ggwork_pick/observe"), loaded["file"]
    assert _heavy_loaded(loaded["modules"]) == []
    # The gateway half of the package stays out of a cron process too.
    assert [name for name in loaded["modules"] if name in {"ggwork_pick.context", "ggwork_pick.routes", "ggwork_pick.service"}] == []


def test_host_serializer_import_is_light():
    """D1 reuses the host's JSON serializer object (deerflow.persistence.engine._json_serializer) in the cron engine."""
    loaded = _probe("deerflow.persistence.engine")
    assert Path(loaded["file"]).is_relative_to(HARNESS), loaded["file"]
    assert _heavy_loaded(loaded["modules"]) == []


class _Registry:
    def __init__(self):
        self.services, self.routes, self.lifecycles = [], [], []

    def service(self, service):
        self.services.append(service)

    def routers(self, routers):
        self.routes.extend(routers)

    def task_lifecycle(self, lifecycle):
        self.lifecycles.append(lifecycle)


def test_install_api_version(tmp_path):
    from ggwork_pick import install

    assert install.__deerflow_api__ == "0.2.1"
    assert install.__deerflow_name__ == "ggwork-pick"
    registry = _Registry()
    install(registry, {"data_dir": str(tmp_path)})
    assert [type(service).__name__ for service in registry.services] == ["PickService"]
    assert registry.services[0].data_dir == tmp_path
    assert [type(lifecycle).__name__ for lifecycle in registry.lifecycles] == ["PickLifecycle"]
    assert [router.prefix for router in registry.routes] == ["/api/pick"]


def test_observe_dependencies_are_declared():
    """The GSC service-account JWT (RS256) and the cookie jar's Fernet come from packages this extension names itself,
    not from whatever the host happens to pull in (TR-01)."""
    import tomllib

    from packaging.requirements import Requirement

    project = tomllib.loads((SOURCE / "pyproject.toml").read_text())["project"]
    requirements = {requirement.name: requirement for requirement in map(Requirement, project["dependencies"])}
    assert "crypto" in requirements["pyjwt"].extras
    assert "cryptography" in requirements
    assert project["version"] == "0.3.0"


def test_exit_codes():
    from ggwork_pick.observe.errors import ExitCode

    assert {code.name: int(code) for code in ExitCode} == {"OK": 0, "FAILED": 1, "REFUSED": 2, "STATE_UNAVAILABLE": 3, "INTERRUPTED": 130}


def test_versions_are_named_and_distinct():
    from ggwork_pick.observe import versions

    rules = {
        versions.TREND_RULES_VERSION: "trend-rules",
        versions.GSC_RULES_VERSION: "gsc-rules",
        versions.LINK_RULES_VERSION: "link-rules",
        versions.WATCH_RULES_VERSION: "watch-rules",
        versions.MARKET_MAP_VERSION: "market-map",
        versions.EVAL_RULES_VERSION: "eval-rules",
    }
    assert len(rules) == 6
    assert all(value == f"{family}-v1" for value, family in rules.items())
    assert versions.MIN_MIGRATION_HEAD == "0007"
    assert versions.COLLECTOR_VERSION and versions.COLLECTOR_VERSION not in rules
