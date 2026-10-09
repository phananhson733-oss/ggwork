import pytest

from ggwork_pick.native_source import NativeSourceProcess
from ggwork_pick.service import SyncSettings


def test_native_mode_replaces_paused_site_only_when_explicitly_enabled():
    env = {"PICK_REALSHORT_FEED_URL": "https://site.test", "PICK_REALSHORT_FEED_TOKEN": "fixture"}
    assert SyncSettings.from_env(env).feed_url == "https://site.test"
    assert SyncSettings.from_env({**env, "PICK_SOURCE_ENABLED": "1"}).feed_url == "http://127.0.0.1:8003"
    assert SyncSettings.from_env({**env, "PICK_SOURCE_ENABLED": "true"}).feed_url == "https://site.test"


@pytest.mark.asyncio
async def test_native_process_requires_explicit_owner_and_database(monkeypatch):
    monkeypatch.setenv("PICK_SOURCE_ENABLED", "1")
    monkeypatch.setenv("PICK_SOURCE_OWNER_ID", "default")
    service = NativeSourceProcess()
    with pytest.raises(ValueError, match="operator owner"):
        await service.start()
    assert service.process is None


def test_private_resource_owner_cannot_be_selected_by_another_user(monkeypatch):
    from ggwork_pick.native_source import require_resource_owner

    monkeypatch.setenv("PICK_SOURCE_OWNER_ID", "owner-one")
    require_resource_owner("owner-one")
    for other in ("", "default", "system:shared", "owner-two"):
        with pytest.raises(PermissionError):
            require_resource_owner(other)
