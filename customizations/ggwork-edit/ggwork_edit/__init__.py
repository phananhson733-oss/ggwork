"""Native editing extension; business state is independent of the pick mirror."""

from deerflow_extension_api import extension


@extension(api="0.2.1", name="ggwork-edit")
def install(registry, config):
    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router
    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.service import EditingService

    service = EditingService()
    relay = Relay(service)
    registry.service(service)
    registry.service(relay)
    registry.routers((build_router(service), build_relay_router(relay)))
    registry.bearer_routers((build_worker_router(service), build_relay_worker_router(relay)), service)
