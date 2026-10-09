import pytest

from ggwork_pick.catalog_sources import export_arguments


def test_catalog_export_rejects_unknown_sources_and_shared_identity():
    for owner, kind, token, table, offset in [
        ("default", "sheet", "RRBAszuhOhNM8StMqRVcGknSnyf", "7ba1a7", 0),
        ("system:shared", "base", "OtnsbnRnwaLmnVsJByscTkFMntd", "tbl5Kzrhuz9B7LTE", 0),
        ("owner", "base", "other", "anything", 0),
        ("owner", "base", "OtnsbnRnwaLmnVsJByscTkFMntd", "tbl5Kzrhuz9B7LTE", -1),
    ]:
        with pytest.raises(ValueError):
            export_arguments(owner, kind, token, table, offset)


def test_only_fixed_read_commands_and_artifact_paths_are_emitted():
    args = export_arguments("owner", "sheet", "RRBAszuhOhNM8StMqRVcGknSnyf", "7ba1a7")
    assert args[:2] == ("sheets", "+csv-get")
    assert args[-2:] == ("--output-path", "source.json")
    args = export_arguments("owner", "base", "OtnsbnRnwaLmnVsJByscTkFMntd", "tbl5Kzrhuz9B7LTE", 2000)
    assert args[:2] == ("base", "+record-list")
    assert args[args.index("--as") + 1] == "user"
    assert args[args.index("--offset") + 1] == "2000"


def test_source_bridge_rejects_flags_that_could_change_identity_or_escape():
    from ggwork_pick.source_lark import request

    with pytest.raises(ValueError):
        request(["auth", "login"])
    with pytest.raises(ValueError):
        request(["sheets", "+csv-get", "--as", "bot"])
    with pytest.raises(ValueError):
        request(["base", "+record-list", "--profile", "other-user"])


def test_catalog_artifact_has_its_own_bounded_size_limit(tmp_path):
    from ggwork_pick.lark_runner import LarkUnavailable, _read_feedback_file

    path = tmp_path / "source.json"
    path.write_bytes(b"x" * 15_033_616)
    with pytest.raises(LarkUnavailable):
        _read_feedback_file(tmp_path, "source.json")
    assert len(_read_feedback_file(tmp_path, "source.json", cap=32 * 1024 * 1024)) == 15_033_616
    with pytest.raises(LarkUnavailable):
        _read_feedback_file(tmp_path, "source.json", cap=1024)
