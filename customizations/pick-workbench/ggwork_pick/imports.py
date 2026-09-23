"""Bounded manual imports: validate the entire batch before publishing it."""

import asyncio
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from uuid import uuid4

from ggwork_pick.contracts import UNSTORABLE_TEXT, DramaInput
from ggwork_pick.repository import PickRepository

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 100_000


def decode_payload(payload: bytes) -> str:
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError("资料为空或超过25MB")
    text = payload.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("资料包含NUL字符")
    return text


def parse_catalog(payload: bytes, format: str, keep_original: bool = True) -> list[dict]:
    text = decode_payload(payload)
    if format == "json":
        raw = json.loads(text)
    elif format == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV缺少列名或存在重复列名")
        raw = []
        for row in reader:
            item = {k: v for k, v in row.items() if v != ""}
            for key in ("tags", "signals", "channel_rules"):
                if key in item:
                    item[key] = json.loads(item[key])
            raw.append(item)
            if len(raw) > MAX_ROWS:
                raise ValueError("剧库超过100000行")
    else:
        raise ValueError("仅支持JSON或CSV剧库")
    if not isinstance(raw, list) or len(raw) > MAX_ROWS or not raw:
        raise ValueError("剧库必须是1至100000行的数组")
    rows, identities = [], set()
    for original in raw:
        drama = DramaInput.model_validate(original)
        row, identity = drama.model_dump(mode="json"), drama.identity
        if identity in identities:
            raise ValueError("同一批次存在重复的来源剧目ID和语种")
        identities.add(identity)
        # Synced batches skip the verbatim copy: the raw blob already holds it, and it doubled every row.
        rows.append({**row, "identity": identity, **({"original": original} if keep_original else {})})
    return rows


def _write_blob(data_dir: Path, owner: str, content_hash: str, payload: bytes) -> str:
    # Owner and incoming filenames never become path components verbatim.
    directory = data_dir / hashlib.sha256(owner.encode()).hexdigest()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / content_hash
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != content_hash:
            raise ValueError("已存原始资料校验失败")
        return str(target)
    temporary = directory / f".{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


class Importer:
    def __init__(self, repository: PickRepository, data_dir: Path):
        self.repository = repository
        self.data_dir = data_dir

    async def catalog(self, payload: bytes, format: str, *, source_as_of: str | None = None, meta: dict | None = None, keep_original: bool = True) -> dict:
        rows = await asyncio.to_thread(parse_catalog, payload, format, keep_original)
        return await self._publish(payload, "catalog", rows, source_as_of=source_as_of, meta=meta)

    async def knowledge(self, payload: bytes, filename: str, source_ref: str) -> dict:
        return await self.knowledge_bundle([(payload, filename, source_ref)])

    async def knowledge_bundle(self, files: list[tuple[bytes, str, str]], *, source_as_of: str | None = None, meta: dict | None = None) -> dict:
        if not files or len(files) > 50 or sum(len(payload) for payload, _, _ in files) > MAX_BYTES:
            raise ValueError("知识批次需1至50份文件，总量不超过25MB")
        documents = []
        seen = set()
        for payload, filename, source_ref in files:
            text = decode_payload(payload)
            if not filename.endswith(".md") or not source_ref.strip() or len(source_ref) > 2048:
                raise ValueError("知识必须是Markdown，并提供来源")
            if UNSTORABLE_TEXT.search(filename) or UNSTORABLE_TEXT.search(source_ref):
                raise ValueError("知识的文件名或来源含 NUL 字符或孤立代理项")
            document_id = hashlib.sha256(source_ref.encode()).hexdigest()
            if document_id in seen:
                raise ValueError("知识批次中存在重复来源")
            seen.add(document_id)
            documents.append(
                dict(
                    document_id=document_id,
                    content_hash=hashlib.sha256(payload).hexdigest(),
                    title=Path(filename).name[:500],
                    source_ref=source_ref,
                    text=text,
                    metadata_json={"filename": Path(filename).name},
                )
            )
        # The immutable original batch is a manifest of all original texts.
        manifest = json.dumps(documents, ensure_ascii=False, sort_keys=True).encode()
        return await self._publish(manifest, "knowledge", documents, source_as_of=source_as_of, meta=meta)

    async def _publish(self, payload: bytes, kind: str, rows: list[dict], *, source_as_of: str | None = None, meta: dict | None = None) -> dict:
        digest = hashlib.sha256(payload).hexdigest()
        path = await asyncio.to_thread(_write_blob, self.data_dir, self.repository.owner_id, digest, payload)
        return await self.repository.publish_import(kind=kind, content_hash=digest, raw_blob_path=path, rows=rows, source_as_of=source_as_of, meta=meta)
