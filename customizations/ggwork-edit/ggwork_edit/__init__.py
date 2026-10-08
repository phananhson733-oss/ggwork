"""Native editing extension; business state is independent of the pick mirror."""

from deerflow_extension_api import extension


@extension(api="0.2.1", name="ggwork-edit")
def install(registry, config):
    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.service import EditingService

    service = EditingService()
    registry.service(service)
    registry.routers((build_router(service),))
    registry.bearer_routers((build_worker_router(service),), service)
