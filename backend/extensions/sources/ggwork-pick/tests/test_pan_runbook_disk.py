"""The container half of the weekly pan check in docs/pick-workbench/supabase.md section 6.

pan_scan lists the raw feed files and the threads' externalized tool outputs that hold a hit, and refuses to give a list when
it could not look everywhere. Raw files are deleted, never rewritten, and only once the source is fixed and the sync paused:
a pull from an unfixed source writes the file back. The runbook's shell commands run with bash in a temporary directory
standing in for /data, under a byte-wise (C) and a UTF-8 locale; the data comes from the real sync and import code, on
SQLite and, when PICK_TEST_PG_URL is set, on PostgreSQL.
"""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from engines import host_engine
from pan_runbook import (
    CHECK,
    CODE_NOTE,
    LOCALES,
    PASSWORD_NBSP,
    PASSWORD_NEWLINE,
    PASSWORD_VT,
    Scan,
    clean_scan,
    feed_signal,
    feed_transport,
    password,
    pattern_of,
    pull,
    runbook_steps,
    scan,
    shell,
    step_lines,
)
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_realshort_sync import TOKEN, feed_row

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="the runbook's commands are shell commands")
TOOL_RESULTS = "user-data/outputs/.tool-results"


@pytest_asyncio.fixture
async def files_service(pick_db_url, tmp_path):
    """The workbench with its data directory at <tmp>/pick and an empty <tmp>/users: tmp_path stands in for /data."""
    from ggwork_pick.service import PickService

    (tmp_path / "users").mkdir()
    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "pick")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


def _upload(note: str, *, ensure_ascii: bool) -> bytes:
    row = {"source": "synthetic", "source_id": "1", "language": "en", "title": "T", "signals": [{"kind": "r", "source_ref": "x", "note": note}]}
    return json.dumps([row], ensure_ascii=ensure_ascii).encode()


def _feed_removal() -> str:
    """Step 7's command that deletes the raw feed files with a hit."""
    [removal] = [line for line in step_lines(7) if line.startswith("grep ") and "| xargs -0r rm" in line]
    return removal


@pytest.mark.asyncio
async def test_raw_files_with_a_hit_are_found_and_deleting_them_is_safe(files_service):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.sync import RealShortSync

    home = files_service.data_dir.parent
    shared, alice = PickRepository.shared(files_service.session_factory), PickRepository(files_service.session_factory, "alice")
    importer = Importer(alice, files_service.data_dir)
    # Each file holds exactly one hit, each in a different form.
    first = await pull(files_service, [feed_row(1, signals=[feed_signal(1, note=PASSWORD_NEWLINE)]), feed_row(4)])  # backslash-n in the feed
    escaped_code = await importer.catalog(_upload("提取码 x7k2", ensure_ascii=True), "json")  # 提取码 as escapes
    escaped_nbsp = await importer.catalog(_upload(PASSWORD_NBSP, ensure_ascii=True), "json")  # keyword, U+00A0 and colon all escaped
    vertical_tab = await importer.catalog(_upload(PASSWORD_VT, ensure_ascii=False), "json")  # U+000B, which JSON always escapes
    csv = 'source,source_id,language,title\nsynthetic,2,en,"剧名 密码\n：ab12"\n'  # a quoted CSV field across two lines
    across_lines = await importer.catalog(csv.encode(), "csv")
    em_space = await importer.catalog(_upload(password(0x2003), ensure_ascii=False), "json")  # a raw U+2003
    uploads = [first["catalog_batch_id"], escaped_code["id"], escaped_nbsp["id"], vertical_tab["id"], across_lines["id"], em_space["id"]]
    fixed = [feed_row(1), feed_row(4)]
    second = await pull(files_service, fixed)
    paths = {row["id"]: row["raw_blob_path"] for row in await alice.batches()}
    assert "提取码" not in Path(paths[escaped_code["id"]]).read_text(encoding="utf-8")
    # The same list whether the ssh session's grep compares characters (C.UTF-8) or bytes (C).
    for locale in LOCALES:
        assert scan(home, locale=locale) == Scan(0, sorted(paths[batch_id] for batch_id in uploads), "", ""), locale
    shell(home, _feed_removal(), locale="C")
    assert all(clean_scan(home, locale=locale) for locale in LOCALES)
    assert not any(Path(paths[batch_id]).exists() for batch_id in uploads)
    # The next sync prunes the old batches, one of whose files is already gone.
    clean = await pull(files_service, [*fixed, feed_row(5)], keep_batches=1)
    statuses = {row["id"]: row["status"] for row in await shared.batches()}
    assert (statuses[first["catalog_batch_id"]], statuses[second["catalog_batch_id"]]) == ("pruned", "pruned")
    assert clean_scan(home)
    # Nothing reads a raw file at runtime; only an import of the same content looks at it again, and rewrites a missing one.
    blob = Path((await shared.current_batch("catalog"))["raw_blob_path"])
    blob.unlink()
    assert (await pull(files_service, [*fixed, feed_row(5)]))["catalog_batch_id"] == clean["catalog_batch_id"] and blob.exists()
    # An overwritten file fails that same import, which is why the runbook deletes and never rewrites.
    blob.write_bytes(b"[]")
    transport = feed_transport([*fixed, feed_row(5)], scope="s")
    refused = await RealShortSync(files_service, base_url="https://realshort.test", token=TOKEN, transport=transport).run("manual")
    assert refused["status"] == "failed" and "校验失败" in refused["error"]


