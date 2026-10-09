"""Read-only authenticated historical radar, independent of live collector state."""

from pathlib import Path
from threading import Lock

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .query import Filters, export_rows, filtered_rows, filters
from .snapshot import load_snapshot


class SnapshotReader:
    """The sealed input is verified once per process, never downloaded or created."""

    def __init__(self, path: Path):
        self.path = path
        self.snapshot = None
        self.lock = Lock()

    def get(self):
        with self.lock:
            if self.snapshot is None:
                try:
                    self.snapshot = load_snapshot(self.path)
                except (OSError, ValueError):
                    raise HTTPException(503, "历史快照尚未就绪或校验失败") from None
            return self.snapshot


def register_radar_routes(router, service, authorize):
    reader = SnapshotReader(service.data_dir / "radar" / "dramas.db")

    def current(request: Request, response: Response):
        authorize(request)
        response.headers["Cache-Control"] = "private, no-store"
        return reader.get()

    @router.get("/radar/stats")
    def stats(snapshot=Depends(current)):
        return snapshot.stats

    @router.get("/radar/dramas")
    def dramas(
        f: Filters = Depends(filters),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        snapshot=Depends(current),
    ):
        rows = filtered_rows(snapshot, f)
        items = [
            {k: v for k, v in row.items() if k not in ("timeline_data", "original", "legacy_replay", "score_differences", "synopsis")}
            for row in rows[offset : offset + limit]
        ]
        return dict(total=len(rows), limit=limit, offset=offset, items=items, snapshot_id=snapshot.manifest["snapshot_id"])

    @router.get("/radar/dramas/{drama_id}")
    def detail(drama_id: int, snapshot=Depends(current)):
        row = snapshot.by_id.get(drama_id)
        if row is None:
            raise HTTPException(404, "找不到这部历史剧目")
        return row

    @router.get("/radar/export")
    def export(f: Filters = Depends(filters), snapshot=Depends(current)):
        return Response(
            export_rows(filtered_rows(snapshot, f), snapshot.manifest["snapshot_id"]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="drama-radar-historical.csv"', "Cache-Control": "private, no-store"},
        )
