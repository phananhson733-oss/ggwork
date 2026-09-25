"""Personal selection data and tools for the DeerFlow workbench.

Only the entry point's decorator is imported here. The gateway half (context, routes, service) loads inside install(),
so `python -m ggwork_pick.observe...` in a cron process does not import langgraph, fastapi or the app config, and does
not run load_dotenv (trends radar plan D7).
"""

import os
from pathlib import Path

from deerflow_extension_api import extension


@extension(api="0.2.1", name="ggwork-pick")
def install(registry, config):
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.routes import build_router
    from ggwork_pick.service import PickService, SyncSettings

    data_dir = Path(config.get("data_dir") or Path(os.environ.get("DEER_FLOW_HOME", ".deer-flow")) / "pick")
    service = PickService(data_dir, SyncSettings.from_env())
    registry.service(service)
    registry.routers((build_router(service),))
    registry.task_lifecycle(PickLifecycle(service))