@pytest.mark.asyncio
async def test_a_source_left_unfixed_writes_the_file_back_so_the_runbook_fixes_it_and_pauses_the_sync(files_service, monkeypatch):
    from ggwork_pick.service import PickService, SyncSettings

    home = files_service.data_dir.parent
    noted = [feed_row(1, signals=[feed_signal(1, note=CODE_NOTE)]), feed_row(4)]
    await pull(files_service, noted)
    [raw] = scan(home).feed_files
    # Cleaned on disk alone while RealShort still has the note: the next pull, scheduled or by hand, writes it back.
    shell(home, _feed_removal())
    assert clean_scan(home)
    await pull(files_service, noted)
    assert scan(home) == Scan(0, [raw], "", "")
    # In the runbook's order (fix the source, pause the sync, clean up, check, resume) the pull after resuming stays clean.
    fixed = [feed_row(1), feed_row(4)]
    await pull(files_service, fixed)
    shell(home, _feed_removal())
    assert clean_scan(home)
    await pull(files_service, fixed)
    assert clean_scan(home)

    # Any real hit, one on disk alone included, starts at step 3; the pause lasts until step 9, after the last check.
    steps = runbook_steps()
    assert "不管在库里还是只在磁盘上，都从第 3 步做起" in steps[2] and "只在磁盘上的也一样" in steps[3]
    assert "同步仍然暂停着" in steps[8] and "恢复同步" in steps[9]
    # The switch step 3 turns off: without that variable the process neither schedules pulls nor takes a manual one.
    [variable] = re.findall(r"先把 `(PICK_REALSHORT_FEED_\w+)` 的值存进密码管理器，再删掉这个变量", steps[3])
    assert f"`{variable}` 的原值加回去" in steps[9]
    monkeypatch.setenv("PICK_REALSHORT_FEED_URL", "https://realshort.test")
    monkeypatch.setenv("PICK_REALSHORT_FEED_TOKEN", TOKEN)
    assert SyncSettings.from_env().configured
    monkeypatch.delenv(variable)
    paused = PickService(home / "paused", SyncSettings.from_env())
    paused._start_schedule()
    assert paused.realshort_sync() is None and paused.scheduler is None


def test_the_scan_stops_at_a_symbolic_link_instead_of_giving_a_list(tmp_path):
    home = tmp_path / "data"
    (home / "pick").mkdir(parents=True)
    (home / "users").mkdir()
    assert all(clean_scan(home, locale=locale) for locale in LOCALES)
    # A thread directory that is a link: find does not follow it, so its hit would be missing from an otherwise empty list.
    outside = tmp_path / "elsewhere/thread-linked"
    (outside / TOOL_RESULTS).mkdir(parents=True)
    (outside / TOOL_RESULTS / "query_candidates-0123456789ab.txt").write_text(CODE_NOTE, encoding="utf-8")
    link = home / "users/alice/threads/thread-linked"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    for locale in LOCALES:
        stopped = scan(home, locale=locale)
        assert (stopped.code, stopped.feed_files, stopped.threads) == (2, None, None) and str(link) in stopped.errors, locale
    link.unlink()
    assert clean_scan(home)


@pytest.mark.parametrize("kind", ["dangling threads root", "looping threads root", "users root"])
def test_the_scan_stops_when_a_root_is_a_link_instead_of_skipping_it(tmp_path, kind):
    # The legacy /data/threads is optional, so the scan checks whether it is there; a link that does not resolve must still
    # count as there, or a broken or looping root would drop out of the scan and the list would look complete.
    home = tmp_path / "data"
    (home / "pick").mkdir(parents=True)
    (home / "users").mkdir()
    assert clean_scan(home)
    if kind == "users root":
        root = home / "users"
        root.rmdir()
        (tmp_path / "elsewhere").mkdir()
        root.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    else:
        root = home / "threads"
        root.symlink_to(tmp_path / "gone" if kind == "dangling threads root" else root, target_is_directory=True)
    for locale in LOCALES:
        stopped = scan(home, locale=locale)
        assert (stopped.code, stopped.feed_files, stopped.threads) == (2, None, None) and str(root) in stopped.errors, locale


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads any file")
def test_the_scan_stops_at_a_file_it_cannot_read_instead_of_giving_a_list(tmp_path):
    home = tmp_path / "data"
    (home / "pick").mkdir(parents=True)
    locked = home / "users/alice/threads/thread-locked" / TOOL_RESULTS / "query_candidates-0123456789ab.txt"
    locked.parent.mkdir(parents=True)
    locked.write_text(CODE_NOTE, encoding="utf-8")
    locked.chmod(0)
    try:
        for locale in LOCALES:
            stopped = scan(home, locale=locale)
            assert (stopped.code, stopped.feed_files, stopped.threads) == (2, None, None) and str(locked) in stopped.errors, locale
    finally:
        locked.chmod(0o600)
    assert scan(home) == Scan(0, [], "thread-locked", "")


def test_the_runbook_gives_the_sha256_of_its_pan_line(tmp_path):
    # The image has no docs/, so PAN is pasted. Copied from a rendered page, an invisible whitespace character can turn into a
    # space, a line break or another whitespace character of the same length; the digest shows any of them.
    if shutil.which("sha256sum") is None:
        pytest.skip("needs sha256sum")
    [command] = [line for line in step_lines(1) if line == 'printf %s "$PAN" | sha256sum']
    [expected] = re.findall(r'`printf %s "\$PAN" \| sha256sum` 应输出 `([0-9a-f]{64})`', runbook_steps()[1])
    pattern = pattern_of(CHECK.read_text(encoding="utf-8"))
    assert expected == hashlib.sha256(pattern.encode()).hexdigest()
    assert expected != hashlib.sha256(pattern.replace(chr(0x2003), chr(0x2002)).encode()).hexdigest()
    for locale in LOCALES:
        assert shell(tmp_path, command, locale=locale) == sorted([expected, "-"]), locale
