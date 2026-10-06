"""Authenticated UI operations. The Agent never receives this write authority."""

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from deerflow_extension_api.auth import resolve_principal
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import Field, ValidationError

from ggwork_pick.contracts import UNSTORABLE_TEXT, StrictInput
from ggwork_pick.imports import MAX_BYTES, Importer
from ggwork_pick.mirror.status import mirror_status
from ggwork_pick.observe.status import obs_status
from ggwork_pick.observe.trends_table import trends_table
from ggwork_pick.repository import SHARED_OWNER, ConflictError, PickRepository
from ggwork_pick.selection import NotesGone, ReplayGone, ReplayUnrunnable, SelectionService, result_view

logger = logging.getLogger(__name__)

MANUAL_SYNC_COOLDOWN = timedelta(minutes=5)
# After a failure the button stays usable, but a broken source is not hammered by repeated clicks.
FAILED_SYNC_BACKOFF = timedelta(minutes=1)


async def mirror_view(service, shared: PickRepository) -> dict | None:
    """/sync's mirror key (P2-8b). It is extra to the v1 sync status, so a failure to read it is reported as
    {"error": <class>} (logged by class only: a database message can quote a value) instead of failing the request."""
    try:
        return await mirror_status(shared, enabled=service.mirror_enabled(), sync_running=service.sync_lock.locked(), now=datetime.now(UTC))
    except Exception as exc:
        logger.warning("[pick-mirror] reading the mirror status for /sync failed: %s", type(exc).__name__)
        return {"error": type(exc).__name__}


async def obs_view(shared: PickRepository) -> dict:
    """/sync's obs key (TR-25, D10): the radar's status and banners at request time. Extra to the v1 sync status like the
    mirror key, so a failed read answers {"error": <class>} (logged by class only) instead of failing the request."""
    try:
        return await obs_status(shared, now=datetime.now(UTC))
    except Exception as exc:
        logger.warning("[pick-obs] reading the observation status for /sync failed: %s", type(exc).__name__)
        return {"error": type(exc).__name__}


TRENDS_TABLE_UNREADABLE = "趋势表暂时读不了，稍后再试"


async def trends_table_view(shared: PickRepository) -> dict:
    """GET /api/pick/obs/trends-table (simplified radar, scope section 6 item 4). A failed read is a 503 with a fixed
    text, logged by class only (a database message can quote a value)."""
    try:
        return await trends_table(shared, now=datetime.now(UTC))
    except Exception as exc:
        logger.warning("[pick-obs] reading the trends table failed: %s", type(exc).__name__)
        raise HTTPException(503, TRENDS_TABLE_UNREADABLE) from None


class SaveInput(StrictInput):
    request_id: str = Field(min_length=1, max_length=128)
    result_id: str = Field(min_length=1, max_length=64)
    item_ids: list[str] = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=2000)


class UpdateInput(StrictInput):
    request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1, strict=True)
    note: str | None = Field(default=None, max_length=2000)
    state: Literal["selected", "removed"] | None = None


def public_batch(row):
    return {key: value for key, value in row.items() if key not in {"owner_id", "raw_blob_path"}}


def public_selection(row):
    return {key: value for key, value in row.items() if key != "owner_id"}


