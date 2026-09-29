"""TR-25b: POST /api/pick/obs/decisions (plan TR-25 and its 2026-09-30 note, D12, D24; contract section 12; the TR-35
handoff in progress.md).

The gateway appends a decision the way decisions_state asks: lock_for_append is the transaction's first statement, then
the effective state, refusal and the insert, all in that transaction. The operator is the signed-in user, never a body
field; the table only grows. A request_id is the owner's: sent again with the same body it answers the row it wrote,
with another body it is refused. Both dialects; the PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select

from ggwork_pick.models import obs_decisions
from ggwork_pick.observe import decisions as appender
from ggwork_pick.observe.decisions_state import WATCH_ADD_CAP, read_effective
from ggwork_pick.repository import SHARED_OWNER

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract" / "decisions.json").read_text(encoding="utf-8"))
ROUTE = "/api/pick/obs/decisions"
ALICE, BOB = {"test-owner": "alice"}, {"test-owner": "bob"}
X = json.dumps(["realshort", "UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZQ", "en"], separators=(",", ":"))
SENTINEL = "SENTINEL-7f3a"
# The appender's own seams, taken before any test wraps them.
REAL_INSERT, REAL_LOCK = appender._insert, appender.lock_for_append


def _valid(name: str) -> dict:
    return next(case["value"] for case in FIXTURE["valid"] if case["name"] == name)


def _identity(n: int) -> str:
    return json.dumps(["kalostv", f"kalostv-drama-{n}-en", "en"], separators=(",", ":"))


def _add(n: int, **fields) -> dict:
    return {"kind": "watch_add", "request_id": f"add-{n}", "note": "", "identity": _identity(n), "geo": "US", "active": True, **fields}


def _pause(n: int) -> dict:
    return {"kind": "watch_pause", "request_id": f"pause-{n}", "note": "", "identity": _identity(n), "geo": None, "paused": True}


async def _rows(service) -> list[dict]:
    async with service.session_factory() as session:
        result = await session.execute(select(obs_decisions).order_by(obs_decisions.c.id))
        return [dict(row) for row in result.mappings()]


async def _state(service):
    async with service.session_factory() as session:
        return await read_effective(session)


async def _post_all(client, bodies, headers=ALICE) -> list[int]:
    statuses = []
    for body in bodies:
        statuses.append((await client.post(ROUTE, headers=headers, json=body)).status_code)
    return statuses


# ---- the operator ---------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decisions_owner_required(app_client):
    client, service = app_client
    body = _valid("alert_irrelevant")
    for headers in ({}, {"test-owner": "default"}, {"test-owner": SHARED_OWNER}):
        assert (await client.post(ROUTE, headers=headers, json=body)).status_code == 401
    # Nobody signed in learns nothing about the body either: 401 before it is read, even when it is not JSON.
    assert (await client.post(ROUTE, json={"kind": "alias_delete"})).status_code == 401
    assert (await client.post(ROUTE, content=b"{", headers={"content-type": "application/json"})).status_code == 401
    # The owner is never a body field.
    assert (await client.post(ROUTE, headers=ALICE, json={**body, "owner_id": "bob"})).status_code == 422
    assert await _rows(service) == []

    assert (await client.post(ROUTE, headers=ALICE, json=body)).status_code == 201
    assert (await client.post(ROUTE, headers=BOB, json={**body, "request_id": "bob-1"})).status_code == 201
    assert [(row["owner_id"], row["request_id"]) for row in await _rows(service)] == [("alice", body["request_id"]), ("bob", "bob-1")]


@pytest.mark.asyncio
async def test_decisions_need_the_database(app_client):
    client, service = app_client
    opened, service.session_factory = service.session_factory, None
    try:
        assert (await client.post(ROUTE, headers=ALICE, json=_valid("alert_irrelevant"))).status_code == 503
    finally:
        service.session_factory = opened


# ---- the contract's nine kinds --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_contract_decision_is_appended_as_sent(app_client):
    client, service = app_client
    # The fixture's bodies share one request_id; a request_id is one decision, so each gets its own here.
    sent = [case["value"] if case["name"] == "padded_request_id" else {**case["value"], "request_id": f"{case['name']}-1"} for case in FIXTURE["valid"]]
    answers = []
    for body in sent:
        response = await client.post(ROUTE, headers=ALICE, json=body)
        assert response.status_code == 201, body
        answers.append(response.json())
    rows = await _rows(service)
    assert [row["id"] for row in rows] == [answer["id"] for answer in answers] == sorted(answer["id"] for answer in answers)
    for body, row, answer in zip(sent, rows, answers, strict=True):
        # StrictInput strips whitespace: what is stored is what the contract model holds, the request_id included.
        stripped = {key: value.strip() if isinstance(value, str) else value for key, value in body.items()}
        assert row["payload_json"] == stripped
        assert (row["kind"], row["request_id"], row["owner_id"]) == (stripped["kind"], stripped["request_id"], "alice")
        assert answer == {"id": row["id"], "kind": row["kind"], "request_id": row["request_id"], "created_at": row["created_at"], "replayed": False}
    assert rows[-1]["request_id"] == "r-1"
    assert (await _state(service)).version == rows[-1]["id"]


@pytest.mark.asyncio
async def test_a_body_outside_the_contract_is_refused_without_echoing_it(app_client):
    client, service = app_client
    for case in FIXTURE["invalid"]:
        response = await client.post(ROUTE, headers=ALICE, json=case["value"])
        assert response.status_code == 422, case["name"]
        problems = response.json()["detail"]["problems"]
        assert any(problem.endswith(f"（{case['error']['type']}）") for problem in problems), (case["name"], problems)
    # Where and which rule, never what was sent: not a tag, an unexpected key, a value, or text that is not JSON.
    body = _valid("alert_irrelevant")
    extra = await client.post(ROUTE, headers=ALICE, json={**body, SENTINEL: "x"})
    assert extra.json()["detail"]["problems"] == ["alert_irrelevant.<extra>（extra_forbidden）"]
    for sent in ({**body, "kind": SENTINEL}, {**body, "note": SENTINEL * 100}, {**body, "alert_id": SENTINEL}, {**body, "note": f"{SENTINEL}\x00"}, [SENTINEL]):
        response = await client.post(ROUTE, headers=ALICE, json=sent)
        assert response.status_code == 422 and SENTINEL not in response.text, (sent, response.text)
    not_json = await client.post(ROUTE, headers={**ALICE, "content-type": "application/json"}, content=f'{{"{SENTINEL}'.encode())
    assert not_json.status_code == 422 and SENTINEL not in not_json.text
    assert not_json.json()["detail"]["problems"] == ["body（json_invalid）"]
    too_big = await client.post(ROUTE, headers=ALICE, json={**body, "note": "x" * (appender.MAX_BODY_BYTES + 1)})
    assert too_big.status_code == 413
    assert await _rows(service) == []


# ---- append only ----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decisions_append_only(app_client):
    client, service = app_client
    confirm = {**_valid("correspondence_confirm"), "request_id": "confirm-1"}
    revoke = {**_valid("correspondence_revoke"), "request_id": "revoke-1"}
    assert (await client.post(ROUTE, headers=ALICE, json=confirm)).status_code == 201
    first = await _rows(service)
    assert (await _state(service)).confirmed_key(confirm["identity"]) is not None
    assert (await client.post(ROUTE, headers=ALICE, json=revoke)).status_code == 201
    rows = await _rows(service)
    # The revocation is a new row; the confirmation's row is untouched, and the state reads both in id order.
    assert rows[0] == first[0] and [row["kind"] for row in rows] == ["correspondence_confirm", "correspondence_revoke"]
    assert (await _state(service)).confirmed_key(confirm["identity"]) is None
    # No way to change or remove a row: the route takes POST only, and nothing answers under a row's id.
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert (await client.request(method, ROUTE, headers=ALICE)).status_code == 405, method
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert (await client.request(method, f"{ROUTE}/{rows[0]['id']}", headers=ALICE)).status_code == 404, method
    assert await _rows(service) == rows


@pytest.mark.asyncio
async def test_a_request_id_is_the_owners_and_answers_its_row_again(app_client):
    client, service = app_client
    body = _add(1)
    first = await client.post(ROUTE, headers=ALICE, json=body)
    again = await client.post(ROUTE, headers=ALICE, json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == {**first.json(), "replayed": True}
    # The same request_id with another body is a mistake, not a new decision.
    other = await client.post(ROUTE, headers=ALICE, json={**body, "active": False})
    assert other.status_code == 409
    assert len(await _rows(service)) == 1
    # Another owner's request_id space is its own.
    assert (await client.post(ROUTE, headers=BOB, json=body)).status_code == 201
    assert [row["owner_id"] for row in await _rows(service)] == ["alice", "bob"]


# ---- the cap (design 4.6 rule 5) -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_add_cap_50(app_client):
    client, service = app_client
    assert await _post_all(client, [_add(n) for n in range(1, WATCH_ADD_CAP + 1)]) == [201] * WATCH_ADD_CAP
    refused = await client.post(ROUTE, headers=ALICE, json=_add(WATCH_ADD_CAP + 1))
    assert refused.status_code == 409
    assert str(WATCH_ADD_CAP) in refused.json()["detail"] and "撤回" in refused.json()["detail"]
    assert len(await _rows(service)) == WATCH_ADD_CAP
    # At the cap: re-adding an entry in effect changes nothing and is not refused; a withdrawal frees one slot.
    assert (await client.post(ROUTE, headers=ALICE, json=_add(7, request_id="add-7-again"))).status_code == 201
    assert (await client.post(ROUTE, headers=ALICE, json=_add(3, request_id="withdraw-3", active=False))).status_code == 201
    assert (await client.post(ROUTE, headers=ALICE, json=_add(WATCH_ADD_CAP + 1, request_id="add-51-again"))).status_code == 201
    state = await _state(service)
    assert (len(state.watch_added), state.over_cap) == (WATCH_ADD_CAP, ())
    assert (_identity(3), "US") not in state.watch_added and (_identity(WATCH_ADD_CAP + 1), "US") in state.watch_added


# ---- one append at a time (TR-35 handoff: the route-level race) -----------------------------------------------------


class _Race:
    """Two appends, the first held open right after its insert. `events` records, in order, each lock taken (with the
    largest id the locking transaction can see) and the moment the first append is let go."""

    def __init__(self, monkeypatch, first_request_id: str):
        self.first_request_id = first_request_id
        self.events: list[tuple[str, int | None]] = []
        self.inserted, self.release, self.second_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def insert_then_hold(session, row):
            appended_id = await REAL_INSERT(session, row)
            if row["request_id"] == self.first_request_id:
                self.inserted.set()
                await self.release.wait()
            return appended_id

        async def lock_and_note(session):
            if self.inserted.is_set():
                self.second_waiting.set()
            await REAL_LOCK(session)
            self.events.append(("locked", (await session.execute(select(func.max(obs_decisions.c.id)))).scalar()))

        monkeypatch.setattr(appender, "_insert", insert_then_hold)
        monkeypatch.setattr(appender, "lock_for_append", lock_and_note)

    async def run(self, client, first: dict, second: dict, second_owner: dict):
        held = asyncio.create_task(client.post(ROUTE, headers=ALICE, json=first))
        await asyncio.wait_for(self.inserted.wait(), 10)
        waiting = asyncio.create_task(client.post(ROUTE, headers=second_owner, json=second))
        await asyncio.wait_for(self.second_waiting.wait(), 10)  # the second append has reached the lock
        await asyncio.sleep(0.2)
        self.events.append(("released", None))
        self.release.set()
        return await held, await waiting


@pytest.mark.asyncio
async def test_concurrent_appends_commit_in_id_order_and_hold_the_cap(app_client, monkeypatch):
    client, service = app_client
    assert await _post_all(client, [_add(n) for n in range(1, WATCH_ADD_CAP - 1)]) == [201] * (WATCH_ADD_CAP - 2)
    # Another owner waits too (one lock for the table, not one per owner): its transaction takes the lock only after
    # the first one committed, and sees that row. A smaller id never commits after a larger one is visible.
    race = _Race(monkeypatch, "add-49")
    first, second = await race.run(client, _add(WATCH_ADD_CAP - 1), _pause(100), BOB)
    assert race.events == [("locked", WATCH_ADD_CAP - 2), ("released", None), ("locked", WATCH_ADD_CAP - 1)]
    assert (first.status_code, second.status_code) == (201, 201)
    assert (first.json()["id"], second.json()["id"]) == (WATCH_ADD_CAP - 1, WATCH_ADD_CAP)
    # Two additions racing for the last slot: exactly one lands, the other sees it and is refused.
    race = _Race(monkeypatch, "add-50")
    first, second = await race.run(client, _add(WATCH_ADD_CAP), _add(WATCH_ADD_CAP + 1), ALICE)
    assert race.events == [("locked", WATCH_ADD_CAP), ("released", None), ("locked", WATCH_ADD_CAP + 1)]
    assert (first.status_code, second.status_code) == (201, 409)
    state = await _state(service)
    assert (state.version, len(state.watch_added), state.over_cap) == (WATCH_ADD_CAP + 1, WATCH_ADD_CAP, ())


# ---- a log this gateway cannot apply (decisions.md) -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, payload",
    [
        ("alias_delete", {"kind": "alias_delete", "request_id": "manual-1", "note": "内部备注：不该出现在任何回答里"}),
        ("alert_irrelevant", {"kind": "alias_confirm", "request_id": "manual-1", "note": "内部备注：不该出现在任何回答里", "alias_id": 5}),
    ],
)
async def test_an_unreadable_log_refuses_every_write_without_its_content(app_client, caplog, kind, payload):
    client, service = app_client
    earlier = _add(9, request_id="before-the-bad-row")
    assert (await client.post(ROUTE, headers=ALICE, json=earlier)).status_code == 201
    async with service.session_factory() as session, session.begin():
        await session.execute(
            insert(obs_decisions).values(kind=kind, request_id="manual-1", owner_id="ops", payload_json=payload, created_at="2026-09-30T01:00:00.000000+00:00")
        )
    before = await _rows(service)
    with caplog.at_level(logging.ERROR, logger="ggwork_pick.routes"):
        # A revocation cannot get through, nor a repeat of a request answered before the row appeared.
        for body in (_valid("correspondence_revoke"), _add(1), earlier):
            response = await client.post(ROUTE, headers=ALICE, json=body)
            assert response.status_code == 503
            assert "内部备注" not in response.text and "decisions.md" in response.json()["detail"]
    assert await _rows(service) == before
    logged = [record.getMessage() for record in caplog.records if record.name == "ggwork_pick.routes"]
    assert len(logged) == 3 and all(f"第 {before[-1]['id']} 行" in message and "内部备注" not in message for message in logged)
