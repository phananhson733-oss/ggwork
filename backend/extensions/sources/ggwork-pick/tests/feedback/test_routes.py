"""Feedback routes must not expose finance data through the shared catalogue status."""

import pytest


@pytest.mark.asyncio
async def test_feedback_routes_require_login_and_default_to_disabled(app_client):
    client, _service = app_client
    assert (await client.get("/api/pick/feedback/status")).status_code == 401
    response = await client.get("/api/pick/feedback/status", headers={"test-owner": "alice"})
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert (await client.post("/api/pick/feedback/sync", headers={"test-owner": "alice"})).status_code == 409


@pytest.mark.asyncio
async def test_feedback_routes_enforce_source_owner_and_redact_status(app_client):
    from ggwork_pick.feedback.sync import FeedbackSyncService

    client, service = app_client
    service.feedback = FeedbackSyncService(service.session_factory, owner_id="alice", enabled=True)
    assert (await client.get("/api/pick/feedback/status", headers={"test-owner": "bob"})).status_code == 403
    assert (await client.post("/api/pick/feedback/sync", headers={"test-owner": "bob"})).status_code == 403
    response = await client.get("/api/pick/feedback/status", headers={"test-owner": "alice"})
    assert response.json()["current"] is None
    assert "owner_id" not in response.text
    assert "manifest_json" not in response.text


def test_feedback_settings_are_explicitly_disabled_and_owner_scoped():
    from ggwork_pick.feedback.settings import FeedbackSettings

    assert FeedbackSettings.from_env({}).enabled is False
    assert FeedbackSettings.from_env({"PICK_FEEDBACK_ENABLED": "true", "PICK_FEEDBACK_OWNER_ID": "alice"}).enabled is False
    settings = FeedbackSettings.from_env({"PICK_FEEDBACK_ENABLED": "1", "PICK_FEEDBACK_OWNER_ID": "alice", "PICK_FEEDBACK_SCHEDULE_ENABLED": "1"})
    assert settings.enabled and settings.scheduled
    with pytest.raises(ValueError):
        FeedbackSettings.from_env({"PICK_FEEDBACK_ENABLED": "1", "PICK_FEEDBACK_OWNER_ID": "system:shared"})