def api_error(exc):
    if isinstance(exc, ConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(422, {"message": "资料字段不符合约定", "fields": [list(e["loc"]) for e in exc.errors()]})
    return HTTPException(400, str(exc))


def csv_cell(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def _printable(value):
    return UNSTORABLE_TEXT.sub("?", value) if isinstance(value, str) else value


class StorableTextRoute(APIRoute):
    """Keep text PostgreSQL cannot store (NUL, lone surrogates) from turning into a 500.

    Bodies are checked by StrictInput. Percent-decoding replaces invalid UTF-8, so NUL is the only such
    character a path or query parameter can carry; PostgreSQL refuses it even as a WHERE value. FastAPI's
    default 422 repeats every invalid input, and a lone surrogate there fails rendering the response
    itself, so validation errors are answered here without the input.
    """

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def storable(request: Request) -> Response:
            params = [*request.path_params.values(), *(value for _, value in request.query_params.multi_items())]
            if any(isinstance(value, str) and UNSTORABLE_TEXT.search(value) for value in params):
                return JSONResponse({"detail": "请求参数含 NUL 字符"}, status_code=422)
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [{"type": e["type"], "loc": [_printable(part) for part in e["loc"]], "msg": _printable(e["msg"])} for e in exc.errors()]
                return JSONResponse({"detail": errors}, status_code=422)

        return storable


def build_router(service):
    router = APIRouter(prefix="/api/pick", tags=["pick-workbench"], route_class=StorableTextRoute)

    def repository(request: Request):
        principal = resolve_principal(request)
        if principal is None or not principal.user_id.strip() or principal.user_id in ("default", SHARED_OWNER):
            raise HTTPException(401, "请先登录")
        if service.session_factory is None:
            raise HTTPException(503, "选剧服务尚未就绪")
        return PickRepository(service.session_factory, principal.user_id)

    async def status_view(record):
        # Only call after an owner-scoped repository read: the host reader is privileged.
        reader = service.run_evidence_reader
        run = await reader.get_run_status(thread_id=record["thread_id"], run_id=record["run_id"]) if reader else None
        status = run.status if run else "unknown"
        if status not in {"pending", "running", "success", "error", "timeout", "interrupted"}:
            status = "unknown"
        # The data_as_of the result froze; a later run reusing its batch does not move it (P2-8a). With the P4-1 switch on,
        # the mirror version the row recorded joins it after that value's key set was checked.
        owner = PickRepository(service.session_factory, record["owner_id"])
        data_as_of = await owner.result_data_as_of(record, emit_mirror_version=service.sync_settings.emits_mirror_version)
        return {**result_view(record), "run_status": status, "data_as_of": data_as_of}

    @router.get("/sync")
    async def sync_status(request: Request):
        repo = repository(request)
        current = await repo.current_batch("catalog")
        info = await repo.batch_info(current["id"]) if current else None
        shared = PickRepository.shared(service.session_factory)
        runs = await shared.sync_runs()
        # The mirror's state (P2-8b): null on SQLite; judged on the shared batches, not on this user's current.
        mirror = await mirror_view(service, shared)
        return {"configured": service.sync_settings.configured, "current": info, "runs": runs, "mirror": mirror, "obs": await obs_view(shared)}

    @router.get("/obs/trends-table")
    async def obs_trends_table(request: Request):
        """The simplified radar's read-only table: the same for every signed-in user."""
        repository(request)
        return await trends_table_view(PickRepository.shared(service.session_factory))

    @router.post("/sync", status_code=202)
    async def sync_now(request: Request):
        repository(request)
        sync = service.realshort_sync()
        if sync is None:
            raise HTTPException(503, "尚未配置 RealShort 数据接口")
        if service.sync_lock.locked():
            return {"status": "already_running"}
        runs = await PickRepository.shared(service.session_factory).sync_runs(limit=1)
        if runs:
            wait = FAILED_SYNC_BACKOFF if runs[0]["status"] == "failed" else MANUAL_SYNC_COOLDOWN
            if datetime.now(UTC) - datetime.fromisoformat(runs[0]["started_at"]) < wait:
                raise HTTPException(429, f"刚同步过，{int(wait.total_seconds() // 60)}分钟内不重复拉取")
        service.spawn(sync.run("manual"))
        return {"status": "started"}

    @router.get("/answer-checks")
    async def list_answer_checks(request: Request, thread_id: str = Query(min_length=1, max_length=64)):
        return {"checks": await repository(request).answer_checks(thread_id)}

    @router.get("/imports")
    async def list_imports(request: Request):
        return {"batches": [public_batch(row) for row in await repository(request).batches()]}

    @router.post("/imports", status_code=201)
    async def import_data(request: Request, kind: Literal["catalog", "knowledge"] = Form(...), files: list[UploadFile] = File(...), source_ref: str = Form("")):
        importer = Importer(repository(request), service.data_dir)
        if not files or len(files) > 50 or (kind == "catalog" and len(files) != 1):
            raise HTTPException(400, "剧库每次导入一份文件；知识每批最多50份")
        try:
            contents, size = [], 0
            for file in files:
                payload = await file.read(MAX_BYTES - size + 1)
                size += len(payload)
                if size > MAX_BYTES:
                    raise HTTPException(413, "资料超过25MB")
                contents.append((payload, file.filename or ""))
            if kind == "catalog":
                payload, filename = contents[0]
                batch = await importer.catalog(payload, filename.rsplit(".", 1)[-1].lower())
            else:
                batch = await importer.knowledge_bundle(
                    [(payload, filename, f"{source_ref}#{filename}" if source_ref else f"upload:{filename}") for payload, filename in contents]
                )
            return public_batch(batch)
        except (ValueError, LookupError) as exc:
            raise api_error(exc) from None
        finally:
            for file in files:
                await file.close()

    @router.get("/results")
    async def list_results(request: Request, thread_id: str):
        return {"results": [await status_view(row) for row in await repository(request).results(thread_id)]}

    @router.get("/results/{result_id}")
    async def get_result(request: Request, result_id: str):
        try:
            return await status_view(await repository(request).result(result_id))
        except LookupError as exc:
            raise api_error(exc) from None

    @router.get("/results/{result_id}/notes")
    async def result_notes(request: Request, result_id: str):
        """Facts rechecked at the result creation time, and each item's row facts, for the card. A separate
        read so the result keeps the shape the frontend parses strictly; an older frontend never asks for it."""
        repo = repository(request)
        try:
            record = await repo.result(result_id)
        except LookupError as exc:
            raise api_error(exc) from None
        data_as_of = await repo.result_data_as_of(record, emit_mirror_version=False)
        try:
            return await SelectionService(repo).notes(record, data_as_of=data_as_of)
        except NotesGone as exc:
            raise HTTPException(410, str(exc)) from None

    @router.get("/replay")
    async def replay(request: Request, result_id: str = Query(min_length=1, max_length=64)):
        """The owner's result re-run on its own batch, for the data page's replay view (plan 2.5 item 4)."""
        repo = repository(request)
        try:
            return await SelectionService(repo).replay(result_id)
        except ReplayGone as exc:
            raise HTTPException(410, str(exc)) from None
        except ReplayUnrunnable as exc:
            # A fixed text: the stored conditions are the owner's, but never echoed back.
            raise HTTPException(409, str(exc)) from None
        except (KeyError, IndexError):
            # Code bugs stay 500s, not a polite "not found".
            raise
        except LookupError as exc:
            raise api_error(exc) from None

    @router.get("/commands/{request_id}")
    async def get_command(request: Request, request_id: str):
        receipt = await repository(request).command_receipt(request_id)
        if receipt is None:
            raise HTTPException(404, "尚无已提交回执")
        return receipt

    @router.get("/selections/export.csv")
    async def export_selections(request: Request):
        rows = await repository(request).selections()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["剧名", "剧场", "语言", "推荐理由", "备注", "保存时间", "来源结果"])
        for row in rows:
            item = row["snapshot_json"]
            writer.writerow(
                [
                    csv_cell(v)
                    for v in [item["title"], item["theater"], item["language"], item["reason"], row["note"], row["created_at"], row["source_result_id"]]
                ]
            )
        return Response(
            "\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="my-selections.csv"'}
        )

    @router.get("/selections")
    async def list_selections(request: Request):
        return {"selections": [public_selection(row) for row in await repository(request).selections()]}

    @router.post("/selections")
    async def save(request: Request, body: SaveInput):
        try:
            repo = repository(request)
            if await repo.command_receipt(body.request_id) is None:
                result = await status_view(await repo.result(body.result_id))
                if result["run_status"] != "success":
                    raise ConflictError("来源运行尚未成功完成，请等待完成或重新选剧")
            return await repo.save_selection(body.request_id, body.result_id, body.item_ids, body.note)
        except (ValueError, LookupError) as exc:
            raise api_error(exc) from None

    @router.patch("/selections/{selection_id}")
    async def update_item(request: Request, selection_id: str, body: UpdateInput):
        try:
            return await repository(request).update_selection(selection_id, body.request_id, body.expected_version, note=body.note, state=body.state)
        except (ValueError, LookupError) as exc:
            raise api_error(exc) from None

    return router
