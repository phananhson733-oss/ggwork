"""Native editing extension; business state is independent of the pick mirror."""

from deerflow_extension_api import extension


@extension(api="0.2.1", name="ggwork-edit")
def install(registry, config):
    from ggwork_edit.capability_routes import build_capability_router
    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.planner_routes import build_planner_router
    from ggwork_edit.planner_setup import ConfiguredPlanner
    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router
    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.service import EditingService

    service = EditingService()
    planner = ConfiguredPlanner(service, config.get("planner_model"))
    relay = Relay(service)
    registry.service(service)
    registry.service(planner)
    registry.service(relay)
    registry.task_lifecycle(EditingLifecycle(service))
    registry.routers((build_router(service), build_capability_router(), build_relay_router(relay)))
    registry.bearer_routers((build_worker_router(service), build_planner_router(service, planner), build_relay_worker_router(relay)), service)
