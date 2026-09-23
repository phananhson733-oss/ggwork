"""Personal selection data and tools for the DeerFlow workbench."""

import os
from pathlib import Path

from deerflow_extension_api import extension

from ggwork_pick.context import PickLifecycle
from ggwork_pick.routes import build_router
from ggwork_pick.service import PickService, SyncSettings


@extension(api="0.2.1", name="ggwork-pick")
def install(registry, config):
    data_dir = Path(config.get("data_dir") or Path(os.environ.get("DEER_FLOW_HOME", ".deer-flow")) / "pick")
    service = PickService(data_dir, SyncSettings.from_env())
    registry.service(service)
    registry.routers((build_router(service),))
    registry.task_lifecycle(PickLifecycle(service))
