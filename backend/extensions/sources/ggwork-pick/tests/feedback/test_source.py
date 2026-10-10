"""All fixtures below are synthetic; no credentials or real business records."""

import pytest


class FakeSource:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0

    async def fields(self, table):
        from ggwork_pick.feedback.contracts import SourceField

        return [SourceField(field_id="fldSynthetic", name="合成字段", field_type="text")]

    async def page(self, table, fields, offset):
        from ggwork_pick.feedback.contracts import SourcePage, SourceRecord

        self.calls += 1
        if self.mode == "failure" and self.calls == 13:
            raise RuntimeError("synthetic source interruption")
        changing = str(self.calls) if self.mode == "changing" else "unchanged"
        records = [SourceRecord(record_id=f"rec-{table.key}-{offset}", values={"fldSynthetic": changing})]
        if self.mode == "duplicate":
            records *= 2
        if self.mode == "empty_more":
            records = []
        more = self.mode in ("loop", "empty_more") or (self.mode == "pages" and offset == 0)
        return SourcePage(table_id=table.table_id, records=records, has_more=more, next_offset=0 if self.mode == "loop" else offset + 1 if more else None)


@pytest.mark.asyncio
async def test_complete_scan_reads_all_pages_twice_and_keeps_field_ids():
    from ggwork_pick.feedback.source import read_snapshot

    source = FakeSource("pages")
    snap = await read_snapshot(source)
    assert source.calls == 52
    assert len(snap.tables) == 13
    assert all(len(table.records) == 2 for table in snap.tables)
    assert snap.tables[0].records[0].values == {"fldSynthetic": "unchanged"}
    assert snap.consistency == "bounded_scan"
    assert snap.transform_version == "feedback-v3"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["duplicate", "loop", "empty_more"])
async def test_pagination_failure_is_not_a_complete_snapshot(mode):
    from ggwork_pick.feedback.source import FeedbackSourceError, read_snapshot

    source = FakeSource(mode)
    with pytest.raises(FeedbackSourceError) as error:
        await read_snapshot(source)
    assert error.value.code == "incomplete"
    assert source.calls < 5


@pytest.mark.asyncio
async def test_continuously_changing_source_has_only_one_retry():
    from ggwork_pick.feedback.source import FeedbackSourceError, read_snapshot

    source = FakeSource("changing")
    with pytest.raises(FeedbackSourceError) as error:
        await read_snapshot(source)
    assert error.value.code == "source_changed"
    assert source.calls == 52


@pytest.mark.asyncio
async def test_last_table_failure_never_returns_partial_success():
    from ggwork_pick.feedback.source import read_snapshot

    with pytest.raises(RuntimeError, match="synthetic source interruption"):
        await read_snapshot(FakeSource("failure"))


@pytest.mark.asyncio
async def test_source_partial_status_survives_a_complete_read():
    from ggwork_pick.feedback.contracts import SourceField, SourcePage, SourceRecord
    from ggwork_pick.feedback.source import read_snapshot

    class PartialSource(FakeSource):
        async def fields(self, table):
            return [SourceField(field_id="fldStatus", name="采集状态", field_type="select")]

        async def page(self, table, fields, offset):
            return SourcePage(table_id=table.table_id, records=[SourceRecord(record_id="synthetic", values={"fldStatus": ["部分缺失"]})], has_more=False)

    snapshot = await read_snapshot(PartialSource())
    assert snapshot.source_quality == "partial"
    assert all(table.complete for table in snapshot.tables)
